"""Canales y grupos que el operador sigue para capturar anuncios.

No es una fuente de ingesta automática: en Fase 0 la captura sigue siendo
manual (decisión cerrada 2 del `CLAUDE.md`). Esta tabla es la lista de rondas
del operador — qué canales revisar y cada cuánto — y el registro explícito de
de dónde sale el dato, que hoy vive como texto libre en
`raw_ingestions.source_reference`.

Existe por la misma razón que `is_pilot` en `products`: la densidad por celda
se construye observando **los mismos** canales de forma repetida
(`docs/decisions.md`, 2026-08-15). Una lista escrita es lo que convierte esa
repetición en rutina en vez de en intención.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, Index, String, Text, Uuid, true
from sqlalchemy.orm import Mapped, mapped_column

from saimc.models.base import Base, utc_now


class Platform(StrEnum):
    """Dónde vive el canal.

    Determina qué se puede hacer con él más adelante: los canales de WhatsApp
    y los grupos de Telegram tienen caminos de automatización distintos, y
    Facebook hoy no tiene ninguno. En Fase 0 es puramente descriptivo.
    """

    WHATSAPP_CHANNEL = "whatsapp_channel"
    TELEGRAM_GROUP = "telegram_group"
    FACEBOOK_GROUP = "facebook_group"
    OTHER = "other"


class Channel(Base):
    __tablename__ = "channels"

    # El nombre identifica el canal en la ronda del operador, así que se exige
    # único: dos filas con el mismo nombre son la misma ronda hecha dos veces.
    __table_args__ = (Index("idx_channels_name", "name", unique=True),)

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    platform: Mapped[str] = mapped_column(String(30), nullable=False)

    # Enlace público al canal. Nullable porque un grupo de WhatsApp al que se
    # entró por invitación no siempre tiene URL estable, y exigirla dejaría
    # fuera justo a los que el operador ya sigue.
    url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Qué se saca de acá: notas del operador, no dato estructurado. Sirve para
    # anotar cosas como «publica lista completa en texto los lunes».
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Un canal que dejó de servir se apaga, no se borra: la referencia
    # histórica en los registros crudos ya ingeridos tiene que seguir
    # teniendo sentido.
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    def __repr__(self) -> str:
        return f"Channel(name={self.name!r}, platform={self.platform}, active={self.is_active})"
