"""Cache de tasas de cambio elTOQUE (`spec.md` §6.3 E, design.md D2).

Tabla de primera clase, sin FK a ninguna otra: guarda una tasa por par
`(currency, rate_date)`, escrita únicamente por fetches exitosos a elTOQUE
(nunca sembrada a mano). `source` viaja como dato, no como parte de la clave —
admitirlo en la clave dejaría dos tasas compitiendo para el mismo par sin
regla de selección, la ambigüedad que la fuente única existe para evitar.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Numeric, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from saimc.models.base import Base, utc_now


class ExchangeRateCache(Base):
    __tablename__ = "exchange_rate_cache"
    __table_args__ = (
        UniqueConstraint(
            "currency", "rate_date", name="uq_exchange_rate_cache_currency_rate_date"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    rate_date: Mapped[date] = mapped_column(Date, nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    # Siempre "elTOQUE" en este slice (fuente única, spec.md §6.3 E). String
    # libre y no un StrEnum de una sola opción: una segunda fuente futura no
    # debe exigir migración de esta columna.
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
