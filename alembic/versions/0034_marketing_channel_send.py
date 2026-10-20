"""Registro del envío de cada publicación al canal de WhatsApp de Guani.

`docs/decisions.md`, 2026-10-20. `saimc.marketing.channel` manda las láminas
de una publicación aprobada una por una; estas dos columnas son su registro de
idempotencia:

- `channel_slides_sent`: cuántas láminas ya salieron, en orden. Si el envío se
  corta a mitad, la repetición sigue desde la siguiente y no duplica.
- `channel_sent_at`: cuándo se completó. Nula hasta que salen todas.

Las filas existentes quedan en 0 / nulo: nada se mandó todavía. No toca
ninguna otra columna ni restricción.

Revision ID: 0034
Revises: 0033
Create Date: 2026-10-20
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "marketing_posts",
        sa.Column("channel_slides_sent", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "marketing_posts",
        sa.Column("channel_sent_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("marketing_posts", "channel_sent_at")
    op.drop_column("marketing_posts", "channel_slides_sent")
