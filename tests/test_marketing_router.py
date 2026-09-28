"""`/marketing` — tarjeta «Dato del día» con datos reales.

Sin Chromium instalado en este entorno de test (igual que en el VPS antes de
correr `playwright install-deps`), así que `card.png` se prueba por el
camino degradado: 503 explicando cómo instalarlo, nunca una pila cruda.
"""

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from saimc.models import (
    ContentType,
    Currency,
    PriceHistory,
    Product,
    Province,
    RawIngestion,
    SaleFormat,
    SourceType,
)
from saimc.models.raw_ingestion import ProcessingStatus
from saimc.web.app import app
from saimc.web.deps import get_db


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    def _get_db_override() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_db] = _get_db_override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def datos(session: Session) -> None:
    """Arroz piloto en La Habana (barata, mucha muestra) y Santiago (cara,
    poca muestra); Pollo fuera del foco."""
    arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True)
    pollo = Product(name="Pollo", category="carnes", unit="lb", aliases=[], is_pilot=False)
    habana = Province(code="hab", name="La Habana")
    santiago = Province(code="scu", name="Santiago de Cuba")
    session.add_all([arroz, pollo, habana, santiago])
    session.commit()
    origen = RawIngestion(
        source_type=SourceType.INBOX_TEXT,
        file_path="raw/m.dat",
        content_hash="hash-marketing",
        content_type=ContentType.TEXT,
        processing_status=ProcessingStatus.CURATED,
        captured_at=datetime(2026, 9, 20, tzinfo=UTC),
    )
    session.add(origen)
    session.flush()
    hoy = date.today()

    def precio(producto: Product, provincia: Province, hace: int, valor: str) -> None:
        session.add(
            PriceHistory(
                product_id=producto.id,
                province_id=provincia.id,
                price=Decimal(valor),
                currency=Currency.CUP.value,
                source_id=origen.id,
                unit="lb",
                sale_format=SaleFormat.RETAIL.value,
                quantity=Decimal("1"),
                published_date=hoy - timedelta(days=hace),
            )
        )

    precio(arroz, habana, 3, "300")
    precio(arroz, habana, 2, "320")
    precio(arroz, habana, 1, "350")
    precio(arroz, santiago, 2, "400")
    precio(pollo, habana, 1, "700")
    session.commit()


class TestPaginaMarketing:
    def test_lista_solo_productos_del_foco(
        self, client: TestClient, datos: None
    ) -> None:
        respuesta = client.get("/marketing")

        assert respuesta.status_code == 200
        assert "Arroz" in respuesta.text
        assert "Pollo" not in respuesta.text

    def test_con_producto_elegido_muestra_la_vista_previa_y_el_boton_de_descarga(
        self, client: TestClient, datos: None
    ) -> None:
        respuesta = client.get("/marketing", params={"product": "Arroz"})

        assert respuesta.status_code == 200
        assert "/marketing/preview?product=Arroz" in respuesta.text
        assert "/marketing/card.png?product=Arroz" in respuesta.text

    def test_producto_fuera_de_foco_no_arma_tarjeta(
        self, client: TestClient, datos: None
    ) -> None:
        respuesta = client.get("/marketing", params={"product": "Pollo"})

        assert respuesta.status_code == 200
        assert "no está en el foco" in respuesta.text


class TestVistaPreviaHTML:
    def test_trae_las_cifras_reales_de_las_dos_provincias(
        self, client: TestClient, datos: None
    ) -> None:
        respuesta = client.get("/marketing/preview", params={"product": "Arroz"})

        assert respuesta.status_code == 200
        assert "Arroz" in respuesta.text
        assert "La Habana" in respuesta.text
        assert "Santiago de Cuba" in respuesta.text
        assert "300" in respuesta.text and "350" in respuesta.text
        assert "CUP/lb" in respuesta.text
        assert "3 anuncios" in respuesta.text  # La Habana, n=3
        assert "1 anuncio<" in respuesta.text  # Santiago, n=1, sin "s"

    def test_la_habana_queda_marcada_como_mas_barata(
        self, client: TestClient, datos: None
    ) -> None:
        respuesta = client.get("/marketing/preview", params={"product": "Arroz"})

        assert "más barata" in respuesta.text

    def test_producto_inexistente_da_404(self, client: TestClient, datos: None) -> None:
        respuesta = client.get("/marketing/preview", params={"product": "Aguacate"})

        assert respuesta.status_code == 404


class TestDescargaPNG:
    def test_sin_chromium_instalado_degrada_a_503_explicando_como_instalarlo(
        self, client: TestClient, datos: None
    ) -> None:
        respuesta = client.get("/marketing/card.png", params={"product": "Arroz"})

        assert respuesta.status_code == 503
        assert "playwright" in respuesta.text.lower()

    def test_producto_fuera_de_foco_da_404_antes_de_intentar_renderizar(
        self, client: TestClient, datos: None
    ) -> None:
        respuesta = client.get("/marketing/card.png", params={"product": "Pollo"})

        assert respuesta.status_code == 404
