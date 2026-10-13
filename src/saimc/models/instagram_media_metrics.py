"""Las cifras de una publicación de Instagram en una lectura: serie temporal.

Slice de seguimiento de Instagram (`docs/spec-slice-instagram.md`). Tabla de
solo altas, como `curation_events` y `jev_signals`: el código no tiene camino
de escritura sobre una fila existente ni de borrado. Meta da las métricas con
ventana limitada y las recalcula con el tiempo (pueden demorar hasta 48 h);
guardar cada lectura es lo que conserva el historial.

Cada columna es nula si Meta no la dio para ese tipo: una story no tiene
`likes`, `comments` ni `saved`, y `avg_watch_time_ms` es solo de reels. Son
cifras agregadas de la cuenta propia; ninguna nombra a quien interactuó.
`views` y no `impressions`: Meta deprecó la segunda para el contenido posterior
al 2024-07-02.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from saimc.models.base import Base, utc_now


class InstagramMediaMetrics(Base):
    __tablename__ = "instagram_media_metrics"
    __table_args__ = (
        # Una lectura por publicación y por corrida: todas las filas de una
        # corrida comparten `captured_at`. El índice del UNIQUE empieza por
        # `media_id` y ya sirve para buscar la última lectura de una publicación.
        UniqueConstraint("media_id", "captured_at", name="uq_instagram_media_metrics_media_captured"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    media_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "instagram_media.id", ondelete="RESTRICT", name="fk_instagram_media_metrics_media_id"
        ),
        nullable=False,
    )
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    views: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reach: Mapped[int | None] = mapped_column(Integer, nullable=True)
    likes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comments: Mapped[int | None] = mapped_column(Integer, nullable=True)
    saved: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shares: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_interactions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # `ig_reels_avg_watch_time` de Meta, que viene en milisegundos. Solo reels.
    avg_watch_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
