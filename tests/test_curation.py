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
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from saimc.config import get_settings
from saimc.extraction import llm_assist, ocr
from saimc.models import (
    Channel,
    ContentType,
    Currency,
    DiscardReason,
    Platform,
    PriceHistory,
    ProcessingStatus,
    Product,
    ProductFormat,
    ProductVariant,
    Province,
    RawIngestion,
    SaleFormat,
    SourceType,
)
from saimc.web.app import app
from saimc.web.auth import require_operator
from saimc.web.deps import get_db, get_rate_fetcher
from saimc.web.routers import curation
from saimc.web.routers.curation import _EXTRA_BLANK_ROWS, _last_capture_notice

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


def _contador(texto: str, etiqueta: str) -> str | None:
    """Valor del contador `etiqueta` en la cabecera de la vista.

    Los contadores viven en el shell como etiqueta y cifra en dos nodos
    hermanos, así que buscar la cifra suelta no probaría nada: un "1" está
    en cualquier parte de la página. Esto exige que la cifra sea la del
    contador pedido.
    """
    encontrado = re.search(
        rf'<div class="m3-stat-label">{re.escape(etiqueta)}</div>\s*'
        rf'<div class="m3-stat-value[^"]*">\s*(\d+)\s*</div>',
        texto,
    )
    return encontrado.group(1) if encontrado else None


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
    format_id: uuid.UUID | None = None,
    variant_id: uuid.UUID | None = None,
    min_purchase_quantity: Decimal | None = None,
    quantity: Decimal | None = None,
    price_basis: str | None = None,
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
        format_id=format_id,
        variant_id=variant_id,
        min_purchase_quantity=min_purchase_quantity,
        quantity=quantity,
        price_basis=price_basis,
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
        assert _contador(response.text, "Curados") == "1"
        assert _contador(response.text, "Por curar") == "2"
        assert _contador(response.text, "Descartados") == "0"

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

        El anuncio mezcla el corte con una línea de arroz: desde el 2026-08-20
        un texto que SOLO trae el corte ya no llega a la cola, así que hace
        falta un producto del foco para que el anuncio se muestre y la garantía
        de esta prueba —la línea del corte no prellena fila— siga siendo
        observable.
        """
        _crear_producto(session, name="Pollo")
        _crear_producto(session, name="Arroz")
        _crear_raw_ingestion(
            session,
            hash_="hash-corte-sin-match",
            original_text="higado de pollo 40lb a 3000 habana\narroz a 30000 habana",
        )

        response = client.get("/curation")

        assert response.status_code == 200
        # El texto del anuncio sigue visible en su textarea — eso es correcto,
        # el operador tiene que poder leerlo. Lo que no debe existir es una
        # fila prellenada para la línea del corte: queda la del arroz, más las
        # filas en blanco de cortesía con el `<select>` sin elegir.
        # Se cuenta solo el form visible, no el `<template>` de "Agregar fila"
        # (su tarjeta también trae el `<select>` sin elegir, pero es un molde
        # inerte hasta que el JS lo clona).
        form_visible = response.text.split('<template id="price-row-template"')[0]
        assert form_visible.count('value="" selected>— seleccionar —') == _EXTRA_BLANK_ROWS

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
        """2 candidatos + 3 filas en blanco fijas = 5 selects `product_id_*` en
        el form visible (el `<template>` de "Agregar fila" trae uno más, inerte)."""
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
        form_visible, _, template = response.text.partition('<template id="price-row-template"')
        assert form_visible.count('name="product_id_') == 5
        assert 'name="row_count" value="5"' in response.text
        # El molde existe y trae su propia fila, indexada en row_count (5).
        assert template.count('name="product_id_') == 1
        assert 'name="product_id_5"' in template

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
        assert '<option value="l" data-basis-label="litro" data-basis-label-plural="litros" selected>' in response.text

    def test_el_campo_price_basis_trae_el_gancho_para_ocultarse(
        self, client: TestClient, session: Session
    ) -> None:
        """`price_basis_wrap_0` es el `id` que `_price_row_scripts.html` usa
        para ocultar el campo cuando la cantidad vale 1 (unit y total dan el
        mismo número ahí). El toggle en sí es JS, sin cobertura de Python —
        mismo criterio D8 que `data-basis-label` arriba: acá solo se afirma
        que el servidor renderiza el gancho del que ese script depende."""
        _crear_producto(session, name="Pollo")
        _crear_raw_ingestion(session, hash_="hash-price-basis-wrap", original_text="pollo a 500")

        response = client.get("/curation")

        assert response.status_code == 200
        assert 'id="price_basis_wrap_0"' in response.text

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
        self, client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Desde 2026-08-20 el saludo en TEXTO ya no llega a la cola: el filtro
        por foco lo esconde antes. El atajo sigue haciendo falta para la imagen,
        que entra sin que se sepa qué dice hasta transcribirla."""
        _crear_producto(session, name="Pollo")
        ingestion = _crear_raw_ingestion(
            session, hash_="hash-saludo-sin-datos", content_type=ContentType.IMAGE
        )
        monkeypatch.setattr(
            ocr,
            "transcribe",
            lambda path, lang="spa": (
                "¡Buenos tardes, familia! ☀️\nEscribinos a estos números:\n"
                "[teléfono removido]\n[teléfono removido]"
            ),
        )

        response = client.get("/curation")

        assert response.status_code == 200
        assert "No se reconoció producto ni precio en este anuncio" in response.text
        assert f'/curation/{ingestion.id}/discard' in response.text
        assert 'name="reason"' in response.text

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
        assert "No se reconoció producto ni precio en este anuncio" not in response.text

    def test_precio_de_producto_no_piloto_no_muestra_el_atajo(
        self, client: TestClient, session: Session
    ) -> None:
        """Reemplaza la premisa de 2026-08-17. Entonces se decidió que un
        precio real de producto ajeno al foco «vale la pena que el operador lo
        vea»; con volumen de WhatsApp esa fue la fuente principal de ruido —los
        negocios publican cemento, diésel y mayonesa todo el día— y el
        2026-08-20 el operador la invirtió: sin producto del foco, no se
        muestra. El atajo no aparece porque ya no hay nada que descartar.
        """
        _crear_producto(session, name="Pollo")  # ningún producto matchea "cemento"
        _crear_raw_ingestion(
            session, hash_="hash-no-piloto-con-precio", original_text="cemento a 500 el saco"
        )

        response = client.get("/curation")

        assert response.status_code == 200
        assert "No hay elementos en la cola." in response.text
        assert "No se reconoció producto ni precio en este anuncio" not in response.text

    def test_el_atajo_descarta_de_verdad(
        self, client: TestClient, session: Session
    ) -> None:
        _crear_producto(session, name="Pollo")
        ingestion = _crear_raw_ingestion(
            session, hash_="hash-atajo-descarta", original_text="buenos días a todos"
        )

        response = client.post(
            f"/curation/{ingestion.id}/discard",
            data={"reason": "no_data"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        session.expire_all()
        assert session.get(RawIngestion, ingestion.id).processing_status == (
            ProcessingStatus.DISCARDED
        )

    def test_el_formulario_completo_sigue_disponible_pero_colapsado(
        self, client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """El atajo no borra el formulario, lo colapsa (docs/decisions.md,
        2026-08-17, corrigiendo el diseño anterior de este mismo día — el
        operador pidió explícitamente que "Datos del anuncio" no compita
        visualmente con el botón de descarte): sigue alcanzable con un
        click si el operador mira la evidencia y quiere cargar algo a
        mano, pero no aparece expandido por default."""
        _crear_producto(session, name="Pollo")
        _crear_raw_ingestion(
            session, hash_="hash-atajo-no-esconde-form", content_type=ContentType.IMAGE
        )
        monkeypatch.setattr(ocr, "transcribe", lambda path, lang="spa": "buenos días a todos")

        response = client.get("/curation")

        assert response.status_code == 200
        assert 'id="curation-form"' in response.text
        etiqueta = re.search(
            r'<details([^>]*)>\s*<summary class="m3-summary[^"]*"[^>]*>'
            r'[\s\S]{0,300}?<h2[^>]*>Datos del anuncio',
            response.text,
        )
        assert etiqueta is not None
        # group(1) son los atributos del <details>, no el bloque entero: así
        # un "open" en el texto de adentro no puede dar un falso positivo.
        assert "open" not in etiqueta.group(1)

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
            r'<details([^>]*)>\s*<summary class="m3-summary[^"]*"[^>]*>'
            r'[\s\S]{0,300}?<h2[^>]*>Datos del anuncio',
            response.text,
        )
        assert etiqueta is not None
        assert "open" in etiqueta.group(1)


class TestSugerenciaAsistidaDeDescarte:
    """docs/decisions.md, 2026-08-22 — el operador reportó que mucho ruido
    sin valor económico llega a la cola y le afecta el tiempo de curaduría.
    La señal correcta no es fuzzy-match de texto (0 grupos de texto
    casi-idéntico medidos contra 159 descartes reales de producción): son
    las tres reglas estructurales de `probable_discard_reasons`. Es
    sugerencia, nunca auto-descarte — el operador sigue confirmando o
    corrigiendo cada elemento (spec.md §6.1)."""

    def test_agotado_muestra_la_sugerencia(self, client: TestClient, session: Session) -> None:
        _crear_producto(session, name="Pollo")
        _crear_provincia(session)
        ingestion = _crear_raw_ingestion(
            session, hash_="hash-agotado", original_text="pollo a 500 habana AGOTADO"
        )

        response = client.get("/curation")

        assert response.status_code == 200
        assert "Probable descarte" in response.text
        assert "parece indicar agotado" in response.text
        assert f'/curation/{ingestion.id}/discard' in response.text

    def test_venta_mayorista_muestra_la_sugerencia(
        self, client: TestClient, session: Session
    ) -> None:
        _crear_producto(session, name="Pollo")
        _crear_provincia(session)
        _crear_raw_ingestion(
            session,
            hash_="hash-mayorista",
            original_text="pollo a 500 habana, venta por contenedor",
        )

        response = client.get("/curation")

        assert response.status_code == 200
        assert "parece venta mayorista/por volumen" in response.text

    def test_anuncio_normal_no_muestra_la_sugerencia(
        self, client: TestClient, session: Session
    ) -> None:
        _crear_producto(session, name="Pollo")
        _crear_provincia(session)
        _crear_raw_ingestion(session, hash_="hash-normal", original_text="pollo a 500 habana")

        response = client.get("/curation")

        assert response.status_code == 200
        assert "Probable descarte" not in response.text

    def test_imagen_sin_producto_reconocido_muestra_la_sugerencia(
        self, client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """El filtro de foco (`docs/decisions.md`, 2026-08-20) ya esconde el
        TEXTO sin producto del foco antes de llegar a la cola — ver
        `TestFueraDeFocoNoLlegaALaCola`. La imagen no: entra igual y se
        transcribe recién al mostrarse, así que acá la señal sí aporta."""
        _crear_producto(session, name="Pollo")
        _crear_provincia(session)
        _crear_raw_ingestion(session, hash_="hash-imagen-ajena", content_type=ContentType.IMAGE)
        monkeypatch.setattr(ocr, "transcribe", lambda path, lang="spa": "cemento a 500 el saco")

        response = client.get("/curation")

        assert response.status_code == 200
        assert "no menciona ningún producto del foco" in response.text

    def test_no_candidatos_no_convive_con_la_sugerencia(
        self, client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cuando ya se ofrece el atajo "sin candidatos" (señal más fuerte:
        ni producto ni precio en ningún renglón), un segundo banner encima
        diciendo lo mismo con otras palabras es ruido, no ayuda."""
        _crear_producto(session, name="Pollo")
        _crear_raw_ingestion(session, hash_="hash-doble-banner", content_type=ContentType.IMAGE)
        monkeypatch.setattr(ocr, "transcribe", lambda path, lang="spa": "buenos días a todos")

        response = client.get("/curation")

        assert response.status_code == 200
        assert "No se reconoció producto ni precio en este anuncio" in response.text
        assert "Probable descarte" not in response.text

    def test_tasa_historica_del_canal_se_muestra(
        self, client: TestClient, session: Session
    ) -> None:
        _crear_producto(session, name="Pollo")
        _crear_provincia(session)
        canal = Channel(
            name="Ventas Mayoristas", platform=Platform.WHATSAPP_CHANNEL, is_active=True
        )
        session.add(canal)
        session.commit()
        descartado = _crear_raw_ingestion(
            session, hash_="hash-canal-descartado-1", channel_id=canal.id
        )
        descartado.processing_status = ProcessingStatus.DISCARDED
        session.commit()
        _crear_raw_ingestion(
            session,
            hash_="hash-canal-actual",
            original_text="pollo a 500 habana",
            channel_id=canal.id,
        )

        response = client.get("/curation")

        assert response.status_code == 200
        assert "Este canal descartó 1 de 1 anuncios anteriores" in response.text

    def test_sin_canal_no_muestra_tasa_historica(
        self, client: TestClient, session: Session
    ) -> None:
        _crear_producto(session, name="Pollo")
        _crear_provincia(session)
        _crear_raw_ingestion(session, hash_="hash-sin-canal", original_text="pollo a 500 habana")

        response = client.get("/curation")

        assert response.status_code == 200
        assert "anuncios anteriores" not in response.text


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


class TestCamposDeJuicioDelOperadorQuedanVacios:
    """docs/decisions.md, 2026-08-21 — escala de venta, envase y compra
    mínima ya no se prellenan desde el historial del canal. Lo que el motor
    no puede leer del texto del anuncio llega en blanco y lo escribe el
    operador, que es quien lo sabe."""

    def test_anuncio_escalonado_prellena_el_minimo_de_la_fila_mayorista(
        self, client: TestClient, session: Session
    ) -> None:
        """El mínimo sí se prellena cuando el propio anuncio declara el
        tamaño del lote: eso lo deriva el motor del texto, no lo hereda de
        una confirmación anterior. Anuncio real del operador (2026-08-20).
        """
        # Las alitas son su propio producto, no Pollo (decisión del operador,
        # 2026-08-20). Mientras no estén en el catálogo el anuncio queda
        # fuera de foco y la cola lo esconde, que es el comportamiento
        # correcto: no se está midiendo alitas.
        _crear_producto(session, name="Alitas de pollo")
        _crear_raw_ingestion(
            session,
            hash_="hash-alitas-escalonado",
            original_text=(
                "ALITAS DE POLLO\n"
                "CAJA DE 40 lb\n"
                "La caja 28,5 usd o 25650 cup.\n"
                "Más de 10 cajas 28\n"
                "Más de 100 cajas 27,5.\n"
            ),
            source_type=SourceType.WHATSAPP_CHANNEL,
            source_reference="Canal mayorista",
        )

        response = client.get("/curation")

        assert response.status_code == 200
        # Fila base: el precio de una caja, sin mínimo declarado.
        assert 'id="price_0" value="28.5"' in response.text
        assert 'id="min_purchase_0" value=""' in response.text
        # Fila mayorista: mejor precio del anuncio y su mínimo en lb, no en
        # cajas — 100 cajas de 40 lb.
        assert 'id="price_1" value="27.5"' in response.text
        assert 'id="min_purchase_1" value="4000"' in response.text
        # El envase y minorista/mayorista siguen siendo juicio del operador:
        # el `<select>` de envase existe y arranca sin elegir.
        assert 'id="packaging_1"' in response.text
        assert '<option value="" selected>— sin envase —</option>' in response.text

    def test_antecedente_confirmado_del_mismo_canal_y_producto_no_prellena_nada(
        self, client: TestClient, session: Session
    ) -> None:
        """El guardián de la decisión 2026-08-21, contra el escenario más
        favorable que existía para el prellenado: mismo canal registrado,
        mismo producto, antecedente confirmado por el propio operador. Ni
        aun así se hereda — la sugerencia acertaba lo suficiente como para
        confirmarse sin mirar, y erraba lo suficiente como para ensuciar el
        dato."""
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
            hash_="hash-historial-vieja",
            source_type=SourceType.WHATSAPP_CHANNEL,
            source_reference="Groupej Morales Surl",
            channel_id=canal.id,
            captured_at=datetime(2026, 8, 10, tzinfo=UTC),
        )
        _crear_precio_curado(
            session,
            ingestion=vieja,
            product=producto,
            sale_format="retail",
            min_purchase_quantity=Decimal("1"),
        )
        # `_pending_in_focus` elige entre los `PENDING` — sin esto,
        # `/curation` mostraría el antecedente, no el anuncio nuevo.
        vieja.processing_status = ProcessingStatus.CURATED
        session.commit()
        _crear_raw_ingestion(
            session,
            hash_="hash-historial-nueva",
            original_text="arroz a 30000",
            source_type=SourceType.WHATSAPP_CHANNEL,
            source_reference="Groupej Morales Surl",
            channel_id=canal.id,
            captured_at=datetime(2026, 8, 17, tzinfo=UTC),
        )

        response = client.get("/curation")

        assert response.status_code == 200
        # El producto sí se propone: eso sale del texto del anuncio.
        assert f'value="{producto.id}" selected' in response.text
        # Los tres campos de juicio, en blanco.
        assert '<option value="retail" selected>' not in response.text
        assert '<option value="saco" selected>' not in response.text
        assert 'id="min_purchase_0" value=""' in response.text


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
                "price_basis_0": "total",
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

    def test_elegir_un_formato_declarado_persiste_format_id_cantidad_y_unidad(
        self, client: TestClient, session: Session
    ) -> None:
        """spec: el formulario elige un formato declarado; `quantity`/`unit` de
        la fila se escriben desde ese formato (curation-observation-capture)."""
        producto = _crear_producto(session)
        formato = ProductFormat(
            product_id=producto.id,
            packaging="saco",
            quantity=Decimal("25"),
            unit="kg",
        )
        session.add(formato)
        session.commit()
        formato_id = formato.id
        ingestion = _crear_raw_ingestion(session, hash_="hash-post-formato-declarado")

        response = client.post(
            f"/curation/{ingestion.id}",
            data={
                "published_date": _fecha_pub(ingestion),
                "row_count": "1",
                "product_id_0": str(producto.id),
                "price_0": "17500",
                "quantity_0": "25",
                "unit_0": "kg",
                "sale_format_0": "wholesale",
                "packaging_0": "saco",
                "price_basis_0": "total",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303

        session.expire_all()
        fila = session.execute(select(PriceHistory)).scalars().one()
        assert fila.format_id == formato_id
        assert fila.quantity == Decimal("25")
        assert fila.unit == "kg"
        assert fila.sale_format == "wholesale"
        # No se crea un segundo formato: se reusa el declarado.
        assert session.execute(select(func.count()).select_from(ProductFormat)).scalar() == 1

    def test_un_formato_nuevo_se_crea_al_vuelo_desde_el_formulario(
        self, client: TestClient, session: Session
    ) -> None:
        """spec: un formato que no existe se crea desde la curaduría sin salir
        de la cola (curation-observation-capture)."""
        producto = _crear_producto(session, name="Pollo")
        ingestion = _crear_raw_ingestion(session, hash_="hash-post-formato-nuevo")

        response = client.post(
            f"/curation/{ingestion.id}",
            data={
                "published_date": _fecha_pub(ingestion),
                "row_count": "1",
                "product_id_0": str(producto.id),
                "price_0": "12400",
                "quantity_0": "80",
                "unit_0": "lb",
                "packaging_0": "caja",
                "price_basis_0": "total",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303

        session.expire_all()
        formato = session.execute(select(ProductFormat)).scalars().one()
        assert (formato.product_id, formato.packaging, formato.quantity, formato.unit) == (
            producto.id,
            "caja",
            Decimal("80"),
            "lb",
        )
        fila = session.execute(select(PriceHistory)).scalars().one()
        assert fila.format_id == formato.id

    def test_el_mismo_triple_en_dos_anuncios_no_duplica_el_formato(
        self, client: TestClient, session: Session
    ) -> None:
        """spec: crear el mismo formato dos veces desde otra redacción da la
        misma fila (idempotencia de `get_or_create_format`)."""
        producto = _crear_producto(session, name="Pollo")
        uno = _crear_raw_ingestion(session, hash_="hash-post-triple-1")
        dos = _crear_raw_ingestion(session, hash_="hash-post-triple-2")

        for ingestion in (uno, dos):
            respuesta = client.post(
                f"/curation/{ingestion.id}",
                data={
                    "published_date": _fecha_pub(ingestion),
                    "row_count": "1",
                    "product_id_0": str(producto.id),
                    "price_0": "12400",
                    "quantity_0": "40",
                    "unit_0": "lb",
                    "packaging_0": "caja",
                    "price_basis_0": "total",
                },
                follow_redirects=False,
            )
            assert respuesta.status_code == 303

        session.expire_all()
        formatos = list(session.execute(select(ProductFormat)).scalars())
        assert len(formatos) == 1
        filas = list(session.execute(select(PriceHistory)).scalars())
        assert {f.format_id for f in filas} == {formatos[0].id}

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

    def test_envase_en_blanco_guarda_format_id_null_y_crea_la_fila(
        self, client: TestClient, session: Session
    ) -> None:
        """spec: sin formato establecido se guarda `format_id = NULL`, y la
        fila se crea igual — mismo criterio que `sale_format` en blanco."""
        producto = _crear_producto(session)
        ingestion = _crear_raw_ingestion(session, hash_="hash-post-envase-vacio")

        response = client.post(
            f"/curation/{ingestion.id}",
            data={
                "published_date": _fecha_pub(ingestion),
                "row_count": "1",
                "product_id_0": str(producto.id),
                "price_0": "450",
                "quantity_0": "10",
                "unit_0": "lb",
                "packaging_0": "",
                "price_basis_0": "total",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303

        session.expire_all()
        fila = session.execute(select(PriceHistory)).scalars().one()
        assert fila.format_id is None
        assert fila.quantity == Decimal("10")  # la fila se crea con sus otros campos
        assert session.execute(select(func.count()).select_from(ProductFormat)).scalar() == 0


class TestVariantesEnCuraduria:
    """`variant_kind_i`/`variant_brand_i`/`variant_origin_i` — declarar y
    elegir una variante son el mismo acto (design.md D7)."""

    def test_elegir_una_variante_declarada_persiste_variant_id(
        self, client: TestClient, session: Session
    ) -> None:
        producto = _crear_producto(session, name="Aceite")
        variante = ProductVariant(
            product_id=producto.id,
            kind="girasol",
            brand="Vitalín",
            origin="importado",
            kind_key="girasol",
            brand_key="vitalin",
            origin_key="importado",
        )
        session.add(variante)
        session.commit()
        variante_id = variante.id
        ingestion = _crear_raw_ingestion(session, hash_="hash-post-variante-declarada")

        response = client.post(
            f"/curation/{ingestion.id}",
            data={
                "published_date": _fecha_pub(ingestion),
                "row_count": "1",
                "product_id_0": str(producto.id),
                "price_0": "450",
                "variant_kind_0": "girasol",
                "variant_brand_0": "Vitalín",
                "variant_origin_0": "importado",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303

        session.expire_all()
        fila = session.execute(select(PriceHistory)).scalars().one()
        assert fila.variant_id == variante_id
        assert session.execute(select(func.count()).select_from(ProductVariant)).scalar() == 1

    def test_una_variante_nueva_se_crea_al_vuelo_desde_el_formulario(
        self, client: TestClient, session: Session
    ) -> None:
        producto = _crear_producto(session, name="Aceite")
        ingestion = _crear_raw_ingestion(session, hash_="hash-post-variante-nueva")

        response = client.post(
            f"/curation/{ingestion.id}",
            data={
                "published_date": _fecha_pub(ingestion),
                "row_count": "1",
                "product_id_0": str(producto.id),
                "price_0": "450",
                "variant_kind_0": "girasol",
                "variant_brand_0": "Vitalín",
                "variant_origin_0": "importado",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303

        session.expire_all()
        variante = session.execute(select(ProductVariant)).scalars().one()
        assert (variante.kind, variante.brand, variante.origin) == (
            "girasol",
            "Vitalín",
            "importado",
        )
        fila = session.execute(select(PriceHistory)).scalars().one()
        assert fila.variant_id == variante.id

    def test_la_misma_terna_dos_veces_incluso_con_otro_acento_no_duplica(
        self, client: TestClient, session: Session
    ) -> None:
        """spec: declarar la misma variante dos veces, incluso con otra
        ortografía, no duplica (curation-observation-capture)."""
        producto = _crear_producto(session, name="Aceite")
        uno = _crear_raw_ingestion(session, hash_="hash-post-variante-terna-1")
        dos = _crear_raw_ingestion(session, hash_="hash-post-variante-terna-2")

        for ingestion, brand in ((uno, "Vitalín"), (dos, "VITALIN")):
            respuesta = client.post(
                f"/curation/{ingestion.id}",
                data={
                    "published_date": _fecha_pub(ingestion),
                    "row_count": "1",
                    "product_id_0": str(producto.id),
                    "price_0": "450",
                    "variant_kind_0": "girasol",
                    "variant_brand_0": brand,
                    "variant_origin_0": "importado",
                },
                follow_redirects=False,
            )
            assert respuesta.status_code == 303

        session.expire_all()
        variantes = list(session.execute(select(ProductVariant)).scalars())
        assert len(variantes) == 1
        filas = list(session.execute(select(PriceHistory)).scalars())
        assert {f.variant_id for f in filas} == {variantes[0].id}

    def test_los_tres_campos_en_blanco_guarda_variant_id_null_y_crea_la_fila(
        self, client: TestClient, session: Session
    ) -> None:
        producto = _crear_producto(session, name="Aceite")
        ingestion = _crear_raw_ingestion(session, hash_="hash-post-variante-vacia")

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
        assert fila.variant_id is None
        assert session.execute(select(func.count()).select_from(ProductVariant)).scalar() == 0

    def test_edit_preselecciona_la_variante_de_la_fila_curada(
        self, client: TestClient, session: Session
    ) -> None:
        producto = _crear_producto(session, name="Aceite")
        curado = _crear_raw_ingestion(session, hash_="hash-variante-edit")
        curado.processing_status = ProcessingStatus.CURATED
        session.commit()
        variante = ProductVariant(
            product_id=producto.id,
            kind="girasol",
            brand="Vitalín",
            origin="importado",
            kind_key="girasol",
            brand_key="vitalin",
            origin_key="importado",
        )
        session.add(variante)
        session.commit()
        _crear_precio_curado(
            session,
            ingestion=curado,
            product=producto,
            price="750",
            variant_id=variante.id,
        )

        response = client.get(f"/curation/{curado.id}/edit")

        assert response.status_code == 200
        assert 'name="variant_kind_0" id="variant_kind_0" value="girasol"' in response.text
        assert 'name="variant_brand_0" id="variant_brand_0" value="Vitalín"' in response.text
        assert 'name="variant_origin_0" id="variant_origin_0" value="importado"' in response.text

    def test_una_marca_ya_usada_aparece_en_el_datalist_del_producto(
        self, client: TestClient, session: Session
    ) -> None:
        """spec: una marca ya usada se elige, no se retipea
        (curation-observation-capture)."""
        producto = _crear_producto(session, name="Aceite")
        variante = ProductVariant(
            product_id=producto.id,
            kind="girasol",
            brand="Vitalín",
            origin="importado",
            kind_key="girasol",
            brand_key="vitalin",
            origin_key="importado",
        )
        session.add(variante)
        session.commit()
        _crear_raw_ingestion(session, hash_="hash-variante-datalist")

        response = client.get("/curation")

        assert response.status_code == 200
        assert f'<datalist id="variant-brand-{producto.id}">' in response.text
        assert '<option value="Vitalín">' in response.text


class TestFormatoDelCatalogo:
    """El `<select>` de formatos ya declarados en `/catalog`.

    Sin él, el operador rearma el triple a mano en cada anuncio y «0.9 L» donde
    el catálogo dice «900 ml» fabrica un `ProductFormat` gemelo en silencio.
    Es capa de interfaz: rellena `packaging_N`/`quantity_N`/`unit_N` y el POST
    llega idéntico al de hoy.
    """

    def _formato(
        self,
        session: Session,
        producto: Product,
        *,
        packaging: str = "caja",
        quantity: str = "40",
        unit: str = "lb",
    ) -> ProductFormat:
        formato = ProductFormat(
            product_id=producto.id,
            packaging=packaging,
            quantity=Decimal(quantity),
            unit=unit,
        )
        session.add(formato)
        session.commit()
        return formato

    def test_las_opciones_traen_el_triple_entero_y_su_etiqueta(
        self, session: Session
    ) -> None:
        producto = _crear_producto(session, name="Aceite")
        self._formato(session, producto, packaging="botella", quantity="900", unit="ml")

        opciones = curation._format_options(session)

        assert opciones == {
            str(producto.id): [
                {
                    "label": "botella de 900 ml",
                    "packaging": "botella",
                    "quantity": "900",
                    "unit": "ml",
                }
            ]
        }

    def test_las_opciones_se_indexan_por_producto(self, session: Session) -> None:
        aceite = _crear_producto(session, name="Aceite")
        pollo = _crear_producto(session, name="Pollo")
        self._formato(session, aceite, packaging="botella", quantity="900", unit="ml")
        self._formato(session, pollo)

        opciones = curation._format_options(session)

        assert set(opciones) == {str(aceite.id), str(pollo.id)}
        assert opciones[str(pollo.id)][0]["label"] == "caja de 40 lb"

    def test_un_producto_sin_formatos_no_tiene_entrada(self, session: Session) -> None:
        _crear_producto(session, name="Aceite")

        assert curation._format_options(session) == {}

    def test_la_cola_ofrece_el_formato_del_producto_detectado(
        self, client: TestClient, session: Session
    ) -> None:
        producto = _crear_producto(session, name="Pollo")
        self._formato(session, producto)
        _crear_raw_ingestion(
            session,
            hash_="hash-formato-cola",
            original_text="pollo a 500 habana",
        )

        response = client.get("/curation")

        assert response.status_code == 200
        assert 'id="format_pick_0"' in response.text
        assert 'data-packaging="caja"' in response.text
        assert 'data-quantity="40"' in response.text
        assert 'data-unit="lb"' in response.text
        assert ">caja de 40 lb</option>" in response.text

    def test_el_select_no_viaja_en_el_post(
        self, client: TestClient, session: Session
    ) -> None:
        """Sin `name` no hay campo nuevo que el backend tenga que entender:
        `_resolve_format_id` sigue viendo los tres campos de siempre."""
        producto = _crear_producto(session, name="Pollo")
        self._formato(session, producto)
        _crear_raw_ingestion(
            session,
            hash_="hash-formato-sin-name",
            original_text="pollo a 500 habana",
        )

        response = client.get("/curation")

        assert 'name="format_pick' not in response.text

    def test_un_producto_sin_formatos_no_muestra_el_select(
        self, client: TestClient, session: Session
    ) -> None:
        """El caso de arranque: nadie declaró formatos todavía y el operador
        carga envase, cantidad y unidad a mano, como antes de este control."""
        _crear_producto(session, name="Pollo")
        _crear_raw_ingestion(
            session,
            hash_="hash-formato-sin-formatos",
            original_text="pollo a 500 habana",
        )

        response = client.get("/curation")

        assert response.status_code == 200
        # `data-format-pick` a secas no sirve: el string también está en el
        # selector del JS, que se sirve siempre.
        assert 'id="format_pick_0"' not in response.text
        assert 'id="packaging_0"' in response.text

    def test_la_edicion_tambien_ofrece_los_formatos(
        self, client: TestClient, session: Session
    ) -> None:
        producto = _crear_producto(session, name="Pollo")
        self._formato(session, producto)
        curado = _crear_raw_ingestion(session, hash_="hash-formato-edit")
        curado.processing_status = ProcessingStatus.CURATED
        session.commit()
        _crear_precio_curado(session, ingestion=curado, product=producto, price="24800")

        response = client.get(f"/curation/{curado.id}/edit")

        assert response.status_code == 200
        assert 'id="format_pick_0"' in response.text
        assert ">caja de 40 lb</option>" in response.text


class TestAgregarFila:
    """El botón "Agregar fila": el clonado es JS (sin cobertura de Python),
    pero el molde `<template>` y su índice sí se afirman acá, y el submit tiene
    que aceptar una fila en un índice más allá de lo que propuso el extractor."""

    def test_el_get_trae_el_molde_y_el_boton(
        self, client: TestClient, session: Session
    ) -> None:
        _crear_producto(session, name="Pollo")
        _crear_raw_ingestion(
            session,
            hash_="hash-agregar-fila-get",
            original_text="pollo a 500 habana",
        )

        texto = client.get("/curation").text

        assert '<template id="price-row-template"' in texto
        assert 'onclick="addProductCard()"' in texto
        # El molde se indexa en `row_count`, el mismo número que el JS usa como
        # semilla del contador: 1 candidato + 3 blancas = 4.
        assert 'data-template-index="4"' in texto
        assert 'name="row_count" value="4"' in texto

    def test_el_submit_acepta_una_fila_en_un_indice_mas_alto_que_lo_propuesto(
        self, client: TestClient, session: Session
    ) -> None:
        """Simula lo que hace el JS: `row_count` sube y la fila nueva viaja en
        un índice por encima de las que propuso el extractor."""
        pollo = _crear_producto(session, name="Pollo")
        arroz = _crear_producto(session, name="Arroz")
        ingestion = _crear_raw_ingestion(
            session,
            hash_="hash-agregar-fila-submit",
            original_text="pollo a 500 habana",
        )

        response = client.post(
            f"/curation/{ingestion.id}",
            data={
                "published_date": _fecha_pub(ingestion),
                # 1 candidato (índice 0) + 3 blancas (1-3) + 1 agregada (4).
                "row_count": "5",
                "product_id_0": str(pollo.id),
                "price_0": "500",
                "product_id_4": str(arroz.id),
                "price_4": "120",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        session.expire_all()
        filas = session.execute(select(PriceHistory)).scalars().all()
        assert {f.product_id for f in filas} == {pollo.id, arroz.id}

    def test_el_submit_ignora_los_huecos_que_deja_un_borrado(
        self, client: TestClient, session: Session
    ) -> None:
        """Borrar una tarjeta del medio no baja `row_count`: los índices
        intermedios llegan vacíos y caen en la rama de fila vacía."""
        pollo = _crear_producto(session, name="Pollo")
        ingestion = _crear_raw_ingestion(
            session, hash_="hash-agregar-fila-hueco", original_text="pollo a 500 habana"
        )

        response = client.post(
            f"/curation/{ingestion.id}",
            data={
                "published_date": _fecha_pub(ingestion),
                "row_count": "6",
                "product_id_0": str(pollo.id),
                "price_0": "500",
                # índices 1-5 sin enviar: tarjetas borradas o vacías.
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        session.expire_all()
        assert session.execute(select(PriceHistory)).scalars().one().product_id == pollo.id


class TestPriceBasisSubmit:
    """`price_basis` declara si `price` es por unidad o por el lote entero
    (proposal.md — «Arroz guyanés a 340lb»). Obligatorio solo cuando
    `quantity != 1`: con cantidad uno, unitario y total son el mismo número
    (design.md, D3), igual que el mínimo de compra opcional de
    `min_purchase_quantity` (D2 de ese cambio).
    """

    def test_quantity_distinta_de_uno_sin_basis_rechaza_el_submit(
        self, client: TestClient, session: Session
    ) -> None:
        arroz = _crear_producto(session, name="Arroz")
        pollo = _crear_producto(session, name="Pollo")
        ingestion = _crear_raw_ingestion(session, hash_="hash-basis-requerido")

        response = client.post(
            f"/curation/{ingestion.id}",
            data={
                "published_date": _fecha_pub(ingestion),
                "row_count": "2",
                "product_id_0": str(arroz.id),
                "price_0": "340",
                "quantity_0": "340",
                "product_id_1": str(pollo.id),
                "price_1": "450",
            },
            follow_redirects=False,
        )

        assert response.status_code == 422
        session.expire_all()
        assert session.execute(select(PriceHistory)).first() is None
        # La fila completa (Pollo) reaparece con sus valores tipeados: nada
        # de lo que el operador cargó se pierde por el error de la otra fila.
        assert f'value="{pollo.id}" selected' in response.text
        assert 'value="450"' in response.text

    def test_quantity_uno_sin_basis_persiste_con_basis_null(
        self, client: TestClient, session: Session
    ) -> None:
        pollo = _crear_producto(session, name="Pollo")
        ingestion = _crear_raw_ingestion(session, hash_="hash-basis-qty-uno")

        response = client.post(
            f"/curation/{ingestion.id}",
            data={
                "published_date": _fecha_pub(ingestion),
                "row_count": "1",
                "product_id_0": str(pollo.id),
                "price_0": "450",
                "quantity_0": "1",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        session.expire_all()
        fila = session.execute(select(PriceHistory)).scalars().one()
        assert fila.price_basis is None

    def test_quantity_ausente_sin_basis_persiste_con_basis_null(
        self, client: TestClient, session: Session
    ) -> None:
        pollo = _crear_producto(session, name="Pollo")
        ingestion = _crear_raw_ingestion(session, hash_="hash-basis-qty-ausente")

        response = client.post(
            f"/curation/{ingestion.id}",
            data={
                "published_date": _fecha_pub(ingestion),
                "row_count": "1",
                "product_id_0": str(pollo.id),
                "price_0": "450",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        session.expire_all()
        fila = session.execute(select(PriceHistory)).scalars().one()
        assert fila.price_basis is None

    def test_basis_valido_persiste(self, client: TestClient, session: Session) -> None:
        arroz = _crear_producto(session, name="Arroz")
        ingestion = _crear_raw_ingestion(session, hash_="hash-basis-valido")

        response = client.post(
            f"/curation/{ingestion.id}",
            data={
                "published_date": _fecha_pub(ingestion),
                "row_count": "1",
                "product_id_0": str(arroz.id),
                "price_0": "340",
                "quantity_0": "340",
                "price_basis_0": "unit",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        session.expire_all()
        fila = session.execute(select(PriceHistory)).scalars().one()
        assert fila.price_basis == "unit"

    def test_basis_basura_con_quantity_uno_degrada_a_null(
        self, client: TestClient, session: Session
    ) -> None:
        """Mismo contrato que `sale_format`: un valor posteado fuera del
        dominio (incluido un envío en blanco) degrada a `NULL` en vez de
        romper el submit, y nunca llega al CHECK de la base."""
        pollo = _crear_producto(session, name="Pollo")
        ingestion = _crear_raw_ingestion(session, hash_="hash-basis-basura")

        response = client.post(
            f"/curation/{ingestion.id}",
            data={
                "published_date": _fecha_pub(ingestion),
                "row_count": "1",
                "product_id_0": str(pollo.id),
                "price_0": "450",
                "quantity_0": "1",
                "price_basis_0": "lote",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        session.expire_all()
        fila = session.execute(select(PriceHistory)).scalars().one()
        assert fila.price_basis is None


class TestPriceBasisEdit:
    """La spec de captura dice que la regla «rige tanto al curar como al
    corregir»: `submit_edit` usa los mismos dos helpers puros que
    `submit_curation` (`_price_basis_value`, `_price_basis_required`), pero
    hasta este test ningún caso de edición posteaba `quantity_0`, así que la
    rama de rechazo nunca se había ejecutado (hallazgo de `sdd-verify`,
    2026-08-21) — solo se sabía correcta por lectura de código, no por
    ejecución.
    """

    def test_quantity_distinta_de_uno_sin_basis_rechaza_la_edicion(
        self, client: TestClient, session: Session
    ) -> None:
        arroz = _crear_producto(session, name="Arroz")
        curado = _crear_raw_ingestion(session, hash_="hash-edit-basis-requerido")
        curado.processing_status = ProcessingStatus.CURATED
        session.commit()
        precio = _crear_precio_curado(
            session, ingestion=curado, product=arroz, price="1", quantity=Decimal("1")
        )

        response = client.post(
            f"/curation/{curado.id}/edit",
            data=_form_edicion(
                curado,
                {
                    "product_id": str(arroz.id),
                    "price": "340",
                    "quantity": "340",
                    "price_id": str(precio.id),
                },
            ),
            follow_redirects=False,
        )

        assert response.status_code == 422
        session.refresh(precio)
        # Nada se pisó: el precio viejo sigue en la base, sin el basis exigido.
        assert precio.price == Decimal("1")
        assert precio.price_basis is None

    def test_quantity_distinta_de_uno_con_basis_valido_actualiza(
        self, client: TestClient, session: Session
    ) -> None:
        arroz = _crear_producto(session, name="Arroz")
        curado = _crear_raw_ingestion(session, hash_="hash-edit-basis-valido")
        curado.processing_status = ProcessingStatus.CURATED
        session.commit()
        precio = _crear_precio_curado(
            session, ingestion=curado, product=arroz, price="1", quantity=Decimal("1")
        )

        response = client.post(
            f"/curation/{curado.id}/edit",
            data=_form_edicion(
                curado,
                {
                    "product_id": str(arroz.id),
                    "price": "340",
                    "quantity": "340",
                    "price_basis": "unit",
                    "price_id": str(precio.id),
                },
            ),
            follow_redirects=False,
        )

        assert response.status_code == 303
        session.refresh(precio)
        assert precio.price == Decimal("340")
        assert precio.price_basis == "unit"

    def test_quantity_uno_sin_basis_actualiza_con_basis_null(
        self, client: TestClient, session: Session
    ) -> None:
        """Guarda de regresión: con `quantity=1` la pregunta sigue inerte
        también al editar, igual que al curar."""
        pollo = _crear_producto(session, name="Pollo")
        curado = _crear_raw_ingestion(session, hash_="hash-edit-basis-qty-uno")
        curado.processing_status = ProcessingStatus.CURATED
        session.commit()
        precio = _crear_precio_curado(session, ingestion=curado, product=pollo, price="450")

        response = client.post(
            f"/curation/{curado.id}/edit",
            data=_form_edicion(
                curado,
                {
                    "product_id": str(pollo.id),
                    "price": "500",
                    "quantity": "1",
                    "price_id": str(precio.id),
                },
            ),
            follow_redirects=False,
        )

        assert response.status_code == 303
        session.refresh(precio)
        assert precio.price == Decimal("500")
        assert precio.price_basis is None


class TestPriceBasisTemplate:
    """El control de `price_basis` nombra la unidad de la fila y nunca viene
    preseleccionado (curation-observation-capture spec, "la prueba de la
    jabita" — mismo criterio que `sale_format`)."""

    def test_fila_nueva_no_preselecciona_ninguna_opcion(
        self, client: TestClient, session: Session
    ) -> None:
        # Sin catálogo piloto, `_pending_in_focus` no filtra nada (no hay
        # relevancia que medir), así que este pendiente sí llega a la cola y
        # las filas en blanco de más (`_EXTRA_BLANK_ROWS`) se renderizan.
        _crear_raw_ingestion(session, hash_="hash-basis-fila-nueva")

        response = client.get("/curation")

        assert response.status_code == 200
        assert 'id="price_basis_0"' in response.text
        assert 'value="unit" selected' not in response.text
        assert 'value="total" selected' not in response.text

    def test_edicion_de_fila_con_basis_unit_prellena_la_seleccion(
        self, client: TestClient, session: Session
    ) -> None:
        arroz = _crear_producto(session, name="Arroz")
        curado = _crear_raw_ingestion(session, hash_="hash-basis-edit-get")
        curado.processing_status = ProcessingStatus.CURATED
        session.commit()
        _crear_precio_curado(
            session, ingestion=curado, product=arroz, price="340",
            quantity=Decimal("340"), unit="lb", price_basis="unit",
        )

        response = client.get(f"/curation/{curado.id}/edit")

        assert '<option value="unit" selected>' in response.text

    def test_unidad_lb_nombra_la_libra_en_la_opcion(
        self, client: TestClient, session: Session
    ) -> None:
        arroz = _crear_producto(session, name="Arroz")
        curado = _crear_raw_ingestion(session, hash_="hash-basis-unidad-lb")
        curado.processing_status = ProcessingStatus.CURATED
        session.commit()
        _crear_precio_curado(
            session, ingestion=curado, product=arroz, price="700", unit="lb",
        )

        response = client.get(f"/curation/{curado.id}/edit")

        assert "Precio por libra" in response.text


class TestLaOpcionTotalNombraElLoteQueCubre:
    """Caso real del 2026-09-05: un anuncio de «caja de 40 lb a 24800» se curó
    con `price_basis='unit'`, así que 24800 se leyó como precio POR LIBRA y la
    caja terminó valiendo 992.000 CUP en la matriz.

    La fila no tenía nada mal: el extractor no propone basis y el select no
    preselecciona. Lo que falló es la copia. Con `unit='lb'` las dos opciones
    se leían «Precio por libra» y «Precio total del anuncio», y ninguna nombra
    lo que el anuncio realmente cotiza —una caja de 40 lb—. Quien piensa en
    cajas lee «una caja es una unidad» y elige la de arriba.

    La opción `total` pasa a nombrar el lote concreto de la fila. Los valores
    enviados (`unit` / `total`) no cambian: es copia, no contrato.
    """

    def _fila_curada(
        self,
        session: Session,
        *,
        hash_: str,
        quantity: Decimal | None,
        unit: str | None,
        packaging: str | None = None,
        packaging_quantity: str = "40",
        packaging_unit: str = "lb",
    ) -> RawIngestion:
        producto = _crear_producto(session, name="Pollo")
        curado = _crear_raw_ingestion(session, hash_=hash_)
        curado.processing_status = ProcessingStatus.CURATED
        session.commit()

        formato_id = None
        if packaging is not None:
            formato = ProductFormat(
                product_id=producto.id,
                packaging=packaging,
                quantity=Decimal(packaging_quantity),
                unit=packaging_unit,
            )
            session.add(formato)
            session.commit()
            formato_id = formato.id

        _crear_precio_curado(
            session, ingestion=curado, product=producto, price="24800",
            quantity=quantity, unit=unit, format_id=formato_id,
        )
        return curado

    def test_el_caso_de_la_caja_de_40_lb_queda_inequivoco(
        self, client: TestClient, session: Session
    ) -> None:
        """El caso que produjo el error, con su envase declarado. La copia NO
        nombra el envase: «la caja completa» obligaría a conocer el género de
        cada uno («la paquete») y «a granel» no admite artículo — el mismo
        motivo por el que `control.html` escribe «por caja» y no «la caja». La
        cantidad y la unidad alcanzan para desambiguar, y sirven también en la
        fila sin envase, que es la mayoría."""
        curado = self._fila_curada(
            session, hash_="hash-basis-caja", quantity=Decimal("40"),
            unit="lb", packaging="caja",
        )

        texto = client.get(f"/curation/{curado.id}/edit").text

        assert "Precio de las 40 libras juntas" in texto
        assert "Precio por libra" in texto

    def test_sin_envase_nombra_la_cantidad_de_la_fila(
        self, client: TestClient, session: Session
    ) -> None:
        """El envase es opcional y vive colapsado, así que la mayoría de las
        filas llegan sin él. La cantidad y la unidad sí están, y alcanzan."""
        curado = self._fila_curada(
            session, hash_="hash-basis-sin-envase", quantity=Decimal("40"), unit="lb",
        )

        texto = client.get(f"/curation/{curado.id}/edit").text

        assert "Precio de las 40 libras juntas" in texto

    def test_la_cantidad_no_arrastra_los_ceros_de_la_columna(
        self, client: TestClient, session: Session
    ) -> None:
        """`str(price.quantity)` sobre una columna con escala da «40.00», y la
        copia tiene que leerse como la escribiría una persona."""
        curado = self._fila_curada(
            session, hash_="hash-basis-ceros", quantity=Decimal("40.00"), unit="lb",
        )

        texto = client.get(f"/curation/{curado.id}/edit").text

        assert "40 libras juntas" in texto
        assert "40.00 libras" not in texto

    def test_una_cantidad_fraccionaria_conserva_sus_decimales(
        self, client: TestClient, session: Session
    ) -> None:
        """Recortar ceros no puede comerse un decimal real: media libra sigue
        siendo media libra."""
        curado = self._fila_curada(
            session, hash_="hash-basis-fraccion", quantity=Decimal("0.50"), unit="lb",
        )

        texto = client.get(f"/curation/{curado.id}/edit").text

        assert "Precio de las 0.5 libras juntas" in texto

    def test_sin_cantidad_ni_unidad_queda_la_copia_generica(
        self, client: TestClient, session: Session
    ) -> None:
        """Sin cantidad no hay lote que nombrar, y el campo ni siquiera es
        obligatorio: la copia de siempre es la correcta."""
        curado = self._fila_curada(
            session, hash_="hash-basis-generico", quantity=None, unit=None,
        )

        texto = client.get(f"/curation/{curado.id}/edit").text

        assert "Precio total del anuncio" in texto

    def test_los_valores_enviados_no_cambian(
        self, client: TestClient, session: Session
    ) -> None:
        """La copia cambia; el contrato con el servidor no. Si esto se rompe,
        se rompe `submit_curation` y todas las filas ya curadas."""
        curado = self._fila_curada(
            session, hash_="hash-basis-contrato", quantity=Decimal("40"),
            unit="lb", packaging="caja",
        )

        texto = client.get(f"/curation/{curado.id}/edit").text

        assert 'value="unit"' in texto
        assert 'value="total"' in texto


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

    def test_cambiar_a_cup_con_formato_nuevo_no_revienta_por_autoflush(
        self, client: TestClient, session: Session
    ) -> None:
        """Bug real de producción (2026-09-04, docs/decisions.md): cambiar la
        moneda a CUP en una fila que ya tenía `exchange_rate` no nulo, en el
        mismo submit que resuelve un `format_id` nuevo, reventaba con
        `IntegrityError`.

        La causa: `existing.currency` se asigna a `"CUP"` unas líneas antes de
        que `_resolve_format_id` llame a `get_or_create_format`, que hace un
        `SELECT` — y ESE `SELECT` dispara el autoflush de la sesión antes de
        que `apply_resolution(existing, NO_RATE)` (más abajo en el mismo
        método) alcance a limpiar el `exchange_rate` viejo. La fila que
        Postgres intenta persistir en ese instante tiene `currency='CUP'` +
        `exchange_rate` no nulo a la vez → viola
        `ck_prices_history_exchange_rate_cup`. En producción, 3 anuncios
        reventaron así el mismo día y las tres correcciones se perdieron
        (reintentado 2-3 veces cada uno, siempre el mismo error).
        """
        pollo = _crear_producto(session, name="Pollo")
        curado = _crear_raw_ingestion(session, hash_="hash-cup-autoflush")
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

        response = client.post(
            f"/curation/{curado.id}/edit",
            data=_form_edicion(
                curado,
                {
                    "product_id": str(pollo.id),
                    "price": "10.00",
                    "currency": Currency.CUP.value,  # el cambio que dispara el bug
                    "price_id": str(precio.id),
                    "packaging": "caja",
                    "quantity": "40",
                    "unit": "lb",
                    "price_basis": "total",
                },
            ),
            follow_redirects=False,
        )

        assert response.status_code == 303
        session.refresh(precio)
        assert precio.currency == Currency.CUP.value
        assert precio.exchange_rate is None  # limpiado, no la tasa vieja de USD
        formato = session.get(ProductFormat, precio.format_id)
        assert formato is not None
        assert (formato.packaging, formato.quantity, formato.unit) == (
            "caja",
            Decimal("40"),
            "lb",
        )


class TestVocabularioDeEnvase:
    """El envase pasa a ser parte del formato de venta: `<select>` sobre
    `PACKAGING`, sin texto libre. Con `quantity`+`unit` de la misma fila
    resuelve el `ProductFormat` al confirmar (design.md D7).
    """

    def test_envase_del_vocabulario_resuelve_el_formato_con_cantidad_y_unidad(
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
                "price_0": "17500",
                "quantity_0": "25",
                "unit_0": "kg",
                "packaging_0": "saco",
                "price_basis_0": "total",
            },
            follow_redirects=False,
        )

        fila = session.execute(select(PriceHistory)).scalars().one()
        formato = session.get(ProductFormat, fila.format_id)
        assert formato is not None
        assert (formato.packaging, formato.quantity, formato.unit) == (
            "saco",
            Decimal("25"),
            "kg",
        )

    def test_envase_sin_cantidad_deja_el_formato_sin_resolver(
        self, client: TestClient, session: Session
    ) -> None:
        """Triple incompleto (falta la cantidad) → `format_id = NULL`, sin
        fabricar un formato a medias."""
        producto = _crear_producto(session)
        ingestion = _crear_raw_ingestion(session, hash_="hash-envase-sin-cantidad")

        client.post(
            f"/curation/{ingestion.id}",
            data={
                "published_date": _fecha_pub(ingestion),
                "row_count": "1",
                "product_id_0": str(producto.id),
                "price_0": "450",
                "unit_0": "kg",
                "packaging_0": "saco",
            },
            follow_redirects=False,
        )

        fila = session.execute(select(PriceHistory)).scalars().one()
        assert fila.format_id is None
        assert session.execute(select(func.count()).select_from(ProductFormat)).scalar() == 0

    def test_edit_preselecciona_el_envase_del_formato_curado(
        self, client: TestClient, session: Session
    ) -> None:
        producto = _crear_producto(session)
        curado = _crear_raw_ingestion(session, hash_="hash-envase-edit-formato")
        curado.processing_status = ProcessingStatus.CURATED
        session.commit()
        formato = ProductFormat(
            product_id=producto.id,
            packaging="paquete",
            quantity=Decimal("10"),
            unit="lb",
        )
        session.add(formato)
        session.commit()
        _crear_precio_curado(
            session,
            ingestion=curado,
            product=producto,
            price="750",
            quantity=Decimal("10"),
            unit="lb",
            format_id=formato.id,
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
            f"/curation/{ingestion.id}/discard",
            data={"reason": "no_data"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/curation"

        session.expire_all()
        refrescado = session.get(RawIngestion, ingestion.id)
        assert refrescado is not None
        assert refrescado.processing_status == ProcessingStatus.DISCARDED
        assert refrescado.discard_reason == DiscardReason.NO_DATA.value
        assert list(session.execute(select(PriceHistory)).scalars()) == []

    def test_guarda_el_motivo_fuera_de_foco(
        self, client: TestClient, session: Session
    ) -> None:
        ingestion = _crear_raw_ingestion(session, hash_="hash-discard-off-focus")

        response = client.post(
            f"/curation/{ingestion.id}/discard",
            data={"reason": "off_focus"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        session.expire_all()
        assert session.get(RawIngestion, ingestion.id).discard_reason == (
            DiscardReason.OFF_FOCUS.value
        )

    def test_el_motivo_es_obligatorio(
        self, client: TestClient, session: Session
    ) -> None:
        """§12.5: la tasa de descarte solo cuenta «sin dato útil». Sin el
        motivo no se sabe de cuál de los dos baldes es, así que el POST no
        pasa."""
        ingestion = _crear_raw_ingestion(session, hash_="hash-discard-sin-motivo")

        response = client.post(
            f"/curation/{ingestion.id}/discard", follow_redirects=False
        )

        assert response.status_code == 422
        session.expire_all()
        assert session.get(RawIngestion, ingestion.id).processing_status == (
            ProcessingStatus.PENDING
        )

    def test_un_motivo_fuera_del_vocabulario_no_pasa(
        self, client: TestClient, session: Session
    ) -> None:
        ingestion = _crear_raw_ingestion(session, hash_="hash-discard-motivo-raro")

        response = client.post(
            f"/curation/{ingestion.id}/discard",
            data={"reason": "porque_si"},
            follow_redirects=False,
        )

        assert response.status_code == 422
        session.expire_all()
        assert session.get(RawIngestion, ingestion.id).processing_status == (
            ProcessingStatus.PENDING
        )


# --- GET /curation/sin-foco ---------------------------------------------------


class TestCurationSinFocoGet:
    def test_lista_solo_lo_que_no_menciona_ningun_piloto(
        self, client: TestClient, session: Session
    ) -> None:
        _crear_producto(session, name="Arroz", aliases=["arroz blanco"])
        con_foco = _crear_raw_ingestion(
            session, hash_="hash-sf-con-foco", original_text="arroz blanco a 500 el saco"
        )
        sin_foco = _crear_raw_ingestion(
            session, hash_="hash-sf-sin-foco", original_text="cemento a 500 el saco"
        )

        response = client.get("/curation/sin-foco")

        assert response.status_code == 200
        assert "cemento a 500 el saco" in response.text
        assert str(sin_foco.id) in response.text
        assert "arroz blanco a 500 el saco" not in response.text
        assert str(con_foco.id) not in response.text

    def test_excluye_imagen_sin_ocr_por_ser_relevancia_desconocida_no_sin_foco(
        self, client: TestClient, session: Session
    ) -> None:
        """`_FOCUS_UNKNOWN` (imagen sin OCR) no es lo mismo que `_FOCUS_NONE`:
        acá no se sabe si menciona un piloto, no se confirmó que no lo haga."""
        _crear_producto(session, name="Arroz", aliases=["arroz blanco"])
        imagen_sin_ocr = _crear_raw_ingestion(
            session,
            hash_="hash-sf-imagen",
            content_type=ContentType.IMAGE,
            original_text=None,
            file_path="foto.png",
        )

        response = client.get("/curation/sin-foco")

        assert response.status_code == 200
        assert str(imagen_sin_ocr.id) not in response.text
        assert "No hay pendientes sin foco." in response.text

    def test_sin_catalogo_piloto_no_lista_nada(
        self, client: TestClient, session: Session
    ) -> None:
        """Sin foco definido no hay señal que confirmar: afirmar "sin foco"
        acá sería inventarla, no leerla (ver `_pending_out_of_focus`)."""
        ingestion = _crear_raw_ingestion(
            session, hash_="hash-sf-sin-piloto", original_text="cemento a 500 el saco"
        )

        response = client.get("/curation/sin-foco")

        assert response.status_code == 200
        assert str(ingestion.id) not in response.text
        assert "No hay pendientes sin foco." in response.text


# --- POST /curation/sin-foco/discard ------------------------------------------


class TestCurationSinFocoDiscard:
    def test_descarta_exactamente_los_ids_enviados_y_no_toca_el_resto(
        self, client: TestClient, session: Session
    ) -> None:
        _crear_producto(session, name="Arroz", aliases=["arroz blanco"])
        sin_foco_1 = _crear_raw_ingestion(
            session, hash_="hash-sfd-1", original_text="cemento a 500 el saco"
        )
        sin_foco_2 = _crear_raw_ingestion(
            session, hash_="hash-sfd-2", original_text="cabilla de media a 1200"
        )
        con_foco = _crear_raw_ingestion(
            session, hash_="hash-sfd-3", original_text="arroz blanco a 500 el saco"
        )
        no_enviado = _crear_raw_ingestion(
            session, hash_="hash-sfd-4", original_text="ferretería varios a 300"
        )

        response = client.post(
            "/curation/sin-foco/discard",
            data={"ingestion_id": [str(sin_foco_1.id), str(sin_foco_2.id)]},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/curation/sin-foco"

        session.expire_all()
        assert session.get(RawIngestion, sin_foco_1.id).processing_status == (
            ProcessingStatus.DISCARDED
        )
        assert session.get(RawIngestion, sin_foco_2.id).processing_status == (
            ProcessingStatus.DISCARDED
        )
        # Todo lo descartado por esta ruta es "fuera de foco" por construcción.
        assert session.get(RawIngestion, sin_foco_1.id).discard_reason == (
            DiscardReason.OFF_FOCUS.value
        )
        assert session.get(RawIngestion, sin_foco_2.id).discard_reason == (
            DiscardReason.OFF_FOCUS.value
        )
        # ni lo que no vino en el POST (aunque también sea "sin foco") ni lo
        # que sí menciona un piloto se tocan.
        assert session.get(RawIngestion, no_enviado.id).processing_status == (
            ProcessingStatus.PENDING
        )
        assert session.get(RawIngestion, con_foco.id).processing_status == (
            ProcessingStatus.PENDING
        )

    def test_no_descarta_un_id_que_al_momento_del_post_ya_no_es_sin_foco(
        self, client: TestClient, session: Session
    ) -> None:
        """Revalida contra el estado actual, no confía en lo que diga el
        form: protege contra un hidden input viejo si el catálogo piloto
        cambió entre el GET que armó la lista y este POST."""
        _crear_producto(session, name="Arroz", aliases=["arroz blanco"])
        ya_no_califica = _crear_raw_ingestion(
            session, hash_="hash-sfd-race", original_text="arroz blanco a 500 el saco"
        )

        response = client.post(
            "/curation/sin-foco/discard",
            data={"ingestion_id": str(ya_no_califica.id)},
            follow_redirects=False,
        )

        assert response.status_code == 303
        session.expire_all()
        assert session.get(RawIngestion, ya_no_califica.id).processing_status == (
            ProcessingStatus.PENDING
        )

    def test_no_toca_un_pendiente_que_ya_se_curo_en_otra_pestana(
        self, client: TestClient, session: Session
    ) -> None:
        ya_curado = _crear_raw_ingestion(
            session, hash_="hash-sfd-ya-curado", original_text="cemento a 500 el saco"
        )
        ya_curado.processing_status = ProcessingStatus.CURATED
        session.commit()

        response = client.post(
            "/curation/sin-foco/discard",
            data={"ingestion_id": str(ya_curado.id)},
            follow_redirects=False,
        )

        assert response.status_code == 303
        session.expire_all()
        assert session.get(RawIngestion, ya_curado.id).processing_status == (
            ProcessingStatus.CURATED
        )

    def test_ignora_ids_invalidos_o_inexistentes_sin_romper_el_lote(
        self, client: TestClient, session: Session
    ) -> None:
        sin_foco = _crear_raw_ingestion(
            session, hash_="hash-sfd-junto-a-basura", original_text="cemento a 500 el saco"
        )

        response = client.post(
            "/curation/sin-foco/discard",
            data={
                "ingestion_id": [
                    "no-es-un-uuid",
                    str(uuid.uuid4()),
                    str(sin_foco.id),
                ]
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        session.expire_all()
        assert session.get(RawIngestion, sin_foco.id).processing_status == (
            ProcessingStatus.DISCARDED
        )


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
        formato = ProductFormat(
            product_id=pollo.id, packaging="caja", quantity=Decimal("40"), unit="lb"
        )
        session.add(formato)
        session.commit()
        precio = _crear_precio_curado(
            session,
            ingestion=curado,
            product=pollo,
            province=habana,
            price="500",
            unit="lb",
            quantity=Decimal("40"),
            sale_format=SaleFormat.RETAIL.value,
            format_id=formato.id,
        )

        response = client.get(f"/curation/{curado.id}/edit")

        assert response.status_code == 200
        assert f'value="{precio.id}"' in response.text
        assert f'value="{pollo.id}" selected' in response.text
        assert 'value="500"' in response.text
        assert f'value="{habana.id}" selected' in response.text
        assert '<option value="lb" data-basis-label="libra" data-basis-label-plural="libras" selected>' in response.text
        assert '<option value="retail" selected>' in response.text
        assert '<option value="caja" selected>' in response.text

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
        "packaging_0": row.get("packaging", ""),
        "min_purchase_0": row.get("min_purchase", ""),
        "province_id_0": row.get("province_id", ""),
        "price_id_0": row.get("price_id", ""),
        "price_basis_0": row.get("price_basis", ""),
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

    def test_actualiza_unit_sale_format_y_formato(
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
                    "quantity": "25",
                    "unit": "kg",
                    "sale_format": "wholesale",
                    "packaging": "saco",
                    "price_id": str(precio.id),
                    "price_basis": "total",
                }
            ),
        )

        session.refresh(precio)
        assert precio.unit == "kg"
        assert precio.sale_format == SaleFormat.WHOLESALE.value
        formato = session.get(ProductFormat, precio.format_id)
        assert formato is not None
        assert (formato.packaging, formato.quantity, formato.unit) == (
            "saco",
            Decimal("25"),
            "kg",
        )

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
                "packaging_0": "",
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
                "packaging_1": "",
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

    def test_sin_nada_del_foco_la_cola_se_muestra_vacia(
        self, client: TestClient, session: Session
    ) -> None:
        """Reemplaza al orden puro de 2026-08-18: un texto que no menciona
        ningún producto del foco ya no se muestra, ni siquiera al final de la
        cola (docs/decisions.md, 2026-08-20)."""
        _crear_producto(session, name="Arroz", aliases=["arroz blanco"])
        _crear_raw_ingestion(
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
        assert "No hay elementos en la cola." in response.text


class TestFueraDeFocoNoLlegaALaCola:
    """docs/decisions.md, 2026-08-20 — un anuncio de texto que no menciona
    ningún producto del foco no se muestra. Extiende al anuncio entero el
    mismo criterio que 2026-08-15 ya aplicaba a las filas del formulario:
    lo que no aporta a una celda no le cuesta un clic al operador.

    Esconder no es descartar: la fila sigue `pending` y su relevancia se
    recalcula en cada carga, así que un producto que entra al foco hace
    reaparecer sus anuncios sin reprocesar nada.
    """

    def test_texto_ajeno_al_foco_no_se_muestra_aunque_sea_lo_unico(
        self, client: TestClient, session: Session
    ) -> None:
        _crear_producto(session, name="Arroz", aliases=["arroz blanco"])
        _crear_raw_ingestion(
            session,
            hash_="hash-fuera-unico",
            original_text="¡Buenos días, familia! Nos encontramos en Calle 20",
            captured_at=datetime(2026, 8, 10, tzinfo=UTC),
        )

        response = client.get("/curation")

        assert response.status_code == 200
        assert "No hay elementos en la cola." in response.text

    def test_el_contador_declara_cuantos_quedaron_fuera_de_foco(
        self, client: TestClient, session: Session
    ) -> None:
        """Esconder en silencio sería mentir sobre el tamaño de la cola: el
        operador tiene que poder distinguir "no entró nada" de "entró ruido"."""
        _crear_producto(session, name="Arroz", aliases=["arroz blanco"])
        _crear_raw_ingestion(
            session,
            hash_="hash-fuera-contador-1",
            original_text="cemento a 500 el saco",
            captured_at=datetime(2026, 8, 10, tzinfo=UTC),
        )
        _crear_raw_ingestion(
            session,
            hash_="hash-fuera-contador-2",
            original_text="cabilla de media a 1200",
            captured_at=datetime(2026, 8, 11, tzinfo=UTC),
        )

        response = client.get("/curation")

        assert response.status_code == 200
        assert "2 fuera de foco" in response.text

    def test_por_curar_cuenta_solo_lo_que_el_operador_va_a_ver(
        self, client: TestClient, session: Session
    ) -> None:
        """El contador de la cola cuenta lo que se va a mostrar. Si siguiera
        contando el ruido escondido, diría "Por curar: 40" sobre una cola que
        se vacía al tercer anuncio."""
        _crear_producto(session, name="Arroz", aliases=["arroz blanco"])
        _crear_raw_ingestion(
            session,
            hash_="hash-fuera-por-curar-ruido",
            original_text="cemento a 500 el saco",
            captured_at=datetime(2026, 8, 10, tzinfo=UTC),
        )
        _crear_raw_ingestion(
            session,
            hash_="hash-fuera-por-curar-foco",
            original_text="arroz a 30000",
            captured_at=datetime(2026, 8, 11, tzinfo=UTC),
        )

        response = client.get("/curation")

        assert response.status_code == 200
        assert _contador(response.text, "Por curar") == "1"

    def test_imagen_sin_ocr_sigue_llegando_a_la_cola(
        self, client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Una imagen no tiene texto: su relevancia es desconocida, no
        negativa. Filtrarla exigiría correr el OCR de la cola entera en cada
        carga de página, que es justo lo que el orden por foco evita."""
        _crear_producto(session, name="Arroz", aliases=["arroz blanco"])
        imagen = _crear_raw_ingestion(
            session,
            hash_="hash-fuera-imagen",
            content_type=ContentType.IMAGE,
            captured_at=datetime(2026, 8, 10, tzinfo=UTC),
        )
        monkeypatch.setattr(ocr, "transcribe", lambda path, lang="spa": "")

        response = client.get("/curation")

        assert response.status_code == 200
        assert f'action="/curation/{imagen.id}"' in response.text

    def test_sin_foco_definido_no_se_esconde_nada(
        self, client: TestClient, session: Session
    ) -> None:
        """Sin ningún producto piloto no hay relevancia que medir. Filtrar
        contra un catálogo vacío dejaría la cola muerta — peor que el ruido
        que el filtro existe para sacar."""
        ingestion = _crear_raw_ingestion(
            session,
            hash_="hash-fuera-sin-foco",
            original_text="cemento a 500 el saco",
            captured_at=datetime(2026, 8, 10, tzinfo=UTC),
        )

        response = client.get("/curation")

        assert response.status_code == 200
        assert f'action="/curation/{ingestion.id}"' in response.text

    def test_un_producto_que_entra_al_foco_hace_reaparecer_sus_anuncios(
        self, client: TestClient, session: Session
    ) -> None:
        """Lo que sostiene que esconder sea reversible: la relevancia se
        calcula en cada carga contra el catálogo del foco, no se congela en
        una columna al ingerir."""
        _crear_producto(session, name="Arroz", aliases=["arroz blanco"])
        escondido = _crear_raw_ingestion(
            session,
            hash_="hash-fuera-reaparece",
            original_text="cemento a 500 el saco",
            captured_at=datetime(2026, 8, 10, tzinfo=UTC),
        )

        assert "No hay elementos en la cola." in client.get("/curation").text

        _crear_producto(session, name="Cemento", aliases=[])

        response = client.get("/curation")

        assert response.status_code == 200
        assert f'action="/curation/{escondido.id}"' in response.text

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


class TestRafagaDeMensajes:
    """El anuncio real no es un mensaje: es una ráfaga de mensajes seguidos.

    Medido contra la base de producción el 2026-08-21. El vendedor publica
    "Para mañana entrará aceite de 900ml" y cuatro minutos y medio después
    "A 4700", en el mismo canal. El primero nombra un producto del foco y
    entra a la cola sin precio; el segundo trae el precio y no nombra nada,
    así que el filtro del 2026-08-20 lo esconde. Curar el primero sin ver el
    segundo es guardar un anuncio incompleto.

    La solución es mostrar la ráfaga como CONTEXTO del que ya entra, no
    ascender al huérfano a la cola. Ascenderlo se probó y se midió: rescataba
    18 pendientes de los cuales 1 servía, porque en un canal activo cualquier
    anuncio de gasolina o keratina cae dentro de la ventana. Ver
    `_focus_rank`.
    """

    def _canal(self, session: Session, name: str = "Groupej Morales") -> Channel:
        canal = Channel(
            name=name,
            platform=Platform.WHATSAPP_CHANNEL,
            province_id=None,
            is_active=True,
        )
        session.add(canal)
        session.commit()
        return canal

    def _par_real(
        self,
        session: Session,
        *,
        canal: Channel,
        salto: timedelta,
        canal_del_precio: Channel | None = None,
    ) -> tuple[RawIngestion, RawIngestion]:
        """El par medido en producción, con el salto de tiempo parametrizado."""
        ancla = datetime(2026, 8, 20, 1, 40, 46, tzinfo=UTC)
        con_producto = _crear_raw_ingestion(
            session,
            hash_="hash-rafaga-producto",
            original_text="Para mañana entrará aceite de 900ml",
            captured_at=ancla,
            source_type=SourceType.WHATSAPP_CHANNEL,
            channel_id=canal.id,
        )
        huerfano = _crear_raw_ingestion(
            session,
            hash_="hash-rafaga-precio",
            original_text="A 4700",
            captured_at=ancla + salto,
            source_type=SourceType.WHATSAPP_CHANNEL,
            channel_id=(canal_del_precio or canal).id,
        )
        return con_producto, huerfano

    def test_la_evidencia_muestra_el_mensaje_que_trae_el_precio(
        self, client: TestClient, session: Session
    ) -> None:
        """El caso que motivó todo: sin esto, el operador cura el aceite sin
        saber que el precio llegó cuatro minutos después."""
        _crear_producto(session, name="Aceite")
        canal = self._canal(session)
        con_producto, _ = self._par_real(
            session, canal=canal, salto=timedelta(minutes=4, seconds=33)
        )

        response = client.get("/curation")

        assert response.status_code == 200
        assert f'action="/curation/{con_producto.id}"' in response.text
        assert "A 4700" in response.text

    def test_el_huerfano_no_entra_a_la_cola_por_tener_contexto(
        self, client: TestClient, session: Session
    ) -> None:
        """Decisión deliberada: la ráfaga informa, no reordena. El precio
        suelto se lee como contexto y sigue fuera de foco, así el filtro del
        2026-08-20 conserva su valor."""
        _crear_producto(session, name="Aceite")
        canal = self._canal(session)
        self._par_real(session, canal=canal, salto=timedelta(minutes=4, seconds=33))

        response = client.get("/curation")

        assert response.status_code == 200
        assert "1 fuera de foco" in response.text

    def test_el_contexto_incluye_lo_ya_curado(
        self, client: TestClient, session: Session
    ) -> None:
        """Que el vecino ya esté curado no lo borra del anuncio: el operador
        sigue necesitando leerlo para entender el que tiene delante."""
        _crear_producto(session, name="Aceite")
        canal = self._canal(session)
        ancla = datetime(2026, 8, 20, 1, 40, 46, tzinfo=UTC)
        ya_curado = _crear_raw_ingestion(
            session,
            hash_="hash-rafaga-curado",
            original_text="Aceite de 900ml disponible",
            captured_at=ancla,
            source_type=SourceType.WHATSAPP_CHANNEL,
            channel_id=canal.id,
        )
        ya_curado.processing_status = ProcessingStatus.CURATED
        session.commit()
        _crear_raw_ingestion(
            session,
            hash_="hash-rafaga-pendiente",
            original_text="Aceite girasol a 4700",
            captured_at=ancla + timedelta(minutes=3),
            source_type=SourceType.WHATSAPP_CHANNEL,
            channel_id=canal.id,
        )

        response = client.get("/curation")

        assert response.status_code == 200
        assert "Aceite de 900ml disponible" in response.text

    def test_el_contexto_no_cruza_canales(
        self, client: TestClient, session: Session
    ) -> None:
        """Dos vendedores publicando a la misma hora no forman un anuncio: el
        precio de uno no describe el producto del otro."""
        _crear_producto(session, name="Aceite")
        uno = self._canal(session, name="Canal Uno")
        otro = self._canal(session, name="Canal Otro")
        self._par_real(
            session,
            canal=uno,
            salto=timedelta(minutes=1),
            canal_del_precio=otro,
        )

        response = client.get("/curation")

        assert response.status_code == 200
        assert "A 4700" not in response.text

    def test_fuera_de_la_ventana_no_hay_contexto(
        self, client: TestClient, session: Session
    ) -> None:
        """Once minutos ya no son el mismo anuncio."""
        _crear_producto(session, name="Aceite")
        canal = self._canal(session)
        self._par_real(session, canal=canal, salto=timedelta(minutes=11))

        response = client.get("/curation")

        assert response.status_code == 200
        assert "A 4700" not in response.text

    def test_sin_canal_conocido_no_hay_contexto(
        self, client: TestClient, session: Session
    ) -> None:
        """Una captura suelta de la bandeja no tiene `channel_id`. Agrupar por
        "ninguno" mezclaría capturas de fuentes sin relación entre sí."""
        _crear_producto(session, name="Aceite")
        ancla = datetime(2026, 8, 20, 1, 40, 46, tzinfo=UTC)
        _crear_raw_ingestion(
            session,
            hash_="hash-rafaga-sin-canal-producto",
            original_text="Para mañana entrará aceite de 900ml",
            captured_at=ancla,
        )
        _crear_raw_ingestion(
            session,
            hash_="hash-rafaga-sin-canal-precio",
            original_text="A 4700",
            captured_at=ancla + timedelta(minutes=1),
        )

        response = client.get("/curation")

        assert response.status_code == 200
        assert "A 4700" not in response.text

    def test_el_contexto_muestra_la_foto_vecina(
        self, client: TestClient, session: Session
    ) -> None:
        """El patrón que el operador reportó: la foto del saco de arroz y, en
        el mensaje siguiente, "arroz a 340 por libra"."""
        _crear_producto(session, name="Arroz")
        canal = self._canal(session)
        ancla = datetime(2026, 8, 20, 1, 40, 46, tzinfo=UTC)
        foto = _crear_raw_ingestion(
            session,
            hash_="hash-rafaga-foto",
            content_type=ContentType.IMAGE,
            captured_at=ancla,
            source_type=SourceType.WHATSAPP_CHANNEL,
            channel_id=canal.id,
        )
        _crear_raw_ingestion(
            session,
            hash_="hash-rafaga-texto-arroz",
            original_text="arroz a 340 por libra",
            captured_at=ancla + timedelta(minutes=2),
            source_type=SourceType.WHATSAPP_CHANNEL,
            channel_id=canal.id,
        )

        response = client.get("/curation")

        assert response.status_code == 200
        # La foto es `_FOCUS_UNKNOWN` y el texto `_FOCUS_MATCH`: manda el texto,
        # y la foto que lo ilustra viaja con él como evidencia vecina.
        assert f'/evidence/{foto.id}' in response.text


class TestUltimaCaptura:
    """El bache de captura del 2026-08-24 (WAHA deslogueado remotamente) se
    leyó al principio como "cola vacía" por foco, cuando el problema real era
    que no entraba nada nuevo desde hacía más de un día — nada en la pantalla
    lo decía. `_last_capture_notice` es la medición pasiva que lo hace visible,
    sin fabricar un umbral de "atrasado" (docs/decisions.md, 2026-08-24).
    """

    def test_sin_ninguna_ingesta_nunca_no_hay_nada_que_medir(
        self, session: Session
    ) -> None:
        assert _last_capture_notice(session) is None

    def test_menos_de_una_hora(self, session: Session) -> None:
        ancla = datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC)
        _crear_raw_ingestion(session, hash_="hash-reciente", captured_at=ancla)

        texto = _last_capture_notice(session, now=ancla + timedelta(minutes=30))

        assert texto == "Última captura: hace menos de una hora."

    def test_singular_de_una_hora(self, session: Session) -> None:
        ancla = datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC)
        _crear_raw_ingestion(session, hash_="hash-1h", captured_at=ancla)

        texto = _last_capture_notice(session, now=ancla + timedelta(hours=1, minutes=10))

        assert texto == "Última captura: hace 1 hora."

    def test_plural_de_horas(self, session: Session) -> None:
        ancla = datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC)
        _crear_raw_ingestion(session, hash_="hash-5h", captured_at=ancla)

        texto = _last_capture_notice(session, now=ancla + timedelta(hours=5))

        assert texto == "Última captura: hace 5 horas."

    def test_pasa_a_dias_despues_de_24_horas(self, session: Session) -> None:
        ancla = datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)
        _crear_raw_ingestion(session, hash_="hash-2d", captured_at=ancla)

        texto = _last_capture_notice(session, now=ancla + timedelta(days=2, hours=3))

        assert texto == "Última captura: hace 2 días."

    def test_singular_de_un_dia(self, session: Session) -> None:
        ancla = datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)
        _crear_raw_ingestion(session, hash_="hash-1d", captured_at=ancla)

        texto = _last_capture_notice(session, now=ancla + timedelta(days=1, hours=2))

        assert texto == "Última captura: hace 1 día."

    def test_toma_el_maximo_sin_importar_el_estado(self, session: Session) -> None:
        """No solo `pending`: un anuncio ya curado o descartado también cuenta
        como "algo entró", que es la pregunta que este indicador responde."""
        vieja = _crear_raw_ingestion(
            session, hash_="hash-vieja", captured_at=datetime(2026, 8, 1, tzinfo=UTC)
        )
        vieja.processing_status = ProcessingStatus.DISCARDED
        reciente = datetime(2026, 8, 24, 10, 0, 0, tzinfo=UTC)
        curada = _crear_raw_ingestion(session, hash_="hash-curada", captured_at=reciente)
        curada.processing_status = ProcessingStatus.CURATED
        session.commit()

        texto = _last_capture_notice(session, now=reciente + timedelta(hours=2))

        assert texto == "Última captura: hace 2 horas."

    def test_el_endpoint_muestra_el_aviso(self, client: TestClient, session: Session) -> None:
        _crear_raw_ingestion(session, hash_="hash-endpoint", captured_at=datetime.now(UTC))

        response = client.get("/curation")

        assert response.status_code == 200
        assert "Última captura:" in response.text


class TestAsistenteLLM:
    """El botón y el endpoint de asistencia (`design.md`, D4 y D10).

    Lo que se prueba acá es el contrato de la interfaz: cuándo se ofrece,
    cuándo NO se llama, que solo llene huecos y que una falla no deje al
    operador peor que antes. La calidad de lo que responda el modelo no se
    prueba acá — se mide contra la cola real.
    """

    def _pendiente_con_hueco(self, session: Session) -> RawIngestion:
        """Texto donde las reglas resuelven el producto y dejan la cantidad
        sin resolver: el caso que el asistente existe para atacar."""
        _crear_producto(session, name="Pollo", aliases=["pollo congelado"])
        return _crear_raw_ingestion(
            session,
            hash_="hash-asistente-hueco",
            original_text="pollo congelado a 500",
        )

    def test_sin_clave_configurada_no_se_ofrece_el_boton(
        self, client: TestClient, session: Session
    ) -> None:
        """Sin clave el asistente no puede hacer nada: ofrecerlo sería
        prometer algo que no va a pasar."""
        self._pendiente_con_hueco(session)

        assert "/assist" not in client.get("/curation").text

    def test_con_clave_y_huecos_se_ofrece(
        self,
        client: TestClient,
        session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        self._pendiente_con_hueco(session)
        monkeypatch.setenv("SAIMC_ANTHROPIC_API_KEY", "clave-de-prueba")
        get_settings.cache_clear()

        assert "/assist" in client.get("/curation").text

    def test_abrir_la_cola_no_llama_a_la_api(
        self,
        client: TestClient,
        session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """La garantía de costo: cargar la página no gasta un peso."""
        self._pendiente_con_hueco(session)
        monkeypatch.setenv("SAIMC_ANTHROPIC_API_KEY", "clave-de-prueba")
        get_settings.cache_clear()
        llamadas: list[object] = []
        monkeypatch.setattr(
            curation,
            "propose_missing",
            lambda *a, **k: llamadas.append(1) or llm_assist.AssistedFields(),
        )

        client.get("/curation")

        assert llamadas == []

    def test_el_asistente_llena_el_hueco(
        self,
        client: TestClient,
        session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        ingestion = self._pendiente_con_hueco(session)
        monkeypatch.setenv("SAIMC_ANTHROPIC_API_KEY", "clave-de-prueba")
        get_settings.cache_clear()
        monkeypatch.setattr(
            curation,
            "propose_missing",
            lambda *a, **k: llm_assist.AssistedFields(quantity=Decimal("40"), unit="lb"),
        )

        texto = client.post(f"/curation/{ingestion.id}/assist").text

        assert 'value="40"' in texto

    def test_no_pisa_lo_que_las_reglas_ya_resolvieron(
        self,
        client: TestClient,
        session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """La garantía de D1: el modelo no puede degradar un campo que ya
        estaba bien. Acá responde un precio distinto y se ignora."""
        ingestion = self._pendiente_con_hueco(session)
        monkeypatch.setenv("SAIMC_ANTHROPIC_API_KEY", "clave-de-prueba")
        get_settings.cache_clear()
        monkeypatch.setattr(
            curation,
            "propose_missing",
            lambda *a, **k: llm_assist.AssistedFields(price=Decimal("999")),
        )

        texto = client.post(f"/curation/{ingestion.id}/assist").text

        assert 'value="500"' in texto
        assert 'value="999"' not in texto

    def test_una_asistencia_fallida_deja_el_formulario_usable(
        self,
        client: TestClient,
        session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        ingestion = self._pendiente_con_hueco(session)
        monkeypatch.setenv("SAIMC_ANTHROPIC_API_KEY", "clave-de-prueba")
        get_settings.cache_clear()
        monkeypatch.setattr(
            curation, "propose_missing", lambda *a, **k: llm_assist.AssistedFields()
        )

        response = client.post(f"/curation/{ingestion.id}/assist")

        assert response.status_code == 200
        assert 'value="500"' in response.text

    def test_los_campos_asistidos_quedan_marcados(
        self,
        client: TestClient,
        session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """D10: el riesgo no es que el modelo se equivoque, es que se
        equivoque de forma convincente sobre un formulario que se recorre
        rápido. La marca es lo que sostiene la revisión."""
        ingestion = self._pendiente_con_hueco(session)
        monkeypatch.setenv("SAIMC_ANTHROPIC_API_KEY", "clave-de-prueba")
        get_settings.cache_clear()
        monkeypatch.setattr(
            curation,
            "propose_missing",
            lambda *a, **k: llm_assist.AssistedFields(quantity=Decimal("40")),
        )

        texto = client.post(f"/curation/{ingestion.id}/assist").text

        assert "data-assisted" in texto

    def test_sin_clave_el_endpoint_no_llama_pero_responde(
        self,
        client: TestClient,
        session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Aunque alguien llegue al endpoint a mano, sin clave no se gasta."""
        ingestion = self._pendiente_con_hueco(session)
        llamadas: list[object] = []
        monkeypatch.setattr(
            curation,
            "propose_missing",
            lambda *a, **k: llamadas.append(1) or llm_assist.AssistedFields(),
        )

        response = client.post(f"/curation/{ingestion.id}/assist")

        assert response.status_code == 200
        assert llamadas == []


class TestHistorialFiltraPorFormato:
    """El enlace «n =» de una celda de la Matriz tiene que aislar las filas de
    ESE formato. Sin el filtro, una celda de «caja de 40 lb» abriría también
    las del paquete de 10 lb: las dos llevan `unit = "lb"` y comparten el
    resto de la identidad efectiva."""

    def test_filtra_las_filas_del_formato_pedido(
        self, client: TestClient, session: Session
    ) -> None:
        pollo = _crear_producto(session, name="Pollo")
        curado = _crear_raw_ingestion(session, hash_="hash-hist-formato")
        curado.processing_status = ProcessingStatus.CURATED
        session.commit()
        caja = ProductFormat(
            product_id=pollo.id, packaging="caja", quantity=Decimal("40"), unit="lb"
        )
        paquete = ProductFormat(
            product_id=pollo.id, packaging="paquete", quantity=Decimal("10"), unit="lb"
        )
        session.add_all([caja, paquete])
        session.commit()
        _crear_precio_curado(
            session, ingestion=curado, product=pollo, price="12400",
            quantity=Decimal("40"), unit="lb", price_basis="total", format_id=caja.id,
        )
        _crear_precio_curado(
            session, ingestion=curado, product=pollo, price="3500",
            quantity=Decimal("10"), unit="lb", price_basis="total", format_id=paquete.id,
        )

        response = client.get(f"/curation/history?product=Pollo&format_id={caja.id}")

        assert response.status_code == 200
        assert "12400" in response.text
        assert "3500" not in response.text

    def test_format_id_vacio_filtra_las_filas_sin_formato(
        self, client: TestClient, session: Session
    ) -> None:
        pollo = _crear_producto(session, name="Pollo")
        curado = _crear_raw_ingestion(session, hash_="hash-hist-sin-formato")
        curado.processing_status = ProcessingStatus.CURATED
        session.commit()
        caja = ProductFormat(
            product_id=pollo.id, packaging="caja", quantity=Decimal("40"), unit="lb"
        )
        session.add(caja)
        session.commit()
        _crear_precio_curado(
            session, ingestion=curado, product=pollo, price="12400",
            quantity=Decimal("40"), unit="lb", price_basis="total", format_id=caja.id,
        )
        _crear_precio_curado(
            session, ingestion=curado, product=pollo, price="999",
            quantity=Decimal("40"), unit="lb", price_basis="total", format_id=None,
        )

        response = client.get("/curation/history?product=Pollo&format_id=")

        assert response.status_code == 200
        assert "999" in response.text
        assert "12400" not in response.text


class TestEditarCuradoBorrarFila:
    """Al corregir un anuncio, el operador puede eliminar una de sus filas —
    p. ej. una fila duplicada, o una que no correspondía. Distinto de
    descartar el anuncio entero: acá el resto de las filas se conserva."""

    def _dos_filas(self, session: Session) -> tuple[RawIngestion, PriceHistory, PriceHistory]:
        pollo = _crear_producto(session, name="Pollo")
        curado = _crear_raw_ingestion(session, hash_="hash-borrar-fila")
        curado.processing_status = ProcessingStatus.CURATED
        session.commit()
        a = _crear_precio_curado(session, ingestion=curado, product=pollo, price="100")
        b = _crear_precio_curado(session, ingestion=curado, product=pollo, price="200")
        return curado, a, b

    def _campos(self, curado: RawIngestion, precio: PriceHistory, i: int, *, borrar: bool) -> dict:
        d = {
            f"product_id_{i}": str(precio.product_id),
            f"price_{i}": str(precio.price),
            f"currency_{i}": Currency.CUP.value,
            f"quantity_{i}": "",
            f"unit_{i}": "",
            f"sale_format_{i}": "",
            f"packaging_{i}": "",
            f"min_purchase_{i}": "",
            f"province_id_{i}": "",
            f"price_id_{i}": str(precio.id),
            f"price_basis_{i}": "",
        }
        if borrar:
            d[f"delete_{i}"] = "on"
        return d

    def test_elimina_la_fila_marcada_y_conserva_el_resto(
        self, client: TestClient, session: Session
    ) -> None:
        curado, a, b = self._dos_filas(session)
        a_id, b_id = a.id, b.id

        data = {
            "source_reference": "",
            "published_date": _fecha_pub(curado),
            "row_count": "2",
            **self._campos(curado, a, 0, borrar=True),
            **self._campos(curado, b, 1, borrar=False),
        }
        response = client.post(f"/curation/{curado.id}/edit", data=data, follow_redirects=False)

        assert response.status_code == 303
        session.expire_all()
        filas = list(session.execute(select(PriceHistory)).scalars())
        assert [f.id for f in filas] == [b_id]
        # El anuncio sigue curado, no descartado.
        assert session.get(RawIngestion, curado.id).processing_status == ProcessingStatus.CURATED

    def test_una_fila_marcada_para_borrar_no_se_valida(
        self, client: TestClient, session: Session
    ) -> None:
        """La fila que se va no tiene que pasar las reglas: un precio vacío en
        la fila marcada no debe bloquear el guardado."""
        curado, a, b = self._dos_filas(session)

        campos_a = self._campos(curado, a, 0, borrar=True)
        campos_a["price_0"] = ""  # inválido, pero se va
        data = {
            "source_reference": "",
            "published_date": _fecha_pub(curado),
            "row_count": "2",
            **campos_a,
            **self._campos(curado, b, 1, borrar=False),
        }
        response = client.post(f"/curation/{curado.id}/edit", data=data, follow_redirects=False)

        assert response.status_code == 303
        session.expire_all()
        assert [f.price for f in session.execute(select(PriceHistory)).scalars()] == [Decimal("200")]

    def test_no_deja_borrar_todas_las_filas(
        self, client: TestClient, session: Session
    ) -> None:
        """Un anuncio sin filas de precio no es una corrección: es un
        descarte, y para eso está el botón de descartar."""
        curado, a, b = self._dos_filas(session)

        data = {
            "source_reference": "",
            "published_date": _fecha_pub(curado),
            "row_count": "2",
            **self._campos(curado, a, 0, borrar=True),
            **self._campos(curado, b, 1, borrar=True),
        }
        response = client.post(f"/curation/{curado.id}/edit", data=data, follow_redirects=False)

        assert response.status_code == 422
        assert "descart" in response.text.lower()
        session.expire_all()
        assert len(list(session.execute(select(PriceHistory)).scalars())) == 2

    def test_el_formulario_de_edicion_ofrece_borrar_cada_fila(
        self, client: TestClient, session: Session
    ) -> None:
        curado, a, b = self._dos_filas(session)

        html = client.get(f"/curation/{curado.id}/edit").text

        assert 'name="delete_0"' in html
        assert 'name="delete_1"' in html


class TestVistaCola:
    """El mapa de la cola: todos los pendientes, con filtros (2026-09-07).

    Existe porque `/curation` muestra un pendiente por vez y esconde lo que
    está fuera de foco, así que no había forma de ver qué hay ni de abrir un
    anuncio puntual.
    """

    def test_lista_tambien_lo_que_esta_fuera_de_foco(
        self, client: TestClient, session: Session
    ) -> None:
        _crear_producto(session, name="Pollo")
        _crear_raw_ingestion(
            session, hash_="cola-en-foco", original_text="Pollo a 1200 la lb"
        )
        _crear_raw_ingestion(
            session, hash_="cola-fuera", original_text="Cemento gris a 25 usd"
        )

        html = client.get("/curation/cola").text

        # La cola normal esconde el cemento; este mapa lo muestra.
        assert "Pollo a 1200" in html
        assert "Cemento gris" in html

    def test_filtra_por_foco(self, client: TestClient, session: Session) -> None:
        _crear_producto(session, name="Pollo")
        _crear_raw_ingestion(
            session, hash_="foco-si", original_text="Pollo a 1200 la lb"
        )
        _crear_raw_ingestion(
            session, hash_="foco-no", original_text="Cemento gris a 25 usd"
        )

        solo_foco = client.get("/curation/cola?foco=si").text
        assert "Pollo a 1200" in solo_foco
        assert "Cemento gris" not in solo_foco

        fuera = client.get("/curation/cola?foco=no").text
        assert "Cemento gris" in fuera
        assert "Pollo a 1200" not in fuera

    def test_filtra_por_texto(self, client: TestClient, session: Session) -> None:
        _crear_producto(session, name="Pollo")
        _crear_raw_ingestion(
            session, hash_="q-1", original_text="Aceite Saude 900 ml a 3400"
        )
        _crear_raw_ingestion(session, hash_="q-2", original_text="Pollo a 1200 la lb")

        html = client.get("/curation/cola?q=900 ml").text

        assert "Aceite Saude" in html
        assert "Pollo a 1200" not in html

    def test_la_imagen_sin_texto_se_marca_y_no_se_transcribe(
        self, client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """El OCR de la cola entera en cada carga es justo lo que esta vista
        no puede pagar: cientos de imágenes por página."""
        _crear_raw_ingestion(
            session,
            hash_="img-sin-texto",
            content_type=ContentType.IMAGE,
            original_text=None,
        )

        def _explotar(*args: object, **kwargs: object) -> str:
            raise AssertionError("la vista de cola no debe correr OCR")

        monkeypatch.setattr(ocr, "transcribe", _explotar)

        html = client.get("/curation/cola").text

        assert "Se transcribe al abrirla" in html

    def test_la_posicion_no_cambia_al_filtrar(
        self, client: TestClient, session: Session
    ) -> None:
        """El `#` ubica en la cola completa; si dependiera del filtro activo,
        «el 3» significaría algo distinto en cada vista."""
        _crear_producto(session, name="Pollo")
        base = datetime.now(UTC) - timedelta(hours=3)
        _crear_raw_ingestion(
            session,
            hash_="pos-0",
            original_text="Cemento gris a 25 usd",
            captured_at=base,
        )
        _crear_raw_ingestion(
            session,
            hash_="pos-1",
            original_text="Pollo a 1200 la lb",
            captured_at=base + timedelta(hours=1),
        )

        html = client.get("/curation/cola?foco=si").text

        # El pollo es el segundo de la cola completa aunque sea el único
        # listado bajo este filtro: su celda de posición dice 1, no 0. Se
        # mira la celda con su clase para que un "1" suelto de la página no
        # haga pasar el test.
        celdas = re.findall(r'<td class="m3-num m3-sm m3-nowrap">(\d+)</td>', html)
        assert celdas == ["1"]

    def test_saltar_por_id_abre_ese_pendiente(
        self, client: TestClient, session: Session
    ) -> None:
        _crear_producto(session, name="Pollo")
        _crear_provincia(session)
        base = datetime.now(UTC) - timedelta(hours=3)
        _crear_raw_ingestion(
            session,
            hash_="salto-primero",
            original_text="Pollo a 1200 la lb",
            captured_at=base,
        )
        segundo = _crear_raw_ingestion(
            session,
            hash_="salto-segundo",
            original_text="Pollo ahumado a 9999 la lb",
            captured_at=base + timedelta(hours=1),
        )

        sin_id = client.get("/curation").text
        assert "Pollo a 1200" in sin_id
        assert "9999" not in sin_id

        con_id = client.get(f"/curation?id={segundo.id}").text
        assert "9999" in con_id

    def test_saltar_a_uno_fuera_de_foco_lo_abre_igual(
        self, client: TestClient, session: Session
    ) -> None:
        """El punto del mapa es ver lo que el foco esconde: si el salto
        respetara el foco, la vista no serviría para eso."""
        _crear_producto(session, name="Pollo")
        _crear_raw_ingestion(session, hash_="foco-ok", original_text="Pollo a 1200 la lb")
        fuera = _crear_raw_ingestion(
            session, hash_="foco-fuera", original_text="Cemento gris a 25 usd"
        )

        html = client.get(f"/curation?id={fuera.id}").text

        assert "Cemento gris" in html

    @pytest.mark.parametrize(
        "sufijo",
        [
            "?id=no-es-un-uuid",
            f"?id={uuid.uuid4()}",
        ],
    )
    def test_id_invalido_o_inexistente_cae_a_la_cola_normal(
        self, client: TestClient, session: Session, sufijo: str
    ) -> None:
        """Degrada en vez de 404: el operador vino a curar y la cola siempre
        tiene algo mejor que un error."""
        _crear_producto(session, name="Pollo")
        _crear_raw_ingestion(session, hash_="fallback", original_text="Pollo a 1200 la lb")

        response = client.get(f"/curation{sufijo}")

        assert response.status_code == 200
        assert "Pollo a 1200" in response.text

    def test_no_lista_lo_ya_curado_ni_lo_descartado(
        self, client: TestClient, session: Session
    ) -> None:
        _crear_producto(session, name="Pollo")
        curado = _crear_raw_ingestion(
            session, hash_="ya-curado", original_text="Pollo curado a 1200"
        )
        curado.processing_status = ProcessingStatus.CURATED
        descartado = _crear_raw_ingestion(
            session, hash_="ya-descartado", original_text="Pollo descartado a 1300"
        )
        descartado.processing_status = ProcessingStatus.DISCARDED
        session.commit()

        html = client.get("/curation/cola").text

        assert "Pollo curado" not in html
        assert "Pollo descartado" not in html

    def test_los_contadores_cuentan_la_cola_completa(
        self, client: TestClient, session: Session
    ) -> None:
        """Los atajos de arriba tienen que decir la forma de la cola entera,
        no la del filtro activo: si contaran lo listado, «🎯 En foco 1» diría
        siempre 1 estando ya dentro de ese filtro."""
        _crear_producto(session, name="Pollo")
        _crear_raw_ingestion(session, hash_="c-foco", original_text="Pollo a 1200 la lb")
        _crear_raw_ingestion(session, hash_="c-fuera", original_text="Cemento a 25 usd")
        _crear_raw_ingestion(
            session, hash_="c-img", content_type=ContentType.IMAGE, original_text=None
        )

        html = client.get("/curation/cola?foco=si").text

        assert '🎯 En foco <span class="m3-tab-count">1</span>' in html
        assert '🖼️ Sin transcribir <span class="m3-tab-count">1</span>' in html
        assert '🚫 Fuera de foco <span class="m3-tab-count">1</span>' in html
        assert 'Todos <span class="m3-tab-count">3</span>' in html
