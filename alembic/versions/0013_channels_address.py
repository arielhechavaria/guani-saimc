"""Agrega `address` a `channels`.

Contexto en `docs/decisions.md` (2026-08-19). En muchos grupos/canales de
WhatsApp la descripción del propio grupo trae la dirección del local del
negocio (se auto-publicita para que lo encuentren) — es dato del NEGOCIO,
no de un anunciante individual (spec.md §10.6 es sobre esto último). Sirve
de insumo para geolocalizar más adelante; acá solo se guarda el texto tal
cual lo carga el operador, sin geocodificar.

Columna nullable y aditiva, mismo criterio que `external_id` (0011) y
`province_id` (0012): un canal ya registrado no tenía este dato, y no hay
forma de inferirlo retroactivamente sin que el operador lo cargue a mano
desde la descripción real del grupo.

`op.batch_alter_table` por el mismo motivo que 0011/0012: SQLite no soporta
bien ciertos `ALTER TABLE` y el test de esta migración corre
`upgrade()`/`downgrade()` contra un engine SQLite real.

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("channels") as batch_op:
        batch_op.add_column(sa.Column("address", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("channels") as batch_op:
        batch_op.drop_column("address")
