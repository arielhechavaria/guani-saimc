"""Historial de precios — `prices_history` en la Processing Layer (whitepaper §7.2).

Cada fila conserva linaje obligatorio hacia el registro crudo del que salió
(`source_id`, RESTRICT) y, opcionalmente, la conversión a CUP cuando existe
tasa de cambio. Este slice es esquema puro: no hay motor de validación ni
columnas de dato personal (ver exclusiones explícitas del spec).
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from saimc.models.base import Base, utc_now


class Currency(StrEnum):
    CUP = "CUP"
    USD = "USD"
    EUR = "EUR"
    MLC = "MLC"


class PriceHistory(Base):
    __tablename__ = "prices_history"  # nombre literal del whitepaper §7.2
    __table_args__ = (
        CheckConstraint(
            "currency <> 'CUP' OR exchange_rate IS NULL",
            name="ck_prices_history_exchange_rate_cup",
        ),
        CheckConstraint(
            "exchange_rate IS NULL OR "
            "(exchange_rate_date IS NOT NULL AND exchange_rate_source IS NOT NULL)",
            name="ck_prices_history_exchange_rate_provenance",
        ),
        CheckConstraint(
            "confidence_score IS NULL OR confidence_score BETWEEN 0 AND 100",
            name="ck_prices_history_confidence_score_range",
        ),
        Index("idx_prices_history_series", "product_id", "province_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "products.id",
            ondelete="RESTRICT",
            name="fk_prices_history_product_id",
        ),
        nullable=False,
    )
    # Nullable a propósito: el whitepaper (línea 733) acepta «Provincia: No
    # determinada» cuando el anuncio solo indica una zona («Oriente»).
    province_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "provinces.id",
            ondelete="RESTRICT",
            name="fk_prices_history_province_id",
        ),
        nullable=True,
    )
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    # Nulo mientras nada obtenga la tasa: un anuncio en USD se guarda sin
    # convertir en vez de bloquearse (§6.3, líneas 896-899).
    price_cup: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    exchange_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    exchange_rate_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    exchange_rate_source: Mapped[str | None] = mapped_column(String(50), nullable=True)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    # Día de publicación del anuncio, no instante — eje temporal de la serie
    # (whitepaper §6.1c/§7.2). Nullable, sin default y sin CHECK: un dato
    # secundario nunca debe bloquear un insert (design.md, D2).
    published_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Linaje al registro crudo. RESTRICT y nunca CASCADE: raw_ingestions es
    # inmutable y ningún precio publicado puede quedar sin su evidencia.
    source_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "raw_ingestions.id",
            ondelete="RESTRICT",
            name="fk_prices_history_source_id",
        ),
        nullable=False,
    )
    # Nullable y sin default: todavía no existe quién lo escriba.
    confidence_score: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
