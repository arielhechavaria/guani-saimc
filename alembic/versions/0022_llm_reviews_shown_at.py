"""Agrega `shown_at` a `llm_reviews` (slice 5, vigilancia del rubber-stamping).

Columna nullable, sin backfill: una fila guardada antes de este slice no tiene
forma de saber cuándo se mostró, así que queda en NULL a propósito. La métrica
de tiempo por decisión arranca desde que se despliega este cambio, no antes.

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-27
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "llm_reviews",
        sa.Column("shown_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("llm_reviews", "shown_at")
