"""Carril 1 encendido: aceptación automática (slice B2b-2).

Contrato: `docs/spec-slice-B2b.md`. Como `lane0`, este módulo SÍ lee y escribe
la base, por eso no se importa desde `saimc.validation`.

Corre `python -m saimc.validation.lane1` (por defecto, en seco).

- **Aplicar** (`apply_lane_1`): el pre-cálculo, después de guardar la propuesta
  y de preguntar por el carril 0, le pregunta al motor (`validate_ad`) con los
  parámetros de `CONFIG`. Si da carril 1, guarda las filas en `prices_history`
  con `provenance = auto` y deja el anuncio curado, en la misma transacción.
  Solo con `SAIMC_LANE1_ENABLED` encendido. «Completar con IA» no pasa por acá:
  el operador ya está mirando el anuncio.
- **El comando** (`run`): aplica el mismo camino a los textos que ya están
  pendientes con una propuesta guardada. No gasta API. En seco lista lo que
  entraría; con `--execute` lo acepta, y solo si el interruptor está encendido.

Una fila automática se arma con las mismas piezas que una curada
(`saimc.curation_rows`): mismo formato, misma fecha por defecto y misma tasa.
Lo que no tiene una persona detrás:

- si la fecha por defecto no sirve (no hay, o es posterior a la captura), el
  anuncio queda pendiente: el formulario le habría pedido corregirla;
- si una fila en divisa no se puede convertir, el anuncio queda pendiente: no
  se publica una fila sin `price_cup`.

- **Auditar** (`audit_queue`, `mark_ok`, `after_edit`, `mark_invalid`,
  `audit_state`; slice B2b-3): el operador revisa lo aceptado solo, después de
  publicado. «Está bien» deja las filas `audited`; corregirlas en la edición
  o marcarlo como no válido cuenta como error. Cada respuesta escribe un
  evento `audited` con `detail = {"lane": 1, "verdict": ...}` y una revisión
  nueva en `llm_reviews`, que sí cuenta como decisión individual: así un canal
  junta historial y puede salir «sucio».
- **Frenar** (`brake`, `Gate`): no hay interruptor guardado. Antes de aceptar
  se calcula, de la base, si el carril está frenado: demasiados sin auditar,
  o un error auditado por encima de la meta. Además, un anuncio con una fila
  en una celda que diverge de sus filas de control (C3) no se acepta.
"""

from __future__ import annotations

import argparse
import hashlib
import random
import sys
import uuid
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from saimc import curation_rows
from saimc.config import Settings, get_settings
from saimc.curation_events import record_curation_event
from saimc.db import session_scope
from saimc.exchange_rate import Fetcher, anchor_date
from saimc.exchange_rate.eltoque import fetch_rate
from saimc.extraction import llm_store, ocr_store
from saimc.extraction.engine import ExtractionProposal
from saimc.extraction.llm_checks import match_rule_rows
from saimc.extraction.llm_propose_ad import AdProposal
from saimc.models import (
    ContentType,
    CurationAction,
    CurationEvent,
    CurationMode,
    DiscardReason,
    LlmProposal,
    LlmReview,
    PriceHistory,
    ProcessingStatus,
    Product,
    Provenance,
    Province,
    RawIngestion,
    ReviewOutcome,
)
from saimc.models.base import utc_now
from saimc.progress import _week_start
from saimc.units import canonical_unit
from saimc.validation import context, lane0, persist
from saimc.validation.engine import validate_ad
from saimc.validation.shadow import split_reasons
from saimc.validation.stats import clopper_pearson_lower, clopper_pearson_upper
from saimc.validation.types import LANE_ACCEPT, AdContext, AdValidation, ValidationConfig


#: Los parámetros del carril 1. Los fijó el operador en G1b
#: (`docs/decisions.md`, 2026-10-09, «G1b se abre adelantada» y «B2b
#: aprobada»): son decisiones suyas, no configuración del despliegue.
#:
#: - celda de 5 referencias, la misma con que se persiste la validación (B1);
#: - un canal puede salir «sucio» desde 3 decisiones individuales, y lo es si
#:   el operador tocó más de la mitad de las filas propuestas;
#: - sin tope de filas por anuncio salvo que `SAIMC_LANE1_MAX_ROWS` lo ponga
#:   (`build_config`). Era 1, subió a 4 el 2026-10-13 y se quitó el 2026-10-19
#:   para las listas largas de los almacenes (`docs/spec-slice-lane1-sin-tope.md`);
#:   el error por número de filas se mira con `--por-filas`;
#: - las señales externas no entran (Jev no separa): sin umbral. Medido otra
#:   vez el 2026-10-16 con producto, moneda y provincia: exigir el acuerdo de
#:   Jev saca 3 errores y 26 anuncios buenos de 204, porque los errores del
#:   carril son de envase y precio, donde Jev no opina
#:   (`docs/spec-slice-jev-decisor.md` §1.4). Jev decide solo en el carril 0;
#: - el desacuerdo entre reglas y modelo no frena: manda el modelo (2026-10-13);
#: - el canal sucio no frena si es lo único y reglas y modelo coinciden en todas
#:   las filas (2026-10-17, `docs/spec-slice-canal-sucio.md`).
def build_config(settings: Settings) -> ValidationConfig:
    """Los parámetros del carril 1. Lo único que sale del ajuste es el tope de
    filas: `SAIMC_LANE1_MAX_ROWS` en 0 (o sin definir) es sin tope (`None`)."""
    return ValidationConfig(
        min_cell_n=persist.MIN_CELL_N,
        channel_min_history=persist.CHANNEL_MIN_HISTORY,
        channel_max_edit_rate=persist.CHANNEL_MAX_EDIT_RATE,
        signal_high_probability=None,
        lane1_max_rows=settings.lane1_max_rows or None,
        lane1_model_over_rules=True,
        lane1_dirty_channel_if_agree=True,
    )


#: Se arma una vez, al importar, con el ajuste del proceso: cambiar el tope
#: pide reiniciar, como cualquier otra variable del `.env`.
CONFIG = build_config(get_settings())

TEXT = ContentType.TEXT.value
IMAGE = ContentType.IMAGE.value
#: Los tipos con freno, auditoría y cota propios (slice OCR como texto): los
#: errores del OCR se miden aparte de los del texto nativo.
TYPES = (TEXT, IMAGE)
#: `detail["text_source"]` del evento de una imagen aceptada: el motor la leyó
#: por el texto de su OCR. Un texto no lleva la clave.
TEXT_SOURCE_OCR = "ocr"
#: Cómo se nombra cada tipo en las salidas y en las pantallas.
TYPE_LABELS: Mapping[str, str] = {TEXT: "textos", IMAGE: "imágenes"}

#: Meta de error del carril 1, por anuncio. La eligió el operador en G1b: la
#: misma del carril 0 de texto, no más laxa.
ERROR_TARGET = 0.10
#: Anuncios auditados para dejar el arranque. Hasta ahí se audita todo y el
#: freno solo salta con evidencia (cota inferior); desde ahí rige la superior.
AUDIT_N0 = 60
#: Cuántos aceptados sin auditar se toleran durante el arranque. `None` = sin
#: tope: el carril no espera al operador, y el único freno es el error medido.
#: Era 30 (hacía cumplir la auditoría total); se quitó el 2026-10-13 por
#: decisión del operador para que el flujo corra solo (ver `docs/decisions.md`).
MAX_UNAUDITED: int | None = None
#: Semilla del orden de la cola durante el arranque (`_start_order`).
START_SEED = "lane1-arranque"
#: Terminado el arranque, cuántos anuncios por semana cerrada entran en la
#: muestra de auditoría, repartidos entre los canales.
WEEKLY_SAMPLE = 30

#: Las tres respuestas de la auditoría.
OK = "ok"
CORRECTED = "corrected"
INVALID = "invalid"
_ERROR_VERDICTS = frozenset({CORRECTED, INVALID})
#: Una corrección que solo llenó huecos: la compra mínima (el modelo nunca la
#: propone) o un `format_id` que estaba vacío. No cambia ningún precio ya
#: publicado: cuenta como auditado sin error. Se decide al leer la bitácora, así
#: que vale también para lo ya auditado (2026-10-13 y 2026-10-17,
#: `docs/decisions.md`).
COMPLETED = "completed"
_MINIMUM_FIELD = "min_purchase_quantity"
_FORMAT_FIELD = "format_id"


def _fills_gap(entry: dict[str, Any]) -> bool:
    """¿La fila editada solo llenó huecos: la compra mínima, o un formato que
    estaba vacío? Cambiar un formato por otro, o quitarlo, es un cambio real."""
    after, before = entry.get("after"), entry.get("before")
    if entry.get("op") != "updated" or not isinstance(after, dict) or not after:
        return False
    if not set(after) <= {_MINIMUM_FIELD, _FORMAT_FIELD}:
        return False
    if _FORMAT_FIELD in after:
        return (
            isinstance(before, dict)
            and before.get(_FORMAT_FIELD) is None
            and after[_FORMAT_FIELD] is not None
        )
    return True


def _only_completes_gaps(detail: dict[str, Any] | None) -> bool:
    """¿La edición solo llenó huecos de filas que ya existían?

    Una fila agregada o borrada, o un cambio de canal, es un cambio real.
    """
    if not detail or "source_reference" in detail:
        return False
    rows = detail.get("rows")
    if not isinstance(rows, list) or not rows:
        return False
    return all(isinstance(entry, dict) and _fills_gap(entry) for entry in rows)


def enabled(settings: Settings) -> bool:
    """¿Está encendido el carril 1? (`SAIMC_LANE1_ENABLED`.)"""
    return settings.lane1_enabled


def image_enabled(settings: Settings) -> bool:
    """¿Acepta también imágenes? `SAIMC_LANE1_IMAGE_ENABLED` Y el carril 1
    encendido (slice OCR como texto). Solo, el de imágenes no hace nada."""
    return settings.lane1_enabled and settings.lane1_image_enabled


def text_of(session: Session, ingestion: RawIngestion) -> str | None:
    """El texto del anuncio para el carril 1, o `None` si no se conoce.

    Un texto, su original. Una imagen, la transcripción del OCR ya guardada y
    redactada: nunca `original_text`, y sin pagar OCR (slice OCR como texto).
    """
    if ingestion.content_type == ContentType.TEXT:
        return ingestion.original_text
    return ocr_store.saved_transcription(session, ingestion)


def rate_fetcher(settings: Settings) -> Fetcher:
    """El mismo `Fetcher` de la cola (`web.deps.get_rate_fetcher`), fuera de
    FastAPI: `eltoque.fetch_rate` con la configuración del proceso."""

    def _fetch(key: str, day: date) -> Decimal | None:
        return fetch_rate(key, day, api_key=settings.el_toque_api, timeout=settings.el_toque_timeout)

    return _fetch


# --- decidir ------------------------------------------------------------------


def decide(
    session: Session,
    ingestion: RawIngestion,
    result: AdProposal,
    *,
    text: str | None,
    catalog: context.Catalog,
) -> tuple[AdContext, AdValidation]:
    """Lo que dice el motor del anuncio, con los parámetros del carril 1.

    Una imagen se evalúa como un texto más, sobre su OCR (`lane1_images`): si
    se acepta o no lo deciden su interruptor y su freno, en `apply_lane_1`.
    """
    ctx = context.build_ad_context(session, ingestion, model=result, catalog=catalog, text=text)
    return ctx, validate_ad(
        ctx, CONFIG, lane1_images=ingestion.content_type == ContentType.IMAGE
    )


def _published_date(ingestion: RawIngestion, text: str) -> date | None:
    """La fecha que el formulario habría prellenado, si el formulario la habría
    aceptado: no posterior al día de la captura en La Habana. Si no, `None`."""
    guessed = curation_rows.default_published_date(ingestion, text)
    if guessed is None or guessed > anchor_date(None, ingestion.captured_at):
        return None
    return guessed


# --- aplicar ------------------------------------------------------------------


def _entries(
    session: Session,
    ingestion: RawIngestion,
    ctx: AdContext,
    published_date: date,
) -> list[PriceHistory]:
    """Las filas de la propuesta, tal como las habría dejado el formulario sin
    que el operador tocara nada.

    El formulario parte de la fila de las reglas que le corresponde para lo que
    el modelo no propone (la compra mínima) y pone encima los campos del
    modelo (`curation._rows_from_model`). La provincia ya viene con la del
    canal aplicada (`build_ad_context`). La variante no la propone nadie.
    """
    model = ctx.model
    assert model is not None  # noqa: S101 — el carril 1 exige propuesta
    rules: list[ExtractionProposal | None] = list(match_rule_rows(model.rows, list(ctx.rule_rows)))
    entries: list[PriceHistory] = []
    for row, rule in zip(model.rows, rules, strict=True):
        # El carril 1 exige producto, precio y provincia (regla 1): no son None.
        assert row.product is not None and row.price is not None  # noqa: S101
        entries.append(
            PriceHistory(
                product_id=row.product.id,
                province_id=None if row.province is None else row.province.id,
                price=row.price,
                currency=row.currency,
                quantity=row.quantity,
                unit=canonical_unit(row.unit),
                sale_format=row.sale_format,
                format_id=curation_rows.resolve_format_id(
                    session,
                    row.product.id,
                    packaging=row.packaging,
                    quantity=row.quantity,
                    unit=row.unit,
                ),
                variant_id=None,
                min_purchase_quantity=None if rule is None else rule.min_purchase_quantity,
                price_basis=row.price_basis,
                published_date=published_date,
                source_id=ingestion.id,
                # Nadie la miró: la decidió el motor (B1, B2b).
                provenance=Provenance.AUTO.value,
            )
        )
    return entries


def apply_lane_1(
    session: Session,
    ingestion: RawIngestion,
    proposal: LlmProposal,
    result: AdProposal,
    *,
    text: str | None,
    catalog: context.Catalog,
    fetcher: Fetcher,
    gate: Gate | None = None,
    images: bool = False,
) -> bool:
    """Acepta el anuncio si el motor lo manda al carril 1. `True` si lo hizo.

    Con `gate`, antes mira el freno de su tipo: si está puesto, o si alguna
    fila cae en una celda divergente, el anuncio sigue pendiente.

    Una imagen entra solo con `images` (`image_enabled`, slice OCR como
    texto) y leída por `text`, el OCR ya redactado. Su evento lleva
    `detail["text_source"] = "ocr"`; un freno puesto en las imágenes no frena
    los textos ni al revés.

    No hace commit: las filas van en la transacción de quien llama, dentro de
    un SAVEPOINT. Si una fila en divisa queda sin convertir se deshace el
    SAVEPOINT entero y el anuncio sigue pendiente, sin filas a medias.

    Lo que escribe es lo mismo que `submit_curation`, con `provenance = auto`
    y `mode = auto`: las filas con su tasa y su validación, el estado del
    anuncio, la revisión en `llm_reviews` (queda fuera de H0 como todo lo
    asistido, y no cuenta como decisión individual) y el evento de bitácora.
    """
    kind = str(ingestion.content_type)
    if kind != TEXT and not (images and kind == IMAGE):
        return False
    if gate is not None and gate.brake_of(kind).on:
        return False
    ctx, validation = decide(session, ingestion, result, text=text, catalog=catalog)
    if validation.lane != LANE_ACCEPT:
        return False
    if gate is not None and gate.blocks(ctx):
        return False
    published_date = _published_date(ingestion, ctx.text)
    if published_date is None:
        return False

    savepoint = session.begin_nested()
    entries = _entries(session, ingestion, ctx, published_date)
    if curation_rows.resolve_rates(session, entries, ingestion, fetcher):
        savepoint.rollback()
        return False
    session.add_all(entries)
    session.flush()
    llm_store.save_review(
        session,
        proposal=proposal,
        outcome=ReviewOutcome.CURATED,
        rows=llm_store.review_rows(
            proposal, [(str(index), entry) for index, entry in enumerate(entries)]
        ),
    )
    persist.validate_and_store(session, ingestion, entries)
    ingestion.processing_status = ProcessingStatus.CURATED
    record_curation_event(
        session,
        ingestion_id=ingestion.id,
        action=CurationAction.CURATED,
        mode=CurationMode.AUTO,
        proposal_id=proposal.id,
        shown_at=None,
        decided_at=utc_now(),
        detail=(
            {"row_count": len(entries)}
            if kind == TEXT
            else {"row_count": len(entries), "text_source": TEXT_SOURCE_OCR}
        ),
    )
    savepoint.commit()
    if gate is not None:
        gate.count_accepted(kind)
    return True


# --- auditar ------------------------------------------------------------------


@dataclass(frozen=True)
class AcceptedAd:
    """Un anuncio que el carril 1 aceptó solo y lo que dijo la auditoría."""

    ingestion: RawIngestion
    #: El evento `curated/auto`.
    event: CurationEvent
    #: `ok`, `corrected`, `invalid`, o `None` si todavía no se auditó.
    verdict: str | None = None
    #: Cuándo se auditó.
    audited_at: datetime | None = None
    #: Sus filas de hoy, descritas para la pantalla.
    rows: tuple[str, ...] = ()
    #: El texto que leyó el motor: el original de un texto o el OCR guardado
    #: (redactado) de una imagen (slice OCR como texto).
    text: str = ""

    @property
    def from_ocr(self) -> bool:
        """¿Se aceptó leyendo el OCR de una imagen?"""
        return self.ingestion.content_type != ContentType.TEXT


def _auto_events(
    session: Session, content_type: str | None = None
) -> dict[uuid.UUID, tuple[RawIngestion, CurationEvent]]:
    """Anuncio -> su aceptación automática (la primera), del más viejo al más
    nuevo. Con `content_type`, solo los de ese tipo."""
    stmt = (
        select(RawIngestion, CurationEvent)
        .join(RawIngestion, RawIngestion.id == CurationEvent.ingestion_id)
        .where(
            CurationEvent.action == CurationAction.CURATED,
            CurationEvent.mode == CurationMode.AUTO,
        )
        .order_by(CurationEvent.decided_at, CurationEvent.ingestion_id)
    )
    if content_type is not None:
        stmt = stmt.where(RawIngestion.content_type == content_type)
    events: dict[uuid.UUID, tuple[RawIngestion, CurationEvent]] = {}
    for ingestion, event in session.execute(stmt):
        events.setdefault(ingestion.id, (ingestion, event))
    return events


def _verdicts(
    session: Session, ingestion_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, tuple[str, datetime]]:
    """Anuncio -> `(respuesta, cuándo)` de la auditoría del carril 1.

    La auditoría del carril 0 también escribe eventos `audited`: los de acá se
    distinguen por `detail["lane"] == 1`.
    """
    ids = list(ingestion_ids)
    if not ids:
        return {}
    stmt = (
        select(CurationEvent)
        .where(
            CurationEvent.ingestion_id.in_(ids),
            CurationEvent.action == CurationAction.AUDITED,
        )
        .order_by(CurationEvent.decided_at)
    )
    verdicts: dict[uuid.UUID, tuple[str, datetime]] = {}
    for event in session.execute(stmt).scalars():
        detail = event.detail or {}
        if detail.get("lane") == 1 and isinstance(detail.get("verdict"), str):
            verdicts[event.ingestion_id] = (detail["verdict"], context.as_utc(event.decided_at))
    _reclassify_minimum_only(session, verdicts)
    return verdicts


def _reclassify_minimum_only(
    session: Session, verdicts: dict[uuid.UUID, tuple[str, datetime]]
) -> None:
    """Las «correcciones» que solo completaron la compra mínima pasan a `completed`.

    La edición que fue la auditoría es el último evento `edited` anterior a la
    respuesta; su `detail` dice qué campos tocó.
    """
    corrected = {i: at for i, (verdict, at) in verdicts.items() if verdict == CORRECTED}
    if not corrected:
        return
    stmt = (
        select(CurationEvent)
        .where(
            CurationEvent.ingestion_id.in_(list(corrected)),
            CurationEvent.action == CurationAction.EDITED,
        )
        .order_by(CurationEvent.decided_at)
    )
    last_edit: dict[uuid.UUID, CurationEvent] = {}
    for event in session.execute(stmt).scalars():
        if context.as_utc(event.decided_at) <= corrected[event.ingestion_id]:
            last_edit[event.ingestion_id] = event
    for ingestion_id, event in last_edit.items():
        if _only_completes_gaps(event.detail):
            verdicts[ingestion_id] = (COMPLETED, corrected[ingestion_id])


def _describe(session: Session, row: PriceHistory) -> str:
    product = session.get(Product, row.product_id)
    province = session.get(Province, row.province_id) if row.province_id else None
    parts = [
        product.name if product is not None else "?",
        f"{row.price.normalize():f} {row.currency}",
    ]
    if row.quantity is not None:
        parts.append(f"{row.quantity.normalize():f} {row.unit or ''}".strip())
    if row.price_basis:
        parts.append("por unidad" if row.price_basis == "unit" else "el lote")
    if province is not None:
        parts.append(province.name)
    return " · ".join(parts)


def _rows(session: Session, ingestion_id: uuid.UUID) -> list[PriceHistory]:
    return list(
        session.execute(
            select(PriceHistory)
            .where(PriceHistory.source_id == ingestion_id)
            .order_by(PriceHistory.created_at, PriceHistory.id)
        ).scalars()
    )


def _pending_audit(
    session: Session, ingestion_id: uuid.UUID
) -> tuple[RawIngestion, CurationEvent] | None:
    """El anuncio y su aceptación automática, si todavía espera auditoría: lo
    aceptó el carril 1, nadie respondió y sigue curado."""
    found = _auto_events(session).get(ingestion_id)
    if found is None or ingestion_id in _verdicts(session, [ingestion_id]):
        return None
    ingestion, _ = found
    return found if ingestion.processing_status == ProcessingStatus.CURATED else None


def _record(
    session: Session,
    ingestion: RawIngestion,
    event: CurationEvent,
    verdict: str,
    *,
    outcome: str,
    rows: Sequence[PriceHistory],
    discard_reason: str | None = None,
) -> None:
    """Escribe la respuesta de la auditoría: el evento y una revisión nueva.

    La revisión compara la propuesta del modelo con las filas tal como
    quedaron, así que una corrección se ve como fila tocada en el historial del
    canal. Se fecha un instante después del evento: `context.batch_decisions`
    parte la historia del anuncio en la auditoría, y lo que viene después
    cuenta como decisión individual.
    """
    now = utc_now()
    record_curation_event(
        session,
        ingestion_id=ingestion.id,
        action=CurationAction.AUDITED,
        mode=CurationMode.INDIVIDUAL,
        proposal_id=event.proposal_id,
        decided_at=now,
        discard_reason=discard_reason,
        detail={"lane": 1, "verdict": verdict},
    )
    proposal = session.get(LlmProposal, event.proposal_id) if event.proposal_id else None
    if proposal is None:
        return
    # Qué fila guardada salió de qué fila de la propuesta: lo dice la revisión
    # que dejó la aceptación automática.
    first = session.execute(
        select(LlmReview)
        .where(LlmReview.proposal_id == proposal.id)
        .order_by(LlmReview.created_at)
        .limit(1)
    ).scalar_one_or_none()
    index_of = {
        entry.get("price_history_id"): entry.get("proposal_index")
        for entry in (first.rows if first is not None else [])
    }
    pairs = [
        ("" if index_of.get(str(row.id)) is None else str(index_of[str(row.id)]), row)
        for row in rows
    ]
    review = llm_store.save_review(
        session,
        proposal=proposal,
        outcome=outcome,
        rows=llm_store.review_rows(proposal, pairs),
        discard_reason=discard_reason,
    )
    review.created_at = now + timedelta(microseconds=1)


def _mark_rows_audited(rows: Sequence[PriceHistory]) -> None:
    for row in rows:
        if row.provenance == Provenance.AUTO.value:
            row.provenance = Provenance.AUDITED.value


def mark_ok(session: Session, ingestion_id: uuid.UUID) -> bool:
    """«Está bien»: las filas pasan a `audited`. `False` si no había nada que
    auditar (no lo aceptó el carril 1, ya se respondió, o ya no está curado)."""
    found = _pending_audit(session, ingestion_id)
    if found is None:
        return False
    ingestion, event = found
    rows = _rows(session, ingestion.id)
    if not rows:
        # Un «está bien» sobre un anuncio vacío contaría como acierto sin que
        # haya nada que confirmar (2026-10-17).
        return False
    _mark_rows_audited(rows)
    _record(session, ingestion, event, OK, outcome=ReviewOutcome.CURATED, rows=rows)
    return True


def curated_without_rows(session: Session) -> int:
    """Anuncios `curated` que no tienen ninguna fila de precio: un estado que
    ningún camino de la app produce y que alguien tiene que mirar."""
    has_rows = select(PriceHistory.id).where(PriceHistory.source_id == RawIngestion.id).exists()
    stmt = (
        select(func.count())
        .select_from(RawIngestion)
        .where(RawIngestion.processing_status == ProcessingStatus.CURATED, ~has_rows)
    )
    return int(session.execute(stmt).scalar_one())


def after_edit(session: Session, ingestion: RawIngestion, *, changed: bool) -> bool:
    """Lo llama la edición de un anuncio curado, después de guardar.

    Si el anuncio lo había aceptado el carril 1 y nadie lo auditó, la edición
    ES la auditoría: con cambios cuenta como error (`corrected`); sin cambios,
    el operador abrió, miró y confirmó (`ok`). En cualquier otro anuncio no
    hace nada.
    """
    found = _pending_audit(session, ingestion.id)
    if found is None:
        return False
    _, event = found
    session.flush()
    rows = _rows(session, ingestion.id)
    _mark_rows_audited(rows)
    _record(
        session,
        ingestion,
        event,
        CORRECTED if changed else OK,
        outcome=ReviewOutcome.CURATED,
        rows=rows,
    )
    return True


def mark_invalid(session: Session, ingestion_id: uuid.UUID, reason: str) -> bool:
    """«No era un anuncio válido»: borra sus filas, descarta el anuncio y
    cuenta como error. `reason` es el motivo de descarte; uno desconocido se
    guarda como `no_data`."""
    found = _pending_audit(session, ingestion_id)
    if found is None:
        return False
    ingestion, event = found
    if reason not in {r.value for r in DiscardReason}:
        reason = DiscardReason.NO_DATA.value
    for row in _rows(session, ingestion.id):
        session.delete(row)
    ingestion.processing_status = ProcessingStatus.DISCARDED
    ingestion.discard_reason = reason
    _record(
        session,
        ingestion,
        event,
        INVALID,
        outcome=ReviewOutcome.DISCARDED,
        rows=[],
        discard_reason=reason,
    )
    return True


# --- la cota y el freno ---------------------------------------------------------


@dataclass(frozen=True)
class AuditState:
    """Lo auditado del carril 1, contra su meta."""

    audited: int
    errors: int
    #: Aceptados solos que nadie auditó todavía.
    unaudited: int
    #: Desde qué día cuenta lo auditado (`SAIMC_LANE1_AUDIT_SINCE`), o `None`.
    since: date | None = None
    #: Auditados fuera de la muestra, después del arranque: no entran en la cota.
    outside: int = 0

    @property
    def lower(self) -> float | None:
        return clopper_pearson_lower(self.errors, self.audited)

    @property
    def upper(self) -> float | None:
        return clopper_pearson_upper(self.errors, self.audited)

    @property
    def starting(self) -> bool:
        """Todavía en el arranque: se audita todo."""
        return self.audited < AUDIT_N0


def _weekly_samples(
    events: Mapping[uuid.UUID, tuple[RawIngestion, CurationEvent]], today: date | None
) -> set[uuid.UUID]:
    """Los anuncios que caen en la muestra de su semana, para toda semana ya
    cerrada (lunes a domingo, la del panel de A2d y de la auditoría del carril 0).

    Estratificada por canal: dentro de cada canal el orden sale al azar con la
    semilla de la semana, y se toma de a uno por canal, en ronda, hasta
    `WEEKLY_SAMPLE`. Así un canal chico no queda afuera porque otro publica
    mucho. La misma semana da siempre la misma muestra.
    """
    weeks: dict[date, dict[str, list[uuid.UUID]]] = {}
    for ingestion, event in events.values():
        week = _week_start(event.decided_at.date())
        if not lane0.is_closed(week, today):
            continue
        channel = ingestion.source_reference or ""
        weeks.setdefault(week, {}).setdefault(channel, []).append(ingestion.id)
    sampled: set[uuid.UUID] = set()
    for week, by_channel in weeks.items():
        # No es criptografía: es que el sorteo se pueda repetir.
        rng = random.Random(f"lane1-audit-{lane0.week_label(week)}")  # noqa: S311
        queues = []
        for channel in sorted(by_channel):
            ids = sorted(by_channel[channel], key=str)
            rng.shuffle(ids)
            queues.append(ids)
        taken = 0
        while taken < WEEKLY_SAMPLE and any(queues):
            for ids in queues:
                if ids and taken < WEEKLY_SAMPLE:
                    sampled.add(ids.pop())
                    taken += 1
    return sampled


def _audit(
    session: Session, settings: Settings, today: date | None, content_type: str = TEXT
) -> tuple[AuditState, list[tuple[RawIngestion, CurationEvent]]]:
    """El estado de la auditoría de un tipo y lo que toca auditar ahora.

    Arranque: cuenta todo lo auditado y toca auditar todo. Después de
    `AUDIT_N0` respuestas, solo cuenta (y solo toca) lo que cae en la muestra
    de su semana; lo demás que el operador audite queda aparte (`outside`),
    porque no es al azar.

    Cada tipo va aparte (slice OCR como texto): su arranque, su muestra
    semanal y su cota salen solo de las aceptaciones de ese tipo.
    """
    since = settings.lane1_audit_since
    events = _auto_events(session, content_type)
    verdicts = _verdicts(session, events)
    answered = sorted(
        ((at, ingestion_id, verdict) for ingestion_id, (verdict, at) in verdicts.items()),
        key=lambda item: (item[0], str(item[1])),
    )
    answered = [item for item in answered if since is None or item[0].date() >= since]
    sampled = _weekly_samples(events, today)
    counted = [
        verdict
        for position, (_, ingestion_id, verdict) in enumerate(answered)
        if position < AUDIT_N0 or ingestion_id in sampled
    ]
    starting = len(counted) < AUDIT_N0
    waiting = [
        (ingestion, event)
        for ingestion, event in events.values()
        if ingestion.id not in verdicts
        and ingestion.processing_status == ProcessingStatus.CURATED
        and (starting or ingestion.id in sampled)
    ]
    state = AuditState(
        audited=len(counted),
        errors=sum(1 for verdict in counted if verdict in _ERROR_VERDICTS),
        unaudited=len(waiting),
        since=since,
        outside=len(answered) - len(counted),
    )
    return state, waiting


def audit_state(
    session: Session, settings: Settings, *, today: date | None = None, content_type: str = TEXT
) -> AuditState:
    """Lo auditado de un tipo contra su meta. Por defecto, los textos."""
    return _audit(session, settings, today, content_type)[0]


@dataclass(frozen=True)
class AuditPending:
    """Lo que falta auditar, para avisar (franja de `/control`, Telegram)."""

    #: Todos los aceptados solos que nadie respondió, toquen o no auditarse ahora.
    #: `AuditState.unaudited` cuenta solo los que tocan, y pasado el arranque
    #: eso es la muestra de semanas cerradas: muy menos que este total.
    total: int
    #: Los que tocan auditar ahora.
    due: int
    starting: bool
    #: Horas que lleva esperando lo que toca: en el arranque, desde que se aceptó
    #: el más viejo; después, desde que cerró la semana más vieja con muestra
    #: pendiente. `None` si no toca nada.
    waiting_hours: int | None


def audit_pending(
    session: Session, settings: Settings, *, now: datetime | None = None
) -> AuditPending:
    """Lo que falta auditar, de los dos tipos juntos: es un aviso de trabajo
    pendiente, no una cota. `starting` dice si los textos, o algún tipo con
    algo por auditar, siguen en su arranque; la espera es la del más viejo."""
    moment = now or utc_now()
    events = _auto_events(session)
    answered = _verdicts(session, events)
    total = sum(
        1
        for ingestion, _ in events.values()
        if ingestion.id not in answered and ingestion.processing_status == ProcessingStatus.CURATED
    )
    due = 0
    starting = False
    oldest: datetime | None = None
    for content_type in TYPES:
        state, waiting = _audit(session, settings, moment.date(), content_type)
        if content_type == TEXT:
            starting = state.starting
        if not waiting:
            continue
        due += len(waiting)
        starting = starting or state.starting
        since = _waiting_since(state, waiting)
        oldest = since if oldest is None else min(oldest, since)
    hours = None if oldest is None else max(0, int((moment - oldest).total_seconds() // 3600))
    return AuditPending(total=total, due=due, starting=starting, waiting_hours=hours)


def _waiting_since(
    state: AuditState, waiting: Sequence[tuple[RawIngestion, CurationEvent]]
) -> datetime:
    """Desde cuándo espera lo que toca auditar de un tipo: en el arranque,
    desde que se aceptó el más viejo; después, desde que cerró la semana más
    vieja con muestra pendiente."""
    if state.starting:
        return min(context.as_utc(event.decided_at) for _, event in waiting)
    closed = min(_week_start(event.decided_at.date()) for _, event in waiting) + timedelta(weeks=1)
    return datetime(closed.year, closed.month, closed.day, tzinfo=UTC)


def audit_queue(
    session: Session, settings: Settings, *, today: date | None = None
) -> list[AcceptedAd]:
    """Lo que toca auditar: todo durante el arranque, en un orden al azar pero
    fijo (así los primeros `AUDIT_N0` auditados son una muestra al azar y no
    los más viejos); después, la muestra de cada semana cerrada, del más viejo
    al más nuevo. Primero los textos y después las imágenes, cada tipo con
    su arranque y su muestra (slice OCR como texto)."""
    queue: list[AcceptedAd] = []
    for content_type in TYPES:
        state, waiting = _audit(session, settings, today, content_type)
        if state.starting:
            waiting = sorted(waiting, key=lambda pair: _start_order(pair[0].id))
        queue += [
            AcceptedAd(
                ingestion=ingestion,
                event=event,
                rows=tuple(_describe(session, row) for row in _rows(session, ingestion.id)),
                text=text_of(session, ingestion) or "",
            )
            for ingestion, event in waiting
        ]
    return queue


def _start_order(ingestion_id: uuid.UUID) -> str:
    """La posición de un anuncio en la cola de arranque: un hash con semilla fija.
    Depende solo del anuncio, así que auditar uno no mueve a los demás."""
    return hashlib.sha256(f"{START_SEED}:{ingestion_id}".encode()).hexdigest()


def provenance_counts(session: Session) -> dict[str, int]:
    """Cuántas filas de `prices_history` hay por procedencia: a mano (`human`),
    automáticas sin auditar (`auto`) y auditadas (`audited`)."""
    counts = dict.fromkeys((p.value for p in Provenance), 0)
    stmt = select(PriceHistory.provenance, func.count()).group_by(PriceHistory.provenance)
    for provenance, count in session.execute(stmt):
        counts[str(provenance)] = count
    return counts


@dataclass(frozen=True)
class Brake:
    on: bool
    #: Por qué está puesto, en castellano. Vacío si no lo está.
    reason: str = ""


@dataclass(frozen=True)
class Summary:
    """El carril 1 en números, para `/control` y para la metodología pública.

    `state` y `brake` son los de los textos, como siempre; las imágenes van
    aparte (`images`, `image_brake`) y nunca se suman a los textos (slice OCR
    como texto). `rows` cuenta las filas de los dos tipos.
    """

    enabled: bool
    #: Procedencia -> filas (`human`, `auto`, `audited`).
    rows: Mapping[str, int]
    state: AuditState
    brake: Brake
    image_enabled: bool = False
    images: AuditState = AuditState(audited=0, errors=0, unaudited=0)
    image_brake: Brake = Brake(False)

    @property
    def automatic_rows(self) -> int:
        """Filas que entraron sin revisión previa, auditadas o no."""
        return self.rows.get(Provenance.AUTO.value, 0) + self.rows.get(
            Provenance.AUDITED.value, 0
        )

    @property
    def active(self) -> bool:
        """Hay algo que contar: el carril está encendido o ya dejó filas."""
        return self.enabled or self.automatic_rows > 0

    @property
    def images_active(self) -> bool:
        """Hay algo que contar de las imágenes: su interruptor está encendido o
        el carril ya aceptó alguna."""
        images = self.images
        return self.image_enabled or images.audited + images.unaudited + images.outside > 0


def summary(session: Session, settings: Settings) -> Summary:
    state = audit_state(session, settings)
    images = audit_state(session, settings, content_type=IMAGE)
    return Summary(
        enabled=enabled(settings),
        rows=provenance_counts(session),
        state=state,
        brake=_brake_of(state),
        image_enabled=image_enabled(settings),
        images=images,
        image_brake=_brake_of(images),
    )


def _percent(value: float) -> str:
    return f"{100 * value:.1f}".replace(".", ",") + " %"


def brake_for(*, audited: int, errors: int, unaudited: int) -> Brake:
    """El freno, a partir de los tres conteos. Pura: no lee la base.

    1. En el arranque, demasiados sin auditar: el carril espera al operador.
    2. En el arranque, evidencia de error: la cota INFERIOR supera la meta. La
       superior no sirve con n chico (0 errores en 10 ya da 26 %).
    3. Desde `AUDIT_N0`, además, la cota SUPERIOR supera la meta.
    """
    state = AuditState(audited=audited, errors=errors, unaudited=unaudited)
    if state.starting and MAX_UNAUDITED is not None and unaudited >= MAX_UNAUDITED:
        return Brake(
            True,
            f"hay {unaudited} aceptados sin auditar (tope {MAX_UNAUDITED}) y todavía "
            f"no se llegó a {AUDIT_N0} auditados: auditá para que el carril siga",
        )
    lower, upper = state.lower, state.upper
    if lower is not None and lower > ERROR_TARGET:
        return Brake(
            True,
            f"{errors} errores en {audited} auditados: la cota inferior del error "
            f"({_percent(lower)}) supera la meta de {_percent(ERROR_TARGET)}",
        )
    if not state.starting and upper is not None and upper > ERROR_TARGET:
        return Brake(
            True,
            f"{errors} errores en {audited} auditados: la cota superior del error "
            f"({_percent(upper)}) supera la meta de {_percent(ERROR_TARGET)}",
        )
    return Brake(False)


def _brake_of(state: AuditState) -> Brake:
    return brake_for(audited=state.audited, errors=state.errors, unaudited=state.unaudited)


def brake(session: Session, settings: Settings, *, content_type: str = TEXT) -> Brake:
    """El freno de un tipo, de lo auditado de ese tipo. Por defecto, los textos."""
    return _brake_of(audit_state(session, settings, content_type=content_type))


def diverging_cells(session: Session, settings: Settings) -> frozenset[tuple[str, str | None]]:
    """`(producto, provincia)` de las celdas públicas cuya mediana diverge de
    sus filas de control (C3, `controls.cell_controls`).

    Más grueso que la celda: no mira unidad, formato ni variante. Si alguna
    celda de ese producto en esa provincia diverge, no se le suma nada solo.
    """
    # Acá y no arriba: `control_cells` arrastra la capa pública.
    from saimc.validation.control_cells import compute_cell_controls

    cells = compute_cell_controls(
        session, burst_window=timedelta(minutes=settings.burst_window_minutes)
    )
    return frozenset(
        (cell.product_name, cell.province_name) for cell in cells if cell.controls.diverges
    )


class Gate:
    """El freno durante una corrida: se lee la base una vez y después se
    cuenta lo que la propia corrida acepta. Uno por tipo (slice OCR como
    texto): el de imágenes sale solo de imágenes y el de textos, de textos."""

    def __init__(self, session: Session, settings: Settings) -> None:
        # Tipo -> [auditados, errores, sin auditar].
        self._counts: dict[str, list[int]] = {}
        for content_type in TYPES:
            state = audit_state(session, settings, content_type=content_type)
            self._counts[content_type] = [state.audited, state.errors, state.unaudited]
        self._diverging = diverging_cells(session, settings)

    @property
    def brake(self) -> Brake:
        """El freno de los textos (el de siempre)."""
        return self.brake_of(TEXT)

    def brake_of(self, content_type: str) -> Brake:
        audited, errors, unaudited = self._counts[content_type]
        return brake_for(audited=audited, errors=errors, unaudited=unaudited)

    def blocks(self, ctx: AdContext) -> bool:
        """¿Alguna fila del anuncio cae en una celda divergente?"""
        model = ctx.model
        if model is None or not self._diverging:
            return False
        return any(
            row.product is not None
            and (row.product.name, None if row.province is None else row.province.name)
            in self._diverging
            for row in model.rows
        )

    def count_accepted(self, content_type: str = TEXT) -> None:
        self._counts[content_type][2] += 1


# --- el tope de filas, medido (docs/spec-slice-tope-filas.md) -------------------


@dataclass(frozen=True)
class RowsStratum:
    """Lo auditado de los anuncios que el carril aceptó con `rows` filas."""

    #: Filas aceptadas (`row_count` del evento), o `None` si el evento no lo dice.
    rows: int | None
    audited: int
    errors: int

    @property
    def upper(self) -> float | None:
        return clopper_pearson_upper(self.errors, self.audited)


def strata_by_rows(answers: Iterable[tuple[int | None, str]]) -> list[RowsStratum]:
    """`(filas, respuesta)` -> un estrato por número de filas, de menos a más.

    Error es lo mismo que en el freno: `corrected` o `invalid`.
    """
    audited: Counter[int | None] = Counter()
    errors: Counter[int | None] = Counter()
    for rows, verdict in answers:
        audited[rows] += 1
        errors[rows] += verdict in _ERROR_VERDICTS
    return [
        RowsStratum(rows, audited[rows], errors[rows])
        for rows in sorted(audited, key=lambda k: (k is None, k or 0))
    ]


def cumulative(strata: Iterable[RowsStratum], cap: int) -> tuple[int, int]:
    """`(auditados, errores)` de los anuncios de hasta `cap` filas."""
    within = [s for s in strata if s.rows is not None and s.rows <= cap]
    return sum(s.audited for s in within), sum(s.errors for s in within)


def admissible_share(upper: float, target: float = ERROR_TARGET) -> float:
    """Qué parte de los aceptados puede ser de un largo nunca auditado sin que
    la cota del conjunto pase la meta, si lo nunca auditado fallara siempre.

    Peor caso: el conjunto queda en `upper + s·(1 − upper)`; despejando `s`.
    """
    if upper >= target:
        return 0.0
    return (target - upper) / (1 - upper)


def audited_by_rows(
    session: Session, *, since: date | None = None, content_type: str = TEXT
) -> list[RowsStratum]:
    """Lo auditado del carril 1 por número de filas, de un tipo (por defecto,
    textos). Con `since`, solo lo respondido desde ese día, como el freno con
    `SAIMC_LANE1_AUDIT_SINCE`.

    Cuenta todo lo auditado, también lo que queda fuera de la muestra
    semanal: es para decidir el tope, no para el freno.
    """
    events = _auto_events(session, content_type)
    answers: list[tuple[int | None, str]] = []
    for ingestion_id, (verdict, at) in _verdicts(session, events).items():
        if since is not None and at.date() < since:
            continue
        _, event = events[ingestion_id]
        answers.append(((event.detail or {}).get("row_count"), verdict))
    return strata_by_rows(answers)


def _rows_lines(strata: Sequence[RowsStratum]) -> list[str]:
    lines = []
    for stratum in strata:
        label = "?" if stratum.rows is None else str(stratum.rows)
        noun = "fila" if stratum.rows == 1 else "filas"
        upper = stratum.upper
        lines.append(
            f"  {label} {noun}: {stratum.errors} en {stratum.audited} auditados, "
            f"cota superior {'-' if upper is None else _percent(upper)}"
        )
    caps = sorted(s.rows for s in strata if s.rows is not None)
    for cap in caps:
        audited, errors = cumulative(strata, cap)
        upper = clopper_pearson_upper(errors, audited)
        if upper is None:
            continue
        lines.append(
            f"  hasta {cap}: {errors} en {audited}, cota superior {_percent(upper)}; "
            f"tolera {_percent(admissible_share(upper))} de anuncios más largos"
        )
    return lines


def _por_filas(session: Session, settings: Settings, out: Callable[[str], None]) -> int:
    """Solo lectura: el error auditado por número de filas, para decidir el tope."""
    cap = CONFIG.lane1_max_rows
    cap_label = "sin tope" if cap is None else f"tope vigente: {cap}"
    out(
        f"Error del carril 1 por filas aceptadas ({cap_label}; "
        f"meta {_percent(ERROR_TARGET)}). Solo lectura."
    )
    since = settings.lane1_audit_since
    for content_type in TYPES:
        strata = audited_by_rows(session, content_type=content_type)
        if content_type != TEXT and not strata:
            continue  # sin imágenes auditadas, la salida es la de siempre
        prefix = "" if content_type == TEXT else "Imágenes (por su OCR), aparte de los textos. "
        out(f"{prefix}Todo lo auditado:")
        for line in _rows_lines(strata):
            out(line)
        if since is not None:
            out(f"{prefix}Desde {since.isoformat()} (SAIMC_LANE1_AUDIT_SINCE):")
            for line in _rows_lines(
                audited_by_rows(session, since=since, content_type=content_type)
            ):
                out(line)
    out(
        "«Tolera» supone que un largo nunca auditado falla siempre: es la parte de "
        "los aceptados que puede tener más filas que el tope sin que la cota pase la meta."
    )
    return 0


# --- el comando: lo que ya está pendiente --------------------------------------


def pending_with_proposal(
    session: Session, limit: int | None = None, *, types: Sequence[str] = (TEXT,)
) -> list[tuple[RawIngestion, LlmProposal]]:
    """Los pendientes de `types` que ya tienen una propuesta `ok`, del más
    viejo al más nuevo. Una imagen, solo con el OCR ya guardado (`text_of`):
    sin él su texto no se conoce sin pagar OCR."""
    stmt = (
        select(RawIngestion)
        .where(
            RawIngestion.processing_status == ProcessingStatus.PENDING,
            RawIngestion.content_type.in_(list(types)),
        )
        .order_by(RawIngestion.captured_at, RawIngestion.id)
    )
    pairs: list[tuple[RawIngestion, LlmProposal]] = []
    for ingestion in session.execute(stmt).scalars():
        if ingestion.content_type != ContentType.TEXT and text_of(session, ingestion) is None:
            continue
        proposal = llm_store.latest_ok_proposal(session, ingestion.id)
        if proposal is None:
            continue
        pairs.append((ingestion, proposal))
        if limit is not None and len(pairs) >= limit:
            break
    return pairs


#: Motivos que el motor no conoce: los pone este módulo, igual que
#: `apply_lane_1`, con los mismos controles y en el mismo orden.
DATE_REASON = "la fecha por defecto cae después del día de La Habana"
CELL_REASON = "la celda diverge de sus filas de control"


def left_out_reasons(
    session: Session,
    ingestion: RawIngestion,
    result: AdProposal,
    *,
    catalog: context.Catalog,
    gate: Gate,
) -> frozenset[str]:
    """Por qué el anuncio no entraría al carril 1. Vacío si entra.

    Son los mismos controles que `apply_lane_1` (salvo el freno y el
    interruptor de imágenes, que son del carril y no del anuncio, y la tasa,
    que en seco no se resuelve). Una imagen se mira por su OCR guardado. Los
    motivos del motor salen de `AdValidation.reasons` sin recalcular nada,
    normalizados como los cuenta el informe de sombra (`shadow.split_reasons`).
    """
    ctx, validation = decide(
        session, ingestion, result, text=text_of(session, ingestion), catalog=catalog
    )
    reasons: set[str] = set()
    if validation.lane != LANE_ACCEPT:
        reasons |= split_reasons(validation.reasons)
    if gate.blocks(ctx):
        reasons.add(CELL_REASON)
    if _published_date(ingestion, ctx.text) is None:
        reasons.add(DATE_REASON)
    return frozenset(reasons)


def count_reasons(left_out: Sequence[frozenset[str]]) -> list[tuple[str, int, int]]:
    """`(motivo, anuncios con ese motivo, anuncios que frena solo ese motivo)`,
    del más frecuente al menos.

    La primera cuenta suma más que el total de anuncios, porque uno puede tener
    varios motivos. La segunda dice cuánto se gana quitando ese freno: lo que
    entraría y hoy no.
    """
    with_reason: Counter[str] = Counter()
    alone: Counter[str] = Counter()
    for reasons in left_out:
        with_reason.update(reasons)
        if len(reasons) == 1:
            alone.update(reasons)
    return sorted(
        ((reason, n, alone[reason]) for reason, n in with_reason.items()),
        key=lambda item: (-item[1], item[0]),
    )


def _reason_lines(left_out: Sequence[frozenset[str]], total: int) -> list[str]:
    """El desglose para el modo en seco."""
    held = [reasons for reasons in left_out if reasons]
    lines = [f"Quedan afuera: {len(held)} de {total}."]
    if not held:
        return lines
    lines.append("Motivos (un anuncio puede tener varios; «solo» = frenado únicamente por ese):")
    lines.append(f"  {'anuncios':>8} {'solo':>5}  motivo")
    lines += [
        f"  {n:>8} {alone:>5}  {reason}" for reason, n, alone in count_reasons(held)
    ]
    return lines


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m saimc.validation.lane1",
        description=(
            "Aplica el carril 1 a los pendientes que ya tienen propuesta (las imágenes, "
            "por su OCR guardado). No gasta API. Por defecto, en seco."
        ),
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Acepta de verdad. Exige SAIMC_LANE1_ENABLED encendido.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Cuántos anuncios mirar.")
    parser.add_argument(
        "--muestra",
        metavar="TEXTO",
        default=None,
        help=(
            "Solo lectura: imprime una muestra de los anuncios que se quedan afuera "
            "por un motivo que contenga TEXTO (p. ej. «las reglas ven un producto»)."
        ),
    )
    parser.add_argument("--n", type=int, default=10, help="Tamaño de la muestra (--muestra).")
    parser.add_argument(
        "--por-filas",
        action="store_true",
        help=(
            "Solo lectura: el error auditado por número de filas aceptadas y el "
            "acumulado hasta cada tope (docs/spec-slice-tope-filas.md)."
        ),
    )
    return parser.parse_args(argv)


def _muestra(
    session: Session,
    pairs: Sequence[tuple[RawIngestion, LlmProposal]],
    catalog: context.Catalog,
    gate: Gate,
    fragment: str,
    n: int,
    out: Callable[[str], None],
) -> int:
    """Solo lectura: una muestra reproducible de los anuncios que un motivo deja
    afuera, con lo que dijo el modelo y el comienzo del texto. Sirve para decidir
    con datos qué hacer con un bloque grande antes de encender el carril."""
    hits: list[tuple[RawIngestion, AdProposal]] = []
    for ingestion, proposal in pairs:
        result = context.stored_proposal(session, proposal, catalog)
        reasons = left_out_reasons(session, ingestion, result, catalog=catalog, gate=gate)
        if any(fragment in reason for reason in reasons):
            hits.append((ingestion, result))
    out(f"Muestra de {min(n, len(hits))} de {len(hits)} anuncios con «{fragment}»:")
    for ingestion, result in random.Random(0).sample(hits, min(n, len(hits))):  # noqa: S311 - muestra reproducible, no cripto
        texto = " ".join((text_of(session, ingestion) or "").split())[:280]
        out(
            f"\n  {str(ingestion.id)[:8]}  {ingestion.source_reference or '(sin canal)'}"
            f"\n  el modelo: discard={result.discard} motivo={result.discard_reason}"
            f"\n  texto: {texto}"
        )
    return 0


def run(
    args: argparse.Namespace,
    session: Session,
    settings: Settings,
    *,
    rate_fetcher: Fetcher,
    out: Callable[[str], None] = print,
) -> int:
    """Corre el modo pedido. Devuelve el código de salida."""
    if args.execute and not enabled(settings):
        out(
            "El carril 1 está apagado: no se acepta nada. Para encenderlo, "
            "SAIMC_LANE1_ENABLED=true en el entorno (ver docs/spec-slice-B2b.md)."
        )
        return 2
    if getattr(args, "por_filas", False):
        return _por_filas(session, settings, out)

    catalog = context.load_catalog(session)
    # Las imágenes se miran siempre (por su OCR guardado): en seco, para que el
    # operador vea qué entraría antes de encender su interruptor; con
    # --execute, `apply_lane_1` las deja pendientes si está apagado.
    pairs = pending_with_proposal(session, args.limit, types=TYPES)
    gate = Gate(session, settings)
    images = image_enabled(settings)
    if not args.execute:
        out("EN SECO: no se escribe nada (con --execute se aceptan).")
    if gate.brake.on:
        out(f"Carril 1 con el freno puesto: {gate.brake.reason}.")
    if gate.brake_of(IMAGE).on:
        out(f"Carril 1 con el freno de imágenes puesto: {gate.brake_of(IMAGE).reason}.")
    muestra = getattr(args, "muestra", None)
    if muestra is not None:
        return _muestra(session, pairs, catalog, gate, muestra, getattr(args, "n", 10), out)
    accepted: list[RawIngestion] = []
    left_out: list[frozenset[str]] = []
    for ingestion, proposal in pairs:
        result = context.stored_proposal(session, proposal, catalog)
        if args.execute:
            ok = apply_lane_1(
                session,
                ingestion,
                proposal,
                result,
                text=text_of(session, ingestion),
                catalog=catalog,
                fetcher=rate_fetcher,
                gate=gate,
                images=images,
            )
            # Un anuncio por transacción, como el pre-cálculo.
            session.commit()
        else:
            reasons = left_out_reasons(session, ingestion, result, catalog=catalog, gate=gate)
            left_out.append(reasons)
            ok = not reasons
        if ok:
            accepted.append(ingestion)
            out(f"  {str(ingestion.id)[:8]}  {ingestion.source_reference or '(sin canal)'}")
    label = "Aceptados solos (carril 1)" if args.execute else "Entrarían al carril 1"
    out(f"{label}: {len(accepted)} de {len(pairs)} pendientes con propuesta.")
    out(
        "  "
        + ", ".join(
            f"{TYPE_LABELS[t]} {sum(1 for i in accepted if i.content_type == t)} de "
            f"{sum(1 for i, _ in pairs if i.content_type == t)}"
            for t in TYPES
        )
        + ("" if images else " (imágenes con SAIMC_LANE1_IMAGE_ENABLED apagado: no se aceptan)")
    )
    if not args.execute:
        for line in _reason_lines(left_out, len(pairs)):
            out(line)
        if gate.brake.on:
            out("Con el freno puesto, --execute no aceptaría ninguno, sea cual sea su motivo.")
        out("En seco no se resuelve la tasa de cambio: una fila en divisa sin tasa no entra.")
    return 0


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    settings = get_settings()
    with session_scope() as session:
        code = run(args, session, settings, rate_fetcher=rate_fetcher(settings))
    sys.exit(code)


if __name__ == "__main__":
    main()
