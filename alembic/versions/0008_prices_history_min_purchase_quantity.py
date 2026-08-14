"""Agrega `min_purchase_quantity` a `prices_history`.

Una columna aditiva nullable, sin backfill: las filas existentes quedan en
`NULL`, que acá significa «el anuncio no declara mínimo» y no «se desconoce»
(design.md, D2). Rellenarlas afirmaría una condición de venta que nadie
declaró — mismo criterio que `0005` y `0006`.

`Numeric(12, 3)`, el mismo tipo que `quantity`, porque el mínimo se expresa en
la unidad de la propia fila y las dos se comparan directamente: el filtro del
rango público es `min_purchase_quantity > quantity` (design.md, D3).

Lleva el CHECK nombrado `ck_prices_history_min_purchase_quantity`
(`NULL OR > 0`) porque es una restricción estructural, no de vocabulario: no
hay lectura de aplicación que vuelva válido un mínimo de cero o negativo. Eso
la distingue de `unit`, cuyo vocabulario cerrado se dejó fuera de la base a
propósito para que crecerlo no exija una migración.

`op.batch_alter_table` por el mismo motivo que `0006`: SQLite no soporta
`ALTER TABLE ... ADD CONSTRAINT` y el test de esta migración corre
`upgrade()`/`downgrade()` contra un engine SQLite real. En PostgreSQL
(producción) `recreate="auto"` emite `ALTER TABLE` ordinario. Vale la misma
advertencia registrada en `0006`: no correr estas migraciones contra una base
SQLite real, porque un `recreate` de batch borraría en silencio los CHECK
preexistentes que la reflexión de SQLite no devuelve.

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("prices_history") as batch_op:
        batch_op.add_column(
            sa.Column("min_purchase_quantity", sa.Numeric(precision=12, scale=3), nullable=True)
        )
        batch_op.create_check_constraint(
            "ck_prices_history_min_purchase_quantity",
            "min_purchase_quantity IS NULL OR min_purchase_quantity > 0",
        )


def downgrade() -> None:
    with op.batch_alter_table("prices_history") as batch_op:
        batch_op.drop_constraint("ck_prices_history_min_purchase_quantity", type_="check")
        batch_op.drop_column("min_purchase_quantity")
