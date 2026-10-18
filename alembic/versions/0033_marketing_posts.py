"""Crea las tablas de la fábrica de publicaciones de `/marketing` (F2).

`docs/spec-slice-fabrica-publicaciones.md`. `marketing_posts` (una publicación
con estado: borrador, aprobada, publicada o descartada) y
`marketing_post_slides` (sus diapositivas ordenadas, con el `snapshot` que se
congela al aprobar).

No toca ninguna tabla existente ni hay relleno: las escriben el lote
`python -m saimc.marketing.daily --execute` y el editor. Ninguna guarda datos
de terceros.

Revision ID: 0033
Revises: 0032
Create Date: 2026-10-18
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "marketing_posts",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("format", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("planned_for", sa.Date(), nullable=False),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column("batch_slot", sa.String(length=64), nullable=True),
        sa.Column("published_url", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("discarded_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("planned_for", "batch_slot", name="uq_marketing_posts_day_slot"),
        sa.CheckConstraint(
            "status IN ('draft', 'approved', 'published', 'discarded')",
            name="ck_marketing_posts_status",
        ),
        sa.CheckConstraint(
            "format IN ('single', 'carousel', 'story')", name="ck_marketing_posts_format"
        ),
        sa.CheckConstraint(
            "origin IN ('batch', 'operator')", name="ck_marketing_posts_origin"
        ),
        sa.CheckConstraint(
            "(origin = 'batch' AND batch_slot IS NOT NULL)"
            " OR (origin = 'operator' AND batch_slot IS NULL)",
            name="ck_marketing_posts_batch_slot",
        ),
        sa.CheckConstraint(
            "status NOT IN ('approved', 'published') OR approved_at IS NOT NULL",
            name="ck_marketing_posts_approved_at",
        ),
        sa.CheckConstraint(
            "status <> 'published' OR (published_url IS NOT NULL AND published_at IS NOT NULL)",
            name="ck_marketing_posts_published",
        ),
        sa.CheckConstraint(
            "status <> 'discarded' OR discarded_at IS NOT NULL",
            name="ck_marketing_posts_discarded_at",
        ),
    )
    op.create_index(
        "ix_marketing_posts_planned_for", "marketing_posts", ["planned_for"]
    )
    op.create_table(
        "marketing_post_slides",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("post_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("card_slug", sa.String(length=64), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=True),
        sa.Column("image_path", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["post_id"],
            ["marketing_posts.id"],
            name="fk_marketing_post_slides_post_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("post_id", "position", name="uq_marketing_post_slides_position"),
        sa.CheckConstraint("position >= 1", name="ck_marketing_post_slides_position"),
    )


def downgrade() -> None:
    op.drop_table("marketing_post_slides")
    op.drop_index("ix_marketing_posts_planned_for", table_name="marketing_posts")
    op.drop_table("marketing_posts")
