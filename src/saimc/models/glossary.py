"""Glosario del operador — `glossary_entries` (slice 3c del asistente LLM).

Reglas cortas y editables que se inyectan en el prompt de `llm_propose_ad`
(ejemplo: «4x10» = 4 paquetes de 10 lb, la caja pesa 40 lb). A diferencia de
`llm_proposals`/`llm_reviews`, esta tabla se edita: es configuración curada
por el operador, no un registro inmutable de lo que pasó
(`docs/spec-slice-3c.md`).
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from saimc.models.base import Base, utc_now


class GlossaryEntry(Base):
    __tablename__ = "glossary_entries"
    __table_args__ = (
        CheckConstraint("term <> ''", name="ck_glossary_entries_term_not_empty"),
        CheckConstraint(
            "definition <> ''", name="ck_glossary_entries_definition_not_empty"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    term: Mapped[str] = mapped_column(String(120), nullable=False)
    definition: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
