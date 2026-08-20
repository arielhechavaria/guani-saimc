"""Agrega `channel_id` a `raw_ingestions`.

Contexto en `docs/decisions.md` (2026-08-19) y `0014`: con `channels.name`
ya no único, resolver el canal de origen de una ingesta por nombre puede
ser ambiguo. `channel_id` es la identidad real de origen, escrita por el
código de ingesta (webhook de WAHA, backfill histórico) cuando el canal se
conoce en el momento de la captura.

Columna nullable y aditiva: `raw_ingestions` es inmutable (`CLAUDE.md`) —
las filas ya existentes nunca reciben este dato de forma retroactiva, y
siguen resolviendo su origen por `source_reference` (nombre) para siempre.

`ondelete="RESTRICT"`, mismo criterio que `Channel.province_id` (`0012`) y
`prices_history.source_id`: un canal se desactiva, nunca se borra, así que
no hay borrado real que deba propagarse.

Sin índice en `channel_id`: nada consulta `raw_ingestions` POR canal; la
lectura va `ingestion.channel_id -> db.get(Channel, ...)` sobre la PK.

`op.batch_alter_table`, mismo motivo que `0011`/`0012`: SQLite no soporta
`ALTER TABLE ... ADD CONSTRAINT` y el test de esta migración corre
`upgrade()`/`downgrade()` contra un engine SQLite real.

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("raw_ingestions") as batch_op:
        batch_op.add_column(sa.Column("channel_id", sa.Uuid(as_uuid=True), nullable=True))
        batch_op.create_foreign_key(
            "fk_raw_ingestions_channel_id",
            "channels",
            ["channel_id"],
            ["id"],
            ondelete="RESTRICT",
        )


def downgrade() -> None:
    with op.batch_alter_table("raw_ingestions") as batch_op:
        batch_op.drop_constraint("fk_raw_ingestions_channel_id", type_="foreignkey")
        batch_op.drop_column("channel_id")
