"""Crea `jev_signals`: lo que contestó Jev sobre cada anuncio (slice A6b).

`docs/spec-slice-A6b.md`. Tabla derivada y de solo altas, con la respuesta tal
cual llegó. `UNIQUE (ingestion_id, questions_hash)`: una señal por anuncio y
por huella de preguntas, así dos procesos sobre el mismo anuncio no dejan dos
filas. El índice de ese UNIQUE ya sirve para buscar por `ingestion_id`: no se
agrega otro.

No toca ninguna tabla existente ni hay relleno: las filas se llenan solas
cuando el pre-cálculo pasa por los textos pendientes.

Revision ID: 0030
Revises: 0029
Create Date: 2026-10-04
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "jev_signals",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("ingestion_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("questions_hash", sa.String(length=16), nullable=False),
        sa.Column("answers", sa.JSON(), nullable=False),
        sa.Column("product_keys", sa.JSON(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(precision=12, scale=8), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("source IN ('prefill', 'backfill')", name="ck_jev_signals_source"),
        sa.ForeignKeyConstraint(
            ["ingestion_id"],
            ["raw_ingestions.id"],
            name="fk_jev_signals_ingestion_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ingestion_id", "questions_hash", name="uq_jev_signals_ingestion_hash"),
    )


def downgrade() -> None:
    op.drop_table("jev_signals")
