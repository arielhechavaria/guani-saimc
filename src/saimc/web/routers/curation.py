"""Cola de curaduría — spec.md §6.1, Etapa 1c y Etapa 2.

Primera y única interfaz humana del sistema en Fase 0: presenta un elemento
`pending` por vez, propone una extracción cuando hay texto, y el operador
confirma o corrige. Cada confirmación produce la fila de `prices_history`
que alimenta H0.

Conversión de moneda (`spec.md` §6.3 E, design.md D4): cada fila con
`currency != CUP` pasa sincrónicamente por `RateResolver` antes del commit,
en ambos caminos de escritura (`submit_curation` y `submit_edit`). La
resolución nunca bloquea el guardado — si nada es resolvible, la fila se
guarda igual con `price_cup`/`exchange_rate*` en `NULL` y el operador ve un
aviso no bloqueante (`?unconverted=N`, ver `_unconverted_notice`).

Nota de esquema: `prices_history.unit` se completa con `canonical_unit()`
sobre lo que el operador confirma en el `<select>` del formulario — el
vocabulario cerrado vive en `saimc.units`, no acá. `sale_format` pasa por el
gate de `SaleFormat` (design.md, D4): solo se guarda cuando coincide con uno
de los dos valores del enum, cualquier otra cosa (incluido un envío en
blanco) degrada a `NULL`.

El envase pasa a ser parte del formato de venta (`product_formats`): el
formulario pide un `<select>` de envase del vocabulario cerrado
(`saimc.units.PACKAGING`), y junto con `quantity`+`unit` de la misma fila
resuelve el triple a un `ProductFormat` vía `get_or_create_format()`. Si el
triple está completo, la fila queda con `format_id`; si falta el envase, con
`format_id = NULL` — mismo criterio que `sale_format` en blanco. La columna
`packaging` de texto libre ya no se escribe acá; la retira la migración
`0019`.
"""

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.datastructures import FormData

from saimc import curation_rows
from saimc.cells import resolve_row
from saimc.config import get_settings
from saimc.curation_events import (
    CatalogNames,
    EditSummary,
    describe_edit,
    parse_shown_at,
    record_curation_event,
    referenced_catalog_ids,
    row_text,
    row_values,
)
from saimc.exchange_rate import NO_RATE, Fetcher, RateResolver, anchor_date, apply_resolution
from saimc.extraction import llm_store
from saimc.extraction.engine import (
    ExtractionProposal,
    guess_source_reference,
    mentions_any_product,
    probable_discard_reasons,
    propose_all,
)
from saimc.extraction.llm_checks import (
    COINCIDE,
    DIFIERE,
    compare_with_rules,
    highlight_segments,
    match_rule_rows,
)
from saimc.extraction.llm_propose_ad import (
    AdProposal,
    ad_text,
    filled_fields,
    propose_ad,
    record_operator_run,
)
from saimc.extraction.reference_set import frozen_version_of
from saimc.formats import format_label, format_quantity
from saimc.glossary import active_glossary_entries, validate_rule
from saimc.ingestion import origin
from saimc.ingestion.channel_gaps import GapReport, load_channel_gap_report
from saimc.ingestion.inbox import ingest_inbox
from saimc.llm.provider import LLMConfig, try_resolve_config
from saimc.models import (
    ContentType,
    CurationAction,
    CurationEvent,
    CurationMode,
    Currency,
    DiscardReason,
    GlossaryEntry,
    LlmProposal,
    PriceHistory,
    ProcessingStatus,
    Product,
    ProductFormat,
    ProductVariant,
    ProposalStatus,
    Provenance,
    Province,
    RawIngestion,
    ReviewOutcome,
    SaleFormat,
)
from saimc.models.base import utc_now
from saimc.privacy import redact_personal_data
from saimc.progress import pilot_products
from saimc.units import PACKAGING, UNITS, canonical_packaging, canonical_unit
from saimc.validation import lane0, lane1, persist
from saimc.variants import get_or_create_variant, variant_key
from saimc.web.auth import require_operator
from saimc.web.deps import get_db, get_rate_fetcher

router = APIRouter(dependencies=[Depends(require_operator)])

# `sale_format` es un juicio explícito del operador (prueba de la jabita, ver
# curation.html), nunca inferido: cualquier valor posteado que no sea uno de
# los dos del enum —incluido un envío en blanco— degrada a `None` en vez de
# romper el submit (design.md, D4).
_SALE_FORMAT_VALUES = {member.value for member in SaleFormat}

def _resolve_format_id(
    db: Session, product_id: uuid.UUID, row: dict[str, str]
) -> uuid.UUID | None:
    """Resuelve el triple (envase, cantidad, unidad) de una fila del form a un
    `format_id`, creando el `ProductFormat` si no existe.

    Devuelve `None` cuando el triple está incompleto —falta el envase, la
    cantidad o la unidad, o alguno cae fuera del vocabulario canónico—: la fila
    se guarda con `format_id = NULL`, mismo criterio que `sale_format` en
    blanco. Nunca fabrica un formato a partir de datos parciales.
    """
    return curation_rows.resolve_format_id(
        db,
        product_id,
        packaging=row["packaging"],
        quantity=_parse_decimal(row["quantity"]),
        unit=row["unit"],
    )


def _resolve_variant_id(
    db: Session, product_id: uuid.UUID, row: dict[str, str]
) -> uuid.UUID | None:
    """Resuelve los tres ejes (kind/brand/origin) de una fila del form a un
    `variant_id`, creando el `ProductVariant` si no existe.

    Devuelve `None` cuando los tres ejes quedan vacíos: la fila se guarda con
    `variant_id = NULL`, mismo criterio que `_resolve_format_id`. Nunca
    fabrica una variante a partir de un eje adivinado — sin vocabulario
    cerrado, cualquier texto no vacío es un eje válido (product-variants).
    """
    variante = get_or_create_variant(
        db,
        product_id=product_id,
        kind=row["variant_kind"],
        brand=row["variant_brand"],
        origin=row["variant_origin"],
    )
    return variante.id if variante is not None else None


def _values_from_proposal(proposal: ExtractionProposal | None) -> dict[str, str]:
    """Traduce una `ExtractionProposal` a strings listos para prellenar una fila del form.

    Campos a nivel de FILA únicamente — `original_text` y `source_reference`
    son a nivel de anuncio y viven aparte (ver `curation_queue`).
    """
    if proposal is None:
        return {
            "price_id": "",
            "product_id": "",
            "price": "",
            "currency": Currency.CUP.value,
            "quantity": "",
            "unit": "",
            "sale_format": "",
            "packaging": "",
            # La extracción no propone variante — decisión cerrada
            # (product-variants): declararla es siempre un acto manual.
            "variant_kind": "",
            "variant_brand": "",
            "variant_origin": "",
            "min_purchase": "",
            "province_id": "",
            "price_basis": "",
            "assisted": "",
        }
    return {
        # Sin fila propia todavía: existe recién cuando se confirma el submit.
        "price_id": "",
        # Solo llegan acá propuestas con producto piloto ya resuelto (ver el
        # filtro en `curation_queue`), así que el `else` es defensivo.
        "product_id": str(proposal.product.id) if proposal.product else "",
        "price": str(proposal.price) if proposal.price is not None else "",
        "currency": proposal.currency,
        "quantity": str(proposal.quantity) if proposal.quantity is not None else "",
        "unit": canonical_unit(proposal.unit) or "",
        # Las reglas nunca lo proponen: es juicio del operador (design.md, D4 y
        # "Data Flow"). Desde el slice 3d lo propone el modelo, y llega acá
        # solo por `_rows_from_model` (docs/decisions.md, 2026-09-25).
        "sale_format": proposal.sale_format or "",
        # Mismo caso: las reglas no proponen envase, el modelo sí (3d). Con
        # `quantity`+`unit` de la fila resuelve el formato al confirmar.
        "packaging": proposal.packaging or "",
        # La extracción no propone variante — decisión cerrada
        # (product-variants): declararla es siempre un acto manual.
        "variant_kind": "",
        "variant_brand": "",
        "variant_origin": "",
        # El slice que design.md D7 dejaba pendiente, resuelto solo para la
        # forma que el motor sabe leer sin adivinar: el precio escalonado de
        # un anuncio mayorista («Más de 100 cajas 27,5»), donde el mínimo se
        # deriva del tamaño de lote que el propio anuncio declara. Cualquier
        # otra forma sigue llegando vacía, que es lo correcto: el operador
        # corrige un campo en blanco más rápido que uno mal prellenado.
        "min_purchase": (
            str(proposal.min_purchase_quantity)
            if proposal.min_purchase_quantity is not None
            else ""
        ),
        "province_id": str(proposal.province.id) if proposal.province else "",
        # D5 fijaba no prellenarlo NUNCA, porque un basis equivocado multiplica
        # el precio final por `quantity` en vez de ser cosmético. Ese riesgo
        # sigue siendo cierto y por eso la propuesta llega vacía salvo en un
        # caso: que el propio anuncio declare a cuánto sale la unidad («Sale
        # 515 Cup Lb») **y** la aritmética confirme cuál de las dos lecturas
        # cierra (`_declared_price_basis`). Ahí no se hereda ni se adivina, se
        # lee. Apartamiento registrado en docs/decisions.md, 2026-08-25, con
        # las 7 filas mal marcadas que lo motivaron.
        "price_basis": proposal.price_basis or "",
        # La llena `_curation_view` cuando la fila pasó por el asistente.
        "assisted": "",
    }


def _values_from_price_history(
    price: PriceHistory,
    fmt: ProductFormat | None = None,
    variant: ProductVariant | None = None,
) -> dict[str, str]:
    """Traduce una fila ya curada a los mismos strings que arma una propuesta.

    Mismo formato de salida que `_values_from_proposal` — es lo que permite
    que la corrección reuse la tarjeta de la cola sin distinguir origen. `fmt`
    es el `ProductFormat` de `price.format_id` ya cargado por el llamador (que
    tiene la sesión): se usa para preseleccionar el envase. `variant` es el
    `ProductVariant` de `price.variant_id`, cargado de la misma forma.
    """
    return {
        "price_id": str(price.id),
        "product_id": str(price.product_id),
        "price": str(price.price),
        "currency": price.currency,
        "quantity": str(price.quantity) if price.quantity is not None else "",
        "unit": price.unit or "",
        "sale_format": price.sale_format or "",
        "packaging": fmt.packaging if fmt is not None else "",
        "variant_kind": variant.kind if variant is not None else "",
        "variant_brand": variant.brand if variant is not None else "",
        "variant_origin": variant.origin if variant is not None else "",
        "min_purchase": (
            str(price.min_purchase_quantity)
            if price.min_purchase_quantity is not None
            else ""
        ),
        "province_id": str(price.province_id) if price.province_id else "",
        "price_basis": price.price_basis or "",
    }


# Qué se cura primero (docs/decisions.md, 2026-08-18). Número más bajo, antes
# en la cola; dentro de un mismo nivel sigue mandando `captured_at`.
_FOCUS_MATCH = 0  # el texto menciona un producto del foco: aporta a una celda
_FOCUS_UNKNOWN = 1  # imagen sin OCR: no se sabe sin transcribirla
_FOCUS_NONE = 2  # texto sin ningún producto del foco


def _burst_window() -> timedelta:
    return timedelta(minutes=get_settings().burst_window_minutes)


def _focus_rank(ingestion: RawIngestion, products: Sequence[Product]) -> int:
    """Cuánto aporta esta ingesta a las celdas que se están midiendo.

    "Todavía no sé" (una imagen, cuyo texto solo existe después del OCR) va
    entre "sí aporta" y "no aporta" a propósito: dejarla al final la mata de
    hambre en cuanto entra volumen de WhatsApp, y transcribir la cola entera
    en cada carga de página para saberlo sería pagar el OCR de todo para
    ordenar.

    La ráfaga (2026-08-21) NO toca este ranking, a propósito. Ascender al
    mensaje huérfano porque un vecino nombra un producto del foco se midió
    contra producción y rescataba 18 pendientes de los cuales 1 era el caso
    buscado: en un canal activo, cualquier anuncio de gasolina o de keratina
    cae a menos de diez minutos de uno del foco. Eso anulaba el filtro del
    2026-08-20 sin comprar casi nada. El huérfano se resuelve por el otro
    lado: se muestra como contexto del anuncio que sí entra (`_burst_context`),
    donde no le cuesta un clic al operador.
    """
    if ingestion.original_text is None:
        return _FOCUS_UNKNOWN
    if mentions_any_product(ingestion.original_text, products):
        return _FOCUS_MATCH
    return _FOCUS_NONE


# Emoji a pedido del operador (2026-09-07): en una lista larga ubica más rápido
# un glifo con color propio que una ligadura monocroma.
#
# UNO por fila, no un racimo. La primera versión apilaba hasta cuatro y el
# operador la rechazó por ilegible: cuatro glifos multicolor en una columna
# angosta son ruido, no información. El glifo dice de qué CLASE es la fila
# —las tres son mutuamente excluyentes porque salen de `_focus_rank`— y es lo
# único que se escanea verticalmente.
_FOCUS_MARKS = {
    _FOCUS_MATCH: ("🎯", "en foco"),
    _FOCUS_UNKNOWN: ("🖼️", "imagen sin transcribir"),
    _FOCUS_NONE: ("🚫", "fuera de foco"),
}

# Las señales secundarias NO son glifos sueltos: van con su palabra en un
# `m3-badge`, que es como el sistema ya nombra un estado. «📦 mayorista» se lee;
# un 📦 solo hay que ir a buscarlo a una leyenda.
_FLAG_OUT_OF_STOCK = ("❌", "agotado", "m3-badge-warn")
_FLAG_WHOLESALE = ("📦", "mayorista", "m3-badge-neutral")

#: Todo el vocabulario de emoji de la cola, en un solo lugar. `_FOCUS_MARKS` y
#: los `_FLAG_*` son la fuente; `🗺️` es el enlace de entrada en `curation.html`
#: y vive acá igual porque `tools/build_emoji_font.py` subsetea Noto Color Emoji
#: a exactamente esta tupla, y `tests/test_static_assets.py` verifica que ni el
#: manifiesto ni las plantillas se salgan de acá. Un emoji nuevo se agrega
#: primero a esta tupla; si no, el tripwire se pone rojo.
QUEUE_EMOJI: tuple[str, ...] = (
    *(glifo for glifo, _ in _FOCUS_MARKS.values()),
    _FLAG_OUT_OF_STOCK[0],
    _FLAG_WHOLESALE[0],
    "🗺️",
)

#: Valores de `?foco=` en `/curation/cola`. Ausente = no filtra.
_FOCUS_FILTERS = {"si": _FOCUS_MATCH, "imagen": _FOCUS_UNKNOWN, "no": _FOCUS_NONE}


@dataclass(frozen=True)
class QueueEntry:
    """Una fila de `/curation/cola`: el anuncio más lo que el motor ve en él.

    Se arma en el router y no en la plantilla porque decidir qué marcas
    aplican es lógica, no presentación: la plantilla sólo las imprime.
    """

    ingestion: RawIngestion
    position: int
    focus: int
    mark: str
    mark_label: str
    flags: tuple[tuple[str, str, str], ...]
    preview: str
    products: tuple[str, ...]
    row_count: int
    priced_count: int

    @property
    def is_image_without_text(self) -> bool:
        return self.focus == _FOCUS_UNKNOWN


def _requested_pending(db: Session, focus_id: uuid.UUID | None) -> RawIngestion | None:
    """El pendiente que `?id=` pide, o `None` si no sirve.

    Revalida `PENDING` en vez de confiar en el id: entre que la vista de cola
    se renderizó y el operador hizo clic, ese anuncio pudo curarse o
    descartarse en otra pestaña. Devolver `None` deja que el llamador caiga al
    primero de la cola, que es lo que el operador quería hacer igual.
    """
    if focus_id is None:
        return None
    ingestion = db.get(RawIngestion, focus_id)
    if ingestion is None or ingestion.processing_status != ProcessingStatus.PENDING:
        return None
    return ingestion


def _pending_all(db: Session) -> list[RawIngestion]:
    """Todos los pendientes, más nuevo primero. Sin filtro de foco.

    El operador publica con lo de las últimas 24 h: lo de hoy va arriba y el
    histórico espera abajo (2026-10-13).

    `_pending_in_focus` esconde lo que no menciona un piloto, que es correcto
    para curar de a uno pero deja al operador sin forma de ver qué hay en la
    cola. Esta es la lista completa, y el foco pasa a ser una columna que se
    filtra en vez de un filtro implícito (docs/decisions.md, 2026-09-07).
    """
    stmt = (
        select(RawIngestion)
        .where(RawIngestion.processing_status == ProcessingStatus.PENDING)
        .order_by(RawIngestion.captured_at.desc())
    )
    return list(db.execute(stmt).scalars().all())


def _queue_entry(
    ingestion: RawIngestion,
    position: int,
    products: Sequence[Product],
    provinces: Sequence[Province],
) -> QueueEntry:
    """Arma la fila de listado de un pendiente, SIN correr OCR.

    Transcribir una imagen cuesta segundos y la cola tiene cientos: hacerlo
    acá pagaría el OCR de toda la cola en cada carga de página, que es el
    mismo motivo por el que `_focus_rank` deja `_FOCUS_UNKNOWN` en vez de
    resolverlo. Una imagen sin texto se lista con su marca y se resuelve
    abriéndola.
    """
    focus = _focus_rank(ingestion, products)
    texto = ingestion.original_text or ""
    mark, mark_label = _FOCUS_MARKS[focus]

    proposals: list[ExtractionProposal] = []
    nombres: list[str] = []
    if texto:
        proposals = propose_all(texto, products, provinces)
        # El nombre del catálogo cuando el motor lo reconoció; si no, el
        # candidato que propondría dar de alta. Sin repetir y en orden de
        # aparición, que es como el operador lee el anuncio.
        for p in proposals:
            nombre = p.product.name if p.product else p.product_name_guess
            if nombre and nombre not in nombres:
                nombres.append(nombre)

    # «Sacó precio» no lleva glifo: la columna de conteo ya dice `2/3`, que es
    # el mismo dato con más precisión y sin sumar un color a la fila.
    flags: list[tuple[str, str, str]] = []
    if texto:
        razones = probable_discard_reasons(texto, products)
        if "parece indicar agotado" in razones:
            flags.append(_FLAG_OUT_OF_STOCK)
        if "parece venta mayorista/por volumen" in razones:
            flags.append(_FLAG_WHOLESALE)

    primera = next((ln.strip() for ln in texto.splitlines() if ln.strip()), "")
    return QueueEntry(
        ingestion=ingestion,
        position=position,
        focus=focus,
        mark=mark,
        mark_label=mark_label,
        flags=tuple(flags),
        preview=primera,
        products=tuple(nombres),
        row_count=len(proposals),
        priced_count=sum(1 for p in proposals if p.price is not None),
    )


def _pending_in_focus(db: Session, products: Sequence[Product]) -> list[RawIngestion]:
    """Los pendientes que el operador va a ver, en orden de curaduría.

    Hasta el 2026-08-18 el foco solo ordenaba: un anuncio de cemento seguía
    en la cola, al final. Con volumen real de WhatsApp eso no alcanzó — los
    negocios publican saludos, alertas de clima, links y ofertas de productos
    ajenos, y todo eso llegaba igual a la cola (`docs/decisions.md`,
    2026-08-20). Ahora el texto que no menciona ningún producto del foco no se
    muestra: es el mismo criterio que 2026-08-15 ya aplicaba a las filas del
    formulario, subido al nivel del anuncio entero.

    Esconder no es descartar. La fila sigue `PENDING` y su relevancia se
    recalcula en cada carga, así que un producto que entra al foco (`is_pilot`
    es un clic en /catalog) hace reaparecer sus anuncios sin reprocesar nada —
    por eso la relevancia no se congela en una columna al ingerir.

    `_FOCUS_UNKNOWN` (imagen sin OCR) sigue entrando: su relevancia es
    desconocida, no negativa, y descubrirla exigiría transcribir la cola
    entera en cada carga de página.
    """
    stmt = (
        select(RawIngestion)
        .where(RawIngestion.processing_status == ProcessingStatus.PENDING)
        .order_by(RawIngestion.captured_at.desc())
    )
    pending = list(db.execute(stmt).scalars().all())
    # Sin foco definido no hay relevancia que medir: filtrar contra un catálogo
    # vacío escondería la cola entera, que es peor que el ruido que el filtro
    # existe para sacar. Pasa de verdad — al arrancar, o si el operador
    # desmarca el último piloto en /catalog.
    if not products:
        return pending
    ranked = [(ingestion, _focus_rank(ingestion, products)) for ingestion in pending]
    # `sorted` es estable y la consulta ya vino ordenada por `captured_at`:
    # eso sale gratis el desempate por antigüedad dentro de cada nivel.
    return [
        ingestion
        for ingestion, rank in sorted(ranked, key=lambda par: par[1])
        if rank != _FOCUS_NONE
    ]


# Orden secundario dentro de cada nivel de foco (slice 4,
# docs/plan-asistente-llm-cola-completa.md, 2026-09-27): primero lo
# coincidente, al final lo dudoso o en desacuerdo.
_AGREEMENT_MATCH = 0
_AGREEMENT_NEUTRAL = 1
_AGREEMENT_DIFFERS = 2


def _latest_ok_proposals(
    db: Session, ingestion_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, LlmProposal]:
    """La última propuesta OK de cada id, en una sola consulta.

    Mismo criterio que `llm_store.latest_ok_proposal`, pero para toda la cola
    de una vez: llamarla pendiente por pendiente pagaría una consulta por fila.
    """
    if not ingestion_ids:
        return {}
    stmt = (
        select(LlmProposal)
        .where(
            LlmProposal.ingestion_id.in_(ingestion_ids),
            LlmProposal.status == ProposalStatus.OK,
        )
        .order_by(LlmProposal.created_at.asc())
    )
    latest: dict[uuid.UUID, LlmProposal] = {}
    for proposal in db.execute(stmt).scalars():
        latest[proposal.ingestion_id] = proposal  # el último pisa: ya viene ASC
    return latest


def _agreement_rank(
    db: Session,
    ingestion: RawIngestion,
    focus: int,
    proposal: LlmProposal | None,
    products: Sequence[Product],
    provinces: Sequence[Province],
) -> int:
    """Dónde cae este pendiente en el orden por acuerdo (0 mejor, 2 peor).

    Una imagen (`_FOCUS_UNKNOWN`) se ordena solo por el ESTADO de su propuesta
    guardada —con filas, sugiere descartar, o ninguna de las dos—, nunca por
    acuerdo con las reglas: calcularlo exigiría transcribirla acá mismo, que es
    justo lo que `_queue_entry` y `_focus_rank` evitan a propósito. El texto sí
    compara contra las reglas, con el mismo `propose_all` + `compare_with_rules`
    que ya usa el formulario.
    """
    if focus == _FOCUS_UNKNOWN:
        if proposal is None:
            return _AGREEMENT_DIFFERS
        if proposal.discard or not proposal.rows:
            return _AGREEMENT_NEUTRAL
        return _AGREEMENT_MATCH

    if proposal is None:
        return _AGREEMENT_NEUTRAL
    ad_proposal, _ = llm_store.to_ad_proposal(db, proposal)
    if ad_proposal.discard or not ad_proposal.rows:
        return _AGREEMENT_NEUTRAL
    rule_rows = propose_all(ingestion.original_text or "", products, provinces)
    statuses = {a.status for a in compare_with_rules(ad_proposal.rows, rule_rows)}
    if DIFIERE in statuses:
        return _AGREEMENT_DIFFERS
    if COINCIDE in statuses:
        return _AGREEMENT_MATCH
    return _AGREEMENT_NEUTRAL


def _order_by_agreement(
    db: Session,
    pending: Sequence[RawIngestion],
    products: Sequence[Product],
    provinces: Sequence[Province],
) -> list[RawIngestion]:
    """Reordena `pending` (ya agrupado por foco) por acuerdo con las reglas.

    Solo reordena DENTRO de cada nivel de foco: `sorted` es estable y el nivel
    de foco de cada ingesta no cambia, así que el orden por antigüedad de
    `_pending_in_focus` se conserva como desempate.
    """
    focus_by_id = {ingestion.id: _focus_rank(ingestion, products) for ingestion in pending}
    proposals = _latest_ok_proposals(db, list(focus_by_id))
    return sorted(
        pending,
        key=lambda ingestion: (
            focus_by_id[ingestion.id],
            _agreement_rank(
                db,
                ingestion,
                focus_by_id[ingestion.id],
                proposals.get(ingestion.id),
                products,
                provinces,
            ),
        ),
    )


def _pending_out_of_focus(db: Session, products: Sequence[Product]) -> list[RawIngestion]:
    """Los pendientes SIN ningún producto del foco — la contracara exacta de
    `_pending_in_focus`, acotada al descarte en lote (`docs/decisions.md`,
    2026-08-31).

    Filtra por `_FOCUS_NONE` únicamente, nunca por `content_type`: lo que
    importa es que se haya CONFIRMADO que ningún renglón del texto menciona
    un piloto, así que no hay precio real que perder al descartar en bloque.
    `_FOCUS_UNKNOWN` (imagen sin OCR) queda afuera a propósito — su relevancia
    es desconocida, no negativa, y confundirla con "sin foco" descartaría
    imágenes que nadie llegó a mirar.

    Sin catálogo piloto no hay señal que confirmar: devuelve vacío en vez de
    tratar la cola entera como "sin foco". Es la guarda inversa de
    `_pending_in_focus` (ahí "sin foco definido" no filtra nada, para no
    esconder de más; acá no afirma nada, para no descartar de más — afirmar
    "sin foco" sin foco definido sería inventar la señal, no leerla).
    """
    if not products:
        return []
    stmt = (
        select(RawIngestion)
        .where(RawIngestion.processing_status == ProcessingStatus.PENDING)
        .order_by(RawIngestion.captured_at.asc())
    )
    pending = list(db.execute(stmt).scalars().all())
    # Lo rescatado de la auditoría del carril 0 (B2a) no vuelve al lote: se
    # cura de a uno («Curar ahora» en /curation/auditoria).
    rescued = lane0.rescued_ids(db, [ingestion.id for ingestion in pending])
    return [
        ingestion
        for ingestion in pending
        if _focus_rank(ingestion, products) == _FOCUS_NONE and ingestion.id not in rescued
    ]


def _burst_context(db: Session, ingestion: RawIngestion) -> list[RawIngestion]:
    """Los mensajes que rodean al que se está curando, en orden cronológico.

    El vendedor parte el anuncio en varios mensajes seguidos: "Para mañana
    entrará aceite de 900ml" y, cuatro minutos y medio después, "A 4700".
    Curar el primero sin ver el segundo es guardar un anuncio sin precio —
    o descartarlo por creerlo inútil, que es lo que pasó con ese par real
    (docs/decisions.md, 2026-08-21).

    A diferencia del ranking, acá NO se filtra por estado: que el vecino ya
    esté curado o descartado no lo borra del anuncio, y leerlo sigue siendo lo
    que explica el mensaje que el operador tiene delante.

    Una consulta, y solo para la cabeza de la cola — no para los cientos de
    pendientes que `_pending_in_focus` acaba de ordenar.
    """
    if ingestion.channel_id is None:
        return []
    window = _burst_window()
    stmt = (
        select(RawIngestion)
        .where(
            RawIngestion.channel_id == ingestion.channel_id,
            RawIngestion.id != ingestion.id,
            RawIngestion.captured_at >= ingestion.captured_at - window,
            RawIngestion.captured_at <= ingestion.captured_at + window,
        )
        .order_by(RawIngestion.captured_at.asc())
    )
    return list(db.execute(stmt).scalars().all())


def _channel_province_id(db: Session, ingestion: RawIngestion) -> str | None:
    """La provincia fija del canal, como id de formulario.

    La regla en sí ("el canal siempre manda", docs/decisions.md 2026-08-17)
    vive en `saimc.ingestion.origin`, junto con la resolución del canal de
    origen. Se mudó allá el 2026-08-21 porque la medición de H0 necesita
    aplicar exactamente la misma regla y no puede importar de un router web.
    Acá queda solo la conversión a string que el `<select>` de la plantilla
    necesita.
    """
    province = origin.channel_province(db, ingestion)
    return str(province.id) if province is not None else None


def _channel_discard_stats(db: Session, ingestion: RawIngestion) -> tuple[int, int] | None:
    """Cuántos de los anuncios ya resueltos de este canal se descartaron.

    Puramente informativo (docs/decisions.md, 2026-08-22): el operador ya
    conoce sus canales ruidosos, esto solo se lo confirma con un número en
    vez de exigirle que lo recuerde. No dispara ningún automatismo — no hay
    umbral de corte, `spec-map.md` marca los umbrales nuevos sin valor
    asignado como "no inventar" y acá no hace falta ninguno.
    """
    if ingestion.channel_id is None:
        return None
    stmt = select(RawIngestion.processing_status, func.count(RawIngestion.id)).where(
        RawIngestion.channel_id == ingestion.channel_id,
        RawIngestion.processing_status != ProcessingStatus.PENDING,
    ).group_by(RawIngestion.processing_status)
    counts = dict(db.execute(stmt).all())
    total = sum(counts.values())
    if total == 0:
        return None
    return (counts.get(ProcessingStatus.DISCARDED, 0), total)


def _pilot_products(db: Session) -> list[Product]:
    """Los productos del conjunto foco, y solo esos.

    Único punto por donde el catálogo entra a la curaduría: alimenta las
    propuestas del motor, el `<select>` de cada fila y la resolución por
    nombre. Filtrar acá restringe las tres cosas a la vez, que es lo que hace
    del foco una propiedad de la herramienta y no una intención del operador
    (`docs/decisions.md`, 2026-08-15).

    Un producto fuera del foco sigue en el catálogo y conserva sus precios
    históricos; lo que no puede es recibir precios nuevos. Volver a incluirlo
    es un clic en /catalog, no una migración.

    La consulta vive en `saimc.progress.pilot_products` y no acá: la medición
    de H0 necesita el mismo catálogo, y tenerlo escrito dos veces fue
    exactamente el defecto del 2026-09-09 (`docs/decisions.md`).
    """
    return pilot_products(db)


def _variant_options(db: Session) -> dict[str, dict[str, list[str]]]:
    """Valores de variante ya declarados, por producto y por eje.

    Alimenta el `<datalist>` de cada eje en `_price_row_card.html`: una vez
    que un producto tiene un valor declarado, el próximo se elige en vez de
    retipearse (product-variants — "pick, don't type"). Sin vocabulario
    cerrado: esto no filtra nada, solo ofrece lo que ya existe.
    """
    opciones: dict[str, dict[str, list[str]]] = {}
    variantes = db.execute(select(ProductVariant).order_by(ProductVariant.kind)).scalars()
    for variante in variantes:
        por_producto = opciones.setdefault(
            str(variante.product_id), {"kind": [], "brand": [], "origin": []}
        )
        for eje, valor in (
            ("kind", variante.kind),
            ("brand", variante.brand),
            ("origin", variante.origin),
        ):
            if valor and valor not in por_producto[eje]:
                por_producto[eje].append(valor)
    return opciones


def _format_options(db: Session) -> dict[str, list[dict[str, str]]]:
    """Formatos ya declarados en `/catalog`, por producto.

    Espejo de `_variant_options`, con un nivel menos: el formato es un triple
    indivisible, así que cada opción viaja entera y el `<select>` de
    `_price_row_card.html` la copia a `packaging_N`/`quantity_N`/`unit_N`.

    Nada de esto llega al POST: el select no tiene `name` y la resolución sigue
    siendo `_resolve_format_id` sobre los tres campos de siempre. Lo que evita
    es que el operador rearme a mano un formato que ya existe y tipee «0.9 L»
    donde el catálogo dice «900 ml» — dos `ProductFormat` para el mismo objeto
    de mercado, que es la fragmentación que el eje de formatos vino a cerrar.
    """
    opciones: dict[str, list[dict[str, str]]] = {}
    formatos = db.execute(
        select(ProductFormat).order_by(ProductFormat.packaging, ProductFormat.quantity)
    ).scalars()
    for formato in formatos:
        opciones.setdefault(str(formato.product_id), []).append(
            {
                "label": format_label(formato.packaging, formato.quantity, formato.unit),
                "packaging": formato.packaging,
                "quantity": format_quantity(formato.quantity),
                "unit": formato.unit,
            }
        )
    return opciones


def _all_provinces(db: Session) -> list[Province]:
    return list(db.execute(select(Province).order_by(Province.name)).scalars().all())


def _active_glossary(db: Session) -> list[GlossaryEntry]:
    return active_glossary_entries(db)


def _status_counts(db: Session, products: Sequence[Product]) -> dict[str, int]:
    """Cuántos elementos hay por estado — para que el operador sepa cuánto
    le queda de la cola mientras cura, sin tener que consultar la base a mano.

    `pending` cuenta lo que se va a mostrar, no lo que hay en la tabla, y el
    resto va aparte en `out_of_focus`: desde que la cola esconde el texto
    ajeno al foco (`docs/decisions.md`, 2026-08-20), un `pending` crudo
    anunciaría cuarenta anuncios sobre una cola que se vacía en el tercero.
    Declarar los escondidos es lo que separa "no entró nada" de "entró ruido".
    """
    stmt = select(RawIngestion.processing_status, func.count(RawIngestion.id)).group_by(
        RawIngestion.processing_status
    )
    counts = dict(db.execute(stmt).all())
    resultado = {status.value: counts.get(status, 0) for status in ProcessingStatus}
    en_foco = len(_pending_in_focus(db, products))
    resultado["out_of_focus"] = resultado[ProcessingStatus.PENDING.value] - en_foco
    resultado[ProcessingStatus.PENDING.value] = en_foco
    return resultado


def _last_capture_notice(db: Session, *, now: datetime | None = None) -> str | None:
    """Hace cuánto entró la ingesta más reciente, en cualquier estado.

    Medición, no veredicto — mismo criterio que `age_days` en
    `exchange_rate/status.py` (§12.6: sin ventana de vigencia asignada, no se
    inventa). Esto no marca nada como "atrasado", solo informa la antigüedad;
    el operador es quien decide si eso es normal. Existe porque el bache de
    captura del 2026-08-23 (WAHA deslogueado remotamente) se leyó al
    principio como "cola vacía" por foco, cuando el problema real era que no
    entraba nada nuevo desde hacía más de un día — nada en la pantalla lo
    decía.
    """
    latest = db.execute(select(func.max(RawIngestion.captured_at))).scalar()
    if latest is None:
        return None
    # SQLite (solo en tests, ver tests/conftest.py) no conserva el offset en
    # el viaje de ida y vuelta pese a `DateTime(timezone=True)` — PostgreSQL sí.
    # La columna es UTC por contrato (docstring del modelo), así que asumirlo
    # acá es fiel al dato, no una adivinanza.
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=UTC)
    hours = int(((now or datetime.now(UTC)) - latest).total_seconds() // 3600)
    if hours < 1:
        return "Última captura: hace menos de una hora."
    if hours < 24:
        return f"Última captura: hace {hours} hora{'s' if hours != 1 else ''}."
    days = hours // 24
    return f"Última captura: hace {days} día{'s' if days != 1 else ''}."


def _unconverted_notice(unconverted: int) -> str | None:
    """Copia del aviso no bloqueante (design.md D7). `None` cuando no hay
    nada que avisar — el template lo omite con un simple `{% if notice %}`.
    """
    if unconverted <= 0:
        return None
    return (
        f"Guardado sin convertir: {unconverted} fila(s) en moneda extranjera "
        "quedaron sin tasa de cambio. Se pueden completar más tarde con el backfill."
    )


def _render_queue(
    request: Request,
    *,
    ingestion: RawIngestion | None,
    products: list[Product],
    provinces: list[Province],
    rows: list[dict[str, str]],
    row_count: int,
    global_values: dict[str, str],
    error: str | None,
    status_counts: dict[str, int],
    variant_options: dict[str, dict[str, list[str]]],
    format_options: dict[str, list[dict[str, str]]],
    blank_row: dict[str, str] | None = None,
    status_code: int = 200,
    unconverted: int = 0,
    pasted: int = 0,
    no_candidates: bool = False,
    burst: Sequence[RawIngestion] = (),
    discard_hints: Sequence[str] = (),
    channel_discard_stats: tuple[int, int] | None = None,
    last_capture_notice: str | None = None,
    gap_report: GapReport | None = None,
    assist_available: bool = False,
    assisted_map: dict[str, list[str]] | None = None,
    assist_error: str | None = None,
    assist_discard: dict | None = None,
    evidence_segments: list[tuple[str, bool]] | None = None,
    proposal_id: str | None = None,
    note: str = "",
    rule_term: str = "",
    rule_definition: str = "",
    rule_added: str = "",
    accept_ready: bool = False,
    shown_at: str | None = None,
) -> HTMLResponse:
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "curation.html",
        {
            "ingestion": ingestion,
            "products": products,
            "provinces": provinces,
            "rows": rows,
            "row_count": row_count,
            "assist_available": assist_available,
            "assisted_map": assisted_map or {},
            # «Aceptar» en vez de «Curar anuncio», con atajo Ctrl+Enter: solo
            # cuando hay una propuesta del modelo a la vista (slice 4).
            "accept_ready": accept_ready,
            # Motivo legible de que el asistente no respondió (o `None`).
            "assist_error": assist_error,
            # Sugerencia de descarte del modelo: motivo y botones (o `None`).
            "assist_discard": assist_discard,
            # El texto del anuncio en tramos, con las citas del modelo
            # marcadas; `None` = mostrarlo tal cual.
            "evidence_segments": evidence_segments,
            # La propuesta guardada a la vista (slice 3a) y la nota del
            # operador. Sin propuesta no hay oculto ni campo de nota.
            "proposal_id": proposal_id,
            "note": note,
            # A3c: lo que el operador escribió en «Guardar como regla» (se
            # conserva si el guardado falla) y el término recién agregado.
            "rule_term": rule_term,
            "rule_definition": rule_definition,
            "rule_added": rule_added,
            # Cuándo se le mostró esta fila al operador (slice 5). `None` en
            # el reintento con error de validación: ese caso no vuelve a
            # calcular "ahora", conserva lo que ya venía (ver más abajo).
            "shown_at": shown_at,
            # Molde para las filas que el operador agregue a mano desde el
            # botón "Agregar fila": el JS del cliente clona este `<template>`
            # y le renumera los índices. Mismo dict vacío que las filas en
            # blanco de más, así que la macro `price_row_card` lo renderiza
            # igual. Cae a un dict vacío en el POST con error de validación,
            # donde el operador corrige lo que ya escribió.
            "blank_row": blank_row or _values_from_proposal(None),
            "values": global_values,
            "error": error,
            "notice": _unconverted_notice(unconverted),
            "last_capture_notice": last_capture_notice,
            "gap_report": gap_report,
            "pasted": pasted,
            "currencies": [c.value for c in Currency],
            "units": UNITS,
            "packaging": PACKAGING,
            "variant_options": variant_options,
            "format_options": format_options,
            "status_counts": status_counts,
            # Solo la vuelve True el GET normal (docs/decisions.md,
            # 2026-08-17): tras un error de validación en el POST (segundo
            # llamador de esta función) el operador está corrigiendo algo que
            # SÍ escribió, mostrarle acá "sin candidatos, descartar" sería
            # confuso — ese llamador nunca pasa este parámetro, usa el
            # default.
            "no_candidates": no_candidates,
            # Los mensajes vecinos del mismo canal (docs/decisions.md,
            # 2026-08-21). Vacío para una captura suelta de la bandeja, que no
            # tiene canal, y para el POST con error de validación: ahí el
            # operador está corrigiendo lo que ya escribió, no leyendo el
            # anuncio por primera vez.
            "burst": burst,
            # Sugerencia asistida de descarte (docs/decisions.md, 2026-08-22):
            # razones calculadas sobre el texto disponible (original o el que
            # salió del OCR), nunca vacío por defecto en el POST con error —
            # el operador está corrigiendo algo que escribió, no leyendo el
            # anuncio por primera vez, mismo criterio que `burst` arriba.
            "discard_hints": discard_hints,
            "channel_discard_stats": channel_discard_stats,
        },
        status_code=status_code,
    )


# Filas en blanco de más que se ofrecen siempre, además de las prellenadas con
# candidatos del motor: el operador carga productos que `propose_all` no detectó
# sin tener que recargar la página (ver diseño del slice que reemplaza
# "guardar y agregar otro").
_EXTRA_BLANK_ROWS = 3


# Separador entre anuncios en el pegado rápido: una línea que sea solo guiones.
# Tolerante a propósito (espacios alrededor, tres guiones o siete): se pega
# desde el teléfono, y exigir una forma exacta convertiría la vía rápida en una
# fuente de errores silenciosos.
_PASTE_SEPARATOR = re.compile(r"^\s*-{3,}\s*$", re.MULTILINE)


def _split_pasted(pasted: str) -> list[str]:
    """Parte el pegado en anuncios y descarta los bloques vacíos.

    Un anuncio por bloque, porque el registro crudo es la unidad de curaduría:
    pegar tres anuncios como uno solo los volvería un único elemento de la cola
    y perdería la trazabilidad de cada precio a su anuncio (spec.md §6.5).
    """
    return [bloque.strip() for bloque in _PASTE_SEPARATOR.split(pasted) if bloque.strip()]


@router.post("/curation/paste")
async def paste_ads(request: Request, db: Session = Depends(get_db)) -> RedirectResponse:
    """Entrada rápida: pegar texto de anuncios sin pasar por un archivo.

    Existe por la medición del 2026-08-15: el 98% del material capturado eran
    imágenes, no por preferencia sino porque desde el teléfono una captura se
    sincroniza sola mientras que meter texto exigía abrir un editor, crear un
    `.txt` y guardarlo en la carpeta sincronizada. No había dónde pegar, y un
    registro de texto rinde varias veces más precios que uno de imagen.

    No es un camino de ingesta paralelo: escribe cada bloque como archivo en la
    bandeja y llama al mismo `ingest_inbox`, así que hereda tal cual el hash de
    contenido, la deduplicación, la redacción de datos personales y el archivo
    de evidencia en el almacén. Una vía rápida con menos garantías que la
    bandeja sería exactamente el agujero que §10.6 prohíbe.
    """
    form = await request.form()
    bloques = _split_pasted(str(form.get("pasted", "")))
    if not bloques:
        return RedirectResponse("/curation", status_code=status.HTTP_303_SEE_OTHER)

    settings = get_settings()
    settings.inbox_dir.mkdir(parents=True, exist_ok=True)

    temporales: list[Path] = []
    for bloque in bloques:
        # Nombre irrelevante: `ingest_file` renombra al hash del contenido al
        # moverlo al almacén. El `.txt` es lo que hace que `classify()` lo
        # reconozca como texto.
        destino = settings.inbox_dir / f"pegado-{uuid.uuid4().hex}.txt"
        destino.write_text(bloque, encoding="utf-8")
        temporales.append(destino)

    try:
        report = ingest_inbox(db, settings.inbox_dir, settings.raw_store_dir, paths=temporales)
    finally:
        # Los duplicados y los salteados no se mueven al almacén: sin esta
        # limpieza el temporal quedaría en la bandeja y volvería a entrar en el
        # próximo GET /curation.
        for temporal in temporales:
            temporal.unlink(missing_ok=True)

    return RedirectResponse(
        f"/curation?pasted={len(report.ingested)}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/curation/cola", response_class=HTMLResponse)
def curation_overview(
    request: Request,
    db: Session = Depends(get_db),
    foco: str | None = None,
    tipo: str | None = None,
    q: str | None = None,
    producto: str | None = None,
) -> HTMLResponse:
    """El mapa de la cola: TODOS los pendientes, con filtros.

    Existe porque `/curation` muestra un pendiente por vez —el más antiguo en
    foco— y no había forma de ver qué más hay ni de abrir uno concreto. El
    operador no podía verificar un cambio del extractor contra un anuncio
    puntual sin curar toda la cola hasta llegar a él (docs/decisions.md,
    2026-09-07).

    No corre OCR: las imágenes sin transcribir se listan con su marca y su
    contenido se resuelve al abrirlas. Ver `_queue_entry` para el motivo.

    Sin paginación, mismo criterio que `/curation/history`: al volumen de
    Fase 0 la cola entra en una página, y paginar antes de tener el problema
    es complejidad sin uso (spec.md §12.6).
    """
    products = _pilot_products(db)
    provinces = _all_provinces(db)

    # La posición se numera sobre la cola COMPLETA y antes de filtrar, para
    # que sea estable: si dependiera del filtro activo, «el #3» significaría
    # algo distinto en cada vista y dejaría de servir para ubicarse.
    todas = [
        _queue_entry(ingestion, posicion, products, provinces)
        for posicion, ingestion in enumerate(_pending_all(db))
    ]
    entradas = todas

    # Contadores por foco, sobre la cola completa. Medido el 2026-09-07: de
    # 284 pendientes, 201 son imágenes sin transcribir, así que la vista sin
    # filtro abre en una pared de filas sin nada que leer. Los contadores son
    # atajos de un clic a los tres subconjuntos, y de paso dicen la forma de
    # la cola antes de que el operador filtre nada.
    counts = {
        "total": len(todas),
        "si": sum(1 for e in todas if e.focus == _FOCUS_MATCH),
        "imagen": sum(1 for e in todas if e.focus == _FOCUS_UNKNOWN),
        "no": sum(1 for e in todas if e.focus == _FOCUS_NONE),
    }

    if foco in _FOCUS_FILTERS:
        entradas = [e for e in entradas if e.focus == _FOCUS_FILTERS[foco]]
    if tipo in (ContentType.TEXT, ContentType.IMAGE):
        entradas = [e for e in entradas if e.ingestion.content_type == tipo]
    if q:
        aguja = q.strip().lower()
        entradas = [
            e for e in entradas if aguja in (e.ingestion.original_text or "").lower()
        ]
    if producto:
        objetivo = producto.strip().lower()
        entradas = [
            e for e in entradas if any(objetivo in n.lower() for n in e.products)
        ]

    # Los glifos de las pestañas salen del mismo `_FOCUS_MARKS` que la columna
    # de cada fila: sin esto la plantilla los repetía a mano y podían divergir.
    focus_marks = {
        clave: _FOCUS_MARKS[rango][0] for clave, rango in _FOCUS_FILTERS.items()
    }

    return request.app.state.templates.TemplateResponse(
        request,
        "curation_cola.html",
        {
            "entries": entradas,
            "counts": counts,
            "focus_marks": focus_marks,
            "filters": {
                "foco": foco or "",
                "tipo": tipo or "",
                "q": q or "",
                "producto": producto or "",
            },
            "pilots": [p.name for p in products],
        },
    )


@router.get("/curation", response_class=HTMLResponse)
def curation_queue(
    request: Request,
    db: Session = Depends(get_db),
    unconverted: int = 0,
    pasted: int = 0,
    id: str | None = None,
    rule_added: str = "",
) -> HTMLResponse:
    """Muestra el elemento `pending` más antiguo, con propuesta pre-llenada si hay texto.

    Para imágenes sin `original_text` (el caso normal: el OCR no corre en la
    ingesta), se transcribe acá mismo para tener un texto candidato con el que
    correr `propose_all()` — el mismo camino que ya usan los ítems de texto. El
    operador ve ese texto en un campo editable y lo confirma o corrige al curar.

    Un anuncio puede ofrecer varios productos (`propose_all` devuelve un
    candidato por línea). Se muestra una fila por candidato, más un puñado de
    filas en blanco para que el operador agregue productos que el motor no
    detectó — todo en una sola tabla que se manda con un único submit
    (reemplaza el flujo anterior de "guardar y agregar otro", tedioso contra
    anuncios reales de varios productos: ver docs/decisions.md).

    Antes de leer la cola, ingesta lo que haya en `inbox/` — reemplaza el paso
    manual de correr `python -m saimc.ingestion` (docs/decisions.md). El
    operador ya abre esta página una vez al día para curar en bloque; no hace
    falta un timer aparte para un volumen de Fase 0.

    `?id=` abre un pendiente puntual en vez del primero de la cola: es el
    salto desde `/curation/cola` (2026-09-07). Acepta cualquier pendiente,
    también uno fuera de foco — el punto de la vista de cola es justamente
    poder mirar lo que el foco esconde. Un id que no existe, que ya no está
    `pending` o que no es un UUID cae al comportamiento normal en vez de dar
    404: el operador llegó acá para curar, y la cola siempre tiene algo mejor
    que un error (el mismo criterio de degradar sin romper que usa
    `discard_out_of_focus` cuando un id del lote ya no califica).
    """
    return _curation_view(
        request,
        db,
        unconverted=unconverted,
        pasted=pasted,
        focus_id=_parse_uuid(id or ""),
        rule_added=rule_added,
    )


def _guess_published_date(ingestion: RawIngestion, text: str) -> date | None:
    """Fecha de publicación que se le prellena al operador: la misma que usa
    el carril 1 (`curation_rows.default_published_date`)."""
    return curation_rows.default_published_date(ingestion, text)


def _curation_view(
    request: Request,
    db: Session,
    *,
    unconverted: int = 0,
    pasted: int = 0,
    rule_added: str = "",
    assist_ingestion_id: uuid.UUID | None = None,
    focus_id: uuid.UUID | None = None,
    shown_at: str | None = None,
) -> HTMLResponse:
    """Arma la vista de la cola. Compartida por el GET y por el endpoint de
    asistencia, que es el mismo render con la propuesta del modelo ya
    consultada — así el botón no puede divergir de lo que la página muestra.

    `focus_id` fija cuál pendiente se muestra; sin él, el primero en foco.
    `shown_at` es el reloj de la primera vista cuando «Completar con IA»
    vuelve a renderizar: se conserva en vez de reiniciarse, porque la espera
    del modelo es tiempo del operador frente al anuncio (A2a).
    """
    settings = get_settings()
    # `None` = sin asistente configurado (proveedor sin clave): la cola
    # funciona igual, a mano, y el botón no se ofrece.
    llm = try_resolve_config(settings)
    ingest_inbox(db, settings.inbox_dir, settings.raw_store_dir)

    # El catálogo del foco se lee antes que la cola: `_pending_in_focus` lo
    # necesita para decidir cuál de los pendientes aporta a una celda.
    products = _pilot_products(db)
    provinces = _all_provinces(db)
    # Dentro de cada nivel de foco, primero lo que coincide con las reglas y al
    # final lo dudoso o en desacuerdo (slice 4).
    en_foco = _order_by_agreement(db, _pending_in_focus(db, products), products, provinces)
    ingestion = _requested_pending(db, focus_id) or (en_foco[0] if en_foco else None)

    proposals: list[ExtractionProposal] = []
    recognized_text = ""
    source_reference_guess = ""
    published_date_guess = ""
    if ingestion is not None and ingestion.content_type == ContentType.TEXT:
        if ingestion.original_text:
            proposals = propose_all(ingestion.original_text, products, provinces)
            # `ingestion.source_reference` gana sobre la adivinanza de texto
            # cuando ya viene cargado (docs/decisions.md, 2026-08-17): el
            # webhook y el backfill de WAHA lo escriben con el nombre exacto
            # del canal al ingerir — un dato cierto, no una heurística.
            # `guess_source_reference` busca el patrón "Nombre - fecha" de
            # capturas estilo Facebook (2026-08-08), que un mensaje de
            # WhatsApp nunca trae, así que sin este orden el campo quedaba
            # vacío pese a que el dato ya estaba en la fila.
            source_reference_guess = (
                ingestion.source_reference or guess_source_reference(ingestion.original_text) or ""
            )
            guessed = _guess_published_date(ingestion, ingestion.original_text)
            published_date_guess = guessed.isoformat() if guessed else ""
    elif ingestion is not None and ingestion.original_text is None:
        # content_type == IMAGE acá (el único otro valor posible).
        settings = get_settings()
        # Mismo helper que el pre-cálculo (`prefill.py`): el texto que ve el
        # modelo no puede depender de quién lo pidió.
        recognized_text = ad_text(db, ingestion, settings.raw_store_dir)
        # La transcripción se guarda al leerla (K3) y se confirma acá mismo: si
        # lo que sigue falla, `get_db` hace rollback y la imagen se transcribiría
        # otra vez en la próxima apertura.
        db.commit()
        if recognized_text:
            proposals = propose_all(recognized_text, products, provinces)
            source_reference_guess = (
                ingestion.source_reference or guess_source_reference(recognized_text) or ""
            )
        # Fuera del `if`: una foto de WhatsApp sin texto reconocido igual trae
        # la hora del mensaje en `captured_at` (A7).
        guessed = _guess_published_date(ingestion, recognized_text)
        published_date_guess = guessed.isoformat() if guessed else ""

    # Texto que ve el operador y que se le da al modelo: el original si es un
    # ítem de texto, lo que salió del OCR si es una imagen. Vacío para una
    # imagen sin texto reconocido: ahí no hay nada que preguntar ni señal que
    # calcular.
    text_for_hints = (
        (
            ingestion.original_text
            if ingestion.content_type == ContentType.TEXT
            else recognized_text
        )
        if ingestion is not None
        else None
    )

    # La asistencia corre acá, con las propuestas de las reglas ya calculadas
    # (son la segunda opinión). Una sola llamada por anuncio y nunca en el GET:
    # solo cuando el operador la pidió.
    assist_result: AdProposal | None = None
    # La propuesta guardada que el formulario lleva a la vista (slice 3a): su
    # id viaja oculto y el guardado captura contra ella qué se tocó.
    shown_proposal: LlmProposal | None = None
    proposal_indices: list[int] | None = None
    if (
        assist_ingestion_id is not None
        and ingestion is not None
        and ingestion.id == assist_ingestion_id
        and text_for_hints
        and llm is not None
    ):
        assist_result = _run_assist(
            text_for_hints,
            products=products,
            provinces=provinces,
            glossary=_active_glossary(db),
            ingestion_id=str(ingestion.id),
            settings=settings,
            llm=llm,
        )
        # Se guarda también si degradó: registra la falla. El modelo es el
        # canónico (sin prefijo de proveedor): con él se tarifa.
        saved = llm_store.save_proposal(
            db,
            ingestion_id=ingestion.id,
            result=assist_result,
            model=llm.model,
        )
        db.commit()
        if saved is not None and not assist_result.degraded:
            shown_proposal = saved
    elif ingestion is not None:
        # Abrir el anuncio con una propuesta que ya anduvo la muestra sin
        # llamar a la API: recargar no vuelve a pagar la llamada.
        shown_proposal = llm_store.latest_ok_proposal(db, ingestion.id)
        if shown_proposal is not None:
            assist_result, proposal_indices = llm_store.to_ad_proposal(db, shown_proposal)
    # `assist_good` es la propuesta solo si el modelo respondió; separar la
    # variable del booleano deja a mypy ver que no es `None` donde se usa.
    assist_good = (
        assist_result if assist_result is not None and not assist_result.degraded else None
    )
    assist_ok = assist_good is not None

    # Solo llegan al formulario las propuestas que cayeron en un producto del
    # foco. Una lista de precios de 25 renglones produce 25 candidatos, de los
    # cuales importan los pocos que son piloto: mostrar el resto obligaría al
    # operador a descartarlos de a uno, que es justamente el trabajo que esta
    # decisión elimina (`docs/decisions.md`, 2026-08-15). Lo descartado no se
    # pierde: el registro crudo es inmutable y conserva el anuncio entero.
    #
    # Con el asistente, las filas salen de las del modelo (ya solo del foco) y
    # las reglas quedan de segunda opinión y de fuente de lo que él no propone.
    assisted_by_row: dict[int, list[str]] = {}
    if assist_good is not None:
        rows, assisted_by_row = _rows_from_model(assist_good, proposals, proposal_indices)
    else:
        rows = [_values_from_proposal(p) for p in proposals if p.product is not None]
    rows.extend(_values_from_proposal(None) for _ in range(_EXTRA_BLANK_ROWS))

    # "El canal siempre manda" (docs/decisions.md, 2026-08-17): pisa lo que
    # haya propuesto `_match_province` en TODAS las filas, incluidas las
    # filas en blanco de más — el operador conoce la provincia del canal de
    # antemano, así que hasta una fila cargada a mano se beneficia de no
    # tener que elegirla.
    # Molde para las filas que el operador agregue a mano después de cargar la
    # página (botón "Agregar fila", clonado por JS). No entra en `rows` —no es
    # una fila más del form ahora—, viaja aparte al `<template>`.
    blank_row = _values_from_proposal(None)

    channel_province_id = _channel_province_id(db, ingestion) if ingestion is not None else None
    if channel_province_id is not None:
        for row in rows:
            row["province_id"] = channel_province_id
        # La provincia ya no es propuesta del modelo: la fija el canal.
        for campos in assisted_by_row.values():
            if "province" in campos:
                campos.remove("province")
        # Una fila agregada a mano también arranca con la provincia del canal:
        # el operador ya la conoce, elegirla de nuevo sería trabajo de más.
        blank_row["province_id"] = channel_province_id

    global_values = {
        "original_text": recognized_text,
        "source_reference": source_reference_guess,
        "published_date": published_date_guess,
    }

    # Las razones de descarte de las reglas se calculan sobre el mismo texto.
    # Con respuesta del modelo se apagan: su veredicto las reemplaza y un
    # «no se reconoció nada» contradiría las filas que él acaba de proponer.
    discard_hints = (
        probable_discard_reasons(text_for_hints, products)
        if text_for_hints and not assist_ok
        else []
    )

    return _render_queue(
        request,
        ingestion=ingestion,
        products=products,
        provinces=provinces,
        rows=rows,
        row_count=len(rows),
        global_values=global_values,
        blank_row=blank_row,
        error=None,
        status_counts=_status_counts(db, products),
        variant_options=_variant_options(db),
        format_options=_format_options(db),
        unconverted=unconverted,
        pasted=pasted,
        rule_added=rule_added,
        # `proposals` es la salida cruda de `propose_all`, antes del filtro
        # por foco de más arriba: vacía significa que no encontró NI producto
        # NI precio en ningún renglón del anuncio entero (docs/decisions.md,
        # 2026-08-17) — un saludo del canal con solo contacto, por ejemplo.
        # Distinto de "no matcheó ningún piloto": eso sí puede traer un
        # precio real de un producto fuera del foco, que vale la pena mirar.
        no_candidates=ingestion is not None and not proposals and not assist_ok,
        burst=_burst_context(db, ingestion) if ingestion is not None else (),
        discard_hints=discard_hints,
        channel_discard_stats=(
            _channel_discard_stats(db, ingestion) if ingestion is not None else None
        ),
        last_capture_notice=_last_capture_notice(db),
        gap_report=load_channel_gap_report(db),
        # El botón se ofrece con clave configurada y texto que mandar. Ya no
        # depende de que las reglas dejen huecos: el modelo propone todo.
        assist_available=bool(llm is not None and text_for_hints),
        assisted_map={str(i): campos for i, campos in assisted_by_row.items()},
        assist_error=(
            _assist_failure_label(assist_result.failure)
            if assist_result is not None and assist_result.degraded
            else None
        ),
        assist_discard=_assist_discard(assist_good) if assist_good is not None else None,
        evidence_segments=(
            _evidence_segments(ingestion.original_text, assist_good)
            if assist_good is not None
            and ingestion is not None
            and ingestion.content_type == ContentType.TEXT
            else None
        ),
        proposal_id=(
            str(shown_proposal.id) if shown_proposal is not None and assist_ok else None
        ),
        # El botón dice «Aceptar» (y responde al atajo) solo cuando hay una
        # propuesta del modelo a la vista para confirmar (slice 4).
        accept_ready=assist_ok,
        # Cuándo se le mostró esta fila al operador (slice 5): viaja oculto en
        # el form y vuelve en el POST para medir tiempo por decisión. Se fija
        # acá, al armar la vista, no en el POST — ahí ya pasó el tiempo que se
        # quiere medir.
        shown_at=(
            shown_at
            if parse_shown_at(shown_at) is not None
            else datetime.now(UTC).isoformat()
        ),
    )


# Las dos rutas de acá abajo van declaradas ANTES que `submit_curation`
# (`POST /curation/{ingestion_id}`) y `discard_curation`
# (`POST /curation/{ingestion_id}/discard`): FastAPI intenta las rutas en
# orden de registro y `{ingestion_id}` matchea cualquier string por igual, así
# que si esas quedaran primero, un POST a `/curation/sin-foco/discard` caería
# ahí con `ingestion_id="sin-foco"` y el 422 de validación de UUID lo tumbaría
# antes de llegar acá.
# La base del precio en palabras del «Reglas: …» (slice 3d).
_PRICE_BASIS_LABELS = {"unit": "por unidad", "total": "total del lote"}

# Por qué no respondió el asistente, en palabras del operador. Las cuatro
# primeras son `llm_assist.classify_failure`; las dos últimas, los cortes de
# `propose_ad` antes de llamar (el botón no se ofrece en esos casos).
_ASSIST_FAILURE_LABELS = {
    "credit": "sin saldo en la cuenta de la API",
    "timeout": "tardó demasiado en responder",
    "network": "no hay conexión con el servicio",
    "auth": "la clave de la API fue rechazada",
    "other": "error inesperado",
    "no_key": "no hay clave configurada",
    "empty_text": "el anuncio no tiene texto",
}

_DISCARD_LABELS = {"off_focus": "fuera de foco", "no_data": "sin dato útil"}


def _assist_failure_label(failure: str) -> str:
    return _ASSIST_FAILURE_LABELS.get(failure, _ASSIST_FAILURE_LABELS["other"])


def _rules_value_text(field: str, rule: ExtractionProposal) -> str:
    """Lo que decían las reglas en `field`, como texto para el «Reglas: …»."""
    if field == "product":
        return rule.product.name if rule.product else ""
    if field == "price":
        return str(rule.price) if rule.price is not None else ""
    if field == "currency":
        return rule.currency
    if field == "quantity":
        return str(rule.quantity) if rule.quantity is not None else ""
    if field == "price_basis":
        return _PRICE_BASIS_LABELS.get(rule.price_basis or "", "")
    return canonical_unit(rule.unit) or ""


def _rows_from_model(
    result: AdProposal,
    rules: list[ExtractionProposal],
    indices: list[int] | None = None,
) -> tuple[list[dict[str, str]], dict[int, list[str]]]:
    """Las filas del formulario a partir de las del modelo.

    Cada tarjeta parte de la fila de las reglas que le corresponde (mismo
    emparejado que H0) para lo que el modelo NO propone —compra mínima,
    variante…— y de los valores por defecto si no hay ninguna. Encima van los
    campos del modelo. La provincia, la base del precio, el formato de venta y
    el envase caen a lo de las reglas si el modelo se abstuvo: abstenerse no es
    dato, y ahí no se marca diferencia. Donde modelo y reglas difieren, la fila
    lleva `hint_<campo>` con lo que decían las reglas y la plantilla lo muestra. Devuelve también,
    por índice de fila, los campos que llenó el modelo (la marca de contorno).

    Cada fila lleva `proposal_row`: su índice en la propuesta guardada (el de
    `indices` si la propuesta se reconstruyó omitiendo alguna fila). Es el
    oculto con el que el guardado sabe qué fila vino del modelo (slice 3a).
    """
    reglas_por_fila = match_rule_rows(result.rows, rules)
    acuerdos = compare_with_rules(result.rows, rules)
    filas: list[dict[str, str]] = []
    asistidos: dict[int, list[str]] = {}
    for i, (row, regla, acuerdo, meta) in enumerate(
        zip(result.rows, reglas_por_fila, acuerdos, result.row_meta, strict=True)
    ):
        valores = _values_from_proposal(regla)
        valores["product_id"] = str(row.product.id) if row.product else ""
        valores["price"] = str(row.price) if row.price is not None else ""
        valores["currency"] = row.currency
        valores["quantity"] = str(row.quantity) if row.quantity is not None else ""
        valores["unit"] = canonical_unit(row.unit) or ""
        if row.province is not None:
            valores["province_id"] = str(row.province.id)
        # Slice 3d: el modelo manda también acá, pero solo cuando contestó.
        abstenidos = set()
        for campo in ("sale_format", "price_basis", "packaging"):
            valor = getattr(row, campo)
            if valor is not None:
                valores[campo] = valor
            else:
                abstenidos.add(campo)
        for campo, estado in acuerdo.fields().items():
            if estado == DIFIERE and regla is not None and campo not in abstenidos:
                valores[f"hint_{campo}"] = _rules_value_text(campo, regla)
        valores["evidence"] = meta.evidence or ""
        valores["proposal_row"] = str(indices[i] if indices is not None else i)
        filas.append(valores)
        asistidos[i] = filled_fields(row)
    return filas, asistidos


def _assist_discard(result: AdProposal) -> dict | None:
    """La sugerencia de descarte del modelo, con sus botones, o `None`.

    El motivo del modelo va primero y como acción principal; el otro motivo,
    como control secundario. Solo salen los dos valores que la CHECK de la base
    admite (`DiscardReason`).
    """
    if not result.discard:
        return None
    sugerido = result.discard_reason if result.discard_reason in _DISCARD_LABELS else None
    return {
        "label": _DISCARD_LABELS.get(sugerido or "", ""),
        "buttons": [
            {
                "value": motivo,
                "label": f"Descartar: {_DISCARD_LABELS[motivo]}",
                "primary": motivo == sugerido,
            }
            for motivo in sorted(_DISCARD_LABELS, key=lambda m: m != sugerido)
        ],
    }


def _evidence_segments(
    text: str | None, result: AdProposal
) -> list[tuple[str, bool]] | None:
    """El texto del anuncio en tramos, con las citas del modelo resaltadas.

    `None` si ninguna cita aparece literal (se muestra el texto tal cual).
    """
    if not text:
        return None
    segmentos = highlight_segments(
        text, [m.evidence for m in result.row_meta if m.evidence]
    )
    return segmentos if any(hit for _, hit in segmentos) else None


def _run_assist(
    text: str,
    *,
    products: Sequence[Product],
    provinces: Sequence[Province],
    glossary: Sequence[GlossaryEntry],
    ingestion_id: str,
    settings,
    llm: LLMConfig,
) -> AdProposal:
    """Una llamada al modelo por anuncio y su registro.

    `llm` es la configuración del proveedor ya resuelta: la clave, la URL, el
    timeout y el modelo salen de ahí (`saimc.llm.provider`).

    `propose_ad` nunca levanta: si degrada, el resultado lo dice y quien llama
    deja el formulario como lo armaron las reglas. El registro de llamadas
    propio de `propose_ad` queda apagado (`log_path=None`): el del operador se
    escribe acá, en la forma que leen H0 y `/control` (`record_operator_run`,
    compartido con el pre-cálculo).
    """
    resultado = propose_ad(
        text,
        products,
        provinces,
        llm=llm,
        ingestion_id=ingestion_id,
        log_path=None,
        glossary=glossary,
    )
    record_operator_run(
        resultado,
        ingestion_id=ingestion_id,
        model=llm.model,
        log_path=settings.llm_assist_log_path,
        source="curation",
    )
    return resultado


@router.post("/curation/{ingestion_id}/assist", response_class=HTMLResponse)
def assist_curation(
    ingestion_id: uuid.UUID,
    request: Request,
    shown_at: str = Form(""),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Pide al modelo la propuesta completa de este anuncio («Completar con IA»).

    POST y no GET a propósito: gasta plata, así que no puede dispararse por
    una recarga del navegador ni por un prefetch. Vuelve a renderizar el mismo
    anuncio (`focus_id`: también si se abrió con `?id=` y no es el primero de
    la cola) con las filas del modelo, sus marcas y su veredicto de descarte.
    """
    return _curation_view(
        request,
        db,
        assist_ingestion_id=ingestion_id,
        focus_id=ingestion_id,
        shown_at=shown_at,
    )


@router.get("/curation/sin-foco", response_class=HTMLResponse)
def curation_out_of_focus(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """Descarte en lote de pendientes sin producto del foco (`docs/decisions.md`,
    2026-08-31).

    Muestra la lista completa antes de que el operador confirme nada —
    spec.md §6.1, "el operador siempre confirma o corrige": nunca es un botón
    ciego. Cada id que se ve acá viaja como campo oculto en el formulario de
    confirmación (`curation_sin_foco.html`), así que lo que se descarta es
    exactamente lo que esta respuesta mostró, no una lista recalculada al
    momento del submit (ver `discard_out_of_focus`).

    Acotada exclusivamente a la señal "sin producto del foco"
    (`_pending_out_of_focus`). Las otras dos de `probable_discard_reasons`
    (agotado, mayorista) corren sobre el texto completo mientras el precio se
    extrae línea por línea: un anuncio puede decir "agotado" en un renglón y
    tener un precio válido en otro, así que agruparlas acá arriesgaría tirar
    una fila real. Sin foco no corre ese riesgo — si ningún renglón nombra un
    piloto, no hay precio que perder — y por eso siguen descartándose de a una
    desde la cola normal.
    """
    products = _pilot_products(db)
    items = _pending_out_of_focus(db, products)
    return request.app.state.templates.TemplateResponse(
        request,
        "curation_sin_foco.html",
        # Cuándo se mostró la lista: viaja oculto y vuelve en el POST del lote.
        {
            "items": items,
            "shown_at": datetime.now(UTC).isoformat(),
            # El chip «Imágenes» del segmento muestra cuántas hay en el lote.
            "n_imagenes": len(_image_batch(db)[0]),
        },
    )


@router.post("/curation/sin-foco/discard")
async def discard_out_of_focus(request: Request, db: Session = Depends(get_db)) -> RedirectResponse:
    """Confirma el descarte en lote de exactamente lo que el operador vio.

    Los ids viajan como campos ocultos repetidos (`ingestion_id`, uno por fila
    de `curation_sin_foco.html`) en vez de recalcular `_pending_out_of_focus`
    en este POST: así lo descartado es lo que el operador efectivamente miró
    en esa carga de página, no lo que haya en la tabla al momento del click
    (mismo principio de spec.md §6.1 que motiva mostrar la lista en primer
    lugar).

    Cada id se revalida igual contra `PENDING` + `_FOCUS_NONE` antes de
    tocarlo — nunca confía ciegamente en el form: entre el GET y este POST
    pudo curarse o descartarse a mano en otra pestaña, o pudo sumarse un
    piloto nuevo en /catalog que reclasifica el anuncio. Lo que ya no
    califica se ignora en silencio, sin romper el resto del lote; ningún otro
    pendiente se toca (mismo contrato "todo o nada solo sobre lo pedido" que
    `discard_curation`, pero por lote en vez de por uno).
    """
    form = await request.form()
    products = _pilot_products(db)
    # Hora de la lista y hora de la decisión: una sola vez para todo el lote,
    # así `(mode, decided_at)` lo identifica (spec A2, «Lote sin foco»).
    shown_at = parse_shown_at(str(form.get("shown_at", "")))
    decided_at = utc_now()
    discarded: list[RawIngestion] = []

    for raw_id in form.getlist("ingestion_id"):
        ingestion_id = _parse_uuid(str(raw_id))
        if ingestion_id is None:
            continue
        ingestion = db.get(RawIngestion, ingestion_id)
        if ingestion is None or ingestion.processing_status != ProcessingStatus.PENDING:
            continue
        if _focus_rank(ingestion, products) != _FOCUS_NONE:
            continue
        if lane0.rescued_ids(db, [ingestion.id]):
            continue  # rescatado de la auditoría (B2a): se cura de a uno
        ingestion.processing_status = ProcessingStatus.DISCARDED
        # Todo lo que se descarta por esta ruta es, por construcción, «fuera de
        # foco»: la lista es exactamente los pendientes sin ningún piloto.
        ingestion.discard_reason = DiscardReason.OFF_FOCUS.value
        discarded.append(ingestion)

    # Un evento por anuncio efectivamente descartado, en la misma transacción.
    for ingestion in discarded:
        record_curation_event(
            db,
            ingestion_id=ingestion.id,
            action=CurationAction.DISCARDED,
            mode=CurationMode.BATCH_OUT_OF_FOCUS,
            discard_reason=DiscardReason.OFF_FOCUS.value,
            shown_at=shown_at,
            decided_at=decided_at,
            detail={"batch_size": len(discarded)},
        )

    db.commit()
    return RedirectResponse(url="/curation/sin-foco", status_code=status.HTTP_303_SEE_OTHER)


@dataclass(frozen=True)
class ImageBatchItem:
    """Una imagen del lote con la propuesta del modelo que la sugiere descartar."""

    ingestion: RawIngestion
    proposal: LlmProposal
    # `off_focus` o `no_data`: siempre el que dio el modelo, nunca uno inventado.
    reason: str


# Motivo del modelo → texto del operador (A1a).
_IMAGE_REASON_LABELS = {
    DiscardReason.OFF_FOCUS.value: "Fuera de foco",
    DiscardReason.NO_DATA.value: "Sin datos de precio",
}


def _image_batch(db: Session) -> tuple[list[ImageBatchItem], int]:
    """La selección del lote de imágenes (A1b) y cuántas imágenes no tienen
    propuesta.

    Una imagen entra si está `PENDING` y su ÚLTIMA propuesta `ok` dice
    `discard = True` Y un motivo (`off_focus` o `no_data`).
    Sin propuesta, con la propuesta degradada, con una que no descarta o que
    descarta sin motivo válido, queda en la cola normal: este lote nunca decide
    por el modelo donde el modelo no habló ni inventa un motivo por él. El GET y el POST salen de esta misma función, así
    que lo que el POST revalida es exactamente lo que el GET pudo mostrar.

    El segundo valor es el aviso «X imágenes sin propuesta»: pendientes sin
    ninguna propuesta `ok` (las degradadas cuentan, porque tampoco hay veredicto).
    """
    stmt = (
        select(RawIngestion)
        .where(
            RawIngestion.processing_status == ProcessingStatus.PENDING,
            RawIngestion.content_type == ContentType.IMAGE,
        )
        .order_by(RawIngestion.captured_at.asc())
    )
    images = list(db.execute(stmt).scalars().all())
    proposals = _latest_ok_proposals(db, [image.id for image in images])
    # Lo rescatado de la auditoría del carril 0 (B2a) no vuelve al lote, que
    # lo marcaría para descartar por defecto.
    rescued = lane0.rescued_ids(db, [image.id for image in images])
    items: list[ImageBatchItem] = []
    for image in images:
        if image.id in rescued:
            continue
        proposal = proposals.get(image.id)
        if proposal is None or proposal.discard is not True:
            continue
        reason = proposal.discard_reason
        if reason not in _IMAGE_REASON_LABELS:
            continue
        items.append(ImageBatchItem(ingestion=image, proposal=proposal, reason=reason))
    return items, len(images) - len(proposals)


@router.get("/curation/sin-foco/imagenes", response_class=HTMLResponse)
def curation_image_batch(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """Lote de imágenes que el modelo propone descartar (A1b), segmento
    «Imágenes» de `/curation/sin-foco`.

    Mismo contrato que el lote de textos (spec.md §6.1): el sistema propone y
    el operador confirma. Van marcadas por defecto y «Rescatar» las desmarca;
    lo desmarcado no viaja en el POST (`discard_images_batch`).
    """
    items, without_proposal = _image_batch(db)
    # Variable tipada y no `return` directo: `state.templates` es `Any` y mypy
    # lo marca como `no-any-return` (el baseline de lint no debe crecer).
    response: HTMLResponse = request.app.state.templates.TemplateResponse(
        request,
        "curation_sin_foco_imagenes.html",
        {
            "items": items,
            "without_proposal": without_proposal,
            "reason_labels": _IMAGE_REASON_LABELS,
            "n_textos": len(_pending_out_of_focus(db, _pilot_products(db))),
            "shown_at": datetime.now(UTC).isoformat(),
        },
    )
    return response


@router.post("/curation/sin-foco/imagenes/discard")
async def discard_images_batch(
    request: Request, db: Session = Depends(get_db)
) -> RedirectResponse:
    """Descarta las imágenes que el operador dejó marcadas (A1b).

    Los ids viajan en las casillas: lo rescatado no se envía y queda `PENDING`.
    Cada id se revalida contra `_image_batch` —puede haberse curado en otra
    pestaña, o el modelo pudo dar otra propuesta—; un id no elegible o ajeno se
    ignora en silencio, sin romper el resto.

    Todo en una transacción por anuncio: el estado `DISCARDED` con el motivo del
    modelo, la revisión (`llm_reviews`, para que siga fuera de H0 como
    asistido) y el evento de bitácora `batch_model`. Un solo `commit`: o queda
    todo o no queda nada.
    """
    form = await request.form()
    eligible = {item.ingestion.id: item for item in _image_batch(db)[0]}
    shown_at = parse_shown_at(str(form.get("shown_at", "")))
    decided_at = utc_now()

    chosen: list[ImageBatchItem] = []
    for raw_id in form.getlist("ingestion_id"):
        ingestion_id = _parse_uuid(str(raw_id))
        item = eligible.pop(ingestion_id, None) if ingestion_id is not None else None
        if item is not None:  # `pop`: un id repetido se descarta una sola vez
            chosen.append(item)

    for item in chosen:
        item.ingestion.processing_status = ProcessingStatus.DISCARDED
        item.ingestion.discard_reason = item.reason
        llm_store.save_review(
            db,
            proposal=item.proposal,
            outcome=ReviewOutcome.DISCARDED,
            discard_reason=item.reason,
            rows=llm_store.review_rows(item.proposal, []),
            shown_at=shown_at,
        )
        record_curation_event(
            db,
            ingestion_id=item.ingestion.id,
            action=CurationAction.DISCARDED,
            mode=CurationMode.BATCH_MODEL,
            discard_reason=item.reason,
            proposal_id=item.proposal.id,
            shown_at=shown_at,
            decided_at=decided_at,
            detail={"batch_size": len(chosen)},
        )

    db.commit()
    return RedirectResponse(
        url="/curation/sin-foco/imagenes", status_code=status.HTTP_303_SEE_OTHER
    )


# --- B2a: auditoría del carril 0 ----------------------------------------------
# Antes de `POST /curation/{ingestion_id}`: con `/curation/auditoria` declarada
# después, esa ruta la capturaría primero y respondería 405 al GET.


def _audit_url(week_label: str, ingestion_id: uuid.UUID | None = None) -> str:
    anchor = f"#c{ingestion_id}" if ingestion_id is not None else ""
    return f"/curation/auditoria?semana={week_label}{anchor}"


@router.get("/curation/auditoria", response_class=HTMLResponse)
def curation_audit(
    request: Request, semana: str = "", db: Session = Depends(get_db)
) -> HTMLResponse:
    """Auditoría semanal de los descartes automáticos (B2a).

    Por defecto, la última semana cerrada; `?semana=2026-W39` abre otra. La
    semana en curso nunca se muestrea. La cota de falsos descartes se acumula
    entre semanas y solo se muestra acá, que pide operador: `/control` es
    público.
    """
    week = lane0.parse_week(semana) or lane0.last_closed_week()
    sample = lane0.week_sample(db, week)
    response: HTMLResponse = request.app.state.templates.TemplateResponse(
        request,
        "curation_auditoria.html",
        {
            "sample": sample,
            "weeks": [lane0.week_label(w) for w in lane0.audited_weeks(db)],
            "bounds": lane0.audit_bounds(db),
            "jev_bound": lane0.jev_bound(db),
            "sample_sizes": lane0.SAMPLE_SIZES,
            "reason_labels": _IMAGE_REASON_LABELS,
            "type_labels": {lane0.TEXT: "Textos", lane0.IMAGE: "Imágenes"},
            "week_end": week + timedelta(days=6),
            "lane1": _lane1_audit(db),
        },
    )
    return response


def _lane1_audit(db: Session) -> dict[str, object]:
    """Lo que la pantalla muestra del carril 1 (B2b-3): lo aceptado solo que
    falta auditar, la cota contra la meta y si el freno está puesto."""
    settings = get_settings()
    state = lane1.audit_state(db, settings)
    pending = lane1.audit_pending(db, settings)
    return {
        "total_unaudited": pending.total,
        "enabled": lane1.enabled(settings),
        "pending": lane1.audit_queue(db, settings),
        "without_rows": lane1.curated_without_rows(db),
        "state": state,
        "brake": lane1.brake_for(
            audited=state.audited, errors=state.errors, unaudited=state.unaudited
        ),
        "target": lane1.ERROR_TARGET,
        "n0": lane1.AUDIT_N0,
        "max_unaudited": lane1.MAX_UNAUDITED,
    }


def _lane1_audit_url() -> str:
    return "/curation/auditoria#aceptados"


@router.post("/curation/auditoria/{ingestion_id}/aceptado-bien")
def confirm_auto_accept(ingestion_id: uuid.UUID, db: Session = Depends(get_db)) -> RedirectResponse:
    """«Está bien»: las filas que aceptó el carril 1 pasan a `audited`
    (`lane1.mark_ok`). Si no había nada que auditar, no cambia nada."""
    if lane1.mark_ok(db, ingestion_id):
        db.commit()
    return RedirectResponse(url=_lane1_audit_url(), status_code=status.HTTP_303_SEE_OTHER)


@router.post("/curation/auditoria/{ingestion_id}/aceptado-invalido")
def reject_auto_accept(
    ingestion_id: uuid.UUID,
    discard_reason: str = Form(DiscardReason.NO_DATA.value),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """«No era un anuncio válido»: borra las filas que aceptó el carril 1 y
    descarta el anuncio (`lane1.mark_invalid`). Cuenta como error."""
    if lane1.mark_invalid(db, ingestion_id, discard_reason):
        db.commit()
    return RedirectResponse(url=_lane1_audit_url(), status_code=status.HTTP_303_SEE_OTHER)


@router.post("/curation/auditoria/{ingestion_id}/rescatar")
def rescue_auto_discard(
    ingestion_id: uuid.UUID, semana: str = Form(""), db: Session = Depends(get_db)
) -> RedirectResponse:
    """«Rescatar»: el descarte automático vuelve a la cola con su propuesta.
    Fuera de la muestra de esa semana, o si ya no está descartado, no cambia
    nada (`lane0.rescue`)."""
    week = lane0.parse_week(semana)
    if week is not None and lane0.rescue(db, ingestion_id, week):
        db.commit()
    label = lane0.week_label(week) if week is not None else ""
    return RedirectResponse(
        url=_audit_url(label, ingestion_id), status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/curation/auditoria/{ingestion_id}/bien")
def confirm_auto_discard(
    ingestion_id: uuid.UUID, semana: str = Form(""), db: Session = Depends(get_db)
) -> RedirectResponse:
    """«Bien descartado»: solo escribe el evento `audited` (`lane0.mark_audited`)."""
    week = lane0.parse_week(semana)
    if week is not None and lane0.mark_audited(db, ingestion_id, week):
        db.commit()
    label = lane0.week_label(week) if week is not None else ""
    return RedirectResponse(
        url=_audit_url(label, ingestion_id), status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/evidence/{ingestion_id}")
def evidence(ingestion_id: uuid.UUID, db: Session = Depends(get_db)) -> FileResponse:
    """Sirve el archivo de evidencia de una imagen. El texto se muestra inline, no acá."""
    ingestion = db.get(RawIngestion, ingestion_id)
    if ingestion is None or ingestion.content_type != ContentType.IMAGE:
        raise HTTPException(status_code=404, detail="No hay evidencia de imagen para ese id")

    file_path = get_settings().raw_store_dir / ingestion.file_path
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="El archivo de evidencia no existe")

    return FileResponse(file_path)


def _require_pending(db: Session, ingestion_id: uuid.UUID) -> RawIngestion:
    ingestion = db.get(RawIngestion, ingestion_id)
    if ingestion is None:
        raise HTTPException(status_code=404, detail="No existe ese elemento de la cola")
    if ingestion.processing_status != ProcessingStatus.PENDING:
        raise HTTPException(
            status_code=409, detail="Este elemento ya fue curado o descartado"
        )
    return ingestion


def _require_curated(db: Session, ingestion_id: uuid.UUID) -> RawIngestion:
    ingestion = db.get(RawIngestion, ingestion_id)
    if ingestion is None:
        raise HTTPException(status_code=404, detail="No existe ese elemento de la cola")
    if ingestion.processing_status != ProcessingStatus.CURATED:
        raise HTTPException(status_code=409, detail="Este elemento todavía no está curado")
    return ingestion


def _curated_rows(db: Session, ingestion_id: uuid.UUID) -> list[PriceHistory]:
    stmt = (
        select(PriceHistory)
        .where(PriceHistory.source_id == ingestion_id)
        .order_by(PriceHistory.created_at)
    )
    return list(db.execute(stmt).scalars().all())


def _parse_uuid(raw: str) -> uuid.UUID | None:
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


def _parse_decimal(raw: str) -> Decimal | None:
    if not raw:
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def _parse_min_purchase(raw: str) -> Decimal | None:
    """Compra mínima que exige el anuncio, en la unidad de la propia fila.

    Vacío, no parseable o no positivo degrada a `None`, nunca rompe el submit
    — mismo contrato que `quantity`. Es lo contrario de lo que se decidió para
    `published_date` el 2026-08-14, y a propósito: allá el vacío **fabricaba**
    un valor equivocado (la fecha de carga sobre un anuncio viejo), acá no
    puede. «Sin mínimo declarado» y «el mínimo es la cantidad de esta fila»
    producen el mismo resultado en el filtro del rango público, así que un
    vacío no afirma nada falso (design.md, D2).

    El no-positivo se ataja acá y no en la base: `ck_prices_history_min_
    purchase_quantity` existe como piso estructural, no como camino de error
    que el operador pueda pisar desde el formulario.
    """
    value = _parse_decimal(raw)
    if value is None or value <= 0:
        return None
    return value


_PRICE_BASIS_VALUES = {"unit", "total"}


def _price_basis_value(raw: str) -> str | None:
    """Gate de vocabulario: cualquier otra cosa —incluido el envío en blanco—
    es «sin resolver», nunca un valor fabricado (mismo contrato que
    `_SALE_FORMAT_VALUES`, design.md D3)."""
    return raw if raw in _PRICE_BASIS_VALUES else None


def _price_basis_required(quantity: Decimal | None) -> bool:
    """Con cantidad uno (o sin cantidad) unitario y total son el mismo
    número: la pregunta es aritméticamente inerte y exigirla ahí solo
    costaría trabajo manual sin evitar ningún error (design.md, D3)."""
    return quantity is not None and quantity != 1


def _parse_date(raw: str) -> date | None:
    """Parsea el `<input type="date">` (ISO `YYYY-MM-DD` del navegador).

    Solo parsea: vacío o no parseable devuelve `None`. Qué se hace con ese
    `None` lo decide `_validated_published_date`, que es quien conoce la
    regla de negocio.
    """
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _validated_published_date(raw: str, captured_at: datetime) -> tuple[date | None, str | None]:
    """Valida la fecha de publicación del anuncio: `(fecha, error)`.

    **Campo obligatorio y bloqueante desde el 2026-08-14**, reemplazando el
    contrato original de «campo nunca bloqueante / degrada a NULL»
    (docs/decisions.md, 2026-08-10). Aquella decisión se apoyaba en que
    ningún consumidor leía la columna; hoy la leen dos, y los dos se
    equivocan en silencio cuando falta:

    - `cells.compute_cells` usa `published_date` como eje temporal de la
      ventana de N días, con repliegue a `created_at`: un anuncio de hace
      tres semanas cargado sin fecha entra al rango vigente como si fuera
      de hoy.
    - `anchor_date` ancla la tasa de cambio en `published_date`, con
      repliegue a `captured_at`: ese mismo anuncio viejo se convierte a CUP
      con la tasa de hoy, que no es la que regía cuando se publicó.

    El repliegue no se saca (las filas anteriores a esta regla tienen la
    columna en `NULL` y hay que seguir leyéndolas): lo que se saca es la
    posibilidad de crear filas nuevas sin fecha.

    La cota superior es el día de la captura en calendario de La Habana
    —vía `anchor_date`, el mismo criterio que usa la conversión monetaria—
    y no `date.today()`: un anuncio no puede haberse publicado después de
    que el operador lo capturó.
    """
    if not raw:
        return None, "Falta la fecha de publicación del anuncio."

    parsed = _parse_date(raw)
    if parsed is None:
        return None, "Fecha de publicación inválida."

    tope = anchor_date(None, captured_at)
    if parsed > tope:
        return None, f"La fecha de publicación no puede ser posterior a la captura ({tope})."

    return parsed, None


def _resolve_row_product(
    db: Session, products_by_name: dict[str, Product], row: dict[str, str]
) -> Product | None:
    """Resuelve el producto de una fila contra el conjunto piloto.

    Ya no da de alta productos. El alta al vuelo desde este formulario es lo
    que produjo 55 productos de una sola observación y duplicados que parten
    la densidad de una serie en dos («Arroz criollo» contra «Arroz», «Azúcar
    blanco» contra «Azúcar blanca»). El catálogo se administra en `/catalog`,
    donde agregar un producto es una decisión explícita y no el efecto
    colateral de haber tipeado un nombre (`docs/decisions.md`, 2026-08-15).

    `products_by_name` sigue en la firma porque los dos caminos de escritura
    lo arman una sola vez por submit; acá se usa como caché de lectura.

    Un `product_id` que no parsea como UUID o que no existe se trata como
    "sin producto" para esta fila, sin romper con una excepción: el operador
    puede haber elegido "— seleccionar —" o el id puede haber quedado obsoleto
    entre el GET y el submit.
    """
    if row["product_id"]:
        product_uuid = _parse_uuid(row["product_id"])
        if product_uuid is not None:
            return db.get(Product, product_uuid)

    return None


def _row_from_form(form: FormData, index: int) -> dict[str, str]:
    """Lee los campos indexados `{campo}_{index}` de un form crudo, como strings.

    `price_id` viaja vacío en la curaduría normal (`submit_curation` no lo lee)
    y con el id real en la corrección (`submit_edit` lo usa para ubicar la fila
    exacta a actualizar, ver ahí el porqué de no matchear solo por posición).
    En la corrección, una fila con `price_id` vacío es una fila que el operador
    agregó: `submit_edit` la crea.
    """
    return {
        "price_id": str(form.get(f"price_id_{index}", "")).strip(),
        "product_id": str(form.get(f"product_id_{index}", "")).strip(),
        "price": str(form.get(f"price_{index}", "")).strip(),
        "currency": str(form.get(f"currency_{index}", "")).strip() or Currency.CUP.value,
        "quantity": str(form.get(f"quantity_{index}", "")).strip(),
        "unit": str(form.get(f"unit_{index}", "")).strip(),
        "sale_format": str(form.get(f"sale_format_{index}", "")).strip(),
        "packaging": str(form.get(f"packaging_{index}", "")).strip(),
        "variant_kind": str(form.get(f"variant_kind_{index}", "")).strip(),
        "variant_brand": str(form.get(f"variant_brand_{index}", "")).strip(),
        "variant_origin": str(form.get(f"variant_origin_{index}", "")).strip(),
        "min_purchase": str(form.get(f"min_purchase_{index}", "")).strip(),
        "province_id": str(form.get(f"province_id_{index}", "")).strip(),
        "price_basis": str(form.get(f"price_basis_{index}", "")).strip(),
        # Índice de la fila en la propuesta del modelo (slice 3a); vacío en
        # una fila agregada a mano y en la corrección, que no lo usa.
        "proposal_row": str(form.get(f"proposal_row_{index}", "")).strip(),
    }


@router.post("/curation/{ingestion_id}", response_model=None)
async def submit_curation(
    ingestion_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    rate_fetcher: Fetcher = Depends(get_rate_fetcher),
) -> HTMLResponse | RedirectResponse:
    """Confirma la curaduría: crea una fila de `prices_history` por cada renglón completo.

    Cada fila nace con `provenance = human` y, desde B1, validada: antes del
    `commit` y en la misma transacción, `validation.persist` le escribe
    `validation_status`, `confidence_score` y `validation_detail`
    (`docs/spec-slice-B1.md`). No depende de que haya propuesta del modelo. Si
    la validación falla el guardado no se cae: esas tres columnas quedan en
    `NULL` y el error va al log. `price_cup`/`exchange_rate*` YA NO
    quedan fijos en None (2026-08-13): se resuelven sincrónicamente vía
    `RateResolver` para toda fila con `currency != CUP`, con el ancla que
    calcula `anchor_date` (`published_date` si el operador lo cargó, si no
    `raw_ingestions.captured_at` en calendario de La Habana). Nunca bloquea el
    guardado — si nada es resolvible, la fila igual se guarda con esas cuatro
    columnas en `NULL` y el redirect avisa `?unconverted=N` (design.md D4/D7).

    La tabla manda `row_count` filas indexadas (`product_id_0`, `price_0`, ...).
    El producto de cada fila sale del `<select>`, que desde el 2026-08-15 solo
    lista pilotos: acá no se dan de alta productos (ver `_resolve_row_product`).
    El operador descarta una tarjeta que no quiere cargar eliminándola del lado
    del cliente (botón "Eliminar" en la plantilla, sin submit): sus campos
    indexados no viajan en el POST, así que cae sola en la rama de fila vacía
    de abajo. Una fila sin producto y sin precio se ignora en silencio (no es
    una fila que el operador quiso cargar); con solo uno de los dos es un error
    explícito, porque ahí sí hay intención de cargar algo pero falta un dato.
    Todo o nada: si alguna fila con datos tiene un error, no se guarda
    ninguna — un estado a medias es más confuso que repetir el submit. La
    fecha de publicación
    entra en ese mismo todo-o-nada desde el 2026-08-14: es obligatoria y
    bloquea el submit entero (ver `_validated_published_date`).
    """
    ingestion = _require_pending(db, ingestion_id)
    form = await request.form()

    original_text = str(form.get("original_text", "")).strip()
    source_reference = str(form.get("source_reference", "")).strip()
    published_date_raw = str(form.get("published_date", "")).strip()
    published_date, published_date_error = _validated_published_date(
        published_date_raw, ingestion.captured_at
    )
    try:
        row_count = int(str(form.get("row_count", "0")))
    except ValueError:
        row_count = 0
    # Slice 3a: la propuesta que el operador tenía a la vista y su nota.
    proposal_id_raw = str(form.get("proposal_id", "")).strip()
    note = str(form.get("note", ""))
    # A3c: una regla del glosario que viaja con el guardado. Con los dos
    # campos vacíos no pasa nada; con uno solo, es un error de esta request.
    rule_term_raw = str(form.get("rule_term", ""))
    rule_definition_raw = str(form.get("rule_definition", ""))
    rule, rule_error = validate_rule(rule_term_raw, rule_definition_raw)
    # Slice 5: cuándo se le mostró la fila (viaja oculto, ver `_curation_view`).
    # Un valor vacío o mal formado no bloquea el guardado — se guarda `None` y
    # esa fila queda afuera de la métrica de tiempo por decisión.
    shown_at_raw = str(form.get("shown_at", "")).strip()
    shown_at = parse_shown_at(shown_at_raw)

    # Catálogo existente + altas de esta misma request: dos filas con el mismo
    # producto nuevo (mismo submit) deben reusar el Product, no duplicarlo.
    products_by_name = {p.name.lower(): p for p in _pilot_products(db)}

    errors: list[str] = []
    if published_date_error is not None:
        errors.append(published_date_error)
    if rule_error is not None:
        errors.append(rule_error)
    typed_rows: list[dict[str, str]] = []
    entries_to_create: list[PriceHistory] = []
    # `proposal_row` de cada fila que se guarda, en el mismo orden.
    proposal_rows: list[str] = []

    for i in range(row_count):
        row = _row_from_form(form, i)
        typed_rows.append(row)

        product = _resolve_row_product(db, products_by_name, row)

        if product is None and not row["price"]:
            continue  # fila vacía: el operador no la llenó, se ignora sin error

        if product is None:
            errors.append(f"Fila {i + 1}: seleccioná o agregá un producto")
            continue

        if not row["price"]:
            errors.append(f"Fila {i + 1}: falta el precio")
            continue

        price_value = _parse_decimal(row["price"])
        if price_value is None:
            errors.append(f"Fila {i + 1}: precio inválido")
            continue

        province_uuid = _parse_uuid(row["province_id"])
        province = db.get(Province, province_uuid) if province_uuid is not None else None

        quantity_value = _parse_decimal(row["quantity"])
        price_basis = _price_basis_value(row["price_basis"])
        if price_basis is None and _price_basis_required(quantity_value):
            errors.append(
                f"Fila {i + 1}: declará si el precio es por unidad o por el lote entero"
            )
            continue

        entries_to_create.append(
            PriceHistory(
                product_id=product.id,
                province_id=province.id if province is not None else None,
                price=price_value,
                currency=row["currency"],
                quantity=quantity_value,
                unit=canonical_unit(row["unit"]),
                sale_format=(
                    row["sale_format"] if row["sale_format"] in _SALE_FORMAT_VALUES else None
                ),
                # El envase + `quantity` + `unit` resuelven el formato; si el
                # triple está incompleto, `format_id` queda en NULL sin fabricar
                # nada (design.md D4). `get_or_create_format` hace `flush()`, así
                # que dos filas del mismo submit con el mismo triple reusan el
                # `ProductFormat` en vez de chocar contra el UNIQUE.
                format_id=_resolve_format_id(db, product.id, row),
                # Los tres ejes resuelven la variante; si los tres quedan
                # vacíos, `variant_id` queda en NULL sin fabricar nada
                # (product-variants). `get_or_create_variant` también hace
                # `flush()`, mismo motivo que arriba.
                variant_id=_resolve_variant_id(db, product.id, row),
                min_purchase_quantity=_parse_min_purchase(row["min_purchase"]),
                price_basis=price_basis,
                published_date=published_date,
                source_id=ingestion.id,
                # La hizo una persona, con el formulario delante (B1).
                provenance=Provenance.HUMAN.value,
            )
        )
        proposal_rows.append(row["proposal_row"])

    if not errors and not entries_to_create:
        errors.append("Cargá al menos un producto con precio.")

    if errors:
        # Todo o nada: cualquier alta de producto que haya llegado a hacer
        # `flush()` en este loop se descarta junto con el resto de la request.
        db.rollback()
        return _render_queue(
            request,
            ingestion=ingestion,
            products=_pilot_products(db),
            provinces=_all_provinces(db),
            rows=typed_rows,
            row_count=row_count,
            global_values={
                "original_text": original_text,
                "source_reference": source_reference,
                "published_date": published_date_raw,
            },
            error=" ".join(errors),
            status_counts=_status_counts(db, _pilot_products(db)),
            variant_options=_variant_options(db),
            format_options=_format_options(db),
            status_code=422,
            # Sin los ocultos, el segundo intento se guardaría sin captura.
            proposal_id=proposal_id_raw or None,
            note=note,
            rule_term=rule_term_raw,
            rule_definition=rule_definition_raw,
            # Conserva el momento en que se mostró la fila la primera vez, no
            # el de este reintento: el operador ya venía decidiendo desde
            # antes de este submit fallido.
            shown_at=shown_at_raw or None,
        )

    # Resolución de tasa DESPUÉS de la rama todo-o-nada (design.md D4): un
    # submit que va a fallar no debe gastar llamadas HTTP ni escribir filas
    # de cache dentro de una transacción que se está por descartar.
    # Compartido con el carril 1 (`curation_rows`, B2b-2): misma ancla y tasa.
    unconverted = curation_rows.resolve_rates(db, entries_to_create, ingestion, rate_fetcher)

    for entry in entries_to_create:
        db.add(entry)

    # Captura de la revisión (slice 3a), en la misma transacción. Un
    # `proposal_id` que no es de este anuncio se ignora: no se captura nada y
    # la curaduría se guarda igual. El `flush` asigna los ids de las filas.
    proposal = llm_store.resolve_proposal(db, proposal_id_raw, ingestion.id)
    if proposal is not None:
        db.flush()
        llm_store.save_review(
            db,
            proposal=proposal,
            outcome=ReviewOutcome.CURATED,
            rows=llm_store.review_rows(
                proposal, list(zip(proposal_rows, entries_to_create, strict=True))
            ),
            note=note,
            shown_at=shown_at,
        )

    # Todo texto capturado que se persiste durante la curaduría pasa por
    # `redact_personal_data` antes de tocar la base — regla de `CLAUDE.md`
    # sobre datos personales. Con el envase ya sin texto libre (pasó a ser un
    # `<select>` sobre `PACKAGING`), los únicos puntos que quedan son
    # `source_reference` y `original_text`, que solo se conocen después de
    # pasar la rama de error todo-o-nada.

    # `source_reference` está diseñado para completarse acá, no viola la
    # inmutabilidad de `raw_ingestions` (ver docstring del modelo).
    if source_reference:
        ingestion.source_reference = redact_personal_data(source_reference)

    # Mismo criterio para `original_text` en imágenes: nulo en la ingesta a
    # propósito (el OCR no corre ahí), lo completa el operador al confirmar
    # la transcripción en la curaduría (ver docstring del modelo). Para texto,
    # `original_text` YA es la evidencia pegada; este campo del form no la
    # reemplaza.
    if ingestion.content_type == ContentType.IMAGE and original_text:
        ingestion.original_text = redact_personal_data(original_text)

    # Validación de las filas (B1), con la tasa ya resuelta y el origen del
    # anuncio ya escrito: el motor mira el `price_cup` que se publica y el
    # mismo canal que va a ver una revalidación. El `flush` va afuera para que
    # un error de escritura salga por su camino y no se tome por uno del motor.
    db.flush()
    persist.validate_and_store(db, ingestion, entries_to_create)

    ingestion.processing_status = ProcessingStatus.CURATED
    record_curation_event(
        db,
        ingestion_id=ingestion.id,
        action=CurationAction.CURATED,
        mode=CurationMode.INDIVIDUAL,
        shown_at=shown_at,
        proposal_id=proposal.id if proposal is not None else None,
        detail={"row_count": len(entries_to_create)},
    )
    # A3c: la regla entra al glosario en la misma transacción. Un término
    # repetido no se rechaza: `/catalog` tampoco lo hace.
    if rule is not None:
        db.add(GlossaryEntry(term=rule[0], definition=rule[1]))
    db.commit()

    params: dict[str, str] = {}
    if unconverted:
        params["unconverted"] = str(unconverted)
    if rule is not None:
        params["rule_added"] = rule[0]
    redirect_url = "/curation" + (f"?{urlencode(params)}" if params else "")
    return RedirectResponse(url=redirect_url, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/curation/{ingestion_id}/discard")
def discard_curation(
    ingestion_id: uuid.UUID,
    reason: DiscardReason = Form(...),
    proposal_id: str = Form(""),
    note: str = Form(""),
    shown_at: str = Form(""),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """El motivo es obligatorio (`spec.md` §12.5): «sin dato útil» (sin precio,
    ilegible, agotado, spam) es lo que la tasa de descarte mide; «fuera de
    foco» (tiene precio pero no es piloto/es mayorista puro) no, y se reporta
    aparte. Los dos botones de la cola mandan uno u otro.

    Con una propuesta del modelo a la vista, los botones mandan el formulario
    de curar entero (`formaction`) y acá se captura la revisión (slice 3a):
    cada fila propuesta queda sin valor final, porque no se guardó ninguna.
    """
    ingestion = _require_pending(db, ingestion_id)
    ingestion.processing_status = ProcessingStatus.DISCARDED
    ingestion.discard_reason = reason.value
    proposal = llm_store.resolve_proposal(db, proposal_id.strip(), ingestion.id)
    if proposal is not None:
        llm_store.save_review(
            db,
            proposal=proposal,
            outcome=ReviewOutcome.DISCARDED,
            discard_reason=reason.value,
            rows=llm_store.review_rows(proposal, []),
            note=note,
        )
    record_curation_event(
        db,
        ingestion_id=ingestion.id,
        action=CurationAction.DISCARDED,
        mode=CurationMode.INDIVIDUAL,
        discard_reason=reason.value,
        shown_at=parse_shown_at(shown_at),
        proposal_id=proposal.id if proposal is not None else None,
    )
    db.commit()
    return RedirectResponse(url="/curation", status_code=status.HTTP_303_SEE_OTHER)


def _catalog_names(db: Session, ids: dict[str, set[str]]) -> CatalogNames:
    """Nombre de cada id de catálogo mencionado, una consulta por tabla. Un id
    que ya no existe simplemente no aparece (la vista pone «(eliminado)»)."""

    def uuids(name: str) -> list[uuid.UUID]:
        return [uuid.UUID(v) for v in ids.get(name, ())]

    names: dict[str, dict[str, str]] = {name: {} for name in ids}
    if ids.get("product_id"):
        for item in db.execute(
            select(Product.id, Product.name).where(Product.id.in_(uuids("product_id")))
        ):
            names["product_id"][str(item[0])] = item[1]
    if ids.get("province_id"):
        for item in db.execute(
            select(Province.id, Province.name).where(Province.id.in_(uuids("province_id")))
        ):
            names["province_id"][str(item[0])] = item[1]
    if ids.get("format_id"):
        for fmt in db.execute(
            select(ProductFormat).where(ProductFormat.id.in_(uuids("format_id")))
        ).scalars():
            names["format_id"][str(fmt.id)] = format_label(
                fmt.packaging, fmt.quantity, fmt.unit
            )
    if ids.get("variant_id"):
        for variant in db.execute(
            select(ProductVariant).where(ProductVariant.id.in_(uuids("variant_id")))
        ).scalars():
            # El CHECK del esquema garantiza que al menos un eje no esté vacío.
            names["variant_id"][str(variant.id)] = " · ".join(
                p for p in (variant.kind, variant.brand, variant.origin) if p
            )
    return names


def _edit_history(
    db: Session, ingestion_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[tuple[datetime, EditSummary]]]:
    """Las ediciones con cambios de cada anuncio, de la más vieja a la más
    nueva (K1). Una consulta de eventos y una por tabla de catálogo, sin
    importar cuántos anuncios se listen. Las ediciones sin cambios (`rows: []`
    y sin `source_reference`) se omiten."""
    if not ingestion_ids:
        return {}
    events = list(
        db.execute(
            select(CurationEvent)
            .where(
                CurationEvent.ingestion_id.in_(ingestion_ids),
                CurationEvent.action == CurationAction.EDITED,
            )
            .order_by(CurationEvent.decided_at, CurationEvent.id)
        ).scalars()
    )
    names = _catalog_names(
        db, referenced_catalog_ids([e.detail for e in events if e.detail])
    )
    history: dict[uuid.UUID, list[tuple[datetime, EditSummary]]] = {}
    for event in events:
        summary = describe_edit(event.detail or {}, names)
        if summary is not None:
            history.setdefault(event.ingestion_id, []).append((event.decided_at, summary))
    return history


@router.get("/curation/history", response_class=HTMLResponse)
def curation_history(
    request: Request,
    db: Session = Depends(get_db),
    product: str | None = None,
    province: str | None = None,
    currency: str | None = None,
    unit: str | None = None,
    sale_format: str | None = None,
    format_id: str | None = None,
    unquantified: int | None = None,
    unresolved_basis: int | None = None,
    unconverted_unit: int | None = None,
    unconverted: int = 0,
) -> HTMLResponse:
    """Lista los anuncios ya curados, más recientes primero, para ubicar cuál
    corregir o eliminar — es la puerta de entrada a `GET /curation/{id}/edit`
    y a `POST /curation/{id}/delete`.

    Los filtros opcionales son la identidad exacta de una celda de
    `compute_cells` (2026-08-12): la columna `n` de `/control` enlaza acá para
    mostrar «los anuncios detrás de este número». Un parámetro **ausente** no
    filtra por esa columna; un parámetro **presente pero vacío** (`unit=`)
    filtra por «sin responder» (NULL) — mismo contrato que ya usa
    `_SALE_FORMAT_VALUES` en el submit: la cadena vacía nunca se confunde con
    "no filtrar".

    `unit` es la unidad **efectiva** de la fila, no la columna cruda
    `prices_history.unit` (2026-09-02): desde que la matriz funde kg con lb,
    una celda fundida abarca varias unidades crudas y solo se aísla
    recomputando `resolve_row` fila por fila. Los tres flags —`unquantified`,
    `unresolved_basis`, `unconverted_unit`, cada uno `0`/`1` o ausente— separan
    la celda resuelta de las que quedaron pendientes por cantidad, basis o
    unidad no convertible; sin ellos el link de una celda de una sola
    observación caía sobre todo el bloque producto/provincia/formato. `currency`
    sigue comparándose contra la moneda cruda y solo lo manda la celda
    `unconverted` (ver 2026-08-13): la ambigüedad moneda↔celda no la cierra
    este cambio y queda anotada en `docs/decisions.md`.

    `unconverted` acá **no** filtra: es el conteo de filas guardadas sin
    convertir que trae el redirect del submit para el aviso de
    `_unconverted_notice`.

    Sin paginación: al volumen de Fase 0 (decenas de anuncios) toda la lista
    entra en una sola página, y paginar antes de tener ese problema es
    complejidad sin uso (spec.md §12.6, mismo criterio que ya rige `/control`).
    Una query de filas por anuncio (N+1) es aceptable al mismo volumen —
    agregarla en una sola consulta con `string_agg` ataría la vista al
    dialecto de PostgreSQL, que los tests no corren (ver tests/conftest.py).
    """
    filtering = any(
        v is not None
        for v in (
            product,
            province,
            currency,
            unit,
            sale_format,
            format_id,
            unquantified,
            unresolved_basis,
            unconverted_unit,
        )
    )

    stmt = (
        select(RawIngestion)
        .where(RawIngestion.processing_status == ProcessingStatus.CURATED)
        .order_by(RawIngestion.captured_at.desc())
    )
    all_ingestions = list(db.execute(stmt).scalars().all())

    ingestions: list[RawIngestion] = []
    rows_by_ingestion: dict[uuid.UUID, list[tuple[str, PriceHistory]]] = {}

    for ingestion in all_ingestions:
        row_stmt = (
            select(Product.name, Product.unit, Province.name, PriceHistory)
            .join(Product, Product.id == PriceHistory.product_id)
            .outerjoin(Province, Province.id == PriceHistory.province_id)
            .where(PriceHistory.source_id == ingestion.id)
            .order_by(PriceHistory.created_at)
        )
        matched: list[tuple[str, PriceHistory]] = []
        for product_name, product_unit, province_name, price in db.execute(row_stmt).all():
            if filtering:
                if product is not None and product_name != product:
                    continue
                if province is not None and (province_name or "") != province:
                    continue
                if currency is not None and price.currency != currency:
                    continue
                if sale_format is not None and (price.sale_format or "") != sale_format:
                    continue
                # El formato es la identidad de la serie: sin este filtro, la
                # celda de «caja de 40 lb» abriría también las del paquete de
                # 10 lb — las dos llevan `unit = "lb"` y comparten el resto.
                if format_id is not None and str(price.format_id or "") != format_id:
                    continue
                effective = resolve_row(
                    currency=price.currency,
                    price=price.price,
                    price_cup=price.price_cup,
                    quantity=price.quantity,
                    price_basis=price.price_basis,
                    unit=price.unit,
                    product_unit=product_unit,
                )
                if unit is not None and (effective.unit or "") != unit:
                    continue
                if unquantified is not None and effective.unquantified != bool(unquantified):
                    continue
                if (
                    unresolved_basis is not None
                    and effective.unresolved_basis != bool(unresolved_basis)
                ):
                    continue
                if (
                    unconverted_unit is not None
                    and effective.unconverted_unit != bool(unconverted_unit)
                ):
                    continue
            matched.append((product_name, price))

        if filtering and not matched:
            continue  # este anuncio no tiene nada que ver con la celda filtrada

        ingestions.append(ingestion)
        rows_by_ingestion[ingestion.id] = matched

    edits_by_ingestion = _edit_history(db, [i.id for i in ingestions])

    return request.app.state.templates.TemplateResponse(
        request,
        "curation_history.html",
        {
            "ingestions": ingestions,
            "rows_by_ingestion": rows_by_ingestion,
            "edits_by_ingestion": edits_by_ingestion,
            "filtering": filtering,
            "filter_product": product,
            "filter_province": province,
            "filter_currency": currency,
            "filter_unit": unit,
            "filter_sale_format": sale_format,
            "notice": _unconverted_notice(unconverted),
        },
    )


@router.post("/curation/{ingestion_id}/delete")
def delete_curated(
    ingestion_id: uuid.UUID, db: Session = Depends(get_db)
) -> RedirectResponse:
    """Elimina un anuncio curado: borra sus filas de `prices_history` y lo
    pasa a `discarded` — no vuelve a la cola ni al historial (2026-08-12).

    El registro crudo **nunca se toca**. No es una promesa de este endpoint,
    es una garantía del esquema: `PriceHistory.source_id` tiene
    `ondelete="RESTRICT"` contra `raw_ingestions` (spec.md §6.1, línea 321),
    así que ni intentando se podría arrastrar el archivo capturado en el
    mismo borrado. Por eso el orden acá no importa para la integridad —
    aunque de todos modos se borran las filas de precio antes de tocar el
    estado del anuncio, por claridad de lectura.
    """
    ingestion = _require_curated(db, ingestion_id)
    deleted_rows = []
    for price in _curated_rows(db, ingestion_id):
        deleted_rows.append(
            {"price_history_id": str(price.id), "before": row_text(row_values(price))}
        )
        db.delete(price)
    ingestion.processing_status = ProcessingStatus.DISCARDED
    # Sin `shown_at`: se dispara desde el historial, donde no hay un «momento
    # en que se mostró» que signifique algo (spec A2).
    record_curation_event(
        db,
        ingestion_id=ingestion.id,
        action=CurationAction.DELETED,
        mode=CurationMode.INDIVIDUAL,
        detail={"rows": deleted_rows},
    )
    db.commit()
    return RedirectResponse(url="/curation/history", status_code=status.HTTP_303_SEE_OTHER)


def _edit_blank_row(db: Session, ingestion: RawIngestion) -> dict[str, str]:
    """Molde de la fila que el operador agrega en la corrección (botón
    «Agregar fila»). Igual que el de la cola: en blanco, con `price_id` vacío
    y con la provincia del canal ya puesta si el canal tiene una fija."""
    blank_row = _values_from_proposal(None)
    channel_province_id = _channel_province_id(db, ingestion)
    if channel_province_id is not None:
        blank_row["province_id"] = channel_province_id
    return blank_row


def _row_identity_from_form(
    product: Product, price_value: Decimal, province: Province | None, row: dict[str, str]
) -> tuple[object, ...]:
    """Lo que hace que dos filas de un mismo anuncio sean «la misma», leído de
    una fila del formulario. `submit_edit` lo usa para no crear una fila
    repetida; `_row_identity_stored` arma la misma tupla, en el mismo orden,
    para una fila ya guardada.

    Entra todo lo que distingue una fila de otra: producto, precio, moneda,
    cantidad, unidad, provincia, base del precio, formato de venta, compra
    mínima, envase y los tres ejes de la variante. Cada campo se normaliza
    igual que al guardar, pero **sin escribir nada**: no se llama a
    `_resolve_format_id` ni a `_resolve_variant_id`, que crearían un
    `ProductFormat` o un `ProductVariant` solo para comparar. En su lugar se
    compara lo mismo que esos helpers usan como clave:

    - El envase solo cuenta si el triple envase/cantidad/unidad está completo,
      que es la condición de `_resolve_format_id` para ligar un formato; con
      el triple incompleto la fila queda sin formato y el envase tipeado se
      pierde, así que acá vale `None`.
    - Los ejes de la variante van plegados con `variant_key` (sin tildes ni
      mayúsculas), la misma clave del UNIQUE de `product_variants`.
    """
    quantity = _parse_decimal(row["quantity"])
    unit = canonical_unit(row["unit"])
    packaging = canonical_packaging(row["packaging"])
    if quantity is None or unit is None:
        packaging = None
    return (
        product.id,
        price_value,
        row["currency"],
        quantity,
        unit,
        province.id if province is not None else None,
        _price_basis_value(row["price_basis"]),
        row["sale_format"] if row["sale_format"] in _SALE_FORMAT_VALUES else None,
        _parse_min_purchase(row["min_purchase"]),
        packaging,
        variant_key(row["variant_kind"]),
        variant_key(row["variant_brand"]),
        variant_key(row["variant_origin"]),
    )


def _row_identity_stored(db: Session, price: PriceHistory) -> tuple[object, ...]:
    """La misma tupla que `_row_identity_from_form`, para una fila guardada.
    El envase y los ejes salen del `ProductFormat` y el `ProductVariant` que
    la fila ya tiene ligados; sin ellos valen `None` y `""`, que es lo que da
    el formulario con esos campos vacíos."""
    fmt = db.get(ProductFormat, price.format_id) if price.format_id else None
    variant = db.get(ProductVariant, price.variant_id) if price.variant_id else None
    return (
        price.product_id,
        price.price,
        price.currency,
        price.quantity,
        price.unit,
        price.province_id,
        price.price_basis,
        price.sale_format,
        price.min_purchase_quantity,
        fmt.packaging if fmt is not None else None,
        variant.kind_key if variant is not None else "",
        variant.brand_key if variant is not None else "",
        variant.origin_key if variant is not None else "",
    )


@router.get("/curation/{ingestion_id}/edit", response_class=HTMLResponse)
def edit_curated(
    ingestion_id: uuid.UUID, request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    """Corrección de un anuncio ya curado.

    Reabre las filas de `prices_history` que ese anuncio ya produjo, contra
    la misma evidencia, sin volver a correr el motor de extracción. Es la vía
    para arreglar un error de tipeo sin SQL directo contra producción.

    Además de corregir y eliminar filas, la pantalla deja **agregar** una que
    faltó en la curaduría original: trae el mismo molde `<template>` y el
    mismo botón «Agregar fila» que la cola. La tarjeta nueva viaja con
    `price_id` vacío y la crea `submit_edit`.
    """
    ingestion = _require_curated(db, ingestion_id)
    prices = _curated_rows(db, ingestion_id)
    rows = [
        _values_from_price_history(
            price,
            db.get(ProductFormat, price.format_id) if price.format_id else None,
            db.get(ProductVariant, price.variant_id) if price.variant_id else None,
        )
        for price in prices
    ]

    return request.app.state.templates.TemplateResponse(
        request,
        "curation_edit.html",
        {
            "ingestion": ingestion,
            "products": _pilot_products(db),
            "provinces": _all_provinces(db),
            "rows": rows,
            "blank_row": _edit_blank_row(db, ingestion),
            "edit_mode": True,
            # B3-4: aviso, no bloqueo. Editar cambia lo publicado y no la copia
            # congelada contra la que mide H0.
            "frozen_version": frozen_version_of(db, ingestion_id),
            "values": {
                "source_reference": ingestion.source_reference or "",
                # Una sola fecha de publicación por anuncio: todas sus filas
                # se cargaron con la misma (ver `submit_curation`), así que
                # alcanza con la de la primera.
                "published_date": (
                    prices[0].published_date.isoformat()
                    if prices and prices[0].published_date
                    else ""
                ),
            },
            "error": None,
            # Cuándo se mostró la corrección: viaja oculto y vuelve al guardar.
            "shown_at": datetime.now(UTC).isoformat(),
            "currencies": [c.value for c in Currency],
            "units": UNITS,
            "packaging": PACKAGING,
            "variant_options": _variant_options(db),
            "format_options": _format_options(db),
        },
    )


@router.post("/curation/{ingestion_id}/edit", response_model=None)
async def submit_edit(
    ingestion_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    rate_fetcher: Fetcher = Depends(get_rate_fetcher),
) -> HTMLResponse | RedirectResponse:
    """Aplica la corrección: actualiza en el lugar las filas que el operador
    reabrió. Puede **borrar** una fila marcada con `delete_i` —una duplicada,
    o una que no correspondía— y puede **crear** las que el operador agregó,
    siempre que el anuncio quede con al menos una. Vaciarlo entero es un
    descarte, no una corrección.

    Cada fila ya curada viaja con su propio `price_id_i`, a diferencia de la
    curaduría original: matchear solo por posición confundiría un
    reordenamiento accidental del formulario con una corrección real. Qué se
    hace con una fila lo decide ese campo, y solo ese:

    - `price_id_i` de una fila de este anuncio: se actualiza (o se borra).
    - `price_id_i` **vacío**: es una fila agregada con «Agregar fila». Si no
      trae ni producto ni precio se ignora, igual que una fila en blanco de
      la cola; si trae algo, se valida con las mismas reglas que una fila de
      `submit_curation` y se crea.
    - `price_id_i` con cualquier otro valor —ilegible, o de otro anuncio—: es
      un error explícito. Un id viejo o cruzado nunca crea una fila nueva por
      accidente: para crear hace falta que el campo llegue vacío.

    Una fila creada acá nace igual que una de la cola: `provenance = human`,
    la fecha de publicación del anuncio (la del formulario, que es la misma
    que se les escribe a las demás filas), formato y variante resueltos con
    los mismos helpers, y la tasa de cambio resuelta si no es CUP. Una fila
    nueva igual a otra del anuncio en todos sus campos (ver
    `_row_identity_from_form`) es un error: es lo que frena el doble envío
    del mismo formulario. Mismo
    contrato todo-o-nada que `submit_curation`: una fila con error —nueva o
    ya curada— bloquea el guardado entero.

    La tasa de cambio SOLO se re-resuelve cuando cambia `currency` o `price`
    respecto de lo que la fila ya tenía guardado (design.md D6): editar un
    campo no relacionado (provincia, unidad, formato) no debe pisar la tasa
    de la fecha en la que el anuncio se publicó con la de hoy. Una fila que
    nunca tuvo tasa (`exchange_rate is None`) igual se intenta resolver en
    cada edición, cambie o no algo más — no hay nada que preservar ahí.
    """
    ingestion = _require_curated(db, ingestion_id)
    form = await request.form()

    source_reference = str(form.get("source_reference", "")).strip()
    published_date_raw = str(form.get("published_date", "")).strip()
    published_date, published_date_error = _validated_published_date(
        published_date_raw, ingestion.captured_at
    )
    try:
        row_count = int(str(form.get("row_count", "0")))
    except ValueError:
        row_count = 0
    shown_at_raw = str(form.get("shown_at", "")).strip()

    existing_by_id = {price.id: price for price in _curated_rows(db, ingestion_id)}
    products_by_name = {p.name.lower(): p for p in _pilot_products(db)}

    errors: list[str] = []
    if published_date_error is not None:
        errors.append(published_date_error)
    typed_rows: list[dict[str, str]] = []
    updates: list[tuple[PriceHistory, Product, Decimal, Province | None, dict[str, str]]] = []
    # Filas nuevas ya validadas. Acá solo se juntan los datos: el `PriceHistory`
    # se arma después de la rama de error, porque resolver formato y variante
    # puede escribir en el catálogo.
    # El primer elemento es el número de la tarjeta, para el mensaje de error
    # de fila repetida, que recién se puede calcular al terminar el recorrido.
    additions: list[tuple[int, Product, Decimal, Province | None, dict[str, str]]] = []
    to_delete: list[PriceHistory] = []

    for i in range(row_count):
        row = _row_from_form(form, i)
        is_new = not row["price_id"]

        product = _resolve_row_product(db, products_by_name, row)
        # Fila agregada y dejada en blanco, o el hueco de una tarjeta que el
        # operador quitó: se ignora sin error, mismo criterio que
        # `submit_curation`. Tampoco se vuelve a dibujar si el form rebota.
        if is_new and product is None and not row["price"]:
            continue

        typed_rows.append(row)
        # El número del mensaje es el de la tarjeta en pantalla. Coincide con
        # `i + 1` salvo que antes haya una fila nueva en blanco, que no se
        # redibuja y correría la cuenta.
        number = len(typed_rows)

        existing: PriceHistory | None = None
        if not is_new:
            price_uuid = _parse_uuid(row["price_id"])
            existing = existing_by_id.get(price_uuid) if price_uuid is not None else None
            if existing is None:
                errors.append(
                    f"Fila {number}: no corresponde a una fila curada de este anuncio"
                )
                continue

            # Fila marcada para borrar: se va sin validarse — un precio vacío
            # en una fila que el operador está sacando no debe bloquear el
            # guardado.
            if form.get(f"delete_{i}"):
                to_delete.append(existing)
                continue

        if product is None:
            errors.append(f"Fila {number}: seleccioná o agregá un producto")
            continue

        if not row["price"]:
            errors.append(f"Fila {number}: falta el precio")
            continue

        price_value = _parse_decimal(row["price"])
        if price_value is None:
            errors.append(f"Fila {number}: precio inválido")
            continue

        province_uuid = _parse_uuid(row["province_id"])
        province = db.get(Province, province_uuid) if province_uuid is not None else None

        quantity_value = _parse_decimal(row["quantity"])
        price_basis = _price_basis_value(row["price_basis"])
        if price_basis is None and _price_basis_required(quantity_value):
            errors.append(
                f"Fila {number}: declará si el precio es por unidad o por el lote entero"
            )
            continue

        if existing is None:
            additions.append((number, product, price_value, province, row))
        else:
            updates.append((existing, product, price_value, province, row))

    # Una fila nueva igual a otra del anuncio no se crea. Es la defensa contra
    # el doble envío: guardar, volver atrás en el navegador y guardar otra vez
    # reenvía la tarjeta con `price_id` vacío, y sin esto la fila se duplica
    # (la cola no lo necesita: su segundo envío rebota con 409 porque el
    # anuncio ya no está pendiente). Se compara contra las filas como van a
    # quedar con ESTE envío: las editadas con sus valores nuevos, las borradas
    # no cuentan, y las que el form no trajo cuentan con lo que tienen
    # guardado. Solo si no hay otro error: con una fila inválida todavía no se
    # sabe cómo queda el anuncio.
    if not errors:
        touched = {existing.id for existing, *_ in updates} | {p.id for p in to_delete}
        # Un `set` de tuplas: `Decimal("10.00")` y `Decimal("10")` son iguales
        # y tienen el mismo hash, así que los ceros de más no esconden un
        # duplicado.
        seen = {
            _row_identity_from_form(product, price_value, province, row)
            for _existing, product, price_value, province, row in updates
        }
        seen |= {
            _row_identity_stored(db, p) for p in existing_by_id.values() if p.id not in touched
        }
        for number, product, price_value, province, row in additions:
            identity = _row_identity_from_form(product, price_value, province, row)
            if identity in seen:
                errors.append(f"Fila {number}: ya hay una fila igual en este anuncio")
            # Se agrega igual: dos filas nuevas idénticas en el mismo envío
            # también chocan entre sí.
            seen.add(identity)

    # Un anuncio sin ninguna fila de precio no es una corrección, es un
    # descarte — y para eso está `POST /curation/{id}/discard`, que además
    # exige el motivo. Acá se rechaza antes de tocar nada. Borrar todas las
    # que había y agregar una nueva sí pasa: el anuncio no queda vacío.
    if not errors and to_delete and not updates and not additions:
        errors.append(
            "No podés dejar el anuncio sin filas: si no sirve, usá «descartar» en su lugar."
        )

    if errors:
        db.rollback()
        return request.app.state.templates.TemplateResponse(
            request,
            "curation_edit.html",
            {
                "ingestion": ingestion,
                "products": _pilot_products(db),
                "provinces": _all_provinces(db),
                "rows": typed_rows,
                "blank_row": _edit_blank_row(db, ingestion),
                "edit_mode": True,
                "values": {
                    "source_reference": source_reference,
                    "published_date": published_date_raw,
                },
                "error": " ".join(errors),
                # El reloj de la primera vista sobrevive al reintento.
                "shown_at": shown_at_raw or None,
                "currencies": [c.value for c in Currency],
                "units": UNITS,
                "packaging": PACKAGING,
                "variant_options": _variant_options(db),
                "format_options": _format_options(db),
            },
            status_code=422,
        )

    resolver = RateResolver(db, rate_fetcher)
    unconverted = 0
    # Lo que había antes de tocar nada, para el `detail` del evento.
    before_values = {existing.id: row_values(existing) for existing, *_ in updates}
    deleted_entries = [
        {"price_history_id": str(price.id), "op": "deleted", "before": row_text(row_values(price))}
        for price in to_delete
    ]
    source_reference_before = ingestion.source_reference
    for existing, product, price_value, province, row in updates:
        # `changed` se calcula ANTES de mutar `existing` (design.md D6): una
        # comparación posterior siempre daría `False`, porque ya se habría
        # pisado el valor viejo. `Decimal("10.00") != Decimal("10")` es
        # `False` — el round-trip de ceros de más no genera un falso positivo.
        changed = row["currency"] != existing.currency or price_value != existing.price

        existing.product_id = product.id
        existing.province_id = province.id if province is not None else None
        existing.price = price_value
        existing.currency = row["currency"]
        existing.published_date = published_date

        # La resolución de tasa va ANTES de `_resolve_format_id` (bug real de
        # producción, 2026-09-04, docs/decisions.md): ese helper puede crear
        # un `ProductFormat` nuevo, y el `session.flush()` explícito que eso
        # dispara persiste TODA la sesión, `existing` incluido. Si para
        # entonces `existing.currency` ya fuera `"CUP"` pero `exchange_rate`
        # todavía tuviera la tasa vieja de la moneda anterior sin limpiar,
        # ese flush intentaba guardar una fila que viola
        # `ck_prices_history_exchange_rate_cup` — reventaba con
        # `IntegrityError` y la corrección entera se perdía. Resolviendo acá,
        # `existing` nunca llega a `_resolve_format_id` en un estado a medio
        # actualizar.
        if row["currency"] == Currency.CUP.value:
            apply_resolution(existing, NO_RATE)  # moneda nacional: nunca lleva tasa
        elif changed or existing.exchange_rate is None:
            anchor = anchor_date(existing.published_date, ingestion.captured_at)
            apply_resolution(existing, resolver.resolve(existing.currency, anchor))
            if existing.exchange_rate is None:
                unconverted += 1
        # si no cambió ni moneda ni precio y ya tenía tasa: no se toca (D6).

        existing.quantity = _parse_decimal(row["quantity"])
        existing.unit = canonical_unit(row["unit"])
        existing.sale_format = (
            row["sale_format"] if row["sale_format"] in _SALE_FORMAT_VALUES else None
        )
        existing.format_id = _resolve_format_id(db, product.id, row)
        # Misma razón que `_resolve_format_id` arriba (D6): va después de
        # resolver la tasa, nunca antes.
        existing.variant_id = _resolve_variant_id(db, product.id, row)
        existing.min_purchase_quantity = _parse_min_purchase(row["min_purchase"])
        # Re-derivados de `row` con los mismos dos helpers puros que ya
        # validaron esta fila en el primer loop (D4): son funciones puras de
        # los strings crudos, así que no pueden derivar del valor validado
        # arriba — ensanchar la tupla `updates` de 5 a 7 elementos para
        # cargarlos ya validados haría lo mismo con más superficie.
        existing.price_basis = _price_basis_value(row["price_basis"])

    for price in to_delete:
        db.delete(price)

    # Filas agregadas: nacen igual que en `submit_curation`. Van después de
    # las actualizaciones a propósito: `_resolve_format_id` y
    # `_resolve_variant_id` pueden hacer `flush()` de toda la sesión, y para
    # entonces cada fila ya curada tiene que estar completa (ver el bug del
    # 2026-09-04, más arriba). La fila nueva todavía no está en la sesión
    # cuando se resuelven, así que ese `flush` no la toca a medio armar.
    created: list[PriceHistory] = []
    for _number, product, price_value, province, row in additions:
        entry = PriceHistory(
            product_id=product.id,
            province_id=province.id if province is not None else None,
            price=price_value,
            currency=row["currency"],
            quantity=_parse_decimal(row["quantity"]),
            unit=canonical_unit(row["unit"]),
            sale_format=(
                row["sale_format"] if row["sale_format"] in _SALE_FORMAT_VALUES else None
            ),
            format_id=_resolve_format_id(db, product.id, row),
            variant_id=_resolve_variant_id(db, product.id, row),
            min_purchase_quantity=_parse_min_purchase(row["min_purchase"]),
            price_basis=_price_basis_value(row["price_basis"]),
            # La misma fecha que se les acaba de escribir a las demás filas.
            published_date=published_date,
            source_id=ingestion.id,
            # La hizo una persona, con el formulario delante (B1).
            provenance=Provenance.HUMAN.value,
        )
        if entry.currency != Currency.CUP.value:
            anchor = anchor_date(entry.published_date, ingestion.captured_at)
            apply_resolution(entry, resolver.resolve(entry.currency, anchor))
            if entry.exchange_rate is None:
                unconverted += 1
        db.add(entry)
        created.append(entry)

    # Mismo criterio que `submit_curation`: solo se pisa si llegó algo. Vaciar
    # `source_reference` desde acá no está soportado en este slice.
    if source_reference:
        ingestion.source_reference = redact_personal_data(source_reference)

    # Solo los campos que cambiaron (comparados por valor, así «10.00» y «10»
    # no cuentan); una fila sin cambios no aparece.
    detail_rows: list[dict] = []
    for existing, *_ in updates:
        before = before_values[existing.id]
        after = row_values(existing)
        changed = [field for field in before if before[field] != after[field]]
        if changed:
            detail_rows.append(
                {
                    "price_history_id": str(existing.id),
                    "op": "updated",
                    "before": row_text({field: before[field] for field in changed}),
                    "after": row_text({field: after[field] for field in changed}),
                }
            )
    # Revalidación (B1): TODAS las filas que quedan del anuncio —las nuevas
    # incluidas—, no solo las tocadas. El `detail` del evento solo lleva lo que
    # cambió el operador: `row_values` no mira las columnas de validación, así
    # que da igual armarlo antes o después. `provenance` de las filas ya
    # curadas no se toca. Las filas borradas ya salieron con el `flush`.
    db.flush()
    # Una fila agregada va con `op: "added"` y todos sus valores en `after`
    # (no hay `before`: no existía). Se arma después del `flush`, que es el
    # que le asigna el id. Quien lea un `detail` viejo no ve nunca esta
    # operación, y quien no la conozca la saltea: `describe_edit` ignora las
    # que no reconoce.
    added_entries = [
        {"price_history_id": str(entry.id), "op": "added", "after": row_text(row_values(entry))}
        for entry in created
    ]
    detail: dict = {"rows": detail_rows + deleted_entries + added_entries}
    if ingestion.source_reference != source_reference_before:
        detail["source_reference"] = {
            "before": source_reference_before,
            "after": ingestion.source_reference,
        }
    remaining = [price for price in existing_by_id.values() if price not in to_delete]
    persist.validate_and_store(db, ingestion, remaining + created)

    # Una edición sin cambios escribe igual su evento: el operador abrió, miró
    # y confirmó, y ese tiempo cuenta (spec A2, pregunta 2).
    record_curation_event(
        db,
        ingestion_id=ingestion.id,
        action=CurationAction.EDITED,
        mode=CurationMode.INDIVIDUAL,
        shown_at=parse_shown_at(shown_at_raw),
        detail=detail,
    )
    # B2b-3: si el anuncio lo había aceptado el carril 1 y nadie lo auditó,
    # esta edición es su auditoría (con cambios, un error; sin cambios, «está
    # bien»). En cualquier otro anuncio no hace nada.
    lane1.after_edit(db, ingestion, changed=bool(detail["rows"]))
    db.commit()

    redirect_url = (
        f"/curation/history?unconverted={unconverted}" if unconverted else "/curation/history"
    )
    return RedirectResponse(url=redirect_url, status_code=status.HTTP_303_SEE_OTHER)
