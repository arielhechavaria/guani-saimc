"""La propuesta del modelo y la captura de lo que el operador hizo con ella.

Slice 3a del plan del asistente (`docs/spec-slice-3a.md`). Dos tablas propias e
inmutables:

- `llm_proposals`: una fila por llamada al modelo (también si degradó, para
  registrar la falla). Pedir otra propuesta crea otra fila.
- `llm_reviews`: una fila por anuncio curado o descartado con una propuesta a la
  vista. «Final» es lo confirmado al guardar la curaduría: una edición
  posterior no la toca.

Ninguna de las dos se actualiza ni se borra: el código no tiene camino de
escritura sobre una fila existente. Las filas van en JSON por anuncio y no una
por campo: con ~1200 anuncios al mes, leer el JSON en Python alcanza.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from saimc.models.base import Base, utc_now


class ProposalStatus(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"


class ReviewOutcome(StrEnum):
    CURATED = "curated"
    DISCARDED = "discarded"


class LlmProposal(Base):
    __tablename__ = "llm_proposals"
    __table_args__ = (
        Index("idx_llm_proposals_ingestion_id", "ingestion_id"),
        CheckConstraint("status IN ('ok', 'degraded')", name="ck_llm_proposals_status"),
        # Mismo vocabulario que `ck_raw_ingestions_discard_reason`.
        CheckConstraint(
            "discard_reason IS NULL OR discard_reason IN ('off_focus', 'no_data')",
            name="ck_llm_proposals_discard_reason",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # RESTRICT: `raw_ingestions` no se borra, y una propuesta sin su anuncio no
    # se puede leer.
    ingestion_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "raw_ingestions.id", ondelete="RESTRICT", name="fk_llm_proposals_ingestion_id"
        ),
        nullable=False,
    )
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    # `llm_propose_ad.PROMPT_VERSION` al momento de la llamada.
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    # Etiqueta de `llm_assist.classify_failure` si degradó; nulo si anduvo.
    failure: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Veredicto del modelo; nulo si degradó.
    discard: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    discard_reason: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Lista de filas: `product_id`, `price` (texto decimal), `currency`,
    # `quantity`, `unit`, `province_id`, `evidence`, `currency_explicit`,
    # `evidence_found`, `price_in_evidence`.
    rows: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    # La respuesta del modelo tal cual. El modelo solo ve texto redactado, así
    # que no trae teléfonos.
    raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class LlmReview(Base):
    __tablename__ = "llm_reviews"
    __table_args__ = (
        Index("idx_llm_reviews_ingestion_id", "ingestion_id"),
        CheckConstraint(
            "outcome IN ('curated', 'discarded')", name="ck_llm_reviews_outcome"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    ingestion_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "raw_ingestions.id", ondelete="RESTRICT", name="fk_llm_reviews_ingestion_id"
        ),
        nullable=False,
    )
    # La propuesta que el operador tenía a la vista.
    proposal_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "llm_proposals.id", ondelete="RESTRICT", name="fk_llm_reviews_proposal_id"
        ),
        nullable=False,
    )
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    # El motivo que eligió el operador al descartar; nulo si curó.
    discard_reason: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Nota libre del operador, con los teléfonos redactados.
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Una entrada por fila propuesta y por fila agregada a mano:
    # `proposal_index`, `price_history_id`, `proposed`, `final`, `edited`.
    rows: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
