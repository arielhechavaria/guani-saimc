"""Catálogo de provincias — configuración, no código.

Agregar una provincia MUST ser una fila de datos, nunca un cambio de código
(spec.md §9.0, «Consecuencias de diseño»): por eso no se modela como `StrEnum` ni como
lista fija.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, String, Uuid, false
from sqlalchemy.orm import Mapped, mapped_column

from saimc.models.base import Base, utc_now


class Province(Base):
    __tablename__ = "provinces"
    __table_args__ = (
        Index("idx_provinces_code", "code", unique=True),
        Index("idx_provinces_name", "name", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Identificador público estable. No se deriva del nombre en runtime: los
    # nombres llevan tildes y el slug es parte de la URL pública.
    code: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # Dos estados, no tres (spec.md §9.0).
    is_published: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
