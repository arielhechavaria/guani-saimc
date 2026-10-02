"""Crea `curation_events`, la bitácora de curaduría (slice A2b).

Puramente aditiva (`docs/spec-slice-A2.md`): una tabla nueva y de solo altas,
sin tocar ninguna existente y sin backfill — el pasado no tiene hora. Una fila
por decisión del operador (curar, descartar, eliminar un curado, editar) con la
hora en que se mostró lo decidido (`shown_at`) y la hora en que se guardó
(`decided_at`).

`sa.JSON` y no `JSONB`, como en 0021: se lee en Python y así la misma
migración corre en PostgreSQL y en el SQLite de los tests.

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "curation_events",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("ingestion_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("mode", sa.String(length=24), nullable=False),
        sa.Column("discard_reason", sa.String(length=16), nullable=True),
        sa.Column("proposal_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("shown_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(
            ["ingestion_id"],
            ["raw_ingestions.id"],
            name="fk_curation_events_ingestion_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id"],
            ["llm_proposals.id"],
            name="fk_curation_events_proposal_id",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "action IN ('curated', 'discarded', 'deleted', 'edited')",
            name="ck_curation_events_action",
        ),
        sa.CheckConstraint(
            "mode IN ('individual', 'batch_out_of_focus')",
            name="ck_curation_events_mode",
        ),
        sa.CheckConstraint(
            "discard_reason IS NULL OR discard_reason IN ('off_focus', 'no_data')",
            name="ck_curation_events_discard_reason",
        ),
    )
    op.create_index("idx_curation_events_ingestion_id", "curation_events", ["ingestion_id"])


def downgrade() -> None:
    op.drop_index("idx_curation_events_ingestion_id", table_name="curation_events")
    op.drop_table("curation_events")
