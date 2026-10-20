"""Publicaciones de la fábrica de `/marketing` y sus diapositivas (F2 de
`docs/plan-fabrica-marketing.md`; `docs/spec-slice-fabrica-publicaciones.md`).

Una publicación nace borrador (la crea el lote de las 05:00 o el operador), se
aprueba —se congelan los datos en `snapshot` y se rasteriza el JPEG—, y se
marca publicada con el enlace del post. O se descarta. Las transiciones viven
en `saimc.marketing.posts`; acá están las tablas y la única garantía que no
depende de pasar por ese módulo: lo congelado no se vuelve a escribir.

Nada de esto toca datos de terceros: `params` solo tiene producto, provincia y
fecha de corte, y las cifras salen siempre de `build_context`.
"""

import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    event,
    inspect,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from saimc.models.base import Base, utc_now


class PostStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    PUBLISHED = "published"
    DISCARDED = "discarded"


class PostFormat(StrEnum):
    SINGLE = "single"
    CAROUSEL = "carousel"
    STORY = "story"


class PostOrigin(StrEnum):
    BATCH = "batch"
    OPERATOR = "operator"


class FrozenSlideError(RuntimeError):
    """Se intentó reescribir algo congelado al aprobar."""


class MarketingPost(Base):
    __tablename__ = "marketing_posts"
    __table_args__ = (
        # Un borrador del lote por día y tipo, aunque el lote corra dos veces.
        # Las del operador tienen `batch_slot` nulo y no chocan (NULL ≠ NULL).
        UniqueConstraint("planned_for", "batch_slot", name="uq_marketing_posts_day_slot"),
        CheckConstraint(
            "status IN ('draft', 'approved', 'published', 'discarded')",
            name="ck_marketing_posts_status",
        ),
        CheckConstraint(
            "format IN ('single', 'carousel', 'story')", name="ck_marketing_posts_format"
        ),
        CheckConstraint("origin IN ('batch', 'operator')", name="ck_marketing_posts_origin"),
        CheckConstraint(
            "(origin = 'batch' AND batch_slot IS NOT NULL)"
            " OR (origin = 'operator' AND batch_slot IS NULL)",
            name="ck_marketing_posts_batch_slot",
        ),
        CheckConstraint(
            "status NOT IN ('approved', 'published') OR approved_at IS NOT NULL",
            name="ck_marketing_posts_approved_at",
        ),
        CheckConstraint(
            "status <> 'published' OR (published_url IS NOT NULL AND published_at IS NOT NULL)",
            name="ck_marketing_posts_published",
        ),
        CheckConstraint(
            "status <> 'discarded' OR discarded_at IS NOT NULL",
            name="ck_marketing_posts_discarded_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    format: Mapped[str] = mapped_column(String(16), nullable=False, default=PostFormat.SINGLE)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=PostStatus.DRAFT, active_history=True
    )
    planned_for: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    # Nulo = automático: sale de los datos hasta que el operador lo edita. Al
    # aprobar se escribe el que corresponda y ya no cambia.
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    origin: Mapped[str] = mapped_column(String(16), nullable=False, default=PostOrigin.OPERATOR)
    batch_slot: Mapped[str | None] = mapped_column(String(64), nullable=True)
    published_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    discarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Envío al canal de WhatsApp (`saimc.marketing.channel`, 2026-10-20).
    # Cuántas láminas ya salieron, en orden: si un envío se corta a mitad, la
    # repetición sigue desde la siguiente y no duplica. `channel_sent_at` se
    # fija recién al completar todas.
    channel_slides_sent: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    channel_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    slides: Mapped[list["MarketingPostSlide"]] = relationship(
        back_populates="post",
        order_by="MarketingPostSlide.position",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class MarketingPostSlide(Base):
    __tablename__ = "marketing_post_slides"
    __table_args__ = (
        UniqueConstraint("post_id", "position", name="uq_marketing_post_slides_position"),
        CheckConstraint("position >= 1", name="ck_marketing_post_slides_position"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    post_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("marketing_posts.id", name="fk_marketing_post_slides_post_id", ondelete="CASCADE"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    card_slug: Mapped[str] = mapped_column(String(64), nullable=False)
    #: `{"product", "province", "data_through"}`: lo que se elige, nunca una cifra.
    params: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    #: `{"show": {bloque: bool}, "subtitle", "cta"}` (las opciones de F1).
    options: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    #: Se llena al aprobar (`saimc.marketing.posts.approve`) y no cambia más.
    # `active_history`: el listener necesita el valor anterior aunque el
    # atributo esté vencido después de un commit.
    snapshot: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True, active_history=True
    )
    #: Relativa a `<marketing_dir>/posts`. Nula sin imagen (borrador, descartada).
    image_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    post: Mapped[MarketingPost] = relationship(back_populates="slides")


#: Lo que no se reescribe en una diapositiva ya congelada. `image_path` sí:
#: descartar borra la imagen.
_FROZEN_SLIDE_FIELDS = ("snapshot", "params", "options", "card_slug", "position")


@event.listens_for(MarketingPostSlide, "before_update")
def _slide_frozen(mapper: Any, connection: Any, target: MarketingPostSlide) -> None:
    estado = inspect(target)
    previo = estado.attrs.snapshot.history
    # El valor guardado antes de este flush: el `deleted` del historial si
    # cambió, el actual si no.
    congelado = previo.deleted[0] if previo.deleted else (None if previo.added else target.snapshot)
    if congelado is None:
        return
    for campo in _FROZEN_SLIDE_FIELDS:
        if estado.attrs[campo].history.has_changes():
            raise FrozenSlideError(f"La diapositiva ya está aprobada: «{campo}» no se cambia.")


@event.listens_for(MarketingPost, "before_update")
def _caption_frozen(mapper: Any, connection: Any, target: MarketingPost) -> None:
    estado = inspect(target)
    historial_estado = estado.attrs.status.history
    estado_previo = (
        historial_estado.deleted[0] if historial_estado.deleted else target.status
    )
    congelada = estado_previo in (PostStatus.APPROVED, PostStatus.PUBLISHED)
    if congelada and estado.attrs.caption.history.has_changes():
        raise FrozenSlideError("La publicación ya está aprobada: el texto no se cambia.")
