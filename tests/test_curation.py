"""Cola de curaduría — endpoints FastAPI (whitepaper §6.1, Etapa 1c y Etapa 2).

Usa el `TestClient` de FastAPI con `app.dependency_overrides[get_db]` para
inyectar la misma fixture `session` (SQLite en memoria) de `tests/conftest.py`,
sin abrir una conexión real ni tocar `conftest.py`.
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime
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
def client(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[TestClient]:
    def _get_db_override() -> Iterator[Session]:
        yield session

    # `GET /curation` ahora ingesta la bandeja antes de mostrar la cola (ver
    # TestIngestaAutomatica). Sin aislar estos directorios, cada test que pega
    # a `/curation` escanearía y MOVERÍA archivos reales de `inbox/` del repo
    # hacia `data/raw/` — la bandeja real vive en ese path por `.env`.
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    monkeypatch.setenv("SAIMC_INBOX_DIR", str(inbox))
    monkeypatch.setenv("SAIMC_RAW_STORE_DIR", str(tmp_path / "data" / "raw"))
    get_settings.cache_clear()

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

    def test_muestra_contador_de_curados_y_por_curar(
        self, client: TestClient, session: Session
    ) -> None:
        """El operador cura en sesiones y quiere saber cuánto le queda sin
        consultar la base a mano (docs/decisions.md)."""
        _crear_raw_ingestion(session, hash_="hash-contador-pending-1")
        _crear_raw_ingestion(session, hash_="hash-contador-pending-2")
        curado = _crear_raw_ingestion(session, hash_="hash-contador-curado-1")
        curado.processing_status = ProcessingStatus.CURATED
        session.commit()

        response = client.get("/curation")

        assert response.status_code == 200
        assert "Curados: <strong>1</strong>" in response.text
        assert "Por curar: <strong>2</strong>" in response.text
        assert "Descartados: <strong>0</strong>" in response.text

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
        assert f'value="{producto.id}" selected' in response.text
        assert 'value="500"' in response.text
        assert f'value="{provincia.id}" selected' in response.text

    def test_prellena_canal_origen_desde_el_texto(
        self, client: TestClient, session: Session
    ) -> None:
        _crear_producto(session, name="Pollo")
        _crear_raw_ingestion(
            session,
            hash_="hash-canal-origen-1",
            original_text="Juan Pérez - Ayer a las 14:35\npollo a 500 habana",
        )

        response = client.get("/curation")

        assert response.status_code == 200
        assert 'id="source_reference" value="Juan Pérez"' in response.text

    def test_prellena_fecha_de_publicacion_desde_el_texto(
        self, client: TestClient, session: Session
    ) -> None:
        _crear_producto(session, name="Pollo")
        _crear_raw_ingestion(
            session,
            hash_="hash-fecha-publicacion-1",
            original_text="Juan Pérez - Ayer a las 14:35\npollo a 500 habana",
            captured_at=datetime(2026, 8, 10, 12, 0, tzinfo=UTC),
        )

        response = client.get("/curation")

        assert response.status_code == 200
        assert 'id="published_date" value="2026-08-09"' in response.text

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
        assert f'value="{producto.id}" selected' in response.text
        assert f'value="{provincia.id}" selected' in response.text

    def test_corte_sin_match_prellena_alta_nueva_abierta_con_nombre_adivinado(
        self, client: TestClient, session: Session
    ) -> None:
        """"Hígado de pollo" no matchea el genérico "Pollo": el "+" se abre
        solo con el nombre adivinado, no un `<select>` prellenado mal."""
        _crear_producto(session, name="Pollo")
        _crear_raw_ingestion(
            session,
            hash_="hash-corte-sin-match",
            original_text="higado de pollo 40lb a 3000 habana",
        )

        response = client.get("/curation")

        assert response.status_code == 200
        assert 'value="higado de pollo"' in response.text
        assert '<details class="mt-1" open>' in response.text
        # El select de producto queda en "— seleccionar —", no en "Pollo".
        assert 'value="" selected>— seleccionar —' in response.text

    def test_texto_multiproducto_prellena_una_fila_por_candidato(
        self, client: TestClient, session: Session
    ) -> None:
        """Con `propose_all`, un anuncio de varias líneas prellena una fila POR línea."""
        pollo = _crear_producto(session, name="Pollo")
        picadillo = _crear_producto(session, name="Picadillo")
        _crear_raw_ingestion(
            session,
            hash_="hash-multiproducto-1",
            original_text=(
                "- pollo congelado 40lb a 30 usd\n"
                "- picadillo 21.5kg a 32.95 usd"
            ),
        )

        response = client.get("/curation")

        assert response.status_code == 200
        assert f'value="{pollo.id}" selected' in response.text
        assert f'value="{picadillo.id}" selected' in response.text
        assert 'value="30"' in response.text
        assert 'value="32.95"' in response.text

    def test_muestra_filas_de_candidatos_mas_las_filas_en_blanco_extra(
        self, client: TestClient, session: Session
    ) -> None:
        """2 candidatos + 3 filas en blanco fijas = 5 selects `product_id_*`."""
        _crear_producto(session, name="Pollo")
        _crear_producto(session, name="Picadillo")
        _crear_raw_ingestion(
            session,
            hash_="hash-filas-extra",
            original_text=(
                "- pollo congelado 40lb a 30 usd\n"
                "- picadillo 21.5kg a 32.95 usd"
            ),
        )

        response = client.get("/curation")

        assert response.status_code == 200
        assert response.text.count('name="product_id_') == 5
        assert 'name="row_count" value="5"' in response.text

    def test_canonicaliza_la_unidad_al_prellenar_el_select(
        self, client: TestClient, session: Session
    ) -> None:
        """spec: canonicalización en el prellenado — "litro" propuesto por el
        motor se muestra ya traducido a la unidad canónica "l" en el select."""
        _crear_producto(session, name="Pollo")
        _crear_provincia(session, code="hab", name="La Habana")
        _crear_raw_ingestion(
            session,
            hash_="hash-canonicaliza-unidad",
            original_text="pollo a 500 habana 1 litro",
        )

        response = client.get("/curation")

        assert response.status_code == 200
        assert '<option value="l" selected>' in response.text

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


class TestIngestaAutomatica:
    """`GET /curation` ingesta la bandeja antes de mostrar la cola — reemplaza
    el paso manual de correr `python -m saimc.ingestion` (docs/decisions.md).
    """

    def test_archivo_nuevo_en_la_bandeja_pasa_a_pending_y_se_muestra(
        self, client: TestClient, session: Session, tmp_path: Path
    ) -> None:
        inbox = Path(get_settings().inbox_dir)
        (inbox / "captura.txt").write_text("pollo a 500 habana", encoding="utf-8")

        response = client.get("/curation")

        assert response.status_code == 200
        ingestion = session.execute(select(RawIngestion)).scalars().one()
        assert ingestion.processing_status == ProcessingStatus.PENDING
        assert ingestion.original_text == "pollo a 500 habana"
        assert str(ingestion.id) in response.text

    def test_archivo_ingerido_se_mueve_de_inbox_al_almacen(
        self, client: TestClient, session: Session
    ) -> None:
        inbox = Path(get_settings().inbox_dir)
        (inbox / "captura.txt").write_text("pollo a 500 habana", encoding="utf-8")

        client.get("/curation")

        ingestion = session.execute(select(RawIngestion)).scalars().one()
        assert not (inbox / "captura.txt").exists()
        stored = Path(get_settings().raw_store_dir) / ingestion.file_path
        assert stored.exists()

    def test_bandeja_vacia_no_rompe_la_cola(self, client: TestClient) -> None:
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
                "row_count": "1",
                "product_id_0": str(producto.id),
                "price_0": "450",
                "currency_0": "CUP",
                "quantity_0": "2",
                "unit_0": "lb",
                "province_id_0": "",
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

    def test_no_persiste_telefonos_del_texto_transcrito(
        self, client: TestClient, session: Session
    ) -> None:
        """`CLAUDE.md`: no se almacenan teléfonos. La curaduría es el único
        punto donde texto capturado se persiste, así que es donde la regla
        tiene que valer (docs/decisions.md 2026-08-11).
        """
        producto = _crear_producto(session)
        ingestion = _crear_raw_ingestion(
            session, hash_="hash-post-telefono", content_type=ContentType.IMAGE
        )

        client.post(
            f"/curation/{ingestion.id}",
            data={
                "row_count": "1",
                "product_id_0": str(producto.id),
                "price_0": "420",
                "currency_0": "CUP",
                "quantity_0": "",
                "unit_0": "lb",
                "province_id_0": "",
                "source_reference": "watsap 55550202",
                "original_text": "+53 5 5550101 Hoy, 9:22 a. m. Azúcar a 420 cup",
            },
            follow_redirects=False,
        )

        session.expire_all()
        refrescado = session.get(RawIngestion, ingestion.id)
        assert refrescado is not None
        assert "5550101" not in (refrescado.original_text or "")
        assert "55550202" not in (refrescado.source_reference or "")
        # El precio, que es el dato que el sistema existe para medir, sobrevive.
        assert "420 cup" in (refrescado.original_text or "")

    def test_dos_filas_completas_crean_dos_price_history(
        self, client: TestClient, session: Session
    ) -> None:
        pollo = _crear_producto(session, name="Pollo")
        picadillo = _crear_producto(session, name="Picadillo")
        ingestion = _crear_raw_ingestion(session, hash_="hash-post-dos-filas")

        response = client.post(
            f"/curation/{ingestion.id}",
            data={
                "row_count": "2",
                "product_id_0": str(pollo.id),
                "price_0": "450",
                "product_id_1": str(picadillo.id),
                "price_1": "300",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303

        session.expire_all()
        refrescado = session.get(RawIngestion, ingestion.id)
        assert refrescado is not None
        assert refrescado.processing_status == ProcessingStatus.CURATED

        filas = list(
            session.execute(
                select(PriceHistory).where(PriceHistory.source_id == ingestion.id)
            ).scalars()
        )
        assert len(filas) == 2
        assert {f.product_id for f in filas} == {pollo.id, picadillo.id}

    def test_published_date_se_propaga_a_todas_las_filas(
        self, client: TestClient, session: Session
    ) -> None:
        """Un solo input de fecha a nivel de anuncio: al confirmar, las N
        filas de `prices_history` quedan con el mismo `published_date`
        (spec: Fan-out multi-producto)."""
        pollo = _crear_producto(session, name="Pollo")
        arroz = _crear_producto(session, name="Arroz")
        aceite = _crear_producto(session, name="Aceite")
        ingestion = _crear_raw_ingestion(session, hash_="hash-post-fecha-fanout")

        response = client.post(
            f"/curation/{ingestion.id}",
            data={
                "row_count": "3",
                "product_id_0": str(pollo.id),
                "price_0": "450",
                "product_id_1": str(arroz.id),
                "price_1": "120",
                "product_id_2": str(aceite.id),
                "price_2": "300",
                "published_date": "2026-08-05",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303

        session.expire_all()
        filas = list(
            session.execute(
                select(PriceHistory).where(PriceHistory.source_id == ingestion.id)
            ).scalars()
        )
        assert len(filas) == 3
        assert {f.published_date for f in filas} == {date(2026, 8, 5)}

    def test_published_date_vacio_o_invalido_no_bloquea_el_submit(
        self, client: TestClient, session: Session
    ) -> None:
        """Campo vacío o valor no parseable: el submit se acepta igual (303,
        no 422) y la fila queda con `published_date = NULL` (spec: Campo
        nunca bloqueante)."""
        pollo = _crear_producto(session, name="Pollo")
        ingestion_vacio = _crear_raw_ingestion(session, hash_="hash-post-fecha-vacia")

        response = client.post(
            f"/curation/{ingestion_vacio.id}",
            data={
                "row_count": "1",
                "product_id_0": str(pollo.id),
                "price_0": "450",
                "published_date": "",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        session.expire_all()
        fila = session.execute(
            select(PriceHistory).where(PriceHistory.source_id == ingestion_vacio.id)
        ).scalar_one()
        assert fila.published_date is None

        ingestion_invalido = _crear_raw_ingestion(session, hash_="hash-post-fecha-basura")

        response = client.post(
            f"/curation/{ingestion_invalido.id}",
            data={
                "row_count": "1",
                "product_id_0": str(pollo.id),
                "price_0": "450",
                "published_date": "no-es-una-fecha",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        session.expire_all()
        fila = session.execute(
            select(PriceHistory).where(PriceHistory.source_id == ingestion_invalido.id)
        ).scalar_one()
        assert fila.published_date is None

        ingestion_futura = _crear_raw_ingestion(session, hash_="hash-post-fecha-futura")
        fecha_futura = (date.today().replace(year=date.today().year + 1)).isoformat()

        response = client.post(
            f"/curation/{ingestion_futura.id}",
            data={
                "row_count": "1",
                "product_id_0": str(pollo.id),
                "price_0": "450",
                "published_date": fecha_futura,
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        session.expire_all()
        fila = session.execute(
            select(PriceHistory).where(PriceHistory.source_id == ingestion_futura.id)
        ).scalar_one()
        assert fila.published_date is None

    def test_filas_vacias_entre_completas_se_ignoran_sin_error(
        self, client: TestClient, session: Session
    ) -> None:
        pollo = _crear_producto(session, name="Pollo")
        picadillo = _crear_producto(session, name="Picadillo")
        ingestion = _crear_raw_ingestion(session, hash_="hash-post-filas-vacias")

        response = client.post(
            f"/curation/{ingestion.id}",
            data={
                "row_count": "3",
                "product_id_0": str(pollo.id),
                "price_0": "450",
                # Fila 1 en blanco: el operador no la usó.
                "product_id_2": str(picadillo.id),
                "price_2": "300",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303

        session.expire_all()
        filas = list(session.execute(select(PriceHistory)).scalars())
        assert len(filas) == 2
        assert {f.product_id for f in filas} == {pollo.id, picadillo.id}

    def test_fila_eliminada_del_dom_no_pisa_las_demas_filas_incluidas(
        self, client: TestClient, session: Session
    ) -> None:
        """Tarjeta eliminada del lado del cliente: ninguno de sus campos indexados
        viaja en el submit (el navegador nunca la manda), así que cae en la misma
        rama que una fila vacía —se ignora sin error— y no afecta al resto."""
        picadillo = _crear_producto(session, name="Picadillo")
        ingestion = _crear_raw_ingestion(session, hash_="hash-post-fila-eliminada")

        response = client.post(
            f"/curation/{ingestion.id}",
            data={
                "row_count": "2",
                # Fila 0 "eliminada" en el cliente: ningún campo con sufijo
                # _0 viaja, como si esa tarjeta nunca hubiera existido.
                "product_id_1": str(picadillo.id),
                "price_1": "300",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303

        session.expire_all()
        filas = list(session.execute(select(PriceHistory)).scalars())
        assert len(filas) == 1
        assert filas[0].product_id == picadillo.id
        assert filas[0].price == Decimal("300")

    def test_cero_filas_completas_devuelve_422_y_no_crea_nada(
        self, client: TestClient, session: Session
    ) -> None:
        ingestion = _crear_raw_ingestion(session, hash_="hash-post-cero-filas")

        response = client.post(
            f"/curation/{ingestion.id}",
            data={"row_count": "3"},
        )

        assert response.status_code == 422

        session.expire_all()
        refrescado = session.get(RawIngestion, ingestion.id)
        assert refrescado is not None
        assert refrescado.processing_status == ProcessingStatus.PENDING
        assert list(session.execute(select(PriceHistory)).scalars()) == []

    def test_sin_price_en_fila_unica_no_crea_fila_ni_cambia_estado(
        self, client: TestClient, session: Session
    ) -> None:
        producto = _crear_producto(session)
        ingestion = _crear_raw_ingestion(session, hash_="hash-post-sin-precio")

        response = client.post(
            f"/curation/{ingestion.id}",
            data={
                "row_count": "1",
                "product_id_0": str(producto.id),
                "price_0": "",
            },
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
            data={
                "row_count": "1",
                "new_product_name_0": "POLLO",
                "price_0": "450",
            },
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
                "row_count": "1",
                "new_product_name_0": "Detergente",
                "product_category_0": "limpieza",
                "product_unit_0": "unidad",
                "price_0": "100",
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

    def test_product_name_nuevo_sin_categoria_ni_unidad_usa_defaults(
        self, client: TestClient, session: Session
    ) -> None:
        """Categoría y unidad ya no son obligatorias: faltando ambas, se crea con los defaults."""
        ingestion = _crear_raw_ingestion(session, hash_="hash-post-nombre-sin-cat-ni-unidad")

        response = client.post(
            f"/curation/{ingestion.id}",
            data={
                "row_count": "1",
                "new_product_name_0": "Detergente",
                "price_0": "100",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303

        session.expire_all()
        productos = list(session.execute(select(Product)).scalars())
        assert len(productos) == 1
        nuevo = productos[0]
        assert nuevo.name == "Detergente"
        assert nuevo.category == "sin categoría"
        assert nuevo.unit == "unidad"

        filas = list(session.execute(select(PriceHistory)).scalars())
        assert len(filas) == 1
        assert filas[0].product_id == nuevo.id

    def test_product_name_nuevo_con_categoria_parcial_usa_default_solo_en_la_que_falta(
        self, client: TestClient, session: Session
    ) -> None:
        """Falta solo `unit`: el default se aplica ahí y `category` respeta lo tipeado."""
        ingestion = _crear_raw_ingestion(session, hash_="hash-post-nombre-cat-parcial")

        response = client.post(
            f"/curation/{ingestion.id}",
            data={
                "row_count": "1",
                "new_product_name_0": "Detergente",
                "product_category_0": "limpieza",
                "price_0": "100",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303

        session.expire_all()
        productos = list(session.execute(select(Product)).scalars())
        assert len(productos) == 1
        nuevo = productos[0]
        assert nuevo.category == "limpieza"
        assert nuevo.unit == "unidad"

    def test_dos_filas_con_mismo_producto_nuevo_crean_un_solo_product(
        self, client: TestClient, session: Session
    ) -> None:
        ingestion = _crear_raw_ingestion(session, hash_="hash-post-producto-nuevo-duplicado")

        response = client.post(
            f"/curation/{ingestion.id}",
            data={
                "row_count": "2",
                "new_product_name_0": "Detergente",
                "product_category_0": "limpieza",
                "product_unit_0": "unidad",
                "price_0": "100",
                "new_product_name_1": "detergente",
                "product_category_1": "limpieza",
                "product_unit_1": "unidad",
                "price_1": "120",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303

        session.expire_all()
        productos = list(session.execute(select(Product)).scalars())
        assert len(productos) == 1

        filas = list(session.execute(select(PriceHistory)).scalars())
        assert len(filas) == 2
        assert {f.product_id for f in filas} == {productos[0].id}

    def test_new_product_name_tiene_prioridad_sobre_product_id_seleccionado(
        self, client: TestClient, session: Session
    ) -> None:
        """Si la fila manda los dos, el alta nueva gana aunque haya un producto ya elegido en el select."""
        viejo = _crear_producto(session, name="Pollo")
        ingestion = _crear_raw_ingestion(session, hash_="hash-post-new-tiene-prioridad")

        response = client.post(
            f"/curation/{ingestion.id}",
            data={
                "row_count": "1",
                "product_id_0": str(viejo.id),
                "new_product_name_0": "Picadillo",
                "price_0": "100",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303

        session.expire_all()
        productos = list(session.execute(select(Product)).scalars())
        assert len(productos) == 2
        nuevo = next(p for p in productos if p.name == "Picadillo")

        filas = list(session.execute(select(PriceHistory)).scalars())
        assert len(filas) == 1
        assert filas[0].product_id == nuevo.id

    def test_precio_sin_producto_devuelve_el_mensaje_de_error_puntual(
        self, client: TestClient, session: Session
    ) -> None:
        ingestion = _crear_raw_ingestion(session, hash_="hash-post-precio-sin-producto")

        response = client.post(
            f"/curation/{ingestion.id}",
            data={
                "row_count": "1",
                "price_0": "100",
            },
        )

        assert response.status_code == 422
        assert "seleccioná o agregá un producto" in response.text.lower()

    def test_producto_sin_precio_devuelve_el_mensaje_de_error_puntual(
        self, client: TestClient, session: Session
    ) -> None:
        producto = _crear_producto(session)
        ingestion = _crear_raw_ingestion(session, hash_="hash-post-producto-sin-precio")

        response = client.post(
            f"/curation/{ingestion.id}",
            data={
                "row_count": "1",
                "product_id_0": str(producto.id),
            },
        )

        assert response.status_code == 422
        assert "falta el precio" in response.text.lower()

    def test_precio_invalido_en_una_fila_no_crea_nada_de_ninguna_fila(
        self, client: TestClient, session: Session
    ) -> None:
        pollo = _crear_producto(session, name="Pollo")
        ingestion = _crear_raw_ingestion(session, hash_="hash-post-precio-invalido")

        response = client.post(
            f"/curation/{ingestion.id}",
            data={
                "row_count": "2",
                "product_id_0": str(pollo.id),
                "price_0": "450",
                "product_id_1": str(pollo.id),
                "price_1": "no-es-un-numero",
            },
        )

        assert response.status_code == 422

        session.expire_all()
        assert list(session.execute(select(PriceHistory)).scalars()) == []

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
                "row_count": "1",
                "product_id_0": str(producto.id),
                "price_0": "450",
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
                "row_count": "1",
                "product_id_0": str(producto.id),
                "price_0": "450",
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
            f"/curation/{uuid.uuid4()}", data={"row_count": "0"}
        )

        assert response.status_code == 404

    def test_persiste_unit_sale_format_y_packaging_tal_como_se_envian(
        self, client: TestClient, session: Session
    ) -> None:
        """spec: filas distinguibles — los tres campos son independientes
        entre sí y del resto de la fila (curation-observation-capture)."""
        producto = _crear_producto(session)
        ingestion = _crear_raw_ingestion(session, hash_="hash-post-unit-sale-format")

        response = client.post(
            f"/curation/{ingestion.id}",
            data={
                "row_count": "1",
                "product_id_0": str(producto.id),
                "price_0": "450",
                "unit_0": "lb",
                "sale_format_0": "wholesale",
                "packaging_0": "saco",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303

        session.expire_all()
        fila = session.execute(select(PriceHistory)).scalars().one()
        assert fila.unit == "lb"
        assert fila.sale_format == "wholesale"
        assert fila.packaging == "saco"

    def test_sale_format_en_blanco_guarda_null_nunca_retail(
        self, client: TestClient, session: Session
    ) -> None:
        """spec: envío en blanco guarda NULL, nunca un `retail` fabricado."""
        producto = _crear_producto(session)
        ingestion = _crear_raw_ingestion(session, hash_="hash-post-sale-format-blanco")

        response = client.post(
            f"/curation/{ingestion.id}",
            data={
                "row_count": "1",
                "product_id_0": str(producto.id),
                "price_0": "450",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303

        session.expire_all()
        fila = session.execute(select(PriceHistory)).scalars().one()
        assert fila.sale_format is None

    def test_unidad_fuera_del_vocabulario_guarda_null_y_crea_la_fila(
        self, client: TestClient, session: Session
    ) -> None:
        """spec: una unidad fuera del vocabulario se guarda como NULL, sin
        bloquear la creación de la fila."""
        producto = _crear_producto(session)
        ingestion = _crear_raw_ingestion(
            session, hash_="hash-post-unidad-fuera-vocabulario"
        )

        response = client.post(
            f"/curation/{ingestion.id}",
            data={
                "row_count": "1",
                "product_id_0": str(producto.id),
                "price_0": "450",
                "unit_0": "quintal",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303

        session.expire_all()
        fila = session.execute(select(PriceHistory)).scalars().one()
        assert fila.unit is None

    def test_telefono_en_packaging_se_redacta_y_saco_queda_intacto(
        self, client: TestClient, session: Session
    ) -> None:
        """spec: un teléfono escrito en `packaging` se redacta."""
        producto = _crear_producto(session)
        ingestion = _crear_raw_ingestion(session, hash_="hash-post-packaging-telefono")

        response = client.post(
            f"/curation/{ingestion.id}",
            data={
                "row_count": "1",
                "product_id_0": str(producto.id),
                "price_0": "450",
                "packaging_0": "saco, llamar al 55550101",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303

        session.expire_all()
        fila = session.execute(select(PriceHistory)).scalars().one()
        assert "55550101" not in (fila.packaging or "")
        assert "saco" in (fila.packaging or "")


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
