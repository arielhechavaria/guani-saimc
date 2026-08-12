"""Catálogo de productos.

Corresponde a `products` en la Processing Layer (spec.md §7.2).
"""

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Index, String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from saimc.models.base import Base, utc_now


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (Index("idx_products_name", "name", unique=True),)

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    unit: Mapped[str] = mapped_column(String(20), nullable=False)
    # Lista de strings. JSON y no ARRAY: es el único tipo de colección que se
    # comporta igual en PostgreSQL y en el SQLite de los tests.
    aliases: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list, server_default=text("'[]'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
