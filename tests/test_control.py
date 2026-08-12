"""Vista de control del operador — `GET /control`."""

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from saimc.models import Currency, PriceHistory, Product, Province, RawIngestion, SaleFormat
from saimc.models.raw_ingestion import ContentType, ProcessingStatus, SourceType
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


def _cargar(session: Session, *, precios: list[str]) -> None:
    azucar = Product(name="Azúcar blanca", category="granos", unit="lb", aliases=[])
    scu = Province(code="scu", name="Santiago de Cuba")
    session.add_all([azucar, scu])
    session.commit()
    origen = RawIngestion(
        source_type=SourceType.INBOX_TEXT,
        file_path="raw/control.txt",
        content_hash="hash-control",
        content_type=ContentType.TEXT,
        processing_status=ProcessingStatus.CURATED,
        captured_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    session.add(origen)
    session.flush()
    for precio in precios:
        session.add(
            PriceHistory(
                product_id=azucar.id,
                province_id=scu.id,
                price=Decimal(precio),
                currency=Currency.CUP.value,
                source_id=origen.id,
            )
        )
    session.commit()


class TestControlPanel:
    def test_sin_datos_no_rompe(self, client: TestClient) -> None:
        response = client.get("/control")

        assert response.status_code == 200
        assert "no hay filas de precio" in response.text.lower()

    def test_muestra_la_celda_con_su_muestra_y_mediana(
        self, client: TestClient, session: Session
    ) -> None:
        _cargar(session, precios=["420", "440", "23500"])

        response = client.get("/control")

        assert response.status_code == 200
        assert "Azúcar blanca" in response.text
        assert "Santiago de Cuba" in response.text
        assert "440.00 CUP" in response.text  # mediana de 420, 440, 23500

    def test_la_amplitud_expone_la_celda_mezclada(
        self, client: TestClient, session: Session
    ) -> None:
        """Caso real: minorista por libra (420) y saco mayorista (23500) en la
        misma celda. La amplitud lo hace visible sin emitir ningún juicio.
        """
        _cargar(session, precios=["420", "440", "23500"])

        response = client.get("/control")

        assert "56.0×" in response.text  # 23500 / 420

    def test_no_emite_veredictos_sobre_la_muestra(
        self, client: TestClient, session: Session
    ) -> None:
        """Whitepaper §12.6: ningún umbral se inventa. La vista informa, no
        califica — nada de "insuficiente", "alta" o "baja".
        """
        _cargar(session, precios=["420"])

        texto = client.get("/control").text.lower()

        for veredicto in ("insuficiente", "dispersión alta", "muestra baja"):
            assert veredicto not in texto

    def test_cuenta_las_celdas_de_una_sola_observacion(
        self, client: TestClient, session: Session
    ) -> None:
        _cargar(session, precios=["420"])

        response = client.get("/control")

        assert "1 de 1 celdas tienen una sola" in response.text


class TestRangoMinorista:
    """`GET /control` también resume el precio minorista por producto,
    normalizado por unidad (2026-08-12): "en qué rango se movió Arroz",
    independiente del envase.
    """

    def _cargar_retail(self, session: Session) -> None:
        arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[])
        scu = Province(code="scu", name="Santiago de Cuba")
        session.add_all([arroz, scu])
        session.commit()
        origen = RawIngestion(
            source_type=SourceType.INBOX_TEXT,
            file_path="raw/rango.txt",
            content_hash="hash-rango-control",
            content_type=ContentType.TEXT,
            processing_status=ProcessingStatus.CURATED,
            captured_at=datetime(2026, 8, 1, tzinfo=UTC),
        )
        session.add(origen)
        session.flush()
        for precio in ("700", "800"):
            session.add(
                PriceHistory(
                    product_id=arroz.id,
                    province_id=scu.id,
                    price=Decimal(precio),
                    currency=Currency.CUP.value,
                    unit="lb",
                    sale_format=SaleFormat.RETAIL.value,
                    quantity=Decimal("1"),
                    source_id=origen.id,
                )
            )
        session.commit()

    def test_sin_filas_minoristas_normalizables_muestra_aviso(
        self, client: TestClient
    ) -> None:
        response = client.get("/control")

        assert response.status_code == 200
        assert "ninguna fila entra todavía en este rango" in response.text.lower()

    def test_muestra_la_mediana_normalizada_por_producto(
        self, client: TestClient, session: Session
    ) -> None:
        self._cargar_retail(session)

        response = client.get("/control")

        assert response.status_code == 200
        assert "750.00 CUP/lb" in response.text  # mediana de 700 y 800

    def test_dias_es_configurable_por_query_string(
        self, client: TestClient, session: Session
    ) -> None:
        """La ventana real (qué fecha queda adentro o afuera) ya la cubre
        `test_ventana_de_dias_excluye_lo_viejo` en test_cells.py con una fecha
        fija — acá solo se verifica que `?days=` llega hasta la plantilla.
        """
        self._cargar_retail(session)

        response = client.get("/control?days=1")

        assert "últimos 1 días" in response.text
        assert "750.00 CUP/lb" in response.text


class TestFiltroDePilotos:
    """`/control` muestra solo los 4 productos piloto (2026-08-12): el resto
    del catálogo (dispersión de 2026-08-11) queda fuera de esta vista por
    ahora, no de la base — es un filtro de pantalla, no un borrado de datos.
    """

    def _cargar(
        self, session: Session, *, producto: str, provincia: str = "Santiago de Cuba"
    ) -> None:
        item = Product(name=producto, category="x", unit="lb", aliases=[])
        scu = session.execute(
            select(Province).where(Province.name == provincia)
        ).scalar_one_or_none()
        if scu is None:
            scu = Province(code="scu", name=provincia)
            session.add(scu)
        session.add(item)
        session.commit()
        origen = RawIngestion(
            source_type=SourceType.INBOX_TEXT,
            file_path=f"raw/{producto}.txt",
            content_hash=f"hash-{producto}",
            content_type=ContentType.TEXT,
            processing_status=ProcessingStatus.CURATED,
            captured_at=datetime(2026, 8, 1, tzinfo=UTC),
        )
        session.add(origen)
        session.flush()
        session.add(
            PriceHistory(
                product_id=item.id,
                province_id=scu.id,
                price=Decimal("100"),
                currency=Currency.CUP.value,
                source_id=origen.id,
            )
        )
        session.commit()

    def test_oculta_productos_fuera_de_los_cuatro_pilotos(
        self, client: TestClient, session: Session
    ) -> None:
        self._cargar(session, producto="Arroz")
        self._cargar(session, producto="Sazon Dubai")

        response = client.get("/control")

        assert "Arroz" in response.text
        assert "Sazon Dubai" not in response.text

    def test_los_cuatro_pilotos_pasan_el_filtro(
        self, client: TestClient, session: Session
    ) -> None:
        for producto in ("Arroz", "Pollo", "Azúcar blanca", "Aceite"):
            self._cargar(session, producto=producto)

        texto = client.get("/control").text

        for producto in ("Arroz", "Pollo", "Azúcar blanca", "Aceite"):
            assert producto in texto

    def test_contador_de_productos_refleja_solo_lo_mostrado(
        self, client: TestClient, session: Session
    ) -> None:
        self._cargar(session, producto="Arroz")
        self._cargar(session, producto="Sazon Dubai")

        response = client.get("/control")

        assert "Productos: <strong>1</strong>" in response.text
