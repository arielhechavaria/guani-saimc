"""Agrega `exchange_rate_cache` (servicio de conversión monetaria, `spec.md` §6.3 E).

Tabla nueva, aditiva-only: no toca ninguna tabla existente, no hace falta
`batch_alter_table` (eso es sólo para `ALTER TABLE` sobre una tabla que ya
existe, ver 0006). Guarda una tasa elTOQUE por par `(currency, rate_date)`,
sin FK a ninguna otra tabla — de primera clase (design.md, D2). `downgrade()`
sólo elimina la tabla: reversible sin pérdida de dato en el resto del
esquema.

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "exchange_rate_cache",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("rate_date", sa.Date(), nullable=False),
        sa.Column("rate", sa.Numeric(12, 4), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "currency", "rate_date", name="uq_exchange_rate_cache_currency_rate_date"
        ),
    )


def downgrade() -> None:
    op.drop_table("exchange_rate_cache")
