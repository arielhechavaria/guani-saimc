"""Armado del `AdContext` desde la base (slice A5c).

El motor (`validation.engine`) es código puro: recibe todo ya leído. Este módulo
es el que lee. Lo usan el informe de sombra (`validation.shadow`), el carril 0
(`validation.lane0`) y, desde B1, el guardado y la revalidación
(`validation.persist`, con `build_stored_rows_context`, al final del archivo).
Solo hace `SELECT`: no escribe, no llama a la API del modelo ni a elTOQUE.

Lo que arma, para un anuncio con su propuesta del modelo:

- el texto (el redactado, o la transcripción del OCR si es una imagen);
- la propuesta del modelo, con la provincia fija del canal aplicada a sus filas
  («el canal siempre manda», `docs/decisions.md` 2026-08-17: es lo que hace el
  formulario de la cola y lo que hizo el backtest);
- la propuesta de las reglas, recalculada sobre el mismo texto;
- por fila, las referencias de su celda **anteriores al día del anuncio**;
- las filas del mismo canal en las 24 h previas;
- el historial del canal: solo decisiones INDIVIDUALES del operador.

Contrato: `docs/spec-motor-validacion.md`, «Entrada» y «Protocolo del modo
sombra».
"""

from __future__ import annotations

import bisect
import dataclasses
import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from saimc.cells import resolve_row
from saimc.config import get_settings
from saimc.exchange_rate import cache as rate_cache
from saimc.exchange_rate.service import anchor_date
from saimc.extraction import llm_checks, llm_propose_ad, llm_store, ocr_store
from saimc.extraction.engine import ExtractionProposal, propose_all
from saimc.extraction.llm_propose_ad import AdProposal
from saimc.ingestion import origin
from saimc.models import (
    Channel,
    ContentType,
    CurationAction,
    CurationEvent,
    CurationMode,
    Currency,
    LlmProposal,
    LlmReview,
    PriceHistory,
    Product,
    ProposalStatus,
    Province,
    RawIngestion,
)
from saimc.progress import pilot_products
from saimc.validation.rules import DUPLICATE_WINDOW
from saimc.validation.types import (
    AdContext,
    CellReference,
    ChannelContext,
    RecentRow,
    RowContext,
)

Transcriber = ocr_store.Transcriber


def as_utc(value: datetime) -> datetime:
    """La fecha con zona horaria. SQLite (los tests) las devuelve sin zona;
    PostgreSQL, con ella. Lo guardado siempre es UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


# --- catálogo ---------------------------------------------------------------


@dataclass(frozen=True)
class Catalog:
    """Lo que se lee una sola vez por corrida: el foco y las provincias."""

    #: Los productos piloto, en el orden en que el extractor los ve.
    pilot_products: Sequence[Product]
    provinces: Sequence[Province]


def load_catalog(session: Session) -> Catalog:
    return Catalog(
        pilot_products=pilot_products(session),
        provinces=list(session.execute(select(Province)).scalars().all()),
    )


# --- qué hizo el operador con una propuesta ----------------------------------


@dataclass(frozen=True)
class ProposalDiff:
    """Diferencia entre las filas que propuso el modelo y las que quedaron.

    Es la definición de «error del carril 1» de la especificación: el operador
    editó o borró algún campo de alguna fila, o agregó una fila.
    """

    #: Filas que propuso el modelo.
    proposed_rows: int = 0
    #: De esas, las que el operador borró (o que no están en el oro).
    deleted_rows: int = 0
    #: De esas, las que quedaron con algún campo distinto.
    edited_rows: int = 0
    #: Filas que el operador agregó a mano (o que el oro trae y el modelo no).
    added_rows: int = 0
    #: Campo -> en cuántas filas quedó distinto.
    field_errors: dict[str, int] = field(default_factory=dict)

    @property
    def changed_rows(self) -> int:
        return self.deleted_rows + self.edited_rows

    @property
    def has_error(self) -> bool:
        return self.changed_rows > 0 or self.added_rows > 0


def diff_review(
    rows: Sequence[Mapping[str, Any]], *, channel_province_id: str | None
) -> ProposalDiff:
    """Lee `llm_reviews.rows` y dice qué cambió el operador.

    No usa el `edited` guardado: lo recalcula contra `final`, porque ese
    `edited` cuenta como edición la provincia que el formulario impone desde el
    canal cuando el modelo no la dio. Eso no lo corrigió el operador, y el
    carril 1 guardaría la misma provincia: con `channel_province_id` se compara
    contra ella.
    """
    proposed_rows = deleted = edited = added = 0
    field_errors: dict[str, int] = {}
    for entry in rows:
        proposed = entry.get("proposed")
        final = entry.get("final")
        if entry.get("proposal_index") is None or not isinstance(proposed, Mapping):
            added += final is not None  # `True` suma 1: `bool` es un `int`
            continue
        proposed_rows += 1
        if not isinstance(final, Mapping):
            deleted += 1
            continue
        effective = dict(proposed)
        if channel_province_id is not None:
            effective["province_id"] = channel_province_id
        changed = [
            name
            for name in llm_store.REVIEW_FIELDS
            if name in effective
            and not llm_store.same_value(name, effective[name], final.get(name))
        ]
        edited += bool(changed)
        for name in changed:
            field_errors[name] = field_errors.get(name, 0) + 1
    return ProposalDiff(proposed_rows, deleted, edited, added, field_errors)


# --- decisiones individuales y de lote ---------------------------------------


@dataclass(frozen=True)
class BatchDecisions:
    """Qué decisiones de la bitácora se tomaron en lote.

    La bitácora no dice a qué revisión corresponde cada evento, así que se
    empareja por `(anuncio, acción)`. Un rescate de la auditoría del carril 0
    (B2a) parte la historia del anuncio en tramos: el descarte automático
    queda antes del rescate, y lo que el operador decida después de rescatarlo
    es una decisión nueva, individual (caso 13 de `docs/spec-slice-B2a.md`).
    Lo mismo una auditoría del carril 1 (B2b-3): la aceptación automática queda
    antes, y la revisión que escribe la auditoría cuenta como individual.
    Sin el tramo, ese descarte de a uno heredaba el `auto` del primero y no
    contaba ni en el historial del canal ni en el modo sombra.
    """

    #: `(anuncio, acción, tramo)` de cada evento con `mode` distinto de
    #: `individual`. El tramo es cuántos rescates del anuncio hubo antes.
    batch: frozenset[tuple[uuid.UUID, str, int]]
    #: Anuncio -> horas de sus rescates, de la más vieja a la más nueva.
    rescues: Mapping[uuid.UUID, tuple[datetime, ...]]

    def segment(self, ingestion_id: uuid.UUID, at: datetime) -> int:
        """Cuántos rescates del anuncio son anteriores a `at`."""
        return bisect.bisect_left(self.rescues.get(ingestion_id, ()), as_utc(at))

    def is_batch(self, review: LlmReview) -> bool:
        key = (
            review.ingestion_id,
            str(review.outcome),
            self.segment(review.ingestion_id, review.created_at),
        )
        return key in self.batch


def batch_decisions(
    session: Session, ingestion_ids: Sequence[uuid.UUID] | None = None
) -> BatchDecisions:
    """Las decisiones que se tomaron en lote (o solas, en el carril 0).

    Una decisión es de lote si la bitácora tiene para ese anuncio, esa acción
    y ese tramo (ver `BatchDecisions`) un evento con `mode` distinto de
    `individual`.
    """
    stmt = select(
        CurationEvent.ingestion_id,
        CurationEvent.action,
        CurationEvent.mode,
        CurationEvent.decided_at,
    )
    if ingestion_ids is not None:
        stmt = stmt.where(CurationEvent.ingestion_id.in_(ingestion_ids))
    events = session.execute(stmt).all()
    rescues: dict[uuid.UUID, list[datetime]] = {}
    for ingestion_id, action, _, decided_at in events:
        # Un rescate (B2a) y una auditoría (B2a, B2b-3) parten la historia: lo
        # que el operador decida después es suyo, no del carril automático.
        if action in (CurationAction.RESCUED, CurationAction.AUDITED):
            rescues.setdefault(ingestion_id, []).append(as_utc(decided_at))
    ordered = {key: tuple(sorted(times)) for key, times in rescues.items()}
    result = BatchDecisions(batch=frozenset(), rescues=ordered)
    batch = frozenset(
        (ingestion_id, str(action), result.segment(ingestion_id, decided_at))
        for ingestion_id, action, mode, decided_at in events
        if mode != CurationMode.INDIVIDUAL
    )
    return dataclasses.replace(result, batch=batch)


def is_individual(review: LlmReview, batch: BatchDecisions) -> bool:
    """¿La decisión de esta revisión se tomó anuncio por anuncio?

    Una revisión sin ningún evento en la bitácora cuenta como individual: es
    anterior a la bitácora (slice A2b), y los dos caminos de lote escriben
    siempre su evento.
    """
    return not batch.is_batch(review)


# --- la propuesta guardada ---------------------------------------------------


def _dropped_in_response(raw_response: str | None, catalog: Catalog) -> int:
    """Filas o campos que el modelo contestó y no resolvieron contra el catálogo.

    `llm_proposals` no guarda ese conteo: se recupera volviendo a validar la
    respuesta cruda contra el catálogo de HOY. Si no hay respuesta o no es
    JSON, 0.
    """
    if not raw_response:
        return 0
    try:
        raw = json.loads(raw_response)
    except json.JSONDecodeError:
        return 0
    parsed = llm_propose_ad.parse_response(raw, catalog.pilot_products, catalog.provinces)
    return 0 if parsed is None else parsed.dropped


def _reverified(session: Session, proposal: LlmProposal, result: AdProposal) -> AdProposal:
    """La cita de cada fila, verificada otra vez contra el texto del anuncio.

    Las banderas se guardan al proponer, con la verificación de ese momento. Una
    propuesta vieja puede traer `evidence_found = False` por emoji o «...» que
    hoy la verificación sí entiende (2026-10-13). Solo sube: una bandera ya
    verdadera nunca se degrada. Solo texto: en una imagen el modelo vio la
    transcripción, no `original_text`.
    """
    ingestion = session.get(RawIngestion, proposal.ingestion_id)
    if (
        ingestion is None
        or ingestion.content_type != ContentType.TEXT
        or not ingestion.original_text
        or not result.rows
    ):
        return result
    fresh = llm_checks.verify_evidence(ingestion.original_text, result.rows, result.row_meta)
    metas = [
        dataclasses.replace(
            old,
            evidence_found=old.evidence_found or new.evidence_found,
            price_in_evidence=True if old.price_in_evidence else new.price_in_evidence,
        )
        for old, new in zip(result.row_meta, fresh, strict=True)
    ]
    return dataclasses.replace(result, row_meta=metas)


def stored_proposal(
    session: Session, proposal: LlmProposal, catalog: Catalog | None = None
) -> AdProposal:
    """El `AdProposal` de una propuesta guardada, tal como lo necesita el motor.

    `llm_store.to_ad_proposal` reconstruye las filas pero no dice si la
    propuesta degradó ni cuántas filas se cayeron: acá se completan los dos.
    """
    catalog = catalog or load_catalog(session)
    result, _ = llm_store.to_ad_proposal(session, proposal)
    result = _reverified(session, proposal, result)
    skipped = len(proposal.rows) - len(result.rows)  # producto que ya no existe
    return dataclasses.replace(
        result,
        degraded=proposal.status != ProposalStatus.OK,
        failure=proposal.failure or "",
        dropped=_dropped_in_response(proposal.raw_response, catalog) + skipped,
        prompt_version=proposal.prompt_version,
    )


# --- el texto ----------------------------------------------------------------


def ad_text(
    session: Session,
    ingestion: RawIngestion,
    *,
    raw_store_dir: Path | None = None,
    transcribe: ocr_store.Transcriber | None = None,
) -> str:
    """El texto sobre el que opinan las reglas: el mismo que vio el modelo.

    Texto: el original, ya redactado en la ingesta. Imagen: la transcripción
    guardada del OCR, ya redactada (`ocr_store.stored_transcription`, K3: la
    misma que vio el modelo, sin volver a correr el OCR); si el OCR no da
    nada, queda la transcripción que confirmó el operador. Vacío si no hay de
    dónde sacarlo.
    """
    if ingestion.content_type == ContentType.TEXT:
        return ingestion.original_text or ""
    directory = raw_store_dir if raw_store_dir is not None else get_settings().raw_store_dir
    text = ocr_store.stored_transcription(session, ingestion, directory, transcribe)
    return text if text.strip() else (ingestion.original_text or "")


# --- canal -------------------------------------------------------------------


def _same_channel(channel: Channel) -> Any:
    """Filtro SQL grueso de las ingestas de un canal. Las anteriores a
    `channel_id` solo traen el nombre; `_is_from` confirma después."""
    return or_(
        RawIngestion.channel_id == channel.id,
        RawIngestion.channel_id.is_(None) & (RawIngestion.source_reference == channel.name),
    )


def _is_from(session: Session, ingestion: RawIngestion, channel: Channel) -> bool:
    resolved = origin.resolve_origin(session, ingestion)
    return resolved is not None and resolved.id == channel.id


def channel_history(
    session: Session,
    channel: Channel,
    *,
    exclude_ingestion_id: uuid.UUID | None = None,
    before: datetime | None = None,
) -> ChannelContext:
    """El historial del canal para la regla 4: solo decisiones individuales.

    `before` corta por la hora de la decisión: sirve para reproducir lo que se
    sabía del canal en un momento dado. Sin él, todo lo decidido hasta hoy.
    """
    stmt = (
        select(LlmReview, RawIngestion)
        .join(RawIngestion, RawIngestion.id == LlmReview.ingestion_id)
        .where(_same_channel(channel))
    )
    pairs = [
        (review, ingestion)
        for review, ingestion in session.execute(stmt)
        if ingestion.id != exclude_ingestion_id
        and (before is None or as_utc(review.created_at) < as_utc(before))
        and _is_from(session, ingestion, channel)
    ]
    batch = batch_decisions(session, [ingestion.id for _, ingestion in pairs])
    province_id = None if channel.province_id is None else str(channel.province_id)
    decisions = proposed = changed = 0
    for review, _ in pairs:
        if not is_individual(review, batch):
            continue
        diff = diff_review(review.rows, channel_province_id=province_id)
        decisions += 1
        proposed += diff.proposed_rows
        changed += diff.changed_rows
    return ChannelContext(
        province_id=channel.province_id,
        decisions=decisions,
        proposed_rows=proposed,
        changed_rows=changed,
    )


def _recent_channel_rows(
    session: Session, ingestion: RawIngestion, channel: Channel, published_at: datetime
) -> list[RecentRow]:
    """Filas ya guardadas del mismo canal en las 24 h previas (regla 5).

    La hora de cada fila es la del anuncio del que salió (`captured_at`):
    `prices_history` solo guarda el día.
    """
    stmt = (
        select(PriceHistory, RawIngestion)
        .join(RawIngestion, RawIngestion.id == PriceHistory.source_id)
        .where(_same_channel(channel), PriceHistory.source_id != ingestion.id)
    )
    recent: list[RecentRow] = []
    for row, source in session.execute(stmt):
        at = as_utc(source.captured_at)
        if timedelta(0) <= published_at - at <= DUPLICATE_WINDOW and _is_from(
            session, source, channel
        ):
            recent.append(RecentRow(row.product_id, row.province_id, row.price, at))
    recent.sort(key=lambda r: r.published_at)
    return recent


# --- celdas ------------------------------------------------------------------


def _price_cup(session: Session, row: ExtractionProposal, day: date) -> Decimal | None:
    """El precio de una fila en divisa llevado a CUP con la tasa del día del
    anuncio. Solo lee la tasa ya cacheada de ESE día: no llama a elTOQUE ni usa
    una tasa de otra fecha. Sin tasa, `None` y la fila no se compara."""
    if row.price is None or row.currency == Currency.CUP.value:
        return None
    rate = rate_cache.get_for_date(session, row.currency, day)
    if rate is None:
        return None
    # Mismo redondeo que `exchange_rate.service.apply_resolution`.
    return (row.price * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _cell_unit(row: ExtractionProposal, price_cup: Decimal | None) -> str | None:
    """La unidad de la celda de la fila, o `None` si la fila no cae en ninguna
    (sin producto, sin precio, sin cantidad, sin tasa o sin saber si dividir):
    son los mismos casos en que las reglas 2 y 6 del motor no opinan."""
    if row.product is None or row.price is None:
        return None
    resolved = resolve_row(
        currency=row.currency,
        price=row.price,
        price_cup=price_cup,
        quantity=row.quantity,
        price_basis=row.price_basis,
        unit=row.unit,
        product_unit=row.product.unit,
    )
    if resolved.unconverted or resolved.unquantified or resolved.unresolved_basis:
        return None
    return resolved.unit


@dataclass(frozen=True)
class _Reference:
    province_id: uuid.UUID | None
    unit: str | None
    price: Decimal


def _references_before(
    session: Session, ingestion: RawIngestion, product_ids: set[uuid.UUID], day: date
) -> dict[uuid.UUID, list[_Reference]]:
    """Precios de `prices_history` anteriores a `day`, por producto, ya en CUP
    y por unidad base (`cells.resolve_row`, lo mismo que `compute_cells`).

    El día de una fila es su `published_date` y, si no lo tiene, el día (en
    Cuba) en que se capturó su anuncio. Es «anterior» si ese día es
    estrictamente menor: del mismo día no se sabe la hora, y no entra. Las
    filas del propio anuncio tampoco.
    """
    if not product_ids:
        return {}
    stmt = (
        select(PriceHistory, Product.unit, RawIngestion.captured_at)
        .join(Product, Product.id == PriceHistory.product_id)
        .join(RawIngestion, RawIngestion.id == PriceHistory.source_id)
        .where(
            PriceHistory.product_id.in_(product_ids),
            PriceHistory.source_id != ingestion.id,
        )
    )
    references: dict[uuid.UUID, list[_Reference]] = {}
    for row, product_unit, captured_at in session.execute(stmt):
        if anchor_date(row.published_date, as_utc(captured_at)) >= day:
            continue
        resolved = resolve_row(
            currency=row.currency,
            price=row.price,
            price_cup=row.price_cup,
            quantity=row.quantity,
            price_basis=row.price_basis,
            unit=row.unit,
            product_unit=product_unit,
        )
        if resolved.unconverted or resolved.unquantified or resolved.unresolved_basis:
            continue
        references.setdefault(row.product_id, []).append(
            _Reference(row.province_id, resolved.unit, resolved.effective_price)
        )
    return references


def _row_contexts(
    session: Session, ingestion: RawIngestion, rows: Sequence[ExtractionProposal], day: date
) -> tuple[RowContext, ...]:
    references = _references_before(
        session, ingestion, {r.product.id for r in rows if r.product is not None}, day
    )
    contexts: list[RowContext] = []
    for row in rows:
        price_cup = _price_cup(session, row, day)
        unit = _cell_unit(row, price_cup)
        same_cell = (
            []
            if row.product is None or unit is None
            else [ref for ref in references.get(row.product.id, []) if ref.unit == unit]
        )
        province_id = None if row.province is None else row.province.id
        contexts.append(
            RowContext(
                cell=CellReference(
                    own_province=tuple(
                        ref.price for ref in same_cell if ref.province_id == province_id
                    ),
                    all_provinces=tuple(ref.price for ref in same_cell),
                ),
                price_cup=price_cup,
            )
        )
    return tuple(contexts)


# --- el contexto -------------------------------------------------------------


def build_ad_context(
    session: Session,
    ingestion: RawIngestion,
    *,
    model: AdProposal | None,
    catalog: Catalog,
    text: str | None = None,
    history_before: datetime | None = None,
    raw_store_dir: Path | None = None,
    transcribe: Transcriber | None = None,
) -> AdContext:
    """Arma el `AdContext` de un anuncio leyendo la base.

    - `model`: la propuesta del modelo (`stored_proposal` para una guardada), o
      `None` si no hay.
    - `text`: el texto si quien llama ya lo tiene (el oro del backtest guarda
      la transcripción del OCR); si no, sale de `ad_text`.
    - `history_before`: hasta qué momento cuenta el historial del canal.

    La fecha del anuncio es `captured_at` (la hora del mensaje en WhatsApp).
    Las señales externas van vacías: no hay ningún proveedor.
    """
    if text is None:
        text = ad_text(session, ingestion, raw_store_dir=raw_store_dir, transcribe=transcribe)
    published_at = as_utc(ingestion.captured_at)
    day = anchor_date(None, published_at)

    channel = origin.resolve_origin(session, ingestion)
    channel_province = origin.channel_province(session, ingestion)
    if model is not None and channel_province is not None:
        # «El canal siempre manda»: igual que el formulario de la cola.
        model = dataclasses.replace(
            model,
            rows=[dataclasses.replace(row, province=channel_province) for row in model.rows],
        )

    return AdContext(
        content_type=str(ingestion.content_type),
        text=text,
        published_at=published_at,
        channel=(
            None
            if channel is None
            else channel_history(
                session, channel, exclude_ingestion_id=ingestion.id, before=history_before
            )
        ),
        model=model,
        rule_rows=propose_all(
            text, catalog.pilot_products, catalog.provinces, channel_province=channel_province
        ),
        pilot_products=catalog.pilot_products,
        province_ids=frozenset(p.id for p in catalog.provinces),
        row_contexts=() if model is None else _row_contexts(session, ingestion, model.rows, day),
        recent_channel_rows=(
            () if channel is None else _recent_channel_rows(session, ingestion, channel, published_at)
        ),
        signals=(),
    )


# --- el contexto de las filas ya guardadas (B1) --------------------------------


@dataclass(frozen=True)
class StoredRow:
    """Una fila de `prices_history` con la forma que leen las reglas, y su celda."""

    row: ExtractionProposal
    context: RowContext


@dataclass(frozen=True)
class StoredRowsContext:
    """Lo que el motor necesita para validar las filas guardadas de un anuncio."""

    #: Hora del anuncio (`captured_at`), para la ventana de la regla 5.
    published_at: datetime
    #: El canal del anuncio, solo con su provincia: el historial no se calcula
    #: mientras la regla 4 no tenga parámetros (`docs/spec-slice-B1.md`).
    channel: ChannelContext | None
    recent_channel_rows: Sequence[RecentRow]
    #: Una por fila de `prices_history`, en el orden en que se pidieron.
    rows: Sequence[StoredRow]


def _as_proposal(session: Session, row: PriceHistory, catalog: Catalog) -> ExtractionProposal:
    """La fila guardada con la forma que reciben las `check_*`: la adaptación
    más chica para reusar las reglas sin duplicarlas."""
    product = next((p for p in catalog.pilot_products if p.id == row.product_id), None)
    if product is None:
        # Un producto que ya no es piloto: la regla 1 falla, pero las demás
        # igual necesitan su unidad base.
        product = session.get(Product, row.product_id)
    return ExtractionProposal(
        product=product,
        price=row.price,
        currency=row.currency,
        quantity=row.quantity,
        unit=row.unit,
        province=next((p for p in catalog.provinces if p.id == row.province_id), None),
        min_purchase_quantity=row.min_purchase_quantity,
        price_basis=row.price_basis,
        sale_format=row.sale_format,
    )


def build_stored_rows_context(
    session: Session,
    ingestion: RawIngestion,
    rows: Sequence[PriceHistory],
    *,
    catalog: Catalog,
) -> StoredRowsContext:
    """Arma el contexto de las filas YA GUARDADAS de un anuncio (slice B1).

    A diferencia de `build_ad_context` no necesita propuesta del modelo, ni el
    texto, ni el OCR: las reglas sobre una fila guardada no leen el anuncio.

    - **Celda:** precios de días estrictamente anteriores al día de la fila
      (`published_date`, o el día de la captura si no la tiene: el mismo
      criterio con el que `_references_before` fecha cada referencia), sin las
      filas del propio anuncio. Como no depende de qué otras filas ya se
      validaron, el resultado no cambia con el orden en que se recorran.
    - **Filas recientes del canal:** las de las 24 h previas a la captura, sin
      el propio anuncio.
    - **Precio en CUP:** el `price_cup` que quedó en la fila, no una segunda
      consulta a la tasa: el motor mira el mismo número que se publica.
    - **Catálogo:** el de hoy.
    """
    published_at = as_utc(ingestion.captured_at)
    channel = origin.resolve_origin(session, ingestion)

    # Las filas de un anuncio comparten fecha de publicación, así que esto es
    # una sola consulta; se agrupa por día para no asumirlo.
    references: dict[date, dict[uuid.UUID, list[_Reference]]] = {}
    stored: list[StoredRow] = []
    for row in rows:
        proposal = _as_proposal(session, row, catalog)
        day = anchor_date(row.published_date, published_at)
        if day not in references:
            references[day] = _references_before(
                session, ingestion, {r.product_id for r in rows}, day
            )
        unit = _cell_unit(proposal, row.price_cup)
        same_cell = (
            []
            if unit is None
            else [ref for ref in references[day].get(row.product_id, []) if ref.unit == unit]
        )
        stored.append(
            StoredRow(
                row=proposal,
                context=RowContext(
                    cell=CellReference(
                        own_province=tuple(
                            ref.price for ref in same_cell if ref.province_id == row.province_id
                        ),
                        all_provinces=tuple(ref.price for ref in same_cell),
                    ),
                    price_cup=row.price_cup,
                ),
            )
        )

    return StoredRowsContext(
        published_at=published_at,
        channel=(
            None
            if channel is None
            else ChannelContext(
                province_id=channel.province_id, decisions=0, proposed_rows=0, changed_rows=0
            )
        ),
        recent_channel_rows=(
            () if channel is None else _recent_channel_rows(session, ingestion, channel, published_at)
        ),
        rows=tuple(stored),
    )
