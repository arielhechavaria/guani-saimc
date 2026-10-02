"""La bitácora de curaduría: una fila por decisión del operador.

Slice A2b (`docs/spec-slice-A2.md`). Tabla de solo altas: el código no tiene
camino de escritura sobre una fila existente. Registra la acción, su modo y el
tiempo (`shown_at` → `decided_at`), haya o no una propuesta del modelo a la
vista; no reemplaza a `llm_reviews`, que compara propuesta y valor final.

`detail` guarda solo campos de `prices_history` (y `source_reference` en una
edición), como texto: nunca el texto del anuncio ni notas (`spec.md` §10.6).
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from saimc.models.base import Base, utc_now


class CurationAction(StrEnum):
    CURATED = "curated"
    DISCARDED = "discarded"
    DELETED = "deleted"
    EDITED = "edited"


class CurationMode(StrEnum):
    INDIVIDUAL = "individual"
    BATCH_OUT_OF_FOCUS = "batch_out_of_focus"
    # A1b: lote de imágenes con el veredicto del modelo (`discard_images_batch`).
    BATCH_MODEL = "batch_model"


class CurationEvent(Base):
    __tablename__ = "curation_events"
    __table_args__ = (
        Index("idx_curation_events_ingestion_id", "ingestion_id"),
        CheckConstraint(
            "action IN ('curated', 'discarded', 'deleted', 'edited')",
            name="ck_curation_events_action",
        ),
        CheckConstraint(
            "mode IN ('individual', 'batch_out_of_focus', 'batch_model')",
            name="ck_curation_events_mode",
        ),
        CheckConstraint(
            "discard_reason IS NULL OR discard_reason IN ('off_focus', 'no_data')",
            name="ck_curation_events_discard_reason",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    ingestion_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "raw_ingestions.id", ondelete="RESTRICT", name="fk_curation_events_ingestion_id"
        ),
        nullable=False,
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    mode: Mapped[str] = mapped_column(String(24), nullable=False)
    # Solo con `action = discarded`.
    discard_reason: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # La propuesta del modelo que estaba a la vista, si había.
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("llm_proposals.id", ondelete="RESTRICT", name="fk_curation_events_proposal_id"),
        nullable=True,
    )
    # Cuándo se mostró lo que el operador decidió; nulo si no viajó o no se
    # pudo interpretar. Se guarda tal cual, sin recortar pausas (A2d decide).
    shown_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
