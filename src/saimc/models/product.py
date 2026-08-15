"""Catálogo de productos.

Corresponde a `products` en la Processing Layer (spec.md §7.2).
"""

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Index, String, Uuid, false, text
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
    # Producto piloto: el conjunto acotado que la Fase 0 mide de verdad
    # (spec.md §9, «3-5 productos bandera»). Solo los pilotos se extraen en la
    # curaduría (docs/decisions.md, 2026-08-15): el catálogo puede tener
    # cualquier cosa, pero un producto fuera del foco no produce precios
    # nuevos. Arranca en `false` a propósito — sumar un piloto es una decisión
    # explícita del operador, no el efecto colateral de haber visto un anuncio.
    is_pilot: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
