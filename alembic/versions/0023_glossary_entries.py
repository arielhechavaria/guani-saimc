"""Crea `glossary_entries` (slice 3c del asistente, glosario del operador).

Puramente aditiva (`docs/spec-slice-3c.md`). A diferencia de `llm_proposals`/
`llm_reviews` (0021), esta tabla se edita: no es un registro de lo que pasó,
es configuración curada por el operador que cambia lo que ven las llamadas
siguientes. Arranca vacía — sin dato de arranque: el ejemplo ya backteado en
`_SYSTEM_PROMPT` (Pollo Muslo 4x10) se queda donde está.

Revision ID: 0023
Revises: 0022
Create Date: 2026-09-27
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "glossary_entries",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("term", sa.String(length=120), nullable=False),
        sa.Column("definition", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("term <> ''", name="ck_glossary_entries_term_not_empty"),
        sa.CheckConstraint(
            "definition <> ''", name="ck_glossary_entries_definition_not_empty"
        ),
    )


def downgrade() -> None:
    op.drop_table("glossary_entries")
