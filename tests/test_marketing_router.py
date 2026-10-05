"""`/marketing` — las 9 tarjetas de la campaña, con datos reales.

`card.png` se prueba por el camino degradado (503 explicando cómo instalarlo,
nunca una pila cruda) simulando un Chromium que no arranca: el resultado no
depende de si la máquina que corre los tests tiene Chromium o no.
"""

import sys
import types
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
from saimc.web.auth import require_operator
from saimc.web.deps import get_db


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    def _get_db_override() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_db] = _get_db_override
    # La puerta del operador tiene cobertura propia en
    # `test_control_marketing_auth.py`; acá se prueba lo que hay detrás.
    app.dependency_overrides[require_operator] = lambda: None
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def datos(session: Session) -> None:
    """Arroz piloto en La Habana (subió de una semana a la otra, mucha
    muestra) y Santiago (una sola observación reciente); Pollo fuera del foco."""
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
                provenance="human",
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

    # Semana actual (0-6 días atrás).
    precio(arroz, habana, 3, "300")
    precio(arroz, habana, 2, "320")
    precio(arroz, habana, 1, "350")
    precio(arroz, santiago, 2, "400")
    precio(pollo, habana, 1, "700")
    # Semana anterior (7-13 días atrás): Arroz en La Habana más barato, para
    # que "Cómo se movió" tenga con qué comparar.
    precio(arroz, habana, 10, "250")
    precio(arroz, habana, 9, "260")
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
        respuesta = client.get("/marketing", params={"kind": "dato-del-dia", "product": "Arroz"})

        assert respuesta.status_code == 200
        assert "/marketing/preview/dato-del-dia?" in respuesta.text
        assert "/marketing/card/dato-del-dia.png?" in respuesta.text

    def test_producto_fuera_de_foco_no_arma_tarjeta(
        self, client: TestClient, datos: None
    ) -> None:
        respuesta = client.get("/marketing", params={"kind": "dato-del-dia", "product": "Pollo"})

        assert respuesta.status_code == 200
        assert "no está en el foco" in respuesta.text

    def test_una_tarjeta_sin_producto_no_pide_seleccion_de_producto(
        self, client: TestClient, datos: None
    ) -> None:
        respuesta = client.get("/marketing", params={"kind": "carrusel-cierre"})

        assert respuesta.status_code == 200
        assert "/marketing/preview/carrusel-cierre?" in respuesta.text


class TestVistaPreviaDatoDelDia:
    def test_trae_las_cifras_reales_de_las_dos_provincias(
        self, client: TestClient, datos: None
    ) -> None:
        respuesta = client.get(
            "/marketing/preview/dato-del-dia", params={"product": "Arroz"}
        )

        assert respuesta.status_code == 200
        assert "Arroz" in respuesta.text
        assert "La Habana" in respuesta.text
        assert "Santiago de Cuba" in respuesta.text
        assert "300" in respuesta.text and "350" in respuesta.text
        assert "CUP/lb" in respuesta.text

    def test_producto_inexistente_da_404(self, client: TestClient, datos: None) -> None:
        respuesta = client.get(
            "/marketing/preview/dato-del-dia", params={"product": "Aguacate"}
        )

        assert respuesta.status_code == 404

    def test_tarjeta_desconocida_da_404(self, client: TestClient, datos: None) -> None:
        respuesta = client.get(
            "/marketing/preview/no-existe", params={"product": "Arroz"}
        )

        assert respuesta.status_code == 404


class TestVistaPreviaComoSeMovio:
    def test_trae_el_cambio_de_la_habana(self, client: TestClient, datos: None) -> None:
        respuesta = client.get(
            "/marketing/preview/como-se-movio", params={"product": "Arroz"}
        )

        assert respuesta.status_code == 200
        assert "Arroz" in respuesta.text
        assert "La Habana" in respuesta.text

    def test_producto_fuera_de_foco_da_404(self, client: TestClient, datos: None) -> None:
        respuesta = client.get(
            "/marketing/preview/como-se-movio", params={"product": "Pollo"}
        )

        assert respuesta.status_code == 404


class TestVistaPreviaDeDondeSale:
    def test_trae_los_anuncios_recientes_de_la_provincia_elegida(
        self, client: TestClient, datos: None
    ) -> None:
        respuesta = client.get(
            "/marketing/preview/de-donde-sale",
            params={"product": "Arroz", "province": "La Habana"},
        )

        assert respuesta.status_code == 200
        assert "Arroz" in respuesta.text
        assert "La Habana" in respuesta.text
        assert "captura" in respuesta.text
        assert "ver anuncio" not in respuesta.text

    def test_provincia_fuera_del_foco_da_404(self, client: TestClient, datos: None) -> None:
        respuesta = client.get(
            "/marketing/preview/de-donde-sale",
            params={"product": "Arroz", "province": "Camagüey"},
        )

        assert respuesta.status_code == 404


class TestTarjetasEstaticas:
    """Portada, consejo, story, portada de Facebook, cómo funciona y
    cierre: sin producto ni provincia, siempre disponibles."""

    @pytest.mark.parametrize(
        ("slug", "marca"),
        [
            ("carrusel-portada", "Guani"),
            ("carrusel-consejo", "Guani"),
            ("story-precalentamiento", "Próximamente"),
            ("portada-facebook", "Cada precio, con sus anuncios detrás"),
            ("como-funciona", "De los grupos de WhatsApp a la cifra"),
            ("carrusel-cierre", "Guani"),
        ],
    )
    def test_vista_previa_200_con_su_texto_fijo(
        self, client: TestClient, datos: None, slug: str, marca: str
    ) -> None:
        respuesta = client.get(f"/marketing/preview/{slug}")

        assert respuesta.status_code == 200
        assert marca in respuesta.text

    def test_story_y_portada_facebook_listan_los_productos_del_foco(
        self, client: TestClient, datos: None
    ) -> None:
        for slug in ("story-precalentamiento", "portada-facebook"):
            respuesta = client.get(f"/marketing/preview/{slug}")
            assert "Arroz" in respuesta.text
            assert "Pollo" not in respuesta.text


@pytest.fixture
def sin_chromium(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simula un Chromium que no arranca, tenga o no la máquina uno instalado."""

    class _Chromium:
        def launch(self) -> None:
            raise RuntimeError("Executable doesn't exist")

    class _Playwright:
        chromium = _Chromium()

        def __enter__(self) -> "_Playwright":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    fake = types.ModuleType("playwright.sync_api")
    fake.sync_playwright = _Playwright  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake)


class TestDescargaPNG:
    def test_sin_chromium_instalado_degrada_a_503_explicando_como_instalarlo(
        self, client: TestClient, datos: None, sin_chromium: None
    ) -> None:
        respuesta = client.get(
            "/marketing/card/dato-del-dia.png", params={"product": "Arroz"}
        )

        assert respuesta.status_code == 503
        assert "playwright" in respuesta.text.lower()

    def test_producto_fuera_de_foco_da_404_antes_de_intentar_renderizar(
        self, client: TestClient, datos: None
    ) -> None:
        respuesta = client.get(
            "/marketing/card/dato-del-dia.png", params={"product": "Pollo"}
        )

        assert respuesta.status_code == 404

    def test_una_tarjeta_estatica_tambien_degrada_a_503_sin_chromium(
        self, client: TestClient, datos: None, sin_chromium: None
    ) -> None:
        respuesta = client.get("/marketing/card/carrusel-cierre.png")

        assert respuesta.status_code == 503
