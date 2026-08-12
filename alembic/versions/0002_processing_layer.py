"""Processing Layer: tablas provinces, products, prices_history

Corresponde a docs/spec.md §7.2 (Processing Layer).

Orden de creación: provinces y products primero (targets de FK), luego
prices_history. El downgrade invierte ese orden.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "provinces",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("code", sa.String(length=30), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column(
            "is_published",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "products",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("unit", sa.String(length=20), nullable=False),
        sa.Column(
            "aliases",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "prices_history",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("product_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("province_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("price", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("price_cup", sa.Numeric(12, 2), nullable=True),
        sa.Column("exchange_rate", sa.Numeric(12, 4), nullable=True),
        sa.Column("exchange_rate_date", sa.Date(), nullable=True),
        sa.Column("exchange_rate_source", sa.String(length=50), nullable=True),
        sa.Column("quantity", sa.Numeric(12, 3), nullable=True),
        sa.Column("source_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("confidence_score", sa.SmallInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name="fk_prices_history_product_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["province_id"],
            ["provinces.id"],
            name="fk_prices_history_province_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["raw_ingestions.id"],
            name="fk_prices_history_source_id",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "currency <> 'CUP' OR exchange_rate IS NULL",
            name="ck_prices_history_exchange_rate_cup",
        ),
        sa.CheckConstraint(
            "exchange_rate IS NULL OR "
            "(exchange_rate_date IS NOT NULL AND exchange_rate_source IS NOT NULL)",
            name="ck_prices_history_exchange_rate_provenance",
        ),
        sa.CheckConstraint(
            "confidence_score IS NULL OR confidence_score BETWEEN 0 AND 100",
            name="ck_prices_history_confidence_score_range",
        ),
    )

    op.create_index("idx_provinces_code", "provinces", ["code"], unique=True)
    op.create_index("idx_provinces_name", "provinces", ["name"], unique=True)
    op.create_index("idx_products_name", "products", ["name"], unique=True)
    op.create_index(
        "idx_prices_history_series",
        "prices_history",
        ["product_id", "province_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_prices_history_series", table_name="prices_history")
    op.drop_index("idx_products_name", table_name="products")
    op.drop_index("idx_provinces_name", table_name="provinces")
    op.drop_index("idx_provinces_code", table_name="provinces")

    op.drop_table("prices_history")
    op.drop_table("products")
    op.drop_table("provinces")
