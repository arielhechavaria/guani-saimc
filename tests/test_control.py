"""Vista de control del operador — `GET /control`."""

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from saimc.models import Currency, PriceHistory, Product, Province, RawIngestion
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
