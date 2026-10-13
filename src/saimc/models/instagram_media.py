"""Las publicaciones de la cuenta de Instagram de Guani: una fila por publicación.

Slice de seguimiento de Instagram (`docs/spec-slice-instagram.md`). Solo la
cuenta propia y solo lo que publicó Guani: el texto (`caption`) es de Guani,
nunca de un tercero. Ni comentarios ni usuarios que interactúan: las cifras de
cada lectura viven en `instagram_media_metrics`, ya agregadas por Meta.

A diferencia de las métricas, esta fila sí se actualiza: el lote refresca el
enlace, el texto y `last_synced_at` en cada lectura (Meta deja editar el texto
de una publicación). Lo que no se pisa es la serie temporal.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from saimc.models.base import Base, utc_now


class InstagramMediaType(StrEnum):
    """Dónde salió la publicación. Decide qué métricas se le piden a Meta.

    Un carrusel es `FEED`: se guarda la publicación, pero Meta no da insights de
    carruseles y el lote no se los pide (`instagram.client.metrics_for`), así
    que en `/marketing` sale sin cifras. Las stories solo tienen métricas
    durante sus 24 h de vida.
    """

    FEED = "FEED"
    REELS = "REELS"
    STORY = "STORY"


class InstagramMedia(Base):
    __tablename__ = "instagram_media"
    __table_args__ = (
        UniqueConstraint("ig_media_id", name="uq_instagram_media_ig_media_id"),
        CheckConstraint(
            "media_type IN ('FEED', 'REELS', 'STORY')", name="ck_instagram_media_media_type"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # El id que da Meta. Es la identidad real: el lote busca por acá.
    ig_media_id: Mapped[str] = mapped_column(String(64), nullable=False)
    media_type: Mapped[str] = mapped_column(String(8), nullable=False)
    # Nulos porque Meta puede no darlos (una story no siempre trae enlace).
    permalink: Mapped[str | None] = mapped_column(Text, nullable=True)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    last_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
