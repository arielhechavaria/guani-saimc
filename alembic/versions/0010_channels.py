"""Crea `channels`: los canales y grupos que el operador sigue.

Tabla nueva, sin relación con `raw_ingestions`: no toca la fila inmutable ni
reinterpreta `source_reference`, que sigue siendo texto libre. Es la lista de
rondas del operador (`docs/decisions.md`, 2026-08-15) — la densidad por celda
se construye observando los mismos canales de forma repetida, y una lista
escrita es lo que vuelve rutina esa repetición.

`platform` va como `String(30)` y no como ENUM de PostgreSQL por el mismo
criterio ya aplicado a `unit` en `0006`: es vocabulario, no restricción
estructural, y agregarle un valor no debería costar una migración.

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "channels",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("platform", sa.String(length=30), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("idx_channels_name", "channels", ["name"], unique=True)


def downgrade() -> None:
    op.drop_index("idx_channels_name", table_name="channels")
    op.drop_table("channels")
