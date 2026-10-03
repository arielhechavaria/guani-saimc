"""La transcripción del OCR de una imagen, guardada ya redactada (slice K3).

`docs/spec-slice-K3.md`. Tabla derivada y de solo altas: el código no tiene
camino de escritura sobre una fila existente ni de borrado, y no toca
`raw_ingestions` (que sigue inmutable). Una fila por imagen y por versión del
OCR (`ocr.OCR_VERSION`): subir la versión agrega filas, no reemplaza ninguna.

Nunca guarda texto sin redactar (`spec.md` §10.6), ni en `text` ni en `boxes`.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from saimc.models.base import Base, utc_now


class OcrTranscription(Base):
    __tablename__ = "ocr_transcriptions"
    __table_args__ = (
        Index("idx_ocr_transcriptions_ingestion_id", "ingestion_id"),
        UniqueConstraint("ingestion_id", "ocr_version", name="uq_ocr_transcriptions_version"),
        CheckConstraint("engine IN ('rapidocr', 'tesseract')", name="ck_ocr_transcriptions_engine"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ingestion_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("raw_ingestions.id", ondelete="RESTRICT", name="fk_ocr_transcriptions_ingestion_id"),
        nullable=False,
    )
    engine: Mapped[str] = mapped_column(String(16), nullable=False)
    ocr_version: Mapped[str] = mapped_column(String(32), nullable=False)
    # Redactado con `redact_personal_data`.
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # Solo con rapidocr: `[{"text", "box", "score", "redacted"}, ...]` en el
    # orden de `text`, también redactado por renglón. Nulo con tesseract.
    boxes: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
