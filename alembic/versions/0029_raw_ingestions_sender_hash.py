"""Agrega `sender_hash` a `raw_ingestions` (slice B5).

`docs/spec-slice-B5.md`. El emisor de un mensaje de grupo como
`HMAC-SHA256(clave, JID normalizado)` en hexadecimal: 64 caracteres. El JID en
claro no se guarda en ningún lado (`spec.md` §10.6).

Columna nullable y aditiva, mismo precedente que `channel_id` (`0015`):
`raw_ingestions` es inmutable, la escribe la ingesta una sola vez y las filas
anteriores quedan en nulo para siempre. No hay backfill.

Con índice, a diferencia de `channel_id`: la columna existe para preguntar
«qué más publicó este emisor».

`op.batch_alter_table`, igual que `0015` y `0027`: el test corre
`upgrade()`/`downgrade()` contra un engine SQLite real.

Revision ID: 0029
Revises: 0028
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX = "idx_raw_ingestions_sender_hash"


def upgrade() -> None:
    with op.batch_alter_table("raw_ingestions") as batch_op:
        batch_op.add_column(sa.Column("sender_hash", sa.String(length=64), nullable=True))
        batch_op.create_index(_INDEX, ["sender_hash"])


def downgrade() -> None:
    with op.batch_alter_table("raw_ingestions") as batch_op:
        batch_op.drop_index(_INDEX)
        batch_op.drop_column("sender_hash")
