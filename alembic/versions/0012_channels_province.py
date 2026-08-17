"""Agrega `province_id` a `channels`.

Contexto en `docs/decisions.md` (2026-08-17). El operador conoce de
antemano la provincia de un canal fijo (ej. Groupej Morales Surl vende
en Cotorro, La Habana) — cargarla en el canal evita depender de que
`_match_province` la adivine del texto de cada mensaje, que puede no
mencionarla en absoluto o mencionar otra por error.

Columna nullable y aditiva, mismo criterio que `external_id` (0011): un
canal ya registrado no tenía este dato, y no hay forma de inferirlo
retroactivamente sin que el operador lo cargue a mano.

`ondelete="RESTRICT"`, igual que `prices_history.province_id`
(`0002_processing_layer.py`): las 16 provincias son datos semilla fijos
(spec.md §9.0), nunca se borran en la operación normal — RESTRICT es una
red de seguridad, no un camino esperado.

`op.batch_alter_table` por el mismo motivo que `0011`: SQLite no soporta
`ALTER TABLE ... ADD CONSTRAINT` y el test de esta migración corre
`upgrade()`/`downgrade()` contra un engine SQLite real.

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("channels") as batch_op:
        batch_op.add_column(sa.Column("province_id", sa.Uuid(as_uuid=True), nullable=True))
        batch_op.create_foreign_key(
            "fk_channels_province_id",
            "provinces",
            ["province_id"],
            ["id"],
            ondelete="RESTRICT",
        )


def downgrade() -> None:
    with op.batch_alter_table("channels") as batch_op:
        batch_op.drop_constraint("fk_channels_province_id", type_="foreignkey")
        batch_op.drop_column("province_id")
