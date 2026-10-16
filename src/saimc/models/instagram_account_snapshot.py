"""Seguidores y publicaciones de la cuenta de Instagram de Guani, por lectura.

Slice de seguimiento de Instagram (`docs/spec-slice-instagram.md`). Una fila
por corrida del lote: es la serie de «seguidores en el tiempo» de
`/marketing`. Tabla de solo altas, como `instagram_media_metrics`. Solo cifras
de la cuenta propia: nunca quiénes son los seguidores.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from saimc.models.base import Base, utc_now


class InstagramAccountSnapshot(Base):
    __tablename__ = "instagram_account_snapshots"
    # `/marketing` lee las lecturas recientes en orden de fecha.
    __table_args__ = (Index("idx_instagram_account_snapshots_captured_at", "captured_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    followers_count: Mapped[int] = mapped_column(Integer, nullable=False)
    media_count: Mapped[int] = mapped_column(Integer, nullable=False)
