"""Escritura de la bitácora de curaduría (slice A2b, `docs/spec-slice-A2.md`).

Un único camino de alta (`record_curation_event`) y un único parseo de
`shown_at` (`parse_shown_at`), para que los cinco caminos de la cola no
diverjan. Ninguna función hace commit: el evento se agrega a la sesión antes
del commit del camino, así que el cambio y su evento van juntos o no queda
ninguno.
"""

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from saimc.models import CurationEvent, PriceHistory
from saimc.models.base import utc_now

# Los campos de `prices_history` que entran al `detail` (spec, «detail»).
# Nunca texto del anuncio ni notas.
ROW_FIELDS = (
    "product_id",
    "province_id",
    "price",
    "currency",
    "quantity",
    "unit",
    "sale_format",
    "price_basis",
    "format_id",
    "variant_id",
    "min_purchase_quantity",
    "published_date",
    "price_cup",
    "exchange_rate",
    "exchange_rate_date",
    "exchange_rate_source",
)


def parse_shown_at(raw: str | None) -> datetime | None:
    """Interpreta el `shown_at` del formulario. Vacío o mal formado da `None`
    y no bloquea nada; una hora sin zona se toma como UTC."""
    text = (raw or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def row_values(price: PriceHistory) -> dict[str, Any]:
    """Los valores crudos de los campos de fila, para comparar antes/después."""
    return {field: getattr(price, field) for field in ROW_FIELDS}


def row_text(values: Mapping[str, Any]) -> dict[str, str | None]:
    """Los valores como texto, igual que en `llm_reviews.rows`."""
    return {
        field: None if value is None else _as_text(value) for field, value in values.items()
    }


def _as_text(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def record_curation_event(
    db: Session,
    *,
    ingestion_id: uuid.UUID,
    action: str,
    mode: str,
    shown_at: datetime | None = None,
    decided_at: datetime | None = None,
    discard_reason: str | None = None,
    proposal_id: uuid.UUID | None = None,
    detail: dict[str, Any] | None = None,
) -> CurationEvent:
    """Agrega el evento a la sesión. `decided_at` por defecto es ahora; el lote
    sin foco lo pasa tomado una sola vez."""
    event = CurationEvent(
        ingestion_id=ingestion_id,
        action=action,
        mode=mode,
        discard_reason=discard_reason,
        proposal_id=proposal_id,
        shown_at=shown_at,
        decided_at=decided_at or utc_now(),
        detail=detail,
    )
    db.add(event)
    return event


# --- Lectura: el historial de ediciones (slice K1) ----------------------------

# Cómo se nombra cada campo de fila en pantalla.
FIELD_LABELS = {
    "product_id": "Producto",
    "province_id": "Provincia",
    "price": "Precio",
    "currency": "Moneda",
    "quantity": "Cantidad",
    "unit": "Unidad",
    "sale_format": "Formato de venta",
    "price_basis": "Base del precio",
    "format_id": "Formato",
    "variant_id": "Variante",
    "min_purchase_quantity": "Compra mínima",
    "published_date": "Fecha de publicación",
    "price_cup": "Precio en CUP",
    "exchange_rate": "Tasa de cambio",
    "exchange_rate_date": "Fecha de la tasa",
    "exchange_rate_source": "Fuente de la tasa",
}

# Los campos que guardan un id del catálogo: se muestran como su nombre.
CATALOG_FIELDS = ("product_id", "province_id", "format_id", "variant_id")

# `{campo: {id: nombre}}`. Lo arma el router en bloque.
CatalogNames = Mapping[str, Mapping[str, str]]

NULL_TEXT = "—"
MISSING_CATALOG_TEXT = "(eliminado)"
SOURCE_REFERENCE_LABEL = "Canal origen"


@dataclass(frozen=True)
class FieldChange:
    """Un campo para mostrar. En una fila eliminada solo se llena `after`
    (el valor que tenía); `before` queda vacío."""

    label: str
    before: str
    after: str


@dataclass(frozen=True)
class RowEdit:
    deleted: bool
    changes: list[FieldChange] = field(default_factory=list)


@dataclass(frozen=True)
class EditSummary:
    rows: list[RowEdit]
    source_reference: FieldChange | None


def referenced_catalog_ids(details: Iterable[Mapping[str, Any]]) -> dict[str, set[str]]:
    """Los ids de catálogo que mencionan los `detail`, por campo, para
    resolver sus nombres con una consulta por tabla."""
    ids: dict[str, set[str]] = {}
    for detail in details:
        for entry in detail.get("rows") or []:
            for side in ("before", "after"):
                for name in CATALOG_FIELDS:
                    value = (entry.get(side) or {}).get(name)
                    if value is not None:
                        ids.setdefault(name, set()).add(str(value))
    return ids


def _display(name: str, value: Any, names: CatalogNames) -> str:
    if value is None:
        return NULL_TEXT
    text = _as_text(value)
    if name in CATALOG_FIELDS:
        return names.get(name, {}).get(text, MISSING_CATALOG_TEXT)
    return text


def describe_edit(detail: Mapping[str, Any], names: CatalogNames) -> EditSummary | None:
    """Convierte el `detail` de un evento `edited` en filas para mostrar.

    Devuelve `None` si la edición no cambió nada (`rows: []` y sin cambio de
    `source_reference`): es una confirmación, no un cambio. Un `detail` mal
    formado se trata igual, sin romper la vista.
    """
    rows: list[RowEdit] = []
    for entry in detail.get("rows") or []:
        before = entry.get("before") or {}
        after = entry.get("after") or {}
        if entry.get("op") == "updated":
            changes = [
                FieldChange(
                    FIELD_LABELS.get(name, name),
                    _display(name, before.get(name), names),
                    _display(name, after.get(name), names),
                )
                for name in before
            ]
            rows.append(RowEdit(deleted=False, changes=changes))
        elif entry.get("op") == "deleted":
            changes = [
                FieldChange(FIELD_LABELS.get(name, name), "", _display(name, value, names))
                for name, value in before.items()
                if value is not None
            ]
            rows.append(RowEdit(deleted=True, changes=changes))

    source = detail.get("source_reference")
    source_change = (
        FieldChange(
            SOURCE_REFERENCE_LABEL,
            _display("source_reference", source.get("before"), names),
            _display("source_reference", source.get("after"), names),
        )
        if isinstance(source, Mapping)
        else None
    )
    if not rows and source_change is None:
        return None
    return EditSummary(rows=rows, source_reference=source_change)
