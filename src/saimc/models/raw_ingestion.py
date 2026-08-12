"""Raw Layer — el registro crudo inmutable.

Corresponde a la tabla definida en `docs/spec.md` §7.1 (Raw Layer).
Se escribe una vez. No se modifica ni se elimina: es la base de la Transparency
Layer, porque el anuncio que respalda cada precio publicado es literalmente el
archivo capturado (spec.md §6.1, Etapa 1).
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from saimc.models.base import Base, utc_now


class ContentType(StrEnum):
    IMAGE = "image"
    TEXT = "text"


class SourceType(StrEnum):
    """Quién depositó el elemento.

    En Fase 0 solo se usan los dos primeros: la bandeja de entrada es la única
    fuente. Los otros dos quedan declarados porque la bandeja es una interfaz
    genérica y las fuentes automáticas de la Etapa 3 escriben en la misma tabla
    (spec.md §6.1, «Ventaja adicional del patrón»).
    """

    INBOX_IMAGE = "inbox_image"
    INBOX_TEXT = "inbox_text"
    TELEGRAM_API = "telegram_api"
    COMMUNITY = "community"


class ProcessingStatus(StrEnum):
    PENDING = "pending"
    CURATED = "curated"
    DISCARDED = "discarded"


class RawIngestion(Base):
    __tablename__ = "raw_ingestions"

    # Nombre tomado de la especificación (§7.1) para que el esquema real y el
    # documento se puedan comparar sin traducción.
    __table_args__ = (
        Index("idx_raw_ingestions_hash", "content_hash", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    source_type: Mapped[str] = mapped_column(String(50), nullable=False)

    # Canal o grupo de origen. Queda nulo en la ingesta y lo completa el operador
    # durante la curaduría: exigirlo al capturar rompería el principio de que la
    # captura no estructura nada (spec.md §6.1, Etapa 1a).
    source_reference: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Ruta a la evidencia preservada, relativa al directorio del almacén.
    file_path: Mapped[str] = mapped_column(Text, nullable=False)

    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    content_type: Mapped[str] = mapped_column(String(20), nullable=False)

    # Texto pegado (para archivos de texto) o transcrito (cuando el operador
    # confirma el OCR en la cola de curaduría). Nulo para imágenes recién
    # ingeridas: el OCR no corre en la ingesta.
    original_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    processing_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ProcessingStatus.PENDING
    )

    # Cuándo se capturó el material (fecha de modificación del archivo).
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # Cuándo entró al sistema.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    def __repr__(self) -> str:
        return (
            f"RawIngestion(id={self.id!s}, "
            f"content_type={self.content_type}, "
            f"status={self.processing_status})"
        )
