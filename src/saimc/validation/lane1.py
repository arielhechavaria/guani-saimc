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

- **Auditar** (`accepted`, `mark_ok`, `after_edit`, `mark_invalid`,
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
import sys
import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from saimc import curation_rows
from saimc.config import Settings, get_settings
from saimc.curation_events import record_curation_event
from saimc.db import session_scope
from saimc.exchange_rate import Fetcher, anchor_date
from saimc.exchange_rate.eltoque import fetch_rate
from saimc.extraction import llm_store
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
from saimc.units import canonical_unit
from saimc.validation import context, persist
from saimc.validation.engine import validate_ad
from saimc.validation.stats import clopper_pearson_lower, clopper_pearson_upper
from saimc.validation.types import LANE_ACCEPT, AdContext, AdValidation, ValidationConfig

#: Los parámetros del carril 1. Los fijó el operador en G1b
#: (`docs/decisions.md`, 2026-10-09, «G1b se abre adelantada» y «B2b
#: aprobada»): son decisiones suyas, no configuración del despliegue.
#:
#: - celda de 5 referencias, la misma con que se persiste la validación (B1);
#: - un canal puede salir «sucio» desde 3 decisiones individuales, y lo es si
#:   el operador tocó más de la mitad de las filas propuestas;
#: - tope de 1 fila por anuncio;
#: - las señales externas no entran (Jev no separa): sin umbral.
CONFIG = ValidationConfig(
    min_cell_n=persist.MIN_CELL_N,
    channel_min_history=3,
    channel_max_edit_rate=0.5,
    signal_high_probability=None,
    lane1_max_rows=1,
)

TEXT = ContentType.TEXT.value

#: Meta de error del carril 1, por anuncio. La eligió el operador en G1b: la
#: misma del carril 0 de texto, no más laxa.
ERROR_TARGET = 0.10
#: Anuncios auditados para dejar el arranque. Hasta ahí se audita todo y el
#: freno solo salta con evidencia (cota inferior); desde ahí rige la superior.
AUDIT_N0 = 60
#: Cuántos aceptados sin auditar se toleran durante el arranque. Es lo que
#: hace cumplir la auditoría total: el carril espera al operador.
MAX_UNAUDITED = 30

#: Las tres respuestas de la auditoría.
OK = "ok"
CORRECTED = "corrected"
INVALID = "invalid"
_ERROR_VERDICTS = frozenset({CORRECTED, INVALID})


def enabled(settings: Settings) -> bool:
    """¿Está encendido el carril 1? (`SAIMC_LANE1_ENABLED`.)"""
    return settings.lane1_enabled


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
    """Lo que dice el motor del anuncio, con los parámetros del carril 1."""
    ctx = context.build_ad_context(session, ingestion, model=result, catalog=catalog, text=text)
    return ctx, validate_ad(ctx, CONFIG)


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
) -> bool:
    """Acepta el anuncio si el motor lo manda al carril 1. `True` si lo hizo.

    Con `gate`, antes mira el freno: si está puesto, o si alguna fila cae en
    una celda divergente, el anuncio sigue pendiente.

    No hace commit: las filas van en la transacción de quien llama, dentro de
    un SAVEPOINT. Si una fila en divisa queda sin convertir se deshace el
    SAVEPOINT entero y el anuncio sigue pendiente, sin filas a medias.

    Lo que escribe es lo mismo que `submit_curation`, con `provenance = auto`
    y `mode = auto`: las filas con su tasa y su validación, el estado del
    anuncio, la revisión en `llm_reviews` (queda fuera de H0 como todo lo
    asistido, y no cuenta como decisión individual) y el evento de bitácora.
    """
    if ingestion.content_type != ContentType.TEXT:
        return False
    if gate is not None and gate.brake.on:
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
        detail={"row_count": len(entries)},
    )
    savepoint.commit()
    if gate is not None:
        gate.count_accepted()
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


def _auto_events(session: Session) -> dict[uuid.UUID, tuple[RawIngestion, CurationEvent]]:
    """Anuncio -> su aceptación automática (la primera), del más viejo al más
    nuevo."""
    stmt = (
        select(RawIngestion, CurationEvent)
        .join(RawIngestion, RawIngestion.id == CurationEvent.ingestion_id)
        .where(
            CurationEvent.action == CurationAction.CURATED,
            CurationEvent.mode == CurationMode.AUTO,
        )
        .order_by(CurationEvent.decided_at, CurationEvent.ingestion_id)
    )
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
    return verdicts


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


def accepted(session: Session, *, only_unaudited: bool = False) -> list[AcceptedAd]:
    """Lo que el carril 1 aceptó solo, del más viejo al más nuevo."""
    events = _auto_events(session)
    verdicts = _verdicts(session, events)
    ads: list[AcceptedAd] = []
    for ingestion, event in events.values():
        verdict, audited_at = verdicts.get(ingestion.id, (None, None))
        if only_unaudited and verdict is not None:
            continue
        ads.append(
            AcceptedAd(
                ingestion=ingestion,
                event=event,
                verdict=verdict,
                audited_at=audited_at,
                rows=tuple(_describe(session, row) for row in _rows(session, ingestion.id)),
            )
        )
    return ads


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
    _mark_rows_audited(rows)
    _record(session, ingestion, event, OK, outcome=ReviewOutcome.CURATED, rows=rows)
    return True


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


def audit_state(session: Session, settings: Settings) -> AuditState:
    since = settings.lane1_audit_since
    events = _auto_events(session)
    verdicts = _verdicts(session, events)
    counted = [
        verdict
        for verdict, at in verdicts.values()
        if since is None or at.date() >= since
    ]
    unaudited = sum(
        1
        for ingestion, _ in events.values()
        if ingestion.id not in verdicts
        and ingestion.processing_status == ProcessingStatus.CURATED
    )
    return AuditState(
        audited=len(counted),
        errors=sum(1 for verdict in counted if verdict in _ERROR_VERDICTS),
        unaudited=unaudited,
        since=since,
    )


@dataclass(frozen=True)
class Brake:
    on: bool
    #: Por qué está puesto, en castellano. Vacío si no lo está.
    reason: str = ""


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
    if state.starting and unaudited >= MAX_UNAUDITED:
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


def brake(session: Session, settings: Settings) -> Brake:
    state = audit_state(session, settings)
    return brake_for(audited=state.audited, errors=state.errors, unaudited=state.unaudited)


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
    cuenta lo que la propia corrida acepta."""

    def __init__(self, session: Session, settings: Settings) -> None:
        state = audit_state(session, settings)
        self._audited = state.audited
        self._errors = state.errors
        self._unaudited = state.unaudited
        self._diverging = diverging_cells(session, settings)

    @property
    def brake(self) -> Brake:
        return brake_for(
            audited=self._audited, errors=self._errors, unaudited=self._unaudited
        )

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

    def count_accepted(self) -> None:
        self._unaudited += 1


# --- el comando: lo que ya está pendiente --------------------------------------


def pending_with_proposal(
    session: Session, limit: int | None = None
) -> list[tuple[RawIngestion, LlmProposal]]:
    """Los textos pendientes que ya tienen una propuesta `ok`, del más viejo
    al más nuevo."""
    stmt = (
        select(RawIngestion)
        .where(
            RawIngestion.processing_status == ProcessingStatus.PENDING,
            RawIngestion.content_type == TEXT,
        )
        .order_by(RawIngestion.captured_at, RawIngestion.id)
    )
    pairs: list[tuple[RawIngestion, LlmProposal]] = []
    for ingestion in session.execute(stmt).scalars():
        proposal = llm_store.latest_ok_proposal(session, ingestion.id)
        if proposal is None:
            continue
        pairs.append((ingestion, proposal))
        if limit is not None and len(pairs) >= limit:
            break
    return pairs


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m saimc.validation.lane1",
        description=(
            "Aplica el carril 1 a los textos pendientes que ya tienen propuesta. "
            "No gasta API. Por defecto, en seco."
        ),
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Acepta de verdad. Exige SAIMC_LANE1_ENABLED encendido.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Cuántos anuncios mirar.")
    return parser.parse_args(argv)


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

    catalog = context.load_catalog(session)
    pairs = pending_with_proposal(session, args.limit)
    gate = Gate(session, settings)
    if not args.execute:
        out("EN SECO: no se escribe nada (con --execute se aceptan).")
    if gate.brake.on:
        out(f"Carril 1 con el freno puesto: {gate.brake.reason}.")
    accepted: list[RawIngestion] = []
    for ingestion, proposal in pairs:
        result = context.stored_proposal(session, proposal, catalog)
        if args.execute:
            ok = apply_lane_1(
                session,
                ingestion,
                proposal,
                result,
                text=None,
                catalog=catalog,
                fetcher=rate_fetcher,
                gate=gate,
            )
            # Un anuncio por transacción, como el pre-cálculo.
            session.commit()
        else:
            ctx, validation = decide(session, ingestion, result, text=None, catalog=catalog)
            ok = (
                validation.lane == LANE_ACCEPT
                and not gate.blocks(ctx)
                and _published_date(ingestion, ctx.text) is not None
            )
        if ok:
            accepted.append(ingestion)
            out(f"  {str(ingestion.id)[:8]}  {ingestion.source_reference or '(sin canal)'}")
    label = "Aceptados solos (carril 1)" if args.execute else "Entrarían al carril 1"
    out(f"{label}: {len(accepted)} de {len(pairs)} textos pendientes con propuesta.")
    if not args.execute:
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
