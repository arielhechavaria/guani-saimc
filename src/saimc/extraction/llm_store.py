"""Persistencia de la propuesta del modelo y de la revisión del operador.

Slice 3a (`docs/spec-slice-3a.md`). `propose_ad` queda puro, sin base: acá vive
lo que la curaduría necesita para guardar una propuesta, volver a mostrarla sin
llamar a la API y capturar qué hizo el operador con ella al guardar o descartar.

Solo hay caminos de ALTA: ninguna función de este módulo modifica ni borra una
fila existente de `llm_proposals` o `llm_reviews`.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, object_session

from saimc.extraction.engine import ExtractionProposal
from saimc.extraction.llm_checks import RowMeta
from saimc.extraction.llm_propose_ad import PROMPT_VERSION, AdProposal
from saimc.models import (
    LlmProposal,
    LlmReview,
    PriceHistory,
    Product,
    ProductFormat,
    ProposalStatus,
    Province,
)
from saimc.privacy import redact_personal_data
from saimc.units import canonical_unit

# Los campos que el modelo propone por fila y que la revisión compara: los
# seis del slice 3a y los tres del 3d.
_FIELDS_3D = ("sale_format", "price_basis", "packaging")
REVIEW_FIELDS = (
    "product_id",
    "price",
    "currency",
    "quantity",
    "unit",
    "province_id",
    *_FIELDS_3D,
)

# Las dos que se comparan como número: «500» y «500.00» son el mismo precio.
_DECIMAL_FIELDS = frozenset({"price", "quantity"})

# Cortes de `propose_ad` antes de llamar: no son invocaciones y no se guardan
# (mismo criterio que el registro del operador).
_NOT_INVOKED = frozenset({"no_key", "empty_text"})


def _text(value: object) -> str | None:
    return None if value is None else str(value)


def save_proposal(
    db: Session, *, ingestion_id: uuid.UUID, result: AdProposal, model: str
) -> LlmProposal | None:
    """Da de alta la propuesta (también si degradó). `None` si no hubo llamada.

    No hace commit: lo decide quien llama.
    """
    if result.failure in _NOT_INVOKED:
        return None
    rows = [
        {
            "product_id": _text(row.product.id) if row.product is not None else None,
            "price": _text(row.price),
            "currency": row.currency,
            "quantity": _text(row.quantity),
            "unit": canonical_unit(row.unit),
            "province_id": _text(row.province.id) if row.province is not None else None,
            "sale_format": row.sale_format,
            "price_basis": row.price_basis,
            "packaging": row.packaging,
            "evidence": meta.evidence,
            "currency_explicit": meta.currency_explicit,
            "evidence_found": meta.evidence_found,
            "price_in_evidence": meta.price_in_evidence,
        }
        for row, meta in zip(result.rows, result.row_meta, strict=True)
    ]
    proposal = LlmProposal(
        ingestion_id=ingestion_id,
        model=model,
        prompt_version=PROMPT_VERSION,
        status=ProposalStatus.DEGRADED if result.degraded else ProposalStatus.OK,
        failure=result.failure or None,
        discard=result.discard,
        discard_reason=result.discard_reason,
        rows=rows,
        raw_response=result.raw_response,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )
    db.add(proposal)
    db.flush()
    return proposal


def latest_ok_proposal(db: Session, ingestion_id: uuid.UUID) -> LlmProposal | None:
    """La última propuesta que anduvo para este anuncio, o `None`."""
    stmt = (
        select(LlmProposal)
        .where(
            LlmProposal.ingestion_id == ingestion_id,
            LlmProposal.status == ProposalStatus.OK,
        )
        .order_by(LlmProposal.created_at.desc())
        .limit(1)
    )
    return db.execute(stmt).scalars().first()


def _as_uuid(raw: object) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(raw))
    except (ValueError, TypeError):
        return None


def _as_decimal(raw: object) -> Decimal | None:
    if raw is None:
        return None
    try:
        return Decimal(str(raw))
    except InvalidOperation:
        return None


def _str_or_none(raw: object) -> str | None:
    return raw if isinstance(raw, str) and raw else None


def to_ad_proposal(db: Session, proposal: LlmProposal) -> tuple[AdProposal, list[int]]:
    """Reconstruye el `AdProposal` de una propuesta guardada, sin llamar a nada.

    Devuelve también el índice original de cada fila: si el producto de una
    fila ya no existe (hoy no se borran productos, pero la fila guardada no
    tiene clave foránea), la fila se omite y los índices del resto no se corren.
    """
    rows: list[ExtractionProposal] = []
    metas: list[RowMeta] = []
    indices: list[int] = []
    for i, item in enumerate(proposal.rows):
        product_uuid = _as_uuid(item.get("product_id"))
        product = db.get(Product, product_uuid) if product_uuid is not None else None
        if product is None:
            continue
        province_uuid = _as_uuid(item.get("province_id"))
        province = db.get(Province, province_uuid) if province_uuid is not None else None
        rows.append(
            ExtractionProposal(
                product=product,
                price=_as_decimal(item.get("price")),
                currency=str(item.get("currency") or "CUP"),
                quantity=_as_decimal(item.get("quantity")),
                unit=item.get("unit"),
                province=province,
                # Una propuesta guardada antes del 3d no los trae: `None`.
                sale_format=_str_or_none(item.get("sale_format")),
                price_basis=_str_or_none(item.get("price_basis")),
                packaging=_str_or_none(item.get("packaging")),
            )
        )
        metas.append(
            RowMeta(
                evidence=item.get("evidence"),
                currency_explicit=item.get("currency_explicit") is True,
                evidence_found=item.get("evidence_found") is True,
                price_in_evidence=item.get("price_in_evidence"),
            )
        )
        indices.append(i)
    result = AdProposal(
        rows=rows,
        row_meta=metas,
        discard=proposal.discard,
        discard_reason=proposal.discard_reason,
        input_tokens=proposal.input_tokens,
        output_tokens=proposal.output_tokens,
        raw_response=proposal.raw_response,
    )
    return result, indices


def resolve_proposal(
    db: Session, raw_id: str, ingestion_id: uuid.UUID
) -> LlmProposal | None:
    """La propuesta que dice el formulario, solo si es de ESTE anuncio.

    El id viaja en un campo oculto: uno que no parsea, que no existe o que es
    de otro anuncio se ignora (no se captura nada y la curaduría sigue).
    """
    proposal_uuid = _as_uuid(raw_id) if raw_id else None
    if proposal_uuid is None:
        return None
    proposal = db.get(LlmProposal, proposal_uuid)
    if proposal is None or proposal.ingestion_id != ingestion_id:
        return None
    return proposal


def _proposed_fields(item: dict[str, Any]) -> dict[str, str | None]:
    """Lo que propuso el modelo. Los tres campos del 3d, solo si la propuesta
    los trae: una guardada antes no se los preguntó al modelo, y el operador
    llenándolos no es corregirlo (`_edited` compara solo lo propuesto)."""
    return {
        campo: item.get(campo)
        for campo in REVIEW_FIELDS
        if campo not in _FIELDS_3D or campo in item
    }


def _final_packaging(entry: PriceHistory) -> str | None:
    """El envase del formato que quedó guardado con la fila, o `None`.

    El envase no es columna de `prices_history`: vive en `ProductFormat`. Sin
    formato (triple incompleto) no quedó ningún envase guardado.
    """
    if entry.format_id is None:
        return None
    db = object_session(entry)
    fmt = db.get(ProductFormat, entry.format_id) if db is not None else None
    return fmt.packaging if fmt is not None else None


def final_fields(entry: PriceHistory) -> dict[str, str | None]:
    """Los campos tal como el operador los confirmó al guardar."""
    return {
        "product_id": _text(entry.product_id),
        "price": _text(entry.price),
        "currency": entry.currency,
        "quantity": _text(entry.quantity),
        "unit": entry.unit,
        "province_id": _text(entry.province_id),
        "sale_format": entry.sale_format,
        "price_basis": entry.price_basis,
        "packaging": _final_packaging(entry),
    }


def _same(field: str, a: str | None, b: str | None) -> bool:
    if field in _DECIMAL_FIELDS:
        return _as_decimal(a) == _as_decimal(b)
    return a == b


def _edited(
    proposed: dict[str, str | None] | None, final: dict[str, str | None] | None
) -> list[str]:
    """Campos donde propuesta y valor final difieren.

    Vacío si falta uno de los dos lados: una fila borrada o agregada a mano ya
    lo dice con su `null`, y listar todos los campos solo agregaría ruido.
    Solo se comparan los campos que la propuesta trae (ver `_proposed_fields`).
    """
    if proposed is None or final is None:
        return []
    return [
        c for c in REVIEW_FIELDS if c in proposed and not _same(c, proposed[c], final[c])
    ]


def _entry(
    index: int | None,
    proposed: dict[str, str | None] | None,
    saved: PriceHistory | None,
) -> dict[str, Any]:
    final = final_fields(saved) if saved is not None else None
    return {
        "proposal_index": index,
        "price_history_id": _text(saved.id) if saved is not None else None,
        "proposed": proposed,
        "final": final,
        "edited": _edited(proposed, final),
    }


def review_rows(
    proposal: LlmProposal, saved: Sequence[tuple[str, PriceHistory]]
) -> list[dict[str, Any]]:
    """Una entrada por fila propuesta y por fila agregada a mano.

    `saved` son las filas guardadas con el `proposal_row` que traía cada una
    del formulario. Un índice vacío, inválido, fuera de rango o repetido cuenta
    como fila agregada a mano: el campo viaja en el formulario y no puede
    inventar una fila propuesta. Una fila propuesta sin fila guardada es una
    que el operador borró (`final` y `price_history_id` en `null`).
    """
    por_indice: dict[int, PriceHistory] = {}
    a_mano: list[PriceHistory] = []
    for raw_index, entry in saved:
        index = int(raw_index) if raw_index.isdigit() else None
        if index is None or index >= len(proposal.rows) or index in por_indice:
            a_mano.append(entry)
        else:
            por_indice[index] = entry

    entries = [
        _entry(i, _proposed_fields(item), por_indice.get(i))
        for i, item in enumerate(proposal.rows)
    ]
    entries.extend(_entry(None, None, entry) for entry in a_mano)
    return entries


def save_review(
    db: Session,
    *,
    proposal: LlmProposal,
    outcome: str,
    rows: list[dict[str, Any]],
    note: str = "",
    discard_reason: str | None = None,
    shown_at: datetime | None = None,
) -> LlmReview:
    """Da de alta la revisión. La nota se guarda con los teléfonos redactados.

    No hace commit: va en la misma transacción que la curaduría o el descarte.
    `shown_at` es cuándo se le mostró la fila al operador (slice 5); `None`
    cuando no viajó o no se pudo interpretar — no bloquea el guardado.
    """
    review = LlmReview(
        ingestion_id=proposal.ingestion_id,
        proposal_id=proposal.id,
        outcome=outcome,
        discard_reason=discard_reason,
        note=redact_personal_data(note.strip()) or None,
        rows=rows,
        shown_at=shown_at,
    )
    db.add(review)
    return review
