"""Escritura de la bitácora de curaduría (slice A2b, `docs/spec-slice-A2.md`).

Un único camino de alta (`record_curation_event`) y un único parseo de
`shown_at` (`parse_shown_at`), para que los cinco caminos de la cola no
diverjan. Ninguna función hace commit: el evento se agrega a la sesión antes
del commit del camino, así que el cambio y su evento van juntos o no queda
ninguno.
"""

import uuid
from collections.abc import Mapping
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
