"""Crea `product_formats` y agrega `prices_history.format_id` + `packaging_legacy`.

**Puramente aditiva** (design.md D4a). No mapea las filas viejas ni dropea
`packaging`: eso es `0019`, que aterriza con el cambio de agregación
(`cells.py`). El motivo del corte: `curation.py` todavía escribe `packaging`
hasta el commit 4 de la cadena, y dropear la columna antes rompería el
formulario de curaduría durante todo el intervalo. `cells.py` nunca lee
`packaging`, así que la agregación está a salvo mientras tanto.

Lo único no estructural que hace es copiar `packaging` → `packaging_legacy`,
para que `0019` pueda dropear `packaging` y su `downgrade` recupere las
variantes escritas a mano ("Arroz importado a 320 la libra") textual — el
mapeo a formato es muchos-a-uno y con pérdida (design.md D6).

`product_formats.packaging` lleva CHECK nombrado, a diferencia de
`prices_history.unit` que a propósito no lo tiene (design.md D2): el envase es
la columna que ya se pudrió una vez con texto libre.

`op.batch_alter_table` por el mismo motivo que `0006`/`0016`/`0017`: SQLite no
soporta `ALTER TABLE ... ADD CONSTRAINT` ni agregar una FK a una tabla
existente, y `tests/test_processing_layer.py::TestProductFormatsMigration`
corre `upgrade()`/`downgrade()` contra un engine SQLite real. En PostgreSQL
`recreate="auto"` emite `ALTER TABLE` ordinario. Misma advertencia: no correr
estas migraciones contra una base SQLite real fuera de ese test.

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "product_formats",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("product_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("packaging", sa.String(length=32), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column("unit", sa.String(length=10), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name="fk_product_formats_product_id",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "product_id",
            "packaging",
            "quantity",
            "unit",
            name="uq_product_formats_identity",
        ),
        sa.CheckConstraint(
            "packaging IN ('paquete', 'saco', 'caja', 'botella', 'pomo', 'a granel')",
            name="ck_product_formats_packaging",
        ),
    )

    with op.batch_alter_table("prices_history") as batch_op:
        batch_op.add_column(
            sa.Column("packaging_legacy", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(
            sa.Column("format_id", sa.Uuid(as_uuid=True), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_prices_history_format_id",
            "product_formats",
            ["format_id"],
            ["id"],
            ondelete="RESTRICT",
        )

    # Copia textual, no mapeo: `0019` mapea y dropea `packaging`; esta columna
    # es lo que hace reversible ese drop (design.md D6).
    op.execute("UPDATE prices_history SET packaging_legacy = packaging")


def downgrade() -> None:
    with op.batch_alter_table("prices_history") as batch_op:
        batch_op.drop_constraint("fk_prices_history_format_id", type_="foreignkey")
        batch_op.drop_column("format_id")
        batch_op.drop_column("packaging_legacy")

    op.drop_table("product_formats")
