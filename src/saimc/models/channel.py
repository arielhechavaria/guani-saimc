"""Canales y grupos que el operador sigue para capturar anuncios.

Empezó como lista de rondas manual (decisión cerrada 2 del `CLAUDE.md`) y el
2026-08-15 pasó a admitir automatización para WhatsApp vía WAHA self-hosted:
apartamiento explícito de esa misma decisión, registrado en `docs/decisions.md`.
Un canal `whatsapp_channel` con `external_id` cargado recibe mensajes por
webhook (`saimc.web.routers.whatsapp_webhook`); el resto sigue siendo
solo la lista de dónde mirar a mano.

Existe por la misma razón que `is_pilot` en `products`: la densidad por celda
se construye observando **los mismos** canales de forma repetida
(`docs/decisions.md`, 2026-08-15). Una lista escrita es lo que convierte esa
repetición en rutina en vez de en intención.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, Uuid, true
from sqlalchemy.orm import Mapped, mapped_column

from saimc.models.base import Base, utc_now


class Platform(StrEnum):
    """Dónde vive el canal.

    Determina qué se puede hacer con él más adelante: los canales de WhatsApp
    y los grupos de Telegram tienen caminos de automatización distintos, y
    Facebook hoy no tiene ninguno. En Fase 0 es puramente descriptivo.

    `WHATSAPP_GROUP` entró el 2026-08-18 para que el operador pueda distinguir
    en `/catalog` un grupo de un canal. Es descripción, no ruteo: el backfill
    decide contra qué endpoint de WAHA pregunta mirando el sufijo del
    `external_id` (`@g.us` vs `@newsletter`), que lo escribe WhatsApp y no se
    puede tipear mal. La columna es `String(30)`, así que agregar un valor no
    necesita migración.
    """

    WHATSAPP_CHANNEL = "whatsapp_channel"
    WHATSAPP_GROUP = "whatsapp_group"
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

    # El identificador que manda WAHA en cada webhook (`from`, algo como
    # "1234567890@newsletter"), no el enlace público. Nullable: un canal
    # puramente manual (la lista de rondas original) nunca lo necesita.
    # Único mientras no sea NULL — Postgres y SQLite permiten varios NULL en
    # una columna UNIQUE, así que dos canales sin automatizar no chocan entre
    # sí. Sin este campo, un webhook no tiene forma de saber a qué fila de
    # `channels` pertenece el mensaje que llegó.
    external_id: Mapped[str | None] = mapped_column(String(120), nullable=True, unique=True)

    # Provincia fija del canal, cargada a mano por el operador (docs/decisions.md,
    # 2026-08-17): "el canal siempre manda" — cuando está cargada, la cola de
    # curaduría la usa en vez de lo que `_match_province` adivine del texto
    # del mensaje. Nullable: no todo canal tiene una provincia fija conocida
    # (uno que cubre varias zonas, o uno recién dado de alta sin ese dato
    # todavía). `ondelete="RESTRICT"`, mismo criterio que
    # `PriceHistory.province_id` — las provincias son semilla fija (spec.md
    # §9.0), nunca se borran en operación normal.
    province_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("provinces.id", ondelete="RESTRICT", name="fk_channels_province_id"),
        nullable=True,
    )

    # Dirección del LOCAL del negocio (no de un anunciante individual),
    # cargada a mano por el operador desde lo que el propio canal/grupo
    # publica en su descripción (docs/decisions.md, 2026-08-19) — un negocio
    # que se auto-publicita, no un dato personal de un tercero (spec.md
    # §10.6: ese riesgo es sobre anunciantes individuales, no sobre el
    # local que ya se anuncia solo para que lo encuentren). Nullable: la
    # mayoría de los canales no trae esta información en su descripción.
    # Sin geocodificar todavía — texto libre, igual que `notes`, para que
    # una geocodificación futura tenga de dónde partir sin tener que volver
    # a mirar cada descripción a mano.
    address: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    @property
    def is_whatsapp_group(self) -> bool:
        """Si esta fila es un grupo de WhatsApp, según el JID que manda WAHA.

        Se pregunta al `external_id` y no a `platform` a propósito: el sufijo
        lo escribe WhatsApp (`@g.us` grupo, `@newsletter` canal) y `platform`
        lo tipea el operador. Cuando los dos podrían contradecirse, gana el
        que no se puede equivocar. Mismo criterio que el ruteo de endpoint en
        `saimc.ingestion.whatsapp_backfill`.

        Importa porque un grupo tiene MUCHOS vendedores adentro (se midieron
        27 emisores distintos en uno solo, 2026-08-18) mientras que un canal
        tiene uno: todo lo que se infiera "del canal" y en realidad sea "del
        vendedor" deja de valer en un grupo.
        """
        return bool(self.external_id) and self.external_id.endswith("@g.us")

    def __repr__(self) -> str:
        return f"Channel(name={self.name!r}, platform={self.platform}, active={self.is_active})"
