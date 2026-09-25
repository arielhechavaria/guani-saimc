"""Crea `llm_proposals` y `llm_reviews` (slice 3a del asistente).

Puramente aditiva (`docs/spec-slice-3a.md`): dos tablas nuevas e inmutables,
sin tocar ninguna existente. `llm_proposals` guarda cada llamada al modelo
(también las degradadas) con su respuesta cruda; `llm_reviews` captura qué hizo
el operador con la propuesta que tenía a la vista al curar o descartar.

`sa.JSON` y no `JSONB`: se lee en Python, no se consulta por dentro, y así la
misma migración corre en PostgreSQL y en el SQLite de los tests.

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_proposals",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("ingestion_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("prompt_version", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("failure", sa.String(length=32), nullable=True),
        sa.Column("discard", sa.Boolean(), nullable=True),
        sa.Column("discard_reason", sa.String(length=16), nullable=True),
        sa.Column("rows", sa.JSON(), nullable=False),
        sa.Column("raw_response", sa.Text(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["ingestion_id"],
            ["raw_ingestions.id"],
            name="fk_llm_proposals_ingestion_id",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("status IN ('ok', 'degraded')", name="ck_llm_proposals_status"),
        sa.CheckConstraint(
            "discard_reason IS NULL OR discard_reason IN ('off_focus', 'no_data')",
            name="ck_llm_proposals_discard_reason",
        ),
    )
    op.create_index("idx_llm_proposals_ingestion_id", "llm_proposals", ["ingestion_id"])

    op.create_table(
        "llm_reviews",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("ingestion_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("proposal_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("discard_reason", sa.String(length=16), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("rows", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["ingestion_id"],
            ["raw_ingestions.id"],
            name="fk_llm_reviews_ingestion_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id"],
            ["llm_proposals.id"],
            name="fk_llm_reviews_proposal_id",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "outcome IN ('curated', 'discarded')", name="ck_llm_reviews_outcome"
        ),
    )
    op.create_index("idx_llm_reviews_ingestion_id", "llm_reviews", ["ingestion_id"])


def downgrade() -> None:
    op.drop_index("idx_llm_reviews_ingestion_id", table_name="llm_reviews")
    op.drop_table("llm_reviews")
    op.drop_index("idx_llm_proposals_ingestion_id", table_name="llm_proposals")
    op.drop_table("llm_proposals")
