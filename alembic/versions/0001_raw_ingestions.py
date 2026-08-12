"""Raw Layer: tabla raw_ingestions

Corresponde a docs/spec.md §7.1 (Raw Layer).

Revision ID: 0001
Revises:
Create Date: 2026-08-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "raw_ingestions",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("source_type", sa.String(length=50), nullable=False),
        sa.Column("source_reference", sa.Text(), nullable=True),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("content_type", sa.String(length=20), nullable=False),
        sa.Column("original_text", sa.Text(), nullable=True),
        sa.Column("processing_status", sa.String(length=20), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "idx_raw_ingestions_hash",
        "raw_ingestions",
        ["content_hash"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("idx_raw_ingestions_hash", table_name="raw_ingestions")
    op.drop_table("raw_ingestions")
