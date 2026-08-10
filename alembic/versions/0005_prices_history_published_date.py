"""Agrega `published_date` a `prices_history` — fecha de publicación del anuncio.

Corresponde a docs/whitepaper.md §6.1c/§7.2: el día (no el instante) en que se
publicó el anuncio, como eje temporal de la serie de precios. Columna nullable,
sin default y sin CHECK (design.md, D2) — un dato secundario que nunca debe
bloquear un insert ni una transacción.

No toca filas existentes: `add_column`/`drop_column` puro, sin backfill.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "prices_history",
        sa.Column("published_date", sa.Date(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("prices_history", "published_date")
