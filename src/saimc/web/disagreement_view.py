"""Anuncios en desacuerdo: el modelo los descarta y las reglas ven un piloto.

El carril 0 del motor (`validation.engine.decide_lane_0`) descarta solo lo que
el modelo descarta por `off_focus` o `no_data` Y las reglas no ven ningún
producto piloto. Cuando el modelo descarta pero las reglas sí ven uno (con algún
dígito en el texto), el carril 0 lo manda a revisión por ESE motivo y nada más.
Esos anuncios son la vista `/curation/desacuerdo`.

Este módulo no decide nada por su cuenta: la pertenencia se averigua con el
propio motor. Se le pregunta a `decide_lane_0` dos veces, con el catálogo
piloto y sin él; si con él da revisión y sin él descarte, el piloto es lo único
que lo frena. Así la definición no copia ningún predicado del motor ni se
desfasa cuando el motor cambie.
"""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from saimc import curation_rows
from saimc.exchange_rate import anchor_date
from saimc.extraction.engine import ExtractionProposal, mentions_any_product
from saimc.models import (
    ContentType,
    LlmProposal,
    PriceHistory,
    ProcessingStatus,
    ProposalStatus,
    Provenance,
    RawIngestion,
)
from saimc.units import canonical_unit
from saimc.validation import LANE_DISCARD, LANE_REVIEW, context, decide_lane_0, lane0
from saimc.validation.engine import has_digit

#: Los motivos con que el modelo puede descartar y que el operador ve.
DISCARD_LABELS = {"off_focus": "fuera de foco", "no_data": "sin dato útil"}


@dataclass(frozen=True)
class DisagreementItem:
    """Un anuncio en desacuerdo, con las dos lecturas listas para mostrar."""

    ingestion: RawIngestion
    proposal: LlmProposal
    #: Lo que dijo el modelo: `off_focus` o `no_data`.
    model_reason: str
    #: Todo lo que las reglas leyeron, también lo incompleto.
    rule_rows: tuple[ExtractionProposal, ...]
    #: Las filas de las reglas que se pueden guardar tal cual.
    savable_rows: tuple[ExtractionProposal, ...]
    #: La fecha de publicación con que se guardarían, o `None` si no hay una
    #: confiable (entonces «Aceptar reglas» no se ofrece).
    published_date: date | None

    @property
    def can_accept_rules(self) -> bool:
        return bool(self.savable_rows) and self.published_date is not None


def _is_savable(row: ExtractionProposal) -> bool:
    """¿Se puede guardar tal cual, sin que nadie complete nada?

    Producto y precio son obligatorios. Con cantidad distinta de uno, el
    formulario exige declarar si el precio es por unidad o por el lote; las
    reglas solo lo proponen cuando el anuncio lo declara (`price_basis`). Si no
    vino, esa pregunta es del operador y este botón no la contesta por él.
    """
    if row.product is None or row.price is None:
        return False
    needs_basis = row.quantity is not None and row.quantity != Decimal(1)
    return not (needs_basis and row.price_basis is None)


def _accepted_date(ingestion: RawIngestion, text: str) -> date | None:
    """La fecha que prellenaría el formulario, si éste la aceptaría: no
    posterior al día de la captura en La Habana. Misma regla que el carril 1."""
    guessed = curation_rows.default_published_date(ingestion, text)
    if guessed is None or guessed > anchor_date(None, ingestion.captured_at):
        return None
    return guessed


def latest_ok_proposals(
    db: Session, ingestion_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, LlmProposal]:
    """La última propuesta OK de cada id, en una sola consulta."""
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


def _item_for(
    db: Session,
    ingestion: RawIngestion,
    proposal: LlmProposal | None,
    catalog: context.Catalog,
) -> DisagreementItem | None:
    text = ingestion.original_text
    if (
        proposal is None
        or text is None
        or ingestion.content_type != ContentType.TEXT
        or proposal.discard is not True
        or proposal.discard_reason not in DISCARD_LABELS
    ):
        return None
    # Filtro barato antes de armar el contexto (canal, historial): sin dígito o
    # sin piloto en el texto el motor no pone al piloto como freno. El veredicto
    # sigue siendo del motor, abajo.
    if not has_digit(text) or not mentions_any_product(text, catalog.pilot_products):
        return None
    result = context.stored_proposal(db, proposal, catalog)
    ctx = context.build_ad_context(db, ingestion, model=result, catalog=catalog, text=text)
    with_pilots = decide_lane_0(ctx)
    without_pilots = decide_lane_0(dataclasses.replace(ctx, pilot_products=()))
    if with_pilots.lane != LANE_REVIEW or without_pilots.lane != LANE_DISCARD:
        return None
    rows = tuple(ctx.rule_rows)
    return DisagreementItem(
        ingestion=ingestion,
        proposal=proposal,
        model_reason=str(proposal.discard_reason),
        rule_rows=rows,
        savable_rows=tuple(row for row in rows if _is_savable(row)),
        published_date=_accepted_date(ingestion, ctx.text),
    )


def pending_disagreements(db: Session) -> list[DisagreementItem]:
    """Los pendientes en desacuerdo, del más viejo al más nuevo.

    Lo rescatado de la auditoría del carril 0 no entra: el operador ya dijo que
    ese anuncio se cura de a uno.
    """
    stmt = (
        select(RawIngestion)
        .where(
            RawIngestion.processing_status == ProcessingStatus.PENDING,
            RawIngestion.content_type == ContentType.TEXT,
        )
        .order_by(RawIngestion.captured_at.asc(), RawIngestion.id)
    )
    pending = list(db.execute(stmt).scalars().all())
    ids = [ingestion.id for ingestion in pending]
    proposals = latest_ok_proposals(db, ids)
    rescued = lane0.rescued_ids(db, ids)
    catalog = context.load_catalog(db)
    items = (
        _item_for(db, ingestion, proposals.get(ingestion.id), catalog)
        for ingestion in pending
        if ingestion.id not in rescued
    )
    return [item for item in items if item is not None]


def disagreement_of(db: Session, ingestion_id: uuid.UUID) -> DisagreementItem | None:
    """El desacuerdo de UN anuncio, o `None` si ya no lo es (curado en otra
    pestaña, rescatado, otra propuesta nueva…). Los POST revalidan con esto."""
    ingestion = db.get(RawIngestion, ingestion_id)
    if ingestion is None or ingestion.processing_status != ProcessingStatus.PENDING:
        return None
    if lane0.rescued_ids(db, [ingestion.id]):
        return None
    proposal = latest_ok_proposals(db, [ingestion.id]).get(ingestion.id)
    return _item_for(db, ingestion, proposal, context.load_catalog(db))


def entries_from_rules(db: Session, item: DisagreementItem) -> list[PriceHistory]:
    """Las filas de `prices_history` de lo que leyeron las reglas.

    Las arma como las habría dejado el formulario sin que el operador tocara
    nada (igual que `lane1._entries`, con el modelo y las reglas cambiados de
    lugar). `provenance = human`: las eligió una persona, con las dos lecturas
    delante.
    """
    assert item.published_date is not None  # noqa: S101 — lo exige quien llama
    entries: list[PriceHistory] = []
    for row in item.savable_rows:
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
                    db,
                    row.product.id,
                    packaging=row.packaging,
                    quantity=row.quantity,
                    unit=row.unit,
                ),
                min_purchase_quantity=row.min_purchase_quantity,
                price_basis=row.price_basis,
                published_date=item.published_date,
                source_id=item.ingestion.id,
                provenance=Provenance.HUMAN.value,
            )
        )
    return entries
