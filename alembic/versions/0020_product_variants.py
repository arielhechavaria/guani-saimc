"""Crea `product_variants` y agrega `prices_history.variant_id`.

Puramente aditiva (design.md, Migration/Rollout). A diferencia de
`0018`/`0019`, no hay dato de origen que copiar ni mapear: la marca y el tipo
nunca estuvieron guardados en ninguna columna, así que no hay backfill y no
hay columna `*_legacy` de respaldo. Toda fila existente arranca con
`variant_id = NULL`: una sola celda, cero fragmentación el día uno.

`op.batch_alter_table` por el mismo motivo que `0018`/`0006`/`0016`/`0017`:
SQLite no soporta agregar una FK a una tabla existente, y
`tests/test_processing_layer.py::TestProductVariantsMigration` corre
`upgrade()`/`downgrade()` contra un engine SQLite real. En PostgreSQL
`recreate="auto"` emite `ALTER TABLE` ordinario.

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "product_variants",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("product_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("brand", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("origin", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("kind_key", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("brand_key", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("origin_key", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name="fk_product_variants_product_id",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "product_id",
            "kind_key",
            "brand_key",
            "origin_key",
            name="uq_product_variants_identity",
        ),
        sa.CheckConstraint(
            "kind_key <> '' OR brand_key <> '' OR origin_key <> ''",
            name="ck_product_variants_not_empty",
        ),
    )

    with op.batch_alter_table("prices_history") as batch_op:
        batch_op.add_column(sa.Column("variant_id", sa.Uuid(as_uuid=True), nullable=True))
        batch_op.create_foreign_key(
            "fk_prices_history_variant_id",
            "product_variants",
            ["variant_id"],
            ["id"],
            ondelete="RESTRICT",
        )


def downgrade() -> None:
    with op.batch_alter_table("prices_history") as batch_op:
        batch_op.drop_constraint("fk_prices_history_variant_id", type_="foreignkey")
        batch_op.drop_column("variant_id")

    op.drop_table("product_variants")
