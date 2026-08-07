"""Cola de curaduría — endpoints FastAPI (whitepaper §6.1, Etapa 1c y Etapa 2).

Usa el `TestClient` de FastAPI con `app.dependency_overrides[get_db]` para
inyectar la misma fixture `session` (SQLite en memoria) de `tests/conftest.py`,
sin abrir una conexión real ni tocar `conftest.py`.
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from saimc.config import get_settings
from saimc.extraction import ocr
from saimc.models import (
    ContentType,
    PriceHistory,
    ProcessingStatus,
    Product,
    Province,
    RawIngestion,
    SourceType,
)
from saimc.web.app import app
from saimc.web.deps import get_db

# --- helpers de armado ------------------------------------------------------


def _crear_raw_ingestion(
    session: Session,
    *,
    hash_: str,
    content_type: str = ContentType.TEXT,
    original_text: str | None = None,
    file_path: str | None = None,
    captured_at: datetime | None = None,
) -> RawIngestion:
    source_type = (
        SourceType.INBOX_TEXT if content_type == ContentType.TEXT else SourceType.INBOX_IMAGE
    )
    ingestion = RawIngestion(
        source_type=source_type,
        file_path=file_path or f"raw/{hash_}.dat",
        content_hash=hash_,
        content_type=content_type,
        original_text=original_text,
        processing_status=ProcessingStatus.PENDING,
        captured_at=captured_at or datetime.now(UTC),
    )
    session.add(ingestion)
    session.commit()
    return ingestion


def _crear_producto(
    session: Session, *, name: str = "Pollo", aliases: list[str] | None = None
) -> Product:
    producto = Product(name=name, category="carnes", unit="lb", aliases=aliases or [])
    session.add(producto)
    session.commit()
    return producto


def _crear_provincia(
    session: Session, *, code: str = "hab", name: str = "La Habana"
) -> Province:
    provincia = Province(code=code, name=name)
    session.add(provincia)
    session.commit()
    return provincia


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    def _get_db_override() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_db] = _get_db_override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Iterator[None]:
    # `get_settings` es un singleton perezoso (lru_cache): sin limpiarlo, el
    # test de evidencia que apunta `raw_store_dir` a un `tmp_path` filtraría
    # ese valor a los tests que corren después.
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# --- GET /curation -----------------------------------------------------------


class TestCurationQueueGet:
    def test_cola_vacia_no_rompe(self, client: TestClient) -> None:
        response = client.get("/curation")

        assert response.status_code == 200
        assert "no hay elementos en la cola" in response.text.lower()

    def test_muestra_el_pendiente_mas_antiguo(
        self, client: TestClient, session: Session
    ) -> None:
        mas_nuevo = _crear_raw_ingestion(
            session,
            hash_="hash-orden-nuevo",
            captured_at=datetime(2026, 8, 5, tzinfo=UTC),
        )
        mas_viejo = _crear_raw_ingestion(
            session,
            hash_="hash-orden-viejo",
            captured_at=datetime(2026, 8, 1, tzinfo=UTC),
        )

        response = client.get("/curation")

        assert response.status_code == 200
        assert str(mas_viejo.id) in response.text
        assert str(mas_nuevo.id) not in response.text

    def test_prellena_propuesta_para_texto(
        self, client: TestClient, session: Session
    ) -> None:
        producto = _crear_producto(session, name="Pollo", aliases=["pollo congelado"])
        provincia = _crear_provincia(session, code="hab", name="La Habana")
        _crear_raw_ingestion(
            session, hash_="hash-propuesta-1", original_text="pollo a 500 habana"
        )

        response = client.get("/curation")

        assert response.status_code == 200
        assert f'value="{producto.name}"' in response.text
        assert 'value="500"' in response.text
        assert f'value="{provincia.id}" selected' in response.text

    def test_formulario_vacio_si_el_ocr_no_reconoce_nada(
        self, client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _crear_producto(session)
        _crear_raw_ingestion(
            session, hash_="hash-imagen-1", content_type=ContentType.IMAGE
        )
        monkeypatch.setattr(ocr, "transcribe", lambda path, lang="spa": "")

        response = client.get("/curation")

        assert response.status_code == 200
        assert 'value=""' in response.text

    def test_imagen_muestra_texto_reconocido_prellenado_y_propuesta(
        self, client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """El texto que el OCR reconoce se usa para `propose()` (mismo camino que texto)."""
        producto = _crear_producto(session, name="Pollo", aliases=["pollo congelado"])
        provincia = _crear_provincia(session, code="hab", name="La Habana")
        _crear_raw_ingestion(
            session, hash_="hash-imagen-ocr-1", content_type=ContentType.IMAGE
        )
        monkeypatch.setattr(
            ocr, "transcribe", lambda path, lang="spa": "pollo a 500 habana"
        )

        response = client.get("/curation")

        assert response.status_code == 200
        assert "pollo a 500 habana" in response.text
        assert f'value="{producto.name}"' in response.text
        assert f'value="{provincia.id}" selected' in response.text

    def test_texto_no_llama_al_ocr(
        self, client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _crear_producto(session)
        _crear_raw_ingestion(
            session, hash_="hash-texto-sin-ocr", original_text="pollo a 500"
        )

        def _fallar_si_se_llama(path, lang="spa"):
            raise AssertionError("no debería llamarse al OCR para content_type text")

        monkeypatch.setattr(ocr, "transcribe", _fallar_si_se_llama)

        response = client.get("/curation")

        assert response.status_code == 200


# --- POST /curation/{id} -----------------------------------------------------


class TestCurationSubmit:
    def test_datos_validos_crea_price_history_y_marca_curated(
        self, client: TestClient, session: Session
    ) -> None:
        producto = _crear_producto(session)
        ingestion = _crear_raw_ingestion(session, hash_="hash-post-valido")

        response = client.post(
            f"/curation/{ingestion.id}",
            data={
                "product_name": producto.name,
                "price": "450",
                "currency": "CUP",
                "quantity": "2",
                "unit": "lb",
                "province_id": "",
                "source_reference": "grupo telegram X",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/curation"

        session.expire_all()
        refrescado = session.get(RawIngestion, ingestion.id)
        assert refrescado is not None
        assert refrescado.processing_status == ProcessingStatus.CURATED
        assert refrescado.source_reference == "grupo telegram X"

        filas = list(session.execute(select(PriceHistory)).scalars())
        assert len(filas) == 1
        precio = filas[0]
        assert precio.source_id == ingestion.id
        assert precio.product_id == producto.id
        assert precio.price == Decimal("450")
        assert precio.confidence_score is None
        assert precio.price_cup is None

    def test_sin_product_name_no_crea_fila_ni_cambia_estado(
        self, client: TestClient, session: Session
    ) -> None:
        ingestion = _crear_raw_ingestion(session, hash_="hash-post-sin-producto")

        response = client.post(
            f"/curation/{ingestion.id}",
            data={"product_name": "", "price": "100"},
        )

        assert response.status_code == 422

        session.expire_all()
        refrescado = session.get(RawIngestion, ingestion.id)
        assert refrescado is not None
        assert refrescado.processing_status == ProcessingStatus.PENDING
        assert list(session.execute(select(PriceHistory)).scalars()) == []

    def test_sin_price_no_crea_fila_ni_cambia_estado(
        self, client: TestClient, session: Session
    ) -> None:
        producto = _crear_producto(session)
        ingestion = _crear_raw_ingestion(session, hash_="hash-post-sin-precio")

        response = client.post(
            f"/curation/{ingestion.id}",
            data={"product_name": producto.name, "price": ""},
        )

        assert response.status_code == 422

        session.expire_all()
        refrescado = session.get(RawIngestion, ingestion.id)
        assert refrescado is not None
        assert refrescado.processing_status == ProcessingStatus.PENDING
        assert list(session.execute(select(PriceHistory)).scalars()) == []

    def test_product_name_existente_case_insensitive_usa_ese_producto(
        self, client: TestClient, session: Session
    ) -> None:
        """Tipear el nombre con mayúsculas distintas matchea el producto ya sembrado, no crea uno nuevo."""
        producto = _crear_producto(session, name="pollo")
        ingestion = _crear_raw_ingestion(session, hash_="hash-post-nombre-case")

        response = client.post(
            f"/curation/{ingestion.id}",
            data={"product_name": "POLLO", "price": "450"},
            follow_redirects=False,
        )

        assert response.status_code == 303

        session.expire_all()
        productos = list(session.execute(select(Product)).scalars())
        assert len(productos) == 1
        filas = list(session.execute(select(PriceHistory)).scalars())
        assert len(filas) == 1
        assert filas[0].product_id == producto.id

    def test_product_name_nuevo_con_categoria_y_unidad_crea_producto(
        self, client: TestClient, session: Session
    ) -> None:
        ingestion = _crear_raw_ingestion(session, hash_="hash-post-nombre-nuevo")

        response = client.post(
            f"/curation/{ingestion.id}",
            data={
                "product_name": "Detergente",
                "product_category": "limpieza",
                "product_unit": "unidad",
                "price": "100",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303

        session.expire_all()
        productos = list(session.execute(select(Product)).scalars())
        assert len(productos) == 1
        nuevo = productos[0]
        assert nuevo.name == "Detergente"
        assert nuevo.category == "limpieza"
        assert nuevo.unit == "unidad"

        filas = list(session.execute(select(PriceHistory)).scalars())
        assert len(filas) == 1
        assert filas[0].product_id == nuevo.id

    def test_product_name_nuevo_sin_categoria_o_unidad_no_crea_nada(
        self, client: TestClient, session: Session
    ) -> None:
        ingestion = _crear_raw_ingestion(session, hash_="hash-post-nombre-incompleto")

        response = client.post(
            f"/curation/{ingestion.id}",
            data={
                "product_name": "Detergente",
                "product_category": "",
                "product_unit": "unidad",
                "price": "100",
            },
        )

        assert response.status_code == 422

        session.expire_all()
        assert list(session.execute(select(Product)).scalars()) == []
        assert list(session.execute(select(PriceHistory)).scalars()) == []
        refrescado = session.get(RawIngestion, ingestion.id)
        assert refrescado is not None
        assert refrescado.processing_status == ProcessingStatus.PENDING

    def test_original_text_no_vacio_en_imagen_actualiza_tras_curar(
        self, client: TestClient, session: Session
    ) -> None:
        producto = _crear_producto(session)
        ingestion = _crear_raw_ingestion(
            session, hash_="hash-post-imagen-ocr", content_type=ContentType.IMAGE
        )

        response = client.post(
            f"/curation/{ingestion.id}",
            data={
                "product_name": producto.name,
                "price": "450",
                "original_text": "pollo a 450",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303

        session.expire_all()
        refrescado = session.get(RawIngestion, ingestion.id)
        assert refrescado is not None
        assert refrescado.original_text == "pollo a 450"

    def test_original_text_en_item_de_texto_no_pisa_el_original(
        self, client: TestClient, session: Session
    ) -> None:
        """El texto original de un ítem de texto es la evidencia pegada: no se reemplaza."""
        producto = _crear_producto(session)
        ingestion = _crear_raw_ingestion(
            session, hash_="hash-post-texto-no-pisa", original_text="texto pegado original"
        )

        response = client.post(
            f"/curation/{ingestion.id}",
            data={
                "product_name": producto.name,
                "price": "450",
                "original_text": "texto que el operador no debería poder pisar",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303

        session.expire_all()
        refrescado = session.get(RawIngestion, ingestion.id)
        assert refrescado is not None
        assert refrescado.original_text == "texto pegado original"

    def test_ingestion_inexistente_devuelve_404(self, client: TestClient) -> None:
        response = client.post(
            f"/curation/{uuid.uuid4()}", data={"product_name": "", "price": ""}
        )

        assert response.status_code == 404


# --- POST /curation/{id}/discard ---------------------------------------------


class TestCurationDiscard:
    def test_marca_discarded_sin_crear_fila(
        self, client: TestClient, session: Session
    ) -> None:
        ingestion = _crear_raw_ingestion(session, hash_="hash-discard-1")

        response = client.post(
            f"/curation/{ingestion.id}/discard", follow_redirects=False
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/curation"

        session.expire_all()
        refrescado = session.get(RawIngestion, ingestion.id)
        assert refrescado is not None
        assert refrescado.processing_status == ProcessingStatus.DISCARDED
        assert list(session.execute(select(PriceHistory)).scalars()) == []


# --- GET /evidence/{id} -------------------------------------------------------


class TestEvidence:
    def test_imagen_existente_devuelve_200(
        self, client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("SAIMC_RAW_STORE_DIR", str(tmp_path))
        get_settings.cache_clear()
        (tmp_path / "foto.png").write_bytes(b"contenido-fake-png")

        ingestion = _crear_raw_ingestion(
            session,
            hash_="hash-evidencia-1",
            content_type=ContentType.IMAGE,
            file_path="foto.png",
        )

        response = client.get(f"/evidence/{ingestion.id}")

        assert response.status_code == 200
        assert response.content == b"contenido-fake-png"

    def test_texto_devuelve_404(self, client: TestClient, session: Session) -> None:
        ingestion = _crear_raw_ingestion(
            session,
            hash_="hash-evidencia-2",
            content_type=ContentType.TEXT,
            original_text="algo",
        )

        response = client.get(f"/evidence/{ingestion.id}")

        assert response.status_code == 404

    def test_id_inexistente_devuelve_404(self, client: TestClient) -> None:
        response = client.get(f"/evidence/{uuid.uuid4()}")

        assert response.status_code == 404
