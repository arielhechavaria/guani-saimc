"""Agrega `price_basis` a `prices_history`.

Una columna aditiva nullable, sin backfill: las filas existentes quedan en
`NULL`, que acá significa «no resuelto» (legado o pendiente de curar) y nunca
se lee como `'total'` — esa lectura implícita es exactamente el bug que este
cambio corrige (`proposal.md`: «Arroz guyanés a 340lb» dividido a 1 CUP/lb en
vez de ~340). El operador que cargó esas filas nunca respondió esta pregunta
con el formulario que existía entonces, y fabricarle una respuesta de
`'total'` sería inventar un dato — mismo criterio que `0005`, `0006` y `0008`.

`String(10)`, mismo tipo que `sale_format`. Lleva el CHECK nombrado
`ck_prices_history_price_basis` (`NULL`, `'unit'` o `'total'`) porque es una
restricción de vocabulario cerrado, mismo patrón que
`ck_prices_history_sale_format` — a diferencia de `unit`, cuyo vocabulario se
dejó fuera de la base a propósito para que crecerlo no exija una migración
(design.md, D2): acá el dominio son exactamente dos valores y un tercero
sería una pregunta distinta, no una palabra más.

`op.batch_alter_table` por el mismo motivo que `0006`/`0008`: SQLite no
soporta `ALTER TABLE ... ADD CONSTRAINT` y el test de esta migración corre
`upgrade()`/`downgrade()` contra un engine SQLite real. En PostgreSQL
(producción) `recreate="auto"` emite `ALTER TABLE` ordinario. Vale la misma
advertencia registrada en `0006`/`0008`: no correr estas migraciones contra
una base SQLite real, porque un `recreate` de batch borraría en silencio los
CHECK preexistentes que la reflexión de SQLite no devuelve.

Nota de numeración (design.md, D1): `proposal.md` y el delta de spec nombran
esta migración `0009`, pero ese número ya está tomado por
`0009_products_is_pilot.py`. El head real de alembic es `0015`
(`0015_raw_ingestions_channel_id.py`), así que esta migración revisa `0015` y
se numera `0016`. El `0009` de la especificación es un identificador
desactualizado, no un requisito de orden.

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("prices_history") as batch_op:
        batch_op.add_column(sa.Column("price_basis", sa.String(length=10), nullable=True))
        batch_op.create_check_constraint(
            "ck_prices_history_price_basis",
            "price_basis IS NULL OR price_basis IN ('unit', 'total')",
        )


def downgrade() -> None:
    with op.batch_alter_table("prices_history") as batch_op:
        batch_op.drop_constraint("ck_prices_history_price_basis", type_="check")
        batch_op.drop_column("price_basis")
