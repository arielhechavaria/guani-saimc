"""Crea `reference_set_members`: el conjunto de referencia congelado (slice B3-1).

`docs/spec-slice-B3.md`, sección 1. Tabla de solo altas, una fila por anuncio y
por versión del conjunto, con las filas confirmadas al congelar (`rows`) y su
hash. `UNIQUE (set_version, ingestion_id)`: un anuncio entra una sola vez a
cada versión, y su índice ya sirve para leer una versión entera.

No toca ninguna tabla existente ni hay relleno: la versión 1 la escribe el
operador con `python -m saimc.extraction.reference_set --execute`.

Revision ID: 0031
Revises: 0030
Create Date: 2026-10-10
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reference_set_members",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("set_version", sa.SmallInteger(), nullable=False),
        sa.Column("ingestion_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("rows", sa.JSON(), nullable=False),
        sa.Column("values_hash", sa.String(length=64), nullable=False),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["ingestion_id"],
            ["raw_ingestions.id"],
            name="fk_reference_set_members_ingestion_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "set_version", "ingestion_id", name="uq_reference_set_members_version_ingestion"
        ),
    )


def downgrade() -> None:
    op.drop_table("reference_set_members")
