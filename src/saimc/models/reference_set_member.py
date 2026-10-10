"""El conjunto de referencia congelado: una fila por anuncio y por versión.

Slice B3-1 (`docs/spec-slice-B3.md`, sección 1). Tabla de solo altas, como
`curation_events`: el código no tiene camino de escritura sobre una fila
existente ni de borrado. Una versión nueva vuelve a insertar todos sus miembros
con el `set_version` siguiente.

`rows` guarda la etiqueta tal como estaba al congelar, para que H0 siga
midiendo contra ella aunque `/edit` cambie o borre la fila viva. Lleva solo el
id de la fila de `prices_history` (como texto, sin clave foránea: la fila viva
puede borrarse) y los seis campos de `row_correctness`. Nunca el texto del
anuncio, el canal ni el emisor (`spec.md` §10.6).
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, SmallInteger, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from saimc.models.base import Base, utc_now


class ReferenceSetMember(Base):
    __tablename__ = "reference_set_members"
    __table_args__ = (
        # El índice de este UNIQUE ya sirve para leer una versión entera: no se
        # agrega otro.
        UniqueConstraint(
            "set_version", "ingestion_id", name="uq_reference_set_members_version_ingestion"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # La primera versión es 1. La vigente es la mayor.
    set_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    ingestion_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "raw_ingestions.id",
            ondelete="RESTRICT",
            name="fk_reference_set_members_ingestion_id",
        ),
        nullable=False,
    )
    # Forma canónica de `saimc.extraction.reference_set.canonical_rows`: todo
    # texto o nulo, para que vuelva idéntico de SQLite y de PostgreSQL.
    rows: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    # SHA-256 de `rows`. Detecta la deriva con una comparación barata.
    values_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    frozen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
