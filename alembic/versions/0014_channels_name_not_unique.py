"""Relaja `idx_channels_name` de único a no único.

Contexto en `docs/decisions.md` (2026-08-19). El nombre de un canal es una
etiqueta de visualización que WhatsApp asigna, no una identidad que el
operador controle: un canal y un grupo distintos pueden llamarse igual
(caso real: "Ventas Mayoristas", un canal y un grupo homónimos). La
identidad real es `external_id` (0011), único mientras no sea NULL —
`0010` exigía además unicidad en `name`, y eso rechazaba altas legítimas.

Quién originó cada `raw_ingestions` lo dice `channel_id` (0015), no este
nombre: el nombre queda como texto libre de WhatsApp, sin restricción.

`op.drop_index`/`op.create_index` planos, NO `batch_alter_table`: `0010`
creó este índice con `op.create_index` liso y SQLite soporta DROP/CREATE
INDEX de forma nativa (a diferencia de `ALTER TABLE ... ADD CONSTRAINT`,
que sí necesita batch en `0011`/`0012`/`0013`).

`downgrade()` falla por diseño en cuanto existan dos filas con el mismo
nombre — recrear el índice único sobre datos duplicados es un error de
integridad real, no un bug de esta migración. La recuperación pasado ese
punto es solo hacia adelante.

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-19
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("idx_channels_name", table_name="channels")
    op.create_index("idx_channels_name", "channels", ["name"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_channels_name", table_name="channels")
    op.create_index("idx_channels_name", "channels", ["name"], unique=True)
