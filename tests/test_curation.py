"""Cola de curaduría — endpoints FastAPI (spec.md §6.1, Etapa 1c y Etapa 2).

Usa el `TestClient` de FastAPI con `app.dependency_overrides[get_db]` para
inyectar la misma fixture `session` (SQLite en memoria) de `tests/conftest.py`,
sin abrir una conexión real ni tocar `conftest.py`.
"""

import re
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from saimc.config import get_settings
from saimc.extraction import ocr
from saimc.models import (
    PACKAGING_MAX_LENGTH,
    Channel,
    ContentType,
    Currency,
    Platform,
    PriceHistory,
    ProcessingStatus,
    Product,
    Province,
    RawIngestion,
    SaleFormat,
    SourceType,
)
from saimc.web.app import app
from saimc.web.auth import require_operator
from saimc.web.deps import get_db, get_rate_fetcher
from saimc.web.routers.curation import _EXTRA_BLANK_ROWS

# --- helpers de armado ------------------------------------------------------


def _crear_raw_ingestion(
    session: Session,
    *,
    hash_: str,
    content_type: str = ContentType.TEXT,
    original_text: str | None = None,
    file_path: str | None = None,
    captured_at: datetime | None = None,
    source_type: str | None = None,
    source_reference: str | None = None,
    channel_id: uuid.UUID | None = None,
) -> RawIngestion:
    resolved_source_type = source_type or (
        SourceType.INBOX_TEXT if content_type == ContentType.TEXT else SourceType.INBOX_IMAGE
    )
    ingestion = RawIngestion(
        source_type=resolved_source_type,
        source_reference=source_reference,
        file_path=file_path or f"raw/{hash_}.dat",
        content_hash=hash_,
        content_type=content_type,
        original_text=original_text,
        processing_status=ProcessingStatus.PENDING,
        captured_at=captured_at or datetime.now(UTC),
        channel_id=channel_id,
    )
    session.add(ingestion)
    session.commit()
    return ingestion


def _fecha_pub(ingestion: RawIngestion) -> str:
    """Fecha de publicación válida para el submit de esta ingesta.

    La fecha es obligatoria y no puede ser posterior al día de la captura
    (docs/decisions.md, 2026-08-14), así que se deriva del `captured_at` de
    la propia ingesta y no de `date.today()`: los tests de conversión usan
    capturas con fecha fija en el pasado. El día anterior en calendario UTC
    siempre cae dentro de la cota, que el endpoint evalúa en calendario de
    La Habana (UTC-4/-5).
    """
    return (ingestion.captured_at.date() - timedelta(days=1)).isoformat()


def _crear_producto(
    session: Session, *, name: str = "Pollo", aliases: list[str] | None = None
) -> Product:
    producto = Product(
        name=name, category="carnes", unit="lb", aliases=aliases or [], is_pilot=True
    )
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


def _crear_precio_curado(
    session: Session,
    *,
    ingestion: RawIngestion,
    product: Product,
    province: Province | None = None,
    price: str = "100",
    currency: str = Currency.CUP.value,
    unit: str | None = None,
    sale_format: str | None = None,
    packaging: str | None = None,
    min_purchase_quantity: Decimal | None = None,
) -> PriceHistory:
    """Arma una fila de `prices_history` ya curada, sin pasar por el submit.

    Los tests de edición parten de un anuncio que ya tiene filas — construirlas
    directo es más claro que simular el POST original solo para llegar al
    estado de partida.
    """
    precio = PriceHistory(
        product_id=product.id,
        province_id=province.id if province else None,
        price=Decimal(price),
        currency=currency,
        unit=unit,
        sale_format=sale_format,
        packaging=packaging,
        min_purchase_quantity=min_purchase_quantity,
        source_id=ingestion.id,
    )
    session.add(precio)
    session.commit()
    return precio


class _FakeRateFetcher:
    """Reemplazo de red para `get_rate_fetcher` (Commit 2): nunca pega a
    elTOQUE, devuelve tasas fijas por `(clave elTOQUE, día)` y cuenta
    llamadas — el seam que prueba tanto el camino feliz como el degradado
    sin depender de `httpx`."""

    def __init__(self, rates: dict[tuple[str, date], Decimal] | None = None) -> None:
        self.rates = rates or {}
        self.calls: list[tuple[str, date]] = []

    def __call__(self, key: str, day: date) -> Decimal | None:
        self.calls.append((key, day))
        return self.rates.get((key, day))


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
    # Sin tasas configuradas por default: ningún test paga red, y el
    # comportamiento de un test que no opta explícitamente por una tasa
    # (override propio dentro del test) sigue siendo "queda sin convertir" —
    # el mismo resultado que antes de que existiera esta dependencia.
    app.dependency_overrides[get_rate_fetcher] = lambda: _FakeRateFetcher()
    # Esta suite prueba la lógica de curaduría, no HTTP Basic — ese
    # comportamiento tiene su propia cobertura en test_curation_auth.py.
    app.dependency_overrides[require_operator] = lambda: None
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

    def test_canal_origen_ya_conocido_gana_sobre_la_adivinanza_de_texto(
        self, client: TestClient, session: Session
    ) -> None:
        """docs/decisions.md, 2026-08-17: el webhook y el backfill de WAHA
        escriben `source_reference` con el nombre exacto del canal al
        ingerir — un dato cierto que debe prellenar el campo sin depender de
        que el texto del mensaje tenga el patrón "Nombre - fecha" que
        `guess_source_reference` busca (y que un mensaje de WhatsApp normal
        nunca trae, a diferencia de una captura de Facebook)."""
        _crear_producto(session, name="Pollo")
        _crear_raw_ingestion(
            session,
            hash_="hash-canal-origen-conocido",
            original_text="pollo a 500 habana",  # sin el patrón "Nombre - fecha"
            source_type=SourceType.WHATSAPP_CHANNEL,
            source_reference="Groupej Morales Surl",
        )

        response = client.get("/curation")

        assert response.status_code == 200
        assert 'id="source_reference" value="Groupej Morales Surl"' in response.text

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

    def test_fecha_de_canal_automatizado_usa_captured_at_no_el_texto(
        self, client: TestClient, session: Session
    ) -> None:
        """docs/decisions.md, 2026-08-17: para un canal de WhatsApp
        automatizado, `captured_at` YA es el timestamp real que WAHA reportó
        del envío — no hace falta (ni conviene) adivinarlo del texto, que
        casi nunca trae el patrón "Nombre - fecha" de capturas Facebook."""
        _crear_producto(session, name="Pollo")
        _crear_raw_ingestion(
            session,
            hash_="hash-fecha-canal-automatizado",
            original_text="pollo a 500 habana",  # sin ningún patrón de fecha
            captured_at=datetime(2026, 8, 16, 11, 49, tzinfo=UTC),
            source_type=SourceType.WHATSAPP_CHANNEL,
            source_reference="Groupej Morales Surl",
        )

        response = client.get("/curation")

        assert response.status_code == 200
        assert 'id="published_date" value="2026-08-16"' in response.text

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

    def test_corte_sin_match_no_prellena_ninguna_fila(
        self, client: TestClient, session: Session
    ) -> None:
        """«Hígado de pollo» no matchea el genérico «Pollo», y desde el
        2026-08-15 eso ya no abre un alta de producto: la línea simplemente no
        produce fila prellenada.

        Es el caso que hace barata una lista de precios de 25 renglones —solo
        llegan al formulario los que caen en un producto piloto—. El anuncio
        completo sigue intacto en el registro crudo, que es inmutable.
        """
        _crear_producto(session, name="Pollo")
        _crear_raw_ingestion(
            session,
            hash_="hash-corte-sin-match",
            original_text="higado de pollo 40lb a 3000 habana",
        )

        response = client.get("/curation")

        assert response.status_code == 200
        # El texto del anuncio sigue visible en su textarea — eso es correcto,
        # el operador tiene que poder leerlo. Lo que no debe existir es una
        # fila prellenada para esa línea: quedan solo las filas en blanco de
        # cortesía, todas con el `<select>` sin elegir.
        assert response.text.count('value="" selected>— seleccionar —') == _EXTRA_BLANK_ROWS

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


class TestSinCandidatosAtajoDeUnClic:
    """docs/decisions.md, 2026-08-17 — el operador pidió no tener que abrir
    un anuncio sin producto ni precio (ej. un saludo del canal con contacto)
    solo para llegar al botón "Descartar" al final del formulario."""

    def test_sin_producto_ni_precio_muestra_el_atajo(
        self, client: TestClient, session: Session
    ) -> None:
        _crear_producto(session, name="Pollo")
        ingestion = _crear_raw_ingestion(
            session,
            hash_="hash-saludo-sin-datos",
            original_text=(
                "¡Buenos tardes, familia! ☀️\nEscribinos a estos números:\n"
                "[teléfono removido]\n[teléfono removido]"
            ),
        )

        response = client.get("/curation")

        assert response.status_code == 200
        assert "Descartar (sin candidatos)" in response.text
        assert f'/curation/{ingestion.id}/discard' in response.text

    def test_con_candidato_piloto_no_muestra_el_atajo(
        self, client: TestClient, session: Session
    ) -> None:
        _crear_producto(session, name="Pollo")
        _crear_provincia(session)
        _crear_raw_ingestion(
            session, hash_="hash-con-candidato", original_text="pollo a 500 habana"
        )

        response = client.get("/curation")

        assert response.status_code == 200
        assert "Descartar (sin candidatos)" not in response.text

    def test_precio_de_producto_no_piloto_no_muestra_el_atajo(
        self, client: TestClient, session: Session
    ) -> None:
        """`propose_all` sí encontró algo (un precio real), aunque no sea de
        un producto del foco — vale la pena que el operador lo vea, no es el
        mismo caso que un mensaje sin ningún dato aprovechable."""
        _crear_producto(session, name="Pollo")  # ningún producto matchea "cemento"
        _crear_raw_ingestion(
            session, hash_="hash-no-piloto-con-precio", original_text="cemento a 500 el saco"
        )

        response = client.get("/curation")

        assert response.status_code == 200
        assert "Descartar (sin candidatos)" not in response.text

    def test_el_atajo_descarta_de_verdad(
        self, client: TestClient, session: Session
    ) -> None:
        _crear_producto(session, name="Pollo")
        ingestion = _crear_raw_ingestion(
            session, hash_="hash-atajo-descarta", original_text="buenos días a todos"
        )

        response = client.post(
            f"/curation/{ingestion.id}/discard", follow_redirects=False
        )

        assert response.status_code == 303
        session.expire_all()
        assert session.get(RawIngestion, ingestion.id).processing_status == (
            ProcessingStatus.DISCARDED
        )

    def test_el_formulario_completo_sigue_disponible_pero_colapsado(
        self, client: TestClient, session: Session
    ) -> None:
        """El atajo no borra el formulario, lo colapsa (docs/decisions.md,
        2026-08-17, corrigiendo el diseño anterior de este mismo día — el
        operador pidió explícitamente que "Datos del anuncio" no compita
        visualmente con el botón de descarte): sigue alcanzable con un
        click si el operador mira la evidencia y quiere cargar algo a
        mano, pero no aparece expandido por default."""
        _crear_producto(session, name="Pollo")
        _crear_raw_ingestion(
            session, hash_="hash-atajo-no-esconde-form", original_text="buenos días a todos"
        )

        response = client.get("/curation")

        assert response.status_code == 200
        assert 'id="curation-form"' in response.text
        etiqueta = re.search(
            r'<details[^>]*>\s*<summary class="card-header"[^>]*>Datos del anuncio',
            response.text,
        )
        assert etiqueta is not None
        assert "open" not in etiqueta.group(0)

    def test_con_candidatos_el_formulario_sigue_abierto_por_default(
        self, client: TestClient, session: Session
    ) -> None:
        """Regresión: el colapso es solo para el caso sin candidatos — un
        anuncio normal no debe empezar escondido."""
        _crear_producto(session, name="Pollo")
        _crear_provincia(session)
        _crear_raw_ingestion(
            session, hash_="hash-con-candidato-form-abierto", original_text="pollo a 500 habana"
        )

        response = client.get("/curation")

        assert response.status_code == 200
        etiqueta = re.search(
            r'<details[^>]*>\s*<summary class="card-header"[^>]*>Datos del anuncio',
            response.text,
        )
        assert etiqueta is not None
        assert "open" in etiqueta.group(0)


class TestProvinciaDelCanalSiempreManda:
    """docs/decisions.md, 2026-08-17 — "el canal siempre manda": si el
    operador cargó la provincia de un canal automatizado en /catalog, esa
    gana sobre lo que el texto del mensaje diga. No es un fallback."""

    def test_provincia_del_canal_pisa_lo_que_dice_el_texto(
        self, client: TestClient, session: Session
    ) -> None:
        _crear_producto(session, name="Pollo")
        habana = _crear_provincia(session, code="hab", name="La Habana")
        _crear_provincia(session, code="scu", name="Santiago de Cuba")
        canal = Channel(
            name="Groupej Morales",
            platform=Platform.WHATSAPP_CHANNEL,
            province_id=habana.id,
            is_active=True,
        )
        session.add(canal)
        session.commit()
        _crear_raw_ingestion(
            session,
            hash_="hash-canal-manda",
            original_text="pollo a 500 santiago",  # el texto dice OTRA provincia
            source_type=SourceType.WHATSAPP_CHANNEL,
            source_reference="Groupej Morales",
        )

        response = client.get("/curation")

        assert response.status_code == 200
        assert f'value="{habana.id}" selected' in response.text

    def test_sin_provincia_fija_en_el_canal_usa_lo_que_dice_el_texto(
        self, client: TestClient, session: Session
    ) -> None:
        _crear_producto(session, name="Pollo")
        santiago = _crear_provincia(session, code="scu", name="Santiago de Cuba")
        canal = Channel(
            name="Groupej Morales",
            platform=Platform.WHATSAPP_CHANNEL,
            province_id=None,
            is_active=True,
        )
        session.add(canal)
        session.commit()
        _crear_raw_ingestion(
            session,
            hash_="hash-canal-sin-provincia",
            original_text="pollo a 500 santiago",
            source_type=SourceType.WHATSAPP_CHANNEL,
            source_reference="Groupej Morales",
        )

        response = client.get("/curation")

        assert response.status_code == 200
        assert f'value="{santiago.id}" selected' in response.text

    def test_fuente_no_automatizada_no_resuelve_contra_ningun_canal(
        self, client: TestClient, session: Session
    ) -> None:
        """Un canal con ese nombre y provincia fija existe, pero este
        anuncio vino de texto pegado a mano — `source_reference` coincide
        por casualidad. Esa certeza de "es justo este canal" solo existe
        para `SourceType.WHATSAPP_CHANNEL`, donde lo escribe código, nunca
        el operador tipeando."""
        _crear_producto(session, name="Pollo")
        habana = _crear_provincia(session, code="hab", name="La Habana")
        santiago = _crear_provincia(session, code="scu", name="Santiago de Cuba")
        canal = Channel(
            name="Groupej Morales",
            platform=Platform.WHATSAPP_CHANNEL,
            province_id=habana.id,
            is_active=True,
        )
        session.add(canal)
        session.commit()
        _crear_raw_ingestion(
            session,
            hash_="hash-fuente-manual-coincide",
            original_text="pollo a 500 santiago",
            source_type=SourceType.INBOX_TEXT,
            source_reference="Groupej Morales",
        )

        response = client.get("/curation")

        assert response.status_code == 200
        assert f'value="{santiago.id}" selected' in response.text


class TestPrellenoDesdeHistorialDelCanal:
    """docs/decisions.md, 2026-08-17 — idea del operador: una mipyme repite
    el mismo formato de venta anuncio tras anuncio, así que la última
    confirmación real para (canal, producto) es mejor prefill que texto
    vacío para escala/envase/mínimo, que nunca se infieren del texto."""

    def test_prellena_escala_envase_y_minimo_desde_la_ultima_confirmacion(
        self, client: TestClient, session: Session
    ) -> None:
        producto = _crear_producto(session, name="Arroz")
        vieja = _crear_raw_ingestion(
            session,
            hash_="hash-historial-vieja",
            source_type=SourceType.WHATSAPP_CHANNEL,
            source_reference="Groupej Morales Surl",
            captured_at=datetime(2026, 8, 10, tzinfo=UTC),
        )
        _crear_precio_curado(
            session,
            ingestion=vieja,
            product=producto,
            sale_format="retail",
            packaging="Saco",
            min_purchase_quantity=Decimal("1"),
        )
        # `_next_pending` elige entre los `PENDING` —
        # sin esto, `/curation` mostraría el antecedente, no el anuncio
        # nuevo que este test quiere ver prellenado.
        vieja.processing_status = ProcessingStatus.CURATED
        session.commit()
        _crear_raw_ingestion(
            session,
            hash_="hash-historial-nueva",
            original_text="arroz a 30000",
            source_type=SourceType.WHATSAPP_CHANNEL,
            source_reference="Groupej Morales Surl",
            captured_at=datetime(2026, 8, 17, tzinfo=UTC),
        )

        response = client.get("/curation")

        assert response.status_code == 200
        assert f'value="{producto.id}" selected' in response.text
        assert '<option value="retail" selected>' in response.text
        assert '<option value="saco" selected>' in response.text
        # `Numeric` de 3 decimales (mismo formato que ya usa
        # `_values_from_price_history`, ver test de edición línea 2650).
        assert 'id="min_purchase_0" value="1.000"' in response.text

    def test_sin_antecedente_queda_vacio_como_siempre(
        self, client: TestClient, session: Session
    ) -> None:
        _crear_producto(session, name="Arroz")
        _crear_raw_ingestion(
            session,
            hash_="hash-historial-sin-antecedente",
            original_text="arroz a 30000",
            source_type=SourceType.WHATSAPP_CHANNEL,
            source_reference="Canal nuevo, nunca vendió Arroz antes",
        )

        response = client.get("/curation")

        assert response.status_code == 200
        assert '<option value="retail" selected>' not in response.text
        assert 'id="min_purchase_0" value=""' in response.text

    def test_no_mezcla_el_historial_de_otro_producto(
        self, client: TestClient, session: Session
    ) -> None:
        """El antecedente es por (canal, producto) — que el canal haya
        vendido Pollo mayorista no debe prellenar Arroz como mayorista."""
        arroz = _crear_producto(session, name="Arroz")
        pollo = _crear_producto(session, name="Pollo")
        vieja = _crear_raw_ingestion(
            session,
            hash_="hash-historial-otro-producto",
            source_type=SourceType.WHATSAPP_CHANNEL,
            source_reference="Groupej Morales Surl",
            captured_at=datetime(2026, 8, 10, tzinfo=UTC),
        )
        _crear_precio_curado(
            session, ingestion=vieja, product=pollo, sale_format="wholesale"
        )
        vieja.processing_status = ProcessingStatus.CURATED
        session.commit()
        _crear_raw_ingestion(
            session,
            hash_="hash-historial-arroz-nuevo",
            original_text="arroz a 30000",
            source_type=SourceType.WHATSAPP_CHANNEL,
            source_reference="Groupej Morales Surl",
            captured_at=datetime(2026, 8, 17, tzinfo=UTC),
        )

        response = client.get("/curation")

        assert response.status_code == 200
        assert f'value="{arroz.id}" selected' in response.text
        assert '<option value="wholesale" selected>' not in response.text

    def test_no_mezcla_el_historial_de_otro_canal(
        self, client: TestClient, session: Session
    ) -> None:
        """Mismo producto, canal distinto: cada mipyme tiene su propio
        formato de venta, no hay razón para heredarlo de otra."""
        producto = _crear_producto(session, name="Arroz")
        vieja = _crear_raw_ingestion(
            session,
            hash_="hash-historial-otro-canal",
            source_type=SourceType.WHATSAPP_CHANNEL,
            source_reference="Otro Canal Cualquiera",
            captured_at=datetime(2026, 8, 10, tzinfo=UTC),
        )
        _crear_precio_curado(
            session, ingestion=vieja, product=producto, sale_format="wholesale"
        )
        vieja.processing_status = ProcessingStatus.CURATED
        session.commit()
        _crear_raw_ingestion(
            session,
            hash_="hash-historial-canal-nuevo",
            original_text="arroz a 30000",
            source_type=SourceType.WHATSAPP_CHANNEL,
            source_reference="Groupej Morales Surl",
            captured_at=datetime(2026, 8, 17, tzinfo=UTC),
        )

        response = client.get("/curation")

        assert response.status_code == 200
        assert '<option value="wholesale" selected>' not in response.text

    def test_grupo_no_prellena_porque_el_antecedente_puede_ser_de_otro_vendedor(
        self, client: TestClient, session: Session
    ) -> None:
        """En un GRUPO el `source_reference` es el nombre del mercado, no el
        del vendedor: se midieron hasta 27 emisores distintos en un mismo
        grupo (2026-08-18). Heredar el saco del vendedor A al anuncio del
        vendedor B escribiría dato plausible pero falso en el formulario, que
        el operador puede confirmar sin notarlo. Vacío es mejor que eso."""
        producto = _crear_producto(session, name="Arroz")
        grupo = Channel(
            name="Mercado Santiago 2.3",
            platform=Platform.WHATSAPP_GROUP,
            external_id="120363412268792398@g.us",
            is_active=True,
        )
        session.add(grupo)
        session.commit()
        vieja = _crear_raw_ingestion(
            session,
            hash_="hash-historial-grupo-vieja",
            source_type=SourceType.WHATSAPP_CHANNEL,
            source_reference="Mercado Santiago 2.3",
            captured_at=datetime(2026, 8, 10, tzinfo=UTC),
        )
        _crear_precio_curado(
            session,
            ingestion=vieja,
            product=producto,
            sale_format="wholesale",
            packaging="Saco",
            min_purchase_quantity=Decimal("5"),
        )
        vieja.processing_status = ProcessingStatus.CURATED
        session.commit()
        _crear_raw_ingestion(
            session,
            hash_="hash-historial-grupo-nueva",
            original_text="arroz a 30000",
            source_type=SourceType.WHATSAPP_CHANNEL,
            source_reference="Mercado Santiago 2.3",
            captured_at=datetime(2026, 8, 17, tzinfo=UTC),
        )

        response = client.get("/curation")

        assert response.status_code == 200
        # El producto sí se propone: eso sale del texto, no del historial.
        assert f'value="{producto.id}" selected' in response.text
        # Escala, envase y mínimo quedan vacíos: son del vendedor, y en un
        # grupo no sabemos cuál vendedor es.
        assert '<option value="wholesale" selected>' not in response.text
        assert '<option value="saco" selected>' not in response.text
        assert 'id="min_purchase_0" value=""' in response.text

    def test_canal_automatizado_si_prellena_aunque_exista_la_guarda_de_grupo(
        self, client: TestClient, session: Session
    ) -> None:
        """La guarda es específica del grupo: un canal `@newsletter` tiene un
        solo emisor, así que el antecedente sigue siendo suyo."""
        producto = _crear_producto(session, name="Arroz")
        canal = Channel(
            name="Groupej Morales Surl",
            platform=Platform.WHATSAPP_CHANNEL,
            external_id="120363419630368180@newsletter",
            is_active=True,
        )
        session.add(canal)
        session.commit()
        vieja = _crear_raw_ingestion(
            session,
            hash_="hash-historial-canal-registrado-vieja",
            source_type=SourceType.WHATSAPP_CHANNEL,
            source_reference="Groupej Morales Surl",
            captured_at=datetime(2026, 8, 10, tzinfo=UTC),
        )
        _crear_precio_curado(
            session,
            ingestion=vieja,
            product=producto,
            sale_format="retail",
            packaging="Saco",
            min_purchase_quantity=Decimal("1"),
        )
        vieja.processing_status = ProcessingStatus.CURATED
        session.commit()
        _crear_raw_ingestion(
            session,
            hash_="hash-historial-canal-registrado-nueva",
            original_text="arroz a 30000",
            source_type=SourceType.WHATSAPP_CHANNEL,
            source_reference="Groupej Morales Surl",
            captured_at=datetime(2026, 8, 17, tzinfo=UTC),
        )

        response = client.get("/curation")

        assert response.status_code == 200
        assert '<option value="retail" selected>' in response.text
        assert '<option value="saco" selected>' in response.text
        assert 'id="min_purchase_0" value="1.000"' in response.text

    def test_usa_la_confirmacion_mas_reciente(
        self, client: TestClient, session: Session
    ) -> None:
        """Si el canal cambió de formato de venta, vale lo último que
        confirmó el operador, no lo primero que vendió."""
        producto = _crear_producto(session, name="Arroz")
        antigua = _crear_raw_ingestion(
            session,
            hash_="hash-historial-antigua",
            source_type=SourceType.WHATSAPP_CHANNEL,
            source_reference="Groupej Morales Surl",
            captured_at=datetime(2026, 8, 1, tzinfo=UTC),
        )
        _crear_precio_curado(
            session, ingestion=antigua, product=producto, sale_format="wholesale"
        )
        antigua.processing_status = ProcessingStatus.CURATED
        reciente = _crear_raw_ingestion(
            session,
            hash_="hash-historial-reciente",
            source_type=SourceType.WHATSAPP_CHANNEL,
            source_reference="Groupej Morales Surl",
            captured_at=datetime(2026, 8, 10, tzinfo=UTC),
        )
        _crear_precio_curado(
            session, ingestion=reciente, product=producto, sale_format="retail"
        )
        reciente.processing_status = ProcessingStatus.CURATED
        session.commit()
        _crear_raw_ingestion(
            session,
            hash_="hash-historial-nueva-2",
            original_text="arroz a 30000",
            source_type=SourceType.WHATSAPP_CHANNEL,
            source_reference="Groupej Morales Surl",
            captured_at=datetime(2026, 8, 17, tzinfo=UTC),
        )

        response = client.get("/curation")

        assert response.status_code == 200
        assert '<option value="retail" selected>' in response.text
        assert '<option value="wholesale" selected>' not in response.text


class TestOrigenDeIngestaAmbiguo:
    """Dos filas de `channels` pueden compartir nombre desde que
    `idx_channels_name` dejó de ser único (migración 0014, docs/decisions.md
    2026-08-19: "Ventas Mayoristas" es a la vez canal y grupo). La cola de
    curaduría no puede volver a resolver el origen por nombre sin admitir
    que el nombre puede ya no alcanzar."""

    def test_dos_canales_con_el_mismo_nombre_no_rompen_la_cola(
        self, client: TestClient, session: Session
    ) -> None:
        _crear_producto(session, name="Arroz")
        canal = Channel(
            name="Ventas Mayoristas",
            platform=Platform.WHATSAPP_CHANNEL,
            external_id="1@newsletter",
            is_active=True,
        )
        grupo = Channel(
            name="Ventas Mayoristas",
            platform=Platform.WHATSAPP_GROUP,
            external_id="1@g.us",
            is_active=True,
        )
        session.add_all([canal, grupo])
        session.commit()
        _crear_raw_ingestion(
            session,
            hash_="hash-nombre-ambiguo",
            original_text="arroz a 30000",
            source_type=SourceType.WHATSAPP_CHANNEL,
            source_reference="Ventas Mayoristas",
        )

        response = client.get("/curation")

        assert response.status_code == 200

    def test_channel_id_nulo_dos_coincidencias_de_nombre_sin_override_de_provincia(
        self, client: TestClient, session: Session
    ) -> None:
        """`_channel_province_id` no puede adivinar cuál de las dos filas
        manda: el override queda afuera y sigue vigente el texto."""
        _crear_producto(session, name="Arroz")
        santiago = _crear_provincia(session, code="scu", name="Santiago de Cuba")
        habana = _crear_provincia(session, code="hab", name="La Habana")
        canal = Channel(
            name="Ventas Mayoristas",
            platform=Platform.WHATSAPP_CHANNEL,
            external_id="1@newsletter",
            province_id=habana.id,
            is_active=True,
        )
        grupo = Channel(
            name="Ventas Mayoristas",
            platform=Platform.WHATSAPP_GROUP,
            external_id="1@g.us",
            province_id=habana.id,
            is_active=True,
        )
        session.add_all([canal, grupo])
        session.commit()
        _crear_raw_ingestion(
            session,
            hash_="hash-ambiguo-sin-override",
            original_text="arroz a 30000 santiago",
            source_type=SourceType.WHATSAPP_CHANNEL,
            source_reference="Ventas Mayoristas",
        )

        response = client.get("/curation")

        assert response.status_code == 200
        assert f'value="{santiago.id}" selected' in response.text
        assert f'value="{habana.id}" selected' not in response.text

    def test_channel_id_presente_aplica_override_de_provincia_del_canal_apuntado(
        self, client: TestClient, session: Session
    ) -> None:
        """Con `channel_id` la resolución es por id, no por nombre — ni
        siquiera necesita que el nombre sea único."""
        _crear_producto(session, name="Arroz")
        habana = _crear_provincia(session, code="hab", name="La Habana")
        _crear_provincia(session, code="scu", name="Santiago de Cuba")
        canal = Channel(
            name="Ventas Mayoristas",
            platform=Platform.WHATSAPP_CHANNEL,
            external_id="1@newsletter",
            province_id=habana.id,
            is_active=True,
        )
        grupo = Channel(
            name="Ventas Mayoristas",
            platform=Platform.WHATSAPP_GROUP,
            external_id="1@g.us",
            is_active=True,
        )
        session.add_all([canal, grupo])
        session.commit()
        _crear_raw_ingestion(
            session,
            hash_="hash-channel-id-override",
            original_text="arroz a 30000 santiago",
            source_type=SourceType.WHATSAPP_CHANNEL,
            source_reference="Ventas Mayoristas",
            channel_id=canal.id,
        )

        response = client.get("/curation")

        assert response.status_code == 200
        assert f'value="{habana.id}" selected' in response.text

    def test_channel_id_apunta_a_grupo_no_prellena_aunque_el_canal_homonimo_tenga_historial(
        self, client: TestClient, session: Session
    ) -> None:
        """El caso central de esta migración (decisión 2026-08-18): un
        `channel_id` que apunta al GRUPO nunca debe heredar el historial del
        CANAL homónimo, aunque el nombre coincida y el canal tenga
        antecedente confirmado."""
        producto = _crear_producto(session, name="Arroz")
        canal = Channel(
            name="Ventas Mayoristas",
            platform=Platform.WHATSAPP_CHANNEL,
            external_id="1@newsletter",
            is_active=True,
        )
        grupo = Channel(
            name="Ventas Mayoristas",
            platform=Platform.WHATSAPP_GROUP,
            external_id="1@g.us",
            is_active=True,
        )
        session.add_all([canal, grupo])
        session.commit()
        vieja = _crear_raw_ingestion(
            session,
            hash_="hash-grupo-vs-canal-vieja",
            source_type=SourceType.WHATSAPP_CHANNEL,
            source_reference="Ventas Mayoristas",
            channel_id=canal.id,
            captured_at=datetime(2026, 8, 10, tzinfo=UTC),
        )
        _crear_precio_curado(
            session,
            ingestion=vieja,
            product=producto,
            sale_format="retail",
            packaging="Saco",
            min_purchase_quantity=Decimal("1"),
        )
        vieja.processing_status = ProcessingStatus.CURATED
        session.commit()
        _crear_raw_ingestion(
            session,
            hash_="hash-grupo-vs-canal-nueva",
            original_text="arroz a 30000",
            source_type=SourceType.WHATSAPP_CHANNEL,
            source_reference="Ventas Mayoristas",
            channel_id=grupo.id,
            captured_at=datetime(2026, 8, 17, tzinfo=UTC),
        )

        response = client.get("/curation")

        assert response.status_code == 200
        assert '<option value="retail" selected>' not in response.text
        assert '<option value="saco" selected>' not in response.text
        assert 'id="min_purchase_0" value=""' in response.text

    def test_channel_id_nulo_dos_coincidencias_de_nombre_sin_preseleccion_historica(
        self, client: TestClient, session: Session
    ) -> None:
        """Sin `channel_id` y con el nombre ambiguo, ninguna de las dos
        filas puede prestar su historial — no importa cuál de las dos lo
        tenga."""
        producto = _crear_producto(session, name="Arroz")
        canal = Channel(
            name="Ventas Mayoristas",
            platform=Platform.WHATSAPP_CHANNEL,
            external_id="1@newsletter",
            is_active=True,
        )
        grupo = Channel(
            name="Ventas Mayoristas",
            platform=Platform.WHATSAPP_GROUP,
            external_id="1@g.us",
            is_active=True,
        )
        session.add_all([canal, grupo])
        session.commit()
        vieja = _crear_raw_ingestion(
            session,
            hash_="hash-ambiguo-historial-vieja",
            source_type=SourceType.WHATSAPP_CHANNEL,
            source_reference="Ventas Mayoristas",
            channel_id=canal.id,
            captured_at=datetime(2026, 8, 10, tzinfo=UTC),
        )
        _crear_precio_curado(
            session,
            ingestion=vieja,
            product=producto,
            sale_format="retail",
            packaging="Saco",
            min_purchase_quantity=Decimal("1"),
        )
        vieja.processing_status = ProcessingStatus.CURATED
        session.commit()
        # La fila nueva es PRE-`channel_id`: llega sin él, como cualquier
        # fila creada antes de esta migración.
        _crear_raw_ingestion(
            session,
            hash_="hash-ambiguo-historial-nueva",
            original_text="arroz a 30000",
            source_type=SourceType.WHATSAPP_CHANNEL,
            source_reference="Ventas Mayoristas",
            captured_at=datetime(2026, 8, 17, tzinfo=UTC),
        )

        response = client.get("/curation")

        assert response.status_code == 200
        assert '<option value="retail" selected>' not in response.text
        assert 'id="min_purchase_0" value=""' in response.text

    def test_channel_id_apunta_a_canal_no_hereda_historial_del_grupo_homonimo(
        self, client: TestClient, session: Session
    ) -> None:
        """La consulta histórica filtra por texto de `source_reference`, que
        el canal y el grupo comparten cuando tienen el mismo nombre. Una
        ingesta con `channel_id` apuntando al CANAL no debe traer historial
        confirmado que en realidad viene de una ingesta del GRUPO, aunque el
        texto coincida — es la misma fuga cruzada que la guarda de grupo
        (`is_whatsapp_group`) ya previene, pero en la dirección contraria."""
        producto = _crear_producto(session, name="Arroz")
        canal = Channel(
            name="Ventas Mayoristas",
            platform=Platform.WHATSAPP_CHANNEL,
            external_id="1@newsletter",
            is_active=True,
        )
        grupo = Channel(
            name="Ventas Mayoristas",
            platform=Platform.WHATSAPP_GROUP,
            external_id="1@g.us",
            is_active=True,
        )
        session.add_all([canal, grupo])
        session.commit()
        vieja_del_grupo = _crear_raw_ingestion(
            session,
            hash_="hash-canal-vs-grupo-vieja",
            source_type=SourceType.WHATSAPP_CHANNEL,
            source_reference="Ventas Mayoristas",
            channel_id=grupo.id,
            captured_at=datetime(2026, 8, 10, tzinfo=UTC),
        )
        _crear_precio_curado(
            session,
            ingestion=vieja_del_grupo,
            product=producto,
            sale_format="wholesale",
            packaging="Saco",
            min_purchase_quantity=Decimal("5"),
        )
        vieja_del_grupo.processing_status = ProcessingStatus.CURATED
        session.commit()
        _crear_raw_ingestion(
            session,
            hash_="hash-canal-vs-grupo-nueva",
            original_text="arroz a 30000",
            source_type=SourceType.WHATSAPP_CHANNEL,
            source_reference="Ventas Mayoristas",
            channel_id=canal.id,
            captured_at=datetime(2026, 8, 17, tzinfo=UTC),
        )

        response = client.get("/curation")

        assert response.status_code == 200
        assert '<option value="wholesale" selected>' not in response.text
        assert '<option value="saco" selected>' not in response.text
        assert 'id="min_purchase_0" value=""' in response.text


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
                "published_date": _fecha_pub(ingestion),
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
                "published_date": _fecha_pub(ingestion),
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
                "published_date": _fecha_pub(ingestion),
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

    def test_sin_fecha_de_publicacion_no_guarda_nada(
        self, client: TestClient, session: Session
    ) -> None:
        """Campo vacío: 422 y ninguna fila creada.

        Reemplaza el contrato original de «campo nunca bloqueante»
        (docs/decisions.md, 2026-08-10 → 2026-08-14). Sin fecha, el repliegue
        a `created_at`/`captured_at` mete el anuncio en la ventana de precio
        vigente y le ancla la tasa de hoy: dos errores silenciosos que solo
        el operador puede evitar, y solo si el formulario se lo exige.
        """
        pollo = _crear_producto(session, name="Pollo")
        ingestion = _crear_raw_ingestion(session, hash_="hash-post-fecha-vacia")

        response = client.post(
            f"/curation/{ingestion.id}",
            data={
                "row_count": "1",
                "product_id_0": str(pollo.id),
                "price_0": "450",
                "published_date": "",
            },
            follow_redirects=False,
        )

        assert response.status_code == 422
        assert "fecha de publicación" in response.text.lower()

        session.expire_all()
        assert (
            session.execute(
                select(PriceHistory).where(PriceHistory.source_id == ingestion.id)
            ).first()
            is None
        )
        refrescado = session.get(RawIngestion, ingestion.id)
        assert refrescado is not None
        assert refrescado.processing_status == ProcessingStatus.PENDING

    def test_fecha_de_publicacion_invalida_no_guarda_nada(
        self, client: TestClient, session: Session
    ) -> None:
        """Valor no parseable: 422, no un `NULL` silencioso."""
        pollo = _crear_producto(session, name="Pollo")
        ingestion = _crear_raw_ingestion(session, hash_="hash-post-fecha-basura")

        response = client.post(
            f"/curation/{ingestion.id}",
            data={
                "row_count": "1",
                "product_id_0": str(pollo.id),
                "price_0": "450",
                "published_date": "no-es-una-fecha",
            },
            follow_redirects=False,
        )

        assert response.status_code == 422
        session.expire_all()
        assert (
            session.execute(
                select(PriceHistory).where(PriceHistory.source_id == ingestion.id)
            ).first()
            is None
        )

    def test_fecha_posterior_a_la_captura_no_guarda_nada(
        self, client: TestClient, session: Session
    ) -> None:
        """La cota es el día de la captura, no `date.today()`: un anuncio no
        pudo publicarse después de que el operador lo capturó."""
        pollo = _crear_producto(session, name="Pollo")
        ingestion = _crear_raw_ingestion(
            session,
            hash_="hash-post-fecha-futura",
            captured_at=datetime(2026, 8, 10, 12, 0, tzinfo=UTC),
        )

        response = client.post(
            f"/curation/{ingestion.id}",
            data={
                "row_count": "1",
                "product_id_0": str(pollo.id),
                "price_0": "450",
                "published_date": "2026-08-11",
            },
            follow_redirects=False,
        )

        assert response.status_code == 422
        session.expire_all()
        assert (
            session.execute(
                select(PriceHistory).where(PriceHistory.source_id == ingestion.id)
            ).first()
            is None
        )

    def test_fecha_de_semanas_atras_se_acepta_tal_cual(
        self, client: TestClient, session: Session
    ) -> None:
        """Un anuncio viejo es dato legítimo: suma a la meta de 500 (§9) y
        entra a la serie con SU fecha, no con la de carga. Es exactamente el
        caso que la obligatoriedad viene a proteger."""
        pollo = _crear_producto(session, name="Pollo")
        ingestion = _crear_raw_ingestion(
            session,
            hash_="hash-post-fecha-vieja",
            captured_at=datetime(2026, 8, 14, 12, 0, tzinfo=UTC),
        )

        response = client.post(
            f"/curation/{ingestion.id}",
            data={
                "row_count": "1",
                "product_id_0": str(pollo.id),
                "price_0": "450",
                "published_date": "2026-07-24",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        session.expire_all()
        fila = session.execute(
            select(PriceHistory).where(PriceHistory.source_id == ingestion.id)
        ).scalar_one()
        assert fila.published_date == date(2026, 7, 24)

    def test_editar_curado_sin_fecha_no_pisa_la_fila(
        self, client: TestClient, session: Session
    ) -> None:
        """Misma regla en la corrección: sin fecha, la edición entera se
        rechaza y la fila queda como estaba."""
        pollo = _crear_producto(session, name="Pollo")
        curado = _crear_raw_ingestion(session, hash_="hash-edit-sin-fecha")
        curado.processing_status = ProcessingStatus.CURATED
        session.commit()
        precio = _crear_precio_curado(session, ingestion=curado, product=pollo, price="100")

        response = client.post(
            f"/curation/{curado.id}/edit",
            data=_form_edicion(
                curado,
                {
                    "product_id": str(pollo.id),
                    "price": "999",
                    "price_id": str(precio.id),
                    "published_date": "",
                },
            ),
            follow_redirects=False,
        )

        assert response.status_code == 422
        session.refresh(precio)
        assert precio.price == Decimal("100")

    def test_compra_minima_se_persiste_por_fila(
        self, client: TestClient, session: Session
    ) -> None:
        """El mínimo es por fila, no por anuncio: un mismo anuncio puede
        ofrecer un producto al detalle y otro solo por lote — el caso real de
        `094f07b0` («Arroz 1kg 21900 por paca de 30 und, al detalle 750»)."""
        arroz = _crear_producto(session, name="Arroz")
        pollo = _crear_producto(session, name="Pollo")
        ingestion = _crear_raw_ingestion(session, hash_="hash-post-minimo")

        response = client.post(
            f"/curation/{ingestion.id}",
            data={
                "published_date": _fecha_pub(ingestion),
                "row_count": "2",
                "product_id_0": str(arroz.id),
                "price_0": "730",
                "quantity_0": "1",
                "unit_0": "kg",
                "min_purchase_0": "30",
                "product_id_1": str(pollo.id),
                "price_1": "450",
                "min_purchase_1": "",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        session.expire_all()
        filas = {
            f.product_id: f
            for f in session.execute(
                select(PriceHistory).where(PriceHistory.source_id == ingestion.id)
            ).scalars()
        }
        assert filas[arroz.id].min_purchase_quantity == Decimal("30")
        assert filas[pollo.id].min_purchase_quantity is None

    @pytest.mark.parametrize("valor", ["", "muchos", "0", "-5"])
    def test_compra_minima_invalida_o_no_positiva_degrada_a_null(
        self, client: TestClient, session: Session, valor: str
    ) -> None:
        """Degrada, no bloquea — contrario a `published_date`. Acá el vacío no
        puede fabricar un valor equivocado: «sin mínimo declarado» y «el mínimo
        es la cantidad de la fila» dan el mismo resultado en el filtro."""
        pollo = _crear_producto(session, name="Pollo")
        ingestion = _crear_raw_ingestion(session, hash_=f"hash-post-minimo-{valor or 'vacio'}")

        response = client.post(
            f"/curation/{ingestion.id}",
            data={
                "published_date": _fecha_pub(ingestion),
                "row_count": "1",
                "product_id_0": str(pollo.id),
                "price_0": "450",
                "min_purchase_0": valor,
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        session.expire_all()
        fila = session.execute(
            select(PriceHistory).where(PriceHistory.source_id == ingestion.id)
        ).scalar_one()
        assert fila.min_purchase_quantity is None

    def test_filas_vacias_entre_completas_se_ignoran_sin_error(
        self, client: TestClient, session: Session
    ) -> None:
        pollo = _crear_producto(session, name="Pollo")
        picadillo = _crear_producto(session, name="Picadillo")
        ingestion = _crear_raw_ingestion(session, hash_="hash-post-filas-vacias")

        response = client.post(
            f"/curation/{ingestion.id}",
            data={
                "published_date": _fecha_pub(ingestion),
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
                "published_date": _fecha_pub(ingestion),
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
                "published_date": _fecha_pub(ingestion),
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

    def test_new_product_name_posteado_ya_no_crea_producto_ni_precio(
        self, client: TestClient, session: Session
    ) -> None:
        """El alta al vuelo se quitó el 2026-08-15 (docs/decisions.md): era el
        camino por el que el catálogo creció a 60 productos con duplicados que
        parten la densidad de una serie. Un cliente viejo que todavía mande el
        campo no debe volver a abrirlo por la puerta de atrás.

        La fila queda con precio y sin producto, que es el error explícito ya
        definido: no se guarda nada y el submit vuelve al formulario. El alta
        de productos vive ahora en /catalog (ver tests/test_catalog.py).
        """
        ingestion = _crear_raw_ingestion(session, hash_="hash-post-alta-cerrada")

        response = client.post(
            f"/curation/{ingestion.id}",
            data={
                "published_date": _fecha_pub(ingestion),
                "row_count": "1",
                "new_product_name_0": "Detergente",
                "price_0": "900",
            },
            follow_redirects=False,
        )

        assert response.status_code == 422
        session.expire_all()
        assert list(session.execute(select(Product)).scalars()) == []
        assert list(session.execute(select(PriceHistory)).scalars()) == []

    def test_precio_sin_producto_devuelve_el_mensaje_de_error_puntual(
        self, client: TestClient, session: Session
    ) -> None:
        ingestion = _crear_raw_ingestion(session, hash_="hash-post-precio-sin-producto")

        response = client.post(
            f"/curation/{ingestion.id}",
            data={
                "published_date": _fecha_pub(ingestion),
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
                "published_date": _fecha_pub(ingestion),
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
                "published_date": _fecha_pub(ingestion),
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

    def test_el_formato_de_venta_respondido_sobrevive_al_re_render_por_error(
        self, client: TestClient, session: Session
    ) -> None:
        """Si el formulario rebota, la respuesta a la prueba de la jabita se
        devuelve tal cual la dio el operador.

        El default sin responder existe para no fabricar `retail`; devolver lo
        que el operador ya eligió no fabrica nada. Perderlo en el rebote es peor
        que molesto: si no lo nota, reenvía `NULL` justo en el campo por el que
        existe este formulario."""
        pollo = _crear_producto(session, name="Pollo")
        ingestion = _crear_raw_ingestion(session, hash_="hash-post-formato-re-render")

        response = client.post(
            f"/curation/{ingestion.id}",
            data={
                "published_date": _fecha_pub(ingestion),
                "row_count": "2",
                "product_id_0": str(pollo.id),
                "price_0": "450",
                "sale_format_0": "wholesale",
                "product_id_1": str(pollo.id),
                "price_1": "no-es-un-numero",
            },
        )

        assert response.status_code == 422
        # La fila 0 recupera su respuesta; la fila 1, que no respondió, no
        # recibe ninguna fabricada.
        assert response.text.count('<option value="wholesale" selected>') == 1
        assert '<option value="retail" selected>' not in response.text

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
                "published_date": _fecha_pub(ingestion),
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
                "published_date": _fecha_pub(ingestion),
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
                "published_date": _fecha_pub(ingestion),
                "row_count": "1",
                "product_id_0": str(producto.id),
                "price_0": "450",
                "unit_0": "lb",
                "sale_format_0": "wholesale",
                "packaging_format_0": "saco",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303

        session.expire_all()
        fila = session.execute(select(PriceHistory)).scalars().one()
        assert fila.unit == "lb"
        assert fila.sale_format == "wholesale"
        assert fila.packaging == "Saco"

    def test_packaging_no_se_confunde_con_la_cantidad_ni_con_la_unidad(
        self, client: TestClient, session: Session
    ) -> None:
        """spec: `quantity` y `unit` dimensionan el lote, `packaging` lo nombra.

        Es la distinción que justifica el campo: sin ella, "saco de 25 kg" se
        aplasta dentro de `quantity` y se pierde el envase."""
        producto = _crear_producto(session)
        ingestion = _crear_raw_ingestion(session, hash_="hash-post-packaging-vs-cantidad")

        response = client.post(
            f"/curation/{ingestion.id}",
            data={
                "published_date": _fecha_pub(ingestion),
                "row_count": "1",
                "product_id_0": str(producto.id),
                "price_0": "17500",
                "quantity_0": "25",
                "unit_0": "kg",
                "packaging_format_0": "saco",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303

        session.expire_all()
        fila = session.execute(select(PriceHistory)).scalars().one()
        assert fila.quantity == Decimal("25")
        assert fila.unit == "kg"
        assert fila.packaging == "Saco"

    def test_dos_filas_comparten_la_unidad_y_difieren_en_el_formato_de_venta(
        self, client: TestClient, session: Session
    ) -> None:
        """spec: el criterio de aceptación del cambio.

        Un anuncio de saco y uno minorista por libra del mismo producto tienen
        que quedar distinguibles. Es exactamente lo que el esquema no podía
        expresar antes: las dos filas llevan `unit = "lb"` y solo `sale_format`
        las separa."""
        arroz = _crear_producto(session, name="Arroz")
        ingestion = _crear_raw_ingestion(session, hash_="hash-post-dos-formatos")

        response = client.post(
            f"/curation/{ingestion.id}",
            data={
                "published_date": _fecha_pub(ingestion),
                "row_count": "2",
                "product_id_0": str(arroz.id),
                "price_0": "318.20",
                "unit_0": "lb",
                "sale_format_0": "wholesale",
                "product_id_1": str(arroz.id),
                "price_1": "350",
                "unit_1": "lb",
                "sale_format_1": "retail",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303

        session.expire_all()
        filas = list(session.execute(select(PriceHistory)).scalars())
        assert len(filas) == 2
        assert {f.unit for f in filas} == {"lb"}
        assert {f.sale_format for f in filas} == {"retail", "wholesale"}

    def test_sale_format_en_blanco_guarda_null_nunca_retail(
        self, client: TestClient, session: Session
    ) -> None:
        """spec: envío en blanco guarda NULL, nunca un `retail` fabricado."""
        producto = _crear_producto(session)
        ingestion = _crear_raw_ingestion(session, hash_="hash-post-sale-format-blanco")

        response = client.post(
            f"/curation/{ingestion.id}",
            data={
                "published_date": _fecha_pub(ingestion),
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

    def test_formato_de_venta_fuera_de_dominio_degrada_a_null_sin_romper(
        self, client: TestClient, session: Session
    ) -> None:
        """Un valor no vacío pero fuera de dominio degrada a NULL.

        El caso en blanco no prueba esto: un gate débil como
        `row["sale_format"] or None` también lo pasaría. Lo que se verifica acá
        es la pertenencia al conjunto, que es lo que evita que un POST armado a
        mano llegue al CHECK y tire un IntegrityError desde la capa web."""
        producto = _crear_producto(session)
        ingestion = _crear_raw_ingestion(session, hash_="hash-post-formato-invalido")

        response = client.post(
            f"/curation/{ingestion.id}",
            data={
                "published_date": _fecha_pub(ingestion),
                "row_count": "1",
                "product_id_0": str(producto.id),
                "price_0": "450",
                "sale_format_0": "mayorista",
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
                "published_date": _fecha_pub(ingestion),
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
                "published_date": _fecha_pub(ingestion),
                "row_count": "1",
                "product_id_0": str(producto.id),
                "price_0": "450",
                "packaging_format_0": "otro",
                "packaging_detail_0": "saco, llamar al 55550101",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303

        session.expire_all()
        fila = session.execute(select(PriceHistory)).scalars().one()
        assert fila.packaging is not None  # si fuera None la negativa pasaría sola
        assert "55550101" not in fila.packaging
        assert "saco" in fila.packaging

    def test_packaging_vacio_guarda_null_y_no_cadena_vacia(
        self, client: TestClient, session: Session
    ) -> None:
        """Un envase sin llenar es NULL, no `""`.

        La diferencia importa: `""` sería un tercer estado no declarado en un
        campo cuya semántica de nulos ya es una concesión asumida. Un helper que
        devolviera `(redactado or "")[:64]` pasaría todo lo demás igual."""
        producto = _crear_producto(session)
        ingestion = _crear_raw_ingestion(session, hash_="hash-post-packaging-vacio")

        response = client.post(
            f"/curation/{ingestion.id}",
            data={
                "published_date": _fecha_pub(ingestion),
                "row_count": "1",
                "product_id_0": str(producto.id),
                "price_0": "450",
                "packaging_format_0": "",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303

        session.expire_all()
        fila = session.execute(select(PriceHistory)).scalars().one()
        assert fila.packaging is None

    def test_packaging_redactado_nunca_excede_el_ancho_de_la_columna(
        self, client: TestClient, session: Session
    ) -> None:
        """La redacción alarga el texto: el placeholder mide 19 caracteres y
        reemplaza 8 dígitos, +11 netos.

        Un envase de 64 caracteres con un teléfono adentro sale redactado en 75
        y no entra en `varchar(64)`: PostgreSQL corta el submit entero con
        StringDataRightTruncation. SQLite no valida el ancho, así que este test
        afirma sobre el largo y no sobre el motor."""
        producto = _crear_producto(session)
        ingestion = _crear_raw_ingestion(session, hash_="hash-post-packaging-largo")

        envase = "saco de 25 kg de arroz, coordinar entrega antes, llamar 55550101"
        assert len(envase) == PACKAGING_MAX_LENGTH

        response = client.post(
            f"/curation/{ingestion.id}",
            data={
                "published_date": _fecha_pub(ingestion),
                "row_count": "1",
                "product_id_0": str(producto.id),
                "price_0": "450",
                "packaging_format_0": "otro",
                "packaging_detail_0": envase,
            },
            follow_redirects=False,
        )

        assert response.status_code == 303

        session.expire_all()
        fila = session.execute(select(PriceHistory)).scalars().one()
        assert len(fila.packaging or "") <= PACKAGING_MAX_LENGTH
        assert "55550101" not in (fila.packaging or "")


class TestConversionAlCurar:
    """Resolución de tasa en `submit_curation` (`spec.md` §6.3 E, design D4).

    `get_rate_fetcher` está sobreescrito por el fixture `client` con un
    `_FakeRateFetcher` sin tasas por default; cada test que necesita una
    tasa real instala su propio override sobre el mismo `app`.
    """

    def test_fetch_exitoso_persiste_tasa_y_price_cup(
        self, client: TestClient, session: Session
    ) -> None:
        producto = _crear_producto(session)
        ingestion = _crear_raw_ingestion(session, hash_="hash-conversion-usd")
        fetcher = _FakeRateFetcher({("USD", date(2026, 4, 20)): Decimal("671.56")})
        app.dependency_overrides[get_rate_fetcher] = lambda: fetcher

        response = client.post(
            f"/curation/{ingestion.id}",
            data={
                "row_count": "1",
                "product_id_0": str(producto.id),
                "price_0": "100.00",
                "currency_0": "USD",
                "quantity_0": "",
                "unit_0": "",
                "province_id_0": "",
                "published_date": "2026-04-20",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/curation"

        session.expire_all()
        precio = session.execute(select(PriceHistory)).scalars().one()
        # La fecha persistida es la SOLICITADA (el ancla): el fake fetcher
        # sólo puede devolver un `Decimal` (mismo contrato que `parse_rate`,
        # D3), así que no hay canal por el que una fecha ajena pueda colarse.
        assert precio.exchange_rate == Decimal("671.56")
        assert precio.exchange_rate_date == date(2026, 4, 20)
        assert precio.exchange_rate_source == "elTOQUE"
        assert precio.price_cup == Decimal("67156.00")
        assert fetcher.calls == [("USD", date(2026, 4, 20))]

    def test_sin_tasa_resolvible_guarda_sin_convertir_y_avisa(
        self, client: TestClient, session: Session
    ) -> None:
        """elTOQUE caída y sin cache: la fila se guarda igual, las cuatro
        columnas quedan NULL y el redirect avisa `unconverted=1` (design D7)."""
        producto = _crear_producto(session)
        ingestion = _crear_raw_ingestion(session, hash_="hash-conversion-sin-tasa")

        response = client.post(
            f"/curation/{ingestion.id}",
            data={
                "published_date": _fecha_pub(ingestion),
                "row_count": "1",
                "product_id_0": str(producto.id),
                "price_0": "100.00",
                "currency_0": "USD",
                "quantity_0": "",
                "unit_0": "",
                "province_id_0": "",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/curation?unconverted=1"

        session.expire_all()
        precio = session.execute(select(PriceHistory)).scalars().one()
        assert precio.price_cup is None
        assert precio.exchange_rate is None
        assert precio.exchange_rate_date is None
        assert precio.exchange_rate_source is None

    def test_cup_nunca_se_manda_a_resolver(
        self, client: TestClient, session: Session
    ) -> None:
        """No basta con que `price_cup` termine en `None` (ya lo cubre el
        test de siempre en `TestCurationSubmit`): acá se prueba que una fila
        CUP ni siquiera llega a invocar el fetcher."""
        producto = _crear_producto(session)
        ingestion = _crear_raw_ingestion(session, hash_="hash-conversion-cup")
        fetcher = _FakeRateFetcher()
        app.dependency_overrides[get_rate_fetcher] = lambda: fetcher

        response = client.post(
            f"/curation/{ingestion.id}",
            data={
                "published_date": _fecha_pub(ingestion),
                "row_count": "1",
                "product_id_0": str(producto.id),
                "price_0": "450",
                "currency_0": "CUP",
                "quantity_0": "",
                "unit_0": "",
                "province_id_0": "",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/curation"
        assert fetcher.calls == []


class TestNotificacionSinConvertir:
    def test_notice_se_renderiza_con_query_param_en_la_cola(
        self, client: TestClient
    ) -> None:
        response = client.get("/curation?unconverted=2")

        assert "Guardado sin convertir" in response.text

    def test_sin_query_param_no_hay_notice_en_la_cola(self, client: TestClient) -> None:
        response = client.get("/curation")

        assert "Guardado sin convertir" not in response.text

    def test_notice_se_renderiza_con_query_param_en_el_historial(
        self, client: TestClient
    ) -> None:
        response = client.get("/curation/history?unconverted=1")

        assert "Guardado sin convertir" in response.text

    def test_sin_query_param_no_hay_notice_en_el_historial(
        self, client: TestClient
    ) -> None:
        response = client.get("/curation/history")

        assert "Guardado sin convertir" not in response.text


class TestConversionAlEditar:
    """D6: editar una fila no debe re-resolver la tasa salvo que cambie
    moneda o precio — la tasa guardada es la última palabra de la fecha en
    la que se publicó ese anuncio, no de cuándo se corrigió."""

    def test_edit_no_relacionado_preserva_la_tasa(
        self, client: TestClient, session: Session
    ) -> None:
        pollo = _crear_producto(session, name="Pollo")
        provincia = _crear_provincia(session)
        curado = _crear_raw_ingestion(session, hash_="hash-edit-preserva-tasa")
        curado.processing_status = ProcessingStatus.CURATED
        session.commit()
        precio = _crear_precio_curado(
            session, ingestion=curado, product=pollo, price="10.00", currency="USD"
        )
        precio.exchange_rate = Decimal("650.00")
        precio.exchange_rate_date = date(2026, 4, 1)
        precio.exchange_rate_source = "elTOQUE"
        precio.price_cup = Decimal("6500.00")
        session.commit()

        fetcher = _FakeRateFetcher({("USD", date(2026, 8, 13)): Decimal("999.99")})
        app.dependency_overrides[get_rate_fetcher] = lambda: fetcher

        client.post(
            f"/curation/{curado.id}/edit",
            data=_form_edicion(
                curado,
                {
                    "product_id": str(pollo.id),
                    # Mismo precio, mismo string round-trip que ya trae la
                    # fila (`"10.00"` -> `Decimal("10.00")` == `Decimal("10.00")`):
                    # `changed` debe dar `False`, no un falso positivo por
                    # ceros de más.
                    "price": "10.00",
                    "currency": "USD",
                    "province_id": str(provincia.id),
                    "price_id": str(precio.id),
                }
            ),
        )

        session.refresh(precio)
        assert fetcher.calls == []
        assert precio.exchange_rate == Decimal("650.00")
        assert precio.exchange_rate_date == date(2026, 4, 1)
        assert precio.exchange_rate_source == "elTOQUE"
        assert precio.price_cup == Decimal("6500.00")
        # La edición sí se aplicó — no es que el submit entero se ignoró.
        assert precio.province_id == provincia.id

    def test_edit_cambia_precio_dispara_resolucion(
        self, client: TestClient, session: Session
    ) -> None:
        pollo = _crear_producto(session, name="Pollo")
        curado = _crear_raw_ingestion(
            session,
            hash_="hash-edit-price-dispara",
            captured_at=datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
        )
        curado.processing_status = ProcessingStatus.CURATED
        session.commit()
        precio = _crear_precio_curado(
            session, ingestion=curado, product=pollo, price="10.00", currency="USD"
        )
        precio.exchange_rate = Decimal("650.00")
        precio.exchange_rate_date = date(2026, 4, 1)
        precio.exchange_rate_source = "elTOQUE"
        precio.price_cup = Decimal("6500.00")
        session.commit()

        ancla = date(2026, 8, 13)
        fetcher = _FakeRateFetcher({("USD", ancla): Decimal("999.99")})
        app.dependency_overrides[get_rate_fetcher] = lambda: fetcher

        client.post(
            f"/curation/{curado.id}/edit",
            data=_form_edicion(
                curado,
                {
                    "product_id": str(pollo.id),
                    "price": "12.00",
                    "currency": "USD",
                    "price_id": str(precio.id),
                    # Explícita, no la del helper: el ancla que se verifica
                    # abajo es exactamente esta fecha (`published_date` manda
                    # sobre `captured_at`).
                    "published_date": ancla.isoformat(),
                }
            ),
        )

        session.refresh(precio)
        assert fetcher.calls == [("USD", ancla)]
        assert precio.exchange_rate == Decimal("999.99")
        assert precio.exchange_rate_date == ancla
        assert precio.exchange_rate_source == "elTOQUE"
        assert precio.price_cup == Decimal("11999.88")


class TestVocabularioDeEnvase:
    """`packaging` deja de ser texto libre y pasa a vocabulario cerrado +
    "Otro" (2026-08-12): un saco no tiene magnitud fija (25 kg, 50 kg, 110 lb
    según el anuncio) y un paquete tampoco (1 kg de arroz, 2 lb de azúcar),
    así que el envase no puede ser `unit` — pero sí puede ser una etiqueta
    cerrada que no interfiere con `unit`+`quantity`, que siguen siendo la
    fuente del cálculo de precio por unidad.
    """

    def test_saco_guarda_la_etiqueta_capitalizada(
        self, client: TestClient, session: Session
    ) -> None:
        producto = _crear_producto(session)
        ingestion = _crear_raw_ingestion(session, hash_="hash-envase-saco")

        client.post(
            f"/curation/{ingestion.id}",
            data={
                "published_date": _fecha_pub(ingestion),
                "row_count": "1",
                "product_id_0": str(producto.id),
                "price_0": "450",
                "packaging_format_0": "saco",
            },
            follow_redirects=False,
        )

        fila = session.execute(select(PriceHistory)).scalars().one()
        assert fila.packaging == "Saco"

    def test_a_granel_guarda_la_etiqueta_capitalizada(
        self, client: TestClient, session: Session
    ) -> None:
        producto = _crear_producto(session)
        ingestion = _crear_raw_ingestion(session, hash_="hash-envase-granel")

        client.post(
            f"/curation/{ingestion.id}",
            data={
                "published_date": _fecha_pub(ingestion),
                "row_count": "1",
                "product_id_0": str(producto.id),
                "price_0": "450",
                "packaging_format_0": "a granel",
            },
            follow_redirects=False,
        )

        fila = session.execute(select(PriceHistory)).scalars().one()
        assert fila.packaging == "A granel"

    def test_otro_sin_detalle_no_guarda_nada(
        self, client: TestClient, session: Session
    ) -> None:
        """"Otro" vacío no fabrica un valor: sin detalle no hay nada que decir."""
        producto = _crear_producto(session)
        ingestion = _crear_raw_ingestion(session, hash_="hash-envase-otro-vacio")

        client.post(
            f"/curation/{ingestion.id}",
            data={
                "published_date": _fecha_pub(ingestion),
                "row_count": "1",
                "product_id_0": str(producto.id),
                "price_0": "450",
                "packaging_format_0": "otro",
                "packaging_detail_0": "",
            },
            follow_redirects=False,
        )

        fila = session.execute(select(PriceHistory)).scalars().one()
        assert fila.packaging is None

    def test_detalle_sin_otro_seleccionado_se_ignora(
        self, client: TestClient, session: Session
    ) -> None:
        """El detalle solo cuenta si el operador eligió explícitamente "Otro" —
        con "Saco" seleccionado, un detalle sobrante no debe colarse."""
        producto = _crear_producto(session)
        ingestion = _crear_raw_ingestion(session, hash_="hash-envase-detalle-ignorado")

        client.post(
            f"/curation/{ingestion.id}",
            data={
                "published_date": _fecha_pub(ingestion),
                "row_count": "1",
                "product_id_0": str(producto.id),
                "price_0": "450",
                "packaging_format_0": "saco",
                "packaging_detail_0": "esto no debería aparecer",
            },
            follow_redirects=False,
        )

        fila = session.execute(select(PriceHistory)).scalars().one()
        assert fila.packaging == "Saco"

    def test_edit_prellena_otro_con_texto_libre_viejo(
        self, client: TestClient, session: Session
    ) -> None:
        """Filas cargadas antes del 2026-08-12 traen texto libre que no calza
        con ninguna etiqueta cerrada — no se pierde, cae en "Otro" con el
        texto original completo como detalle."""
        producto = _crear_producto(session)
        curado = _crear_raw_ingestion(session, hash_="hash-envase-edit-viejo")
        curado.processing_status = ProcessingStatus.CURATED
        session.commit()
        _crear_precio_curado(
            session, ingestion=curado, product=producto, price="31000",
            packaging="Saco de arroz De 110 lb",
        )

        response = client.get(f"/curation/{curado.id}/edit")

        assert '<option value="otro" selected>' in response.text
        assert 'value="Saco de arroz De 110 lb"' in response.text

    def test_edit_prellena_etiqueta_cerrada_sin_detalle(
        self, client: TestClient, session: Session
    ) -> None:
        producto = _crear_producto(session)
        curado = _crear_raw_ingestion(session, hash_="hash-envase-edit-cerrado")
        curado.processing_status = ProcessingStatus.CURATED
        session.commit()
        _crear_precio_curado(
            session, ingestion=curado, product=producto, price="750", packaging="Paquete",
        )

        response = client.get(f"/curation/{curado.id}/edit")

        assert '<option value="paquete" selected>' in response.text


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


# --- GET /curation/history ---------------------------------------------------


class TestCurationHistory:
    def test_sin_curados_no_rompe(self, client: TestClient) -> None:
        response = client.get("/curation/history")

        assert response.status_code == 200
        assert "no hay anuncios curados" in response.text.lower()

    def test_lista_solo_curados_con_su_resumen(
        self, client: TestClient, session: Session
    ) -> None:
        pollo = _crear_producto(session, name="Pollo")
        habana = _crear_provincia(session)

        curado = _crear_raw_ingestion(session, hash_="hash-curado", original_text="pollo 500")
        curado.processing_status = ProcessingStatus.CURATED
        session.commit()
        _crear_precio_curado(
            session, ingestion=curado, product=pollo, province=habana, price="500"
        )

        pendiente = _crear_raw_ingestion(session, hash_="hash-pendiente")

        response = client.get("/curation/history")

        assert response.status_code == 200
        assert "Pollo" in response.text
        assert "500" in response.text
        assert f"/curation/{curado.id}/edit" in response.text
        assert str(pendiente.id) not in response.text

    def test_mas_reciente_primero(self, client: TestClient, session: Session) -> None:
        pollo = _crear_producto(session, name="Pollo")
        viejo = _crear_raw_ingestion(
            session, hash_="hash-viejo", captured_at=datetime(2026, 8, 1, tzinfo=UTC)
        )
        viejo.processing_status = ProcessingStatus.CURATED
        session.commit()
        _crear_precio_curado(session, ingestion=viejo, product=pollo)

        nuevo = _crear_raw_ingestion(
            session, hash_="hash-nuevo", captured_at=datetime(2026, 8, 10, tzinfo=UTC)
        )
        nuevo.processing_status = ProcessingStatus.CURATED
        session.commit()
        _crear_precio_curado(session, ingestion=nuevo, product=pollo)

        response = client.get("/curation/history")

        assert response.text.index(str(nuevo.id)) < response.text.index(str(viejo.id))


class TestCurationHistoryFiltrada:
    """La columna `n` de `/control` enlaza acá con la identidad exacta de la
    celda (2026-08-12): mismos campos que agrupan `compute_cells` — producto,
    provincia, moneda, unidad, formato. `""` en un filtro significa «sin
    responder» (NULL), no «no filtrar»: mismo contrato que el resto del
    sistema (`_SALE_FORMAT_VALUES`, `canonical_unit`).
    """

    def test_filtra_por_producto_provincia_unidad_y_formato(
        self, client: TestClient, session: Session
    ) -> None:
        arroz = _crear_producto(session, name="Arroz")
        habana = _crear_provincia(session)

        celda_objetivo = _crear_raw_ingestion(session, hash_="hash-celda-objetivo")
        celda_objetivo.processing_status = ProcessingStatus.CURATED
        session.commit()
        _crear_precio_curado(
            session, ingestion=celda_objetivo, product=arroz, province=habana,
            price="750", unit="lb", sale_format=SaleFormat.RETAIL.value,
        )

        otra_celda = _crear_raw_ingestion(session, hash_="hash-otra-celda")
        otra_celda.processing_status = ProcessingStatus.CURATED
        session.commit()
        _crear_precio_curado(
            session, ingestion=otra_celda, product=arroz, province=habana,
            price="31000", unit="lb", sale_format=SaleFormat.WHOLESALE.value,
        )

        response = client.get(
            "/curation/history",
            params={
                "product": "Arroz", "province": "La Habana", "currency": "CUP",
                "unit": "lb", "sale_format": "retail",
            },
        )

        assert str(celda_objetivo.id) in response.text
        assert str(otra_celda.id) not in response.text

    def test_filtro_vacio_significa_sin_responder(
        self, client: TestClient, session: Session
    ) -> None:
        arroz = _crear_producto(session, name="Arroz")

        sin_formato = _crear_raw_ingestion(session, hash_="hash-sin-formato")
        sin_formato.processing_status = ProcessingStatus.CURATED
        session.commit()
        _crear_precio_curado(
            session, ingestion=sin_formato, product=arroz, price="700", unit="lb",
        )

        con_formato = _crear_raw_ingestion(session, hash_="hash-con-formato")
        con_formato.processing_status = ProcessingStatus.CURATED
        session.commit()
        _crear_precio_curado(
            session, ingestion=con_formato, product=arroz, price="750", unit="lb",
            sale_format=SaleFormat.RETAIL.value,
        )

        response = client.get(
            "/curation/history",
            params={"product": "Arroz", "unit": "lb", "sale_format": ""},
        )

        assert str(sin_formato.id) in response.text
        assert str(con_formato.id) not in response.text

    def test_sin_query_params_muestra_todo(
        self, client: TestClient, session: Session
    ) -> None:
        pollo = _crear_producto(session, name="Pollo")
        curado = _crear_raw_ingestion(session, hash_="hash-sin-filtro")
        curado.processing_status = ProcessingStatus.CURATED
        session.commit()
        _crear_precio_curado(session, ingestion=curado, product=pollo)

        response = client.get("/curation/history")

        assert str(curado.id) in response.text


# --- POST /curation/{id}/delete -----------------------------------------------


class TestEliminarCurado:
    """Elimina las filas de `prices_history` del anuncio y lo pasa a
    `discarded` (2026-08-12) — el registro crudo nunca se toca: `source_id`
    tiene RESTRICT contra `raw_ingestions`, así que ni este endpoint podría
    borrar el archivo capturado aunque quisiera.
    """

    def test_borra_las_filas_y_pasa_a_discarded(
        self, client: TestClient, session: Session
    ) -> None:
        pollo = _crear_producto(session, name="Pollo")
        curado = _crear_raw_ingestion(session, hash_="hash-a-eliminar")
        curado.processing_status = ProcessingStatus.CURATED
        session.commit()
        precio = _crear_precio_curado(session, ingestion=curado, product=pollo)

        response = client.post(
            f"/curation/{curado.id}/delete", follow_redirects=False
        )

        assert response.status_code == 303
        assert session.get(PriceHistory, precio.id) is None

        session.refresh(curado)
        assert curado.processing_status == ProcessingStatus.DISCARDED

    def test_el_registro_crudo_sobrevive(
        self, client: TestClient, session: Session
    ) -> None:
        pollo = _crear_producto(session, name="Pollo")
        curado = _crear_raw_ingestion(
            session, hash_="hash-evidencia-sobrevive", original_text="pollo 500"
        )
        curado.processing_status = ProcessingStatus.CURATED
        session.commit()
        _crear_precio_curado(session, ingestion=curado, product=pollo)

        client.post(f"/curation/{curado.id}/delete", follow_redirects=False)

        sobreviviente = session.get(RawIngestion, curado.id)
        assert sobreviviente is not None
        assert sobreviviente.original_text == "pollo 500"

    def test_pendiente_no_se_puede_eliminar(
        self, client: TestClient, session: Session
    ) -> None:
        pendiente = _crear_raw_ingestion(session, hash_="hash-pend-delete")

        response = client.post(f"/curation/{pendiente.id}/delete")

        assert response.status_code == 409

    def test_id_inexistente_devuelve_404(self, client: TestClient) -> None:
        response = client.post(f"/curation/{uuid.uuid4()}/delete")

        assert response.status_code == 404


# --- GET /curation/{id}/edit --------------------------------------------------


class TestEditarCuradoGet:
    def test_precarga_los_valores_ya_curados(
        self, client: TestClient, session: Session
    ) -> None:
        pollo = _crear_producto(session, name="Pollo")
        habana = _crear_provincia(session)
        curado = _crear_raw_ingestion(session, hash_="hash-edit-get")
        curado.processing_status = ProcessingStatus.CURATED
        session.commit()
        precio = _crear_precio_curado(
            session,
            ingestion=curado,
            product=pollo,
            province=habana,
            price="500",
            unit="lb",
            sale_format=SaleFormat.RETAIL.value,
            packaging="jaba",
        )

        response = client.get(f"/curation/{curado.id}/edit")

        assert response.status_code == 200
        assert f'value="{precio.id}"' in response.text
        assert f'value="{pollo.id}" selected' in response.text
        assert 'value="500"' in response.text
        assert f'value="{habana.id}" selected' in response.text
        assert '<option value="lb" selected>' in response.text
        assert '<option value="retail" selected>' in response.text
        assert 'value="jaba"' in response.text

    def test_pendiente_no_se_puede_editar(
        self, client: TestClient, session: Session
    ) -> None:
        pendiente = _crear_raw_ingestion(session, hash_="hash-pend-edit")

        response = client.get(f"/curation/{pendiente.id}/edit")

        assert response.status_code == 409

    def test_id_inexistente_devuelve_404(self, client: TestClient) -> None:
        response = client.get(f"/curation/{uuid.uuid4()}/edit")

        assert response.status_code == 404

    def test_no_ofrece_eliminar_ni_badge_de_detectado(
        self, client: TestClient, session: Session
    ) -> None:
        pollo = _crear_producto(session, name="Pollo")
        curado = _crear_raw_ingestion(session, hash_="hash-edit-nofrills")
        curado.processing_status = ProcessingStatus.CURATED
        session.commit()
        _crear_precio_curado(session, ingestion=curado, product=pollo)

        response = client.get(f"/curation/{curado.id}/edit")

        assert "removeProductCard" not in response.text
        assert "Detectado" not in response.text


# --- POST /curation/{id}/edit -------------------------------------------------


def _form_edicion(
    ingestion: RawIngestion, row: dict[str, str], *, row_count: int = 1
) -> dict[str, str]:
    """Formulario de corrección completo. Toma la ingesta porque la fecha de
    publicación es obligatoria también al editar y se acota contra su
    `captured_at` (ver `_fecha_pub`)."""
    return {
        "source_reference": "",
        "published_date": row.get("published_date", _fecha_pub(ingestion)),
        "row_count": str(row_count),
        "product_id_0": row.get("product_id", ""),
        "new_product_name_0": row.get("new_product_name", ""),
        "product_category_0": "",
        "product_unit_0": "",
        "price_0": row.get("price", ""),
        "currency_0": row.get("currency", Currency.CUP.value),
        "quantity_0": row.get("quantity", ""),
        "unit_0": row.get("unit", ""),
        "sale_format_0": row.get("sale_format", ""),
        "packaging_format_0": row.get("packaging_format", ""),
        "packaging_detail_0": row.get("packaging_detail", ""),
        "min_purchase_0": row.get("min_purchase", ""),
        "province_id_0": row.get("province_id", ""),
        "price_id_0": row.get("price_id", ""),
    }


class TestEditarCuradoPost:
    def test_corrige_precio_en_el_lugar_sin_crear_fila(
        self, client: TestClient, session: Session
    ) -> None:
        pollo = _crear_producto(session, name="Pollo")
        curado = _crear_raw_ingestion(session, hash_="hash-corrige-precio")
        curado.processing_status = ProcessingStatus.CURATED
        session.commit()
        precio = _crear_precio_curado(session, ingestion=curado, product=pollo, price="2.00")

        response = client.post(
            f"/curation/{curado.id}/edit",
            data=_form_edicion(
                curado,
                {
                    "product_id": str(pollo.id),
                    "price": "420.00",
                    "price_id": str(precio.id),
                }
            ),
            follow_redirects=False,
        )

        assert response.status_code == 303

        total = session.execute(select(PriceHistory)).scalars().all()
        assert len(total) == 1
        actualizado = total[0]
        assert actualizado.id == precio.id
        assert actualizado.price == Decimal("420.00")

    def test_actualiza_unit_sale_format_y_packaging(
        self, client: TestClient, session: Session
    ) -> None:
        pollo = _crear_producto(session, name="Pollo")
        curado = _crear_raw_ingestion(session, hash_="hash-corrige-campos")
        curado.processing_status = ProcessingStatus.CURATED
        session.commit()
        precio = _crear_precio_curado(session, ingestion=curado, product=pollo, price="500")

        client.post(
            f"/curation/{curado.id}/edit",
            data=_form_edicion(
                curado,
                {
                    "product_id": str(pollo.id),
                    "price": "500",
                    "unit": "lb",
                    "sale_format": "wholesale",
                    "packaging_format": "otro",
                    "packaging_detail": "saco de 25 kg",
                    "price_id": str(precio.id),
                }
            ),
        )

        session.refresh(precio)
        assert precio.unit == "lb"
        assert precio.sale_format == SaleFormat.WHOLESALE.value
        assert precio.packaging == "saco de 25 kg"

    def test_compra_minima_round_trip_al_corregir(
        self, client: TestClient, session: Session
    ) -> None:
        """La fila ya curada muestra su mínimo al reabrirse, y la corrección
        lo actualiza. Sin el prellenado, corregir cualquier otro campo
        borraría el mínimo en silencio."""
        arroz = _crear_producto(session, name="Arroz")
        curado = _crear_raw_ingestion(session, hash_="hash-corrige-minimo")
        curado.processing_status = ProcessingStatus.CURATED
        session.commit()
        precio = _crear_precio_curado(
            session,
            ingestion=curado,
            product=arroz,
            price="730",
            # Con la escala explícita: la fixture usa `expire_on_commit=False`,
            # así que el valor que renderiza la plantilla es el objeto en
            # memoria y no el round-trip de la columna `Numeric(12, 3)`.
            # Sembrarlo ya escalado hace que las dos formas coincidan.
            min_purchase_quantity=Decimal("30.000"),
        )

        formulario = client.get(f"/curation/{curado.id}/edit")
        assert 'id="min_purchase_0" value="30.000"' in formulario.text

        client.post(
            f"/curation/{curado.id}/edit",
            data=_form_edicion(
                curado,
                {
                    "product_id": str(arroz.id),
                    "price": "730",
                    "min_purchase": "10",
                    "price_id": str(precio.id),
                },
            ),
        )

        session.refresh(precio)
        assert precio.min_purchase_quantity == Decimal("10")

    def test_price_id_de_otro_anuncio_es_error_explicito(
        self, client: TestClient, session: Session
    ) -> None:
        pollo = _crear_producto(session, name="Pollo")
        curado_a = _crear_raw_ingestion(session, hash_="hash-a")
        curado_a.processing_status = ProcessingStatus.CURATED
        curado_b = _crear_raw_ingestion(session, hash_="hash-b")
        curado_b.processing_status = ProcessingStatus.CURATED
        session.commit()
        precio_de_b = _crear_precio_curado(session, ingestion=curado_b, product=pollo)

        response = client.post(
            f"/curation/{curado_a.id}/edit",
            data=_form_edicion(
                curado_a,
                {
                    "product_id": str(pollo.id),
                    "price": "999",
                    "price_id": str(precio_de_b.id),
                }
            ),
        )

        assert response.status_code == 422
        assert "no corresponde" in response.text.lower()

        session.refresh(precio_de_b)
        assert precio_de_b.price != Decimal("999")

    def test_precio_invalido_no_guarda_nada(
        self, client: TestClient, session: Session
    ) -> None:
        pollo = _crear_producto(session, name="Pollo")
        curado = _crear_raw_ingestion(session, hash_="hash-invalido")
        curado.processing_status = ProcessingStatus.CURATED
        session.commit()
        precio = _crear_precio_curado(session, ingestion=curado, product=pollo, price="500")

        response = client.post(
            f"/curation/{curado.id}/edit",
            data=_form_edicion(
                curado,
                {
                    "product_id": str(pollo.id),
                    "price": "no-es-un-numero",
                    "price_id": str(precio.id),
                }
            ),
        )

        assert response.status_code == 422
        session.refresh(precio)
        assert precio.price == Decimal("500")

    def test_no_agrega_ni_borra_filas(self, client: TestClient, session: Session) -> None:
        pollo = _crear_producto(session, name="Pollo")
        curado = _crear_raw_ingestion(session, hash_="hash-cardinalidad")
        curado.processing_status = ProcessingStatus.CURATED
        session.commit()
        precio_1 = _crear_precio_curado(session, ingestion=curado, product=pollo, price="100")
        precio_2 = _crear_precio_curado(session, ingestion=curado, product=pollo, price="200")

        response = client.post(
            f"/curation/{curado.id}/edit",
            data={
                "source_reference": "",
                "published_date": _fecha_pub(curado),
                "row_count": "2",
                "product_id_0": str(pollo.id),
                "new_product_name_0": "",
                "product_category_0": "",
                "product_unit_0": "",
                "price_0": "150",
                "currency_0": Currency.CUP.value,
                "quantity_0": "",
                "unit_0": "",
                "sale_format_0": "",
                "packaging_format_0": "",
                "province_id_0": "",
                "price_id_0": str(precio_1.id),
                "product_id_1": str(pollo.id),
                "new_product_name_1": "",
                "product_category_1": "",
                "product_unit_1": "",
                "price_1": "250",
                "currency_1": Currency.CUP.value,
                "quantity_1": "",
                "unit_1": "",
                "sale_format_1": "",
                "packaging_format_1": "",
                "province_id_1": "",
                "price_id_1": str(precio_2.id),
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        total = session.execute(select(PriceHistory)).scalars().all()
        assert len(total) == 2
        session.refresh(precio_1)
        session.refresh(precio_2)
        assert precio_1.price == Decimal("150")
        assert precio_2.price == Decimal("250")

    def test_pendiente_no_se_puede_corregir(
        self, client: TestClient, session: Session
    ) -> None:
        pendiente = _crear_raw_ingestion(session, hash_="hash-pend-post")

        response = client.post(
            f"/curation/{pendiente.id}/edit",
            data=_form_edicion(pendiente, {"price": "1", "price_id": str(uuid.uuid4())}),
        )

        assert response.status_code == 409


class TestOrdenDeLaColaPorFoco:
    """docs/decisions.md, 2026-08-18 — la cola deja de ser FIFO puro: primero
    lo que menciona un producto del foco, después lo que todavía no se puede
    saber (imagen sin OCR), y al final lo que no aporta a ninguna celda. Nada
    se descarta ni se filtra: solo cambia el orden en que el operador lo ve.
    """

    def test_anuncio_del_foco_gana_a_uno_mas_viejo_sin_foco(
        self, client: TestClient, session: Session
    ) -> None:
        _crear_producto(session, name="Arroz", aliases=["arroz blanco"])
        _crear_raw_ingestion(
            session,
            hash_="hash-orden-sin-foco",
            original_text="cemento a 500 el saco",
            captured_at=datetime(2026, 8, 10, tzinfo=UTC),
        )
        del_foco = _crear_raw_ingestion(
            session,
            hash_="hash-orden-con-foco",
            original_text="arroz a 30000",
            captured_at=datetime(2026, 8, 17, tzinfo=UTC),
        )

        response = client.get("/curation")

        assert response.status_code == 200
        assert f'action="/curation/{del_foco.id}"' in response.text

    def test_entre_dos_del_foco_sigue_ganando_el_mas_viejo(
        self, client: TestClient, session: Session
    ) -> None:
        """El foco reordena entre grupos, no dentro: la cola sigue siendo
        FIFO para todo lo que aporta lo mismo."""
        _crear_producto(session, name="Arroz", aliases=["arroz blanco"])
        viejo = _crear_raw_ingestion(
            session,
            hash_="hash-orden-foco-viejo",
            original_text="arroz a 30000",
            captured_at=datetime(2026, 8, 10, tzinfo=UTC),
        )
        _crear_raw_ingestion(
            session,
            hash_="hash-orden-foco-nuevo",
            original_text="arroz a 31000",
            captured_at=datetime(2026, 8, 17, tzinfo=UTC),
        )

        response = client.get("/curation")

        assert response.status_code == 200
        assert f'action="/curation/{viejo.id}"' in response.text

    def test_sin_nada_del_foco_muestra_el_mas_viejo_como_siempre(
        self, client: TestClient, session: Session
    ) -> None:
        """Sin regresión: una cola donde nada aporta al foco se comporta
        exactamente como antes de esta decisión."""
        _crear_producto(session, name="Arroz", aliases=["arroz blanco"])
        viejo = _crear_raw_ingestion(
            session,
            hash_="hash-orden-nada-viejo",
            original_text="cemento a 500 el saco",
            captured_at=datetime(2026, 8, 10, tzinfo=UTC),
        )
        _crear_raw_ingestion(
            session,
            hash_="hash-orden-nada-nuevo",
            original_text="cabilla de media a 1200",
            captured_at=datetime(2026, 8, 17, tzinfo=UTC),
        )

        response = client.get("/curation")

        assert response.status_code == 200
        assert f'action="/curation/{viejo.id}"' in response.text

    def test_imagen_sin_ocr_no_queda_detras_de_texto_ajeno_al_foco(
        self, client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Una imagen no tiene `original_text`, así que su relevancia no se
        puede saber sin transcribirla. "Todavía no sé" va antes que "sé que
        no aporta": si no, las imágenes se mueren de hambre al fondo de la
        cola apenas entra volumen de WhatsApp."""
        _crear_producto(session, name="Arroz", aliases=["arroz blanco"])
        _crear_raw_ingestion(
            session,
            hash_="hash-orden-texto-ajeno",
            original_text="cemento a 500 el saco",
            captured_at=datetime(2026, 8, 10, tzinfo=UTC),
        )
        imagen = _crear_raw_ingestion(
            session,
            hash_="hash-orden-imagen",
            content_type=ContentType.IMAGE,
            captured_at=datetime(2026, 8, 17, tzinfo=UTC),
        )
        monkeypatch.setattr(ocr, "transcribe", lambda path, lang="spa": "")

        response = client.get("/curation")

        assert response.status_code == 200
        assert f'action="/curation/{imagen.id}"' in response.text

    def test_texto_del_foco_gana_a_una_imagen_mas_vieja(
        self, client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """La otra mitad de la regla anterior: una relevancia confirmada vale
        más que una desconocida, aunque la desconocida sea más vieja."""
        _crear_producto(session, name="Arroz", aliases=["arroz blanco"])
        _crear_raw_ingestion(
            session,
            hash_="hash-orden-imagen-vieja",
            content_type=ContentType.IMAGE,
            captured_at=datetime(2026, 8, 10, tzinfo=UTC),
        )
        del_foco = _crear_raw_ingestion(
            session,
            hash_="hash-orden-foco-nuevo-vs-imagen",
            original_text="arroz a 30000",
            captured_at=datetime(2026, 8, 17, tzinfo=UTC),
        )
        monkeypatch.setattr(ocr, "transcribe", lambda path, lang="spa": "")

        response = client.get("/curation")

        assert response.status_code == 200
        assert f'action="/curation/{del_foco.id}"' in response.text
