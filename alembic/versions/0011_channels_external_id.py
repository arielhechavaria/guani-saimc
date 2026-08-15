"""Agrega `external_id` a `channels`.

Implementa la automatización de canales de WhatsApp (`docs/decisions.md`,
2026-08-15): apartamiento consciente de las decisiones cerradas 2 y 4. El
webhook de WAHA (`saimc.web.routers.whatsapp_webhook`) recibe el `from` de
cada mensaje —algo como "1234567890@newsletter"— y necesita mapearlo a una
fila de `channels` sin ambigüedad.

Columna nullable y aditiva: un canal registrado antes de esta migración era
solo la lista de rondas manual del operador y nunca tuvo este identificador.
No hay backfill posible ni deseable — no hay forma de inferir el `external_id`
real de WAHA a partir de lo que ya está en la tabla.

`unique=True` con nulls permitidos: tanto PostgreSQL como SQLite tratan NULL
como distinto de sí mismo en una restricción UNIQUE, así que varios canales sin
automatizar conviven sin chocar. Solo un `external_id` con VALOR repetido
rompería la restricción, que es justo el caso a impedir: dos canales no pueden
apuntar al mismo remitente de WAHA.

`op.batch_alter_table` por el mismo motivo que `0009`/`0006`/`0008`: SQLite no
soporta `ALTER TABLE ... ADD CONSTRAINT` y el test de esta migración corre
`upgrade()`/`downgrade()` contra un engine SQLite real.

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("channels") as batch_op:
        batch_op.add_column(sa.Column("external_id", sa.String(length=120), nullable=True))
        batch_op.create_unique_constraint("uq_channels_external_id", ["external_id"])


def downgrade() -> None:
    with op.batch_alter_table("channels") as batch_op:
        batch_op.drop_constraint("uq_channels_external_id", type_="unique")
        batch_op.drop_column("external_id")
