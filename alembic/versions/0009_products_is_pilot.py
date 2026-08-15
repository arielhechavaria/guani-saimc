"""Agrega `is_pilot` a `products` y enciende el conjunto piloto vigente.

La columna implementa la decisión del 2026-08-15 (`docs/decisions.md`): la
Fase 0 se gobierna por densidad por celda, no por volumen, así que la
curaduría extrae precio solo de los productos del conjunto foco. El catálogo
puede contener cualquier cosa; el foco es un subconjunto declarado.

`server_default=false()` y no un backfill por heurística: apagar todo y
encender explícitamente es lo único que no inventa un criterio. El `UPDATE`
posterior enciende exactamente los cuatro nombres que `saimc.progress` ya
declaraba como `PILOT_PRODUCTS` — la elección es del operador
(`docs/decisions.md`, 2026-08-11), no de esta migración, que solo la mueve de
una tupla en código a la base.

Es deliberado que no sean los cinco de `spec.md` §9. «Leche» y «Huevos» están
sembrados y no tienen una sola observación, mientras que «Azúcar blanca»,
que el documento no nombra, acumuló once. Encender los cinco del documento
habría contradicho la decisión que el operador ya había tomado con datos a la
vista.

El resto del catálogo queda en `false`, incluidos los productos que el
operador dio de alta al vuelo durante la curaduría. Cuál se promueve es
decisión suya en /catalog, no de una migración que adivine por conteo.

`op.batch_alter_table` por el mismo motivo que `0006` y `0008`: el test de
esta migración corre `upgrade()`/`downgrade()` contra un engine SQLite real.

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Los cuatro que `saimc.progress.PILOT_PRODUCTS` declaraba hasta esta
# migración. Dentro del rango 3-5 que fija spec.md §9.
_PILOT_NAMES: tuple[str, ...] = ("Aceite", "Arroz", "Azúcar blanca", "Pollo")


def upgrade() -> None:
    with op.batch_alter_table("products") as batch_op:
        batch_op.add_column(
            sa.Column(
                "is_pilot",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )

    products = sa.table("products", sa.column("name", sa.String), sa.column("is_pilot", sa.Boolean))
    op.execute(
        products.update()
        .where(products.c.name.in_(_PILOT_NAMES))
        .values(is_pilot=True)
    )


def downgrade() -> None:
    with op.batch_alter_table("products") as batch_op:
        batch_op.drop_column("is_pilot")
