"""Crea las cuatro tablas del seguimiento de la cuenta de Instagram de Guani.

`docs/spec-slice-instagram.md`. `instagram_media` (una fila por publicación),
`instagram_media_metrics` (serie temporal, solo altas, `UNIQUE (media_id,
captured_at)`), `instagram_account_snapshots` (seguidores por lectura) e
`instagram_credentials` (fila única con el token vigente, `CHECK (id = 1)`).

No toca ninguna tabla existente ni hay relleno: las escribe el lote
`python -m saimc.instagram.sync --execute`. Ninguna guarda datos de terceros.

Revision ID: 0032
Revises: 0031
Create Date: 2026-10-13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "instagram_media",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("ig_media_id", sa.String(length=64), nullable=False),
        sa.Column("media_type", sa.String(length=8), nullable=False),
        sa.Column("permalink", sa.Text(), nullable=True),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ig_media_id", name="uq_instagram_media_ig_media_id"),
        sa.CheckConstraint(
            "media_type IN ('FEED', 'REELS', 'STORY')", name="ck_instagram_media_media_type"
        ),
    )
    op.create_table(
        "instagram_media_metrics",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("media_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("views", sa.Integer(), nullable=True),
        sa.Column("reach", sa.Integer(), nullable=True),
        sa.Column("likes", sa.Integer(), nullable=True),
        sa.Column("comments", sa.Integer(), nullable=True),
        sa.Column("saved", sa.Integer(), nullable=True),
        sa.Column("shares", sa.Integer(), nullable=True),
        sa.Column("total_interactions", sa.Integer(), nullable=True),
        sa.Column("avg_watch_time_ms", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["media_id"],
            ["instagram_media.id"],
            name="fk_instagram_media_metrics_media_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "media_id", "captured_at", name="uq_instagram_media_metrics_media_captured"
        ),
    )
    op.create_table(
        "instagram_account_snapshots",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("followers_count", sa.Integer(), nullable=False),
        sa.Column("media_count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_instagram_account_snapshots_captured_at",
        "instagram_account_snapshots",
        ["captured_at"],
    )
    op.create_table(
        "instagram_credentials",
        sa.Column("id", sa.SmallInteger(), autoincrement=False, nullable=False),
        sa.Column("access_token", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("id = 1", name="ck_instagram_credentials_single_row"),
    )


def downgrade() -> None:
    op.drop_table("instagram_credentials")
    op.drop_index(
        "idx_instagram_account_snapshots_captured_at", table_name="instagram_account_snapshots"
    )
    op.drop_table("instagram_account_snapshots")
    op.drop_table("instagram_media_metrics")
    op.drop_table("instagram_media")
