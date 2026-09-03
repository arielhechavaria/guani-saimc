"""Agrega `discard_reason` a `raw_ingestions`.

Columna aditiva nullable, sin backfill: las 378 filas descartadas antes de este
cambio quedan en `NULL`, que significa «motivo no registrado» — el operador que
las descartó nunca respondió esta pregunta con el formulario que existía
entonces, y adivinarle un motivo (¿fuera de foco? ¿sin dato?) sería inventar el
dato. Además el 66% de esos descartes son imágenes sin `original_text`, así que
tampoco se puede derivar el motivo después. Mismo criterio que `price_basis` en
`0016`.

`String(20)`, con el CHECK nombrado `ck_raw_ingestions_discard_reason`
(`NULL`, `'off_focus'` o `'no_data'`): vocabulario cerrado de exactamente dos
valores, mismo patrón que `ck_prices_history_price_basis`. Un tercer valor sería
una pregunta distinta, no una palabra más.

El acoplamiento «solo se completa si `processing_status = 'discarded'`» lo
sostiene la aplicación, no un CHECK entre columnas: `discard_curation` y
`discard_out_of_focus` son los únicos que lo escriben, y siempre junto con el
estado.

`op.batch_alter_table` por el mismo motivo que `0016`: SQLite no soporta
`ALTER TABLE ... ADD CONSTRAINT` y el test corre `upgrade()`/`downgrade()`
contra un engine SQLite real. En PostgreSQL `recreate="auto"` emite `ALTER
TABLE` ordinario. Misma advertencia que `0016`: no correr estas migraciones
contra una base SQLite real.

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("raw_ingestions") as batch_op:
        batch_op.add_column(sa.Column("discard_reason", sa.String(length=20), nullable=True))
        batch_op.create_check_constraint(
            "ck_raw_ingestions_discard_reason",
            "discard_reason IS NULL OR discard_reason IN ('off_focus', 'no_data')",
        )


def downgrade() -> None:
    with op.batch_alter_table("raw_ingestions") as batch_op:
        batch_op.drop_constraint("ck_raw_ingestions_discard_reason", type_="check")
        batch_op.drop_column("discard_reason")
