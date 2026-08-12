"""Agrega `unit`, `sale_format` y `packaging` a `prices_history`.

Tres columnas aditivas nullable, sin backfill: las 53 filas existentes quedan
en `NULL` en las tres. `unit` (String(10)) no lleva CHECK — el vocabulario
cerrado (`saimc.units.UNITS`) se aplica en código de aplicación, así que
crecerlo nunca exige una migración (design.md, D1). `sale_format`
(String(10)) sí lleva el CHECK nombrado `ck_prices_history_sale_format`
porque su dominio es una sola pregunta cerrada de sí/no y este proyecto
corre DML manual contra producción. `packaging` (String(64)) es texto libre.

Usa `op.batch_alter_table` para las cuatro operaciones (design.md, D2):
SQLite no soporta `ALTER TABLE ... ADD CONSTRAINT`, y
`tests/test_processing_layer.py::TestUnitSaleFormatPackagingMigration`
corre `upgrade()`/`downgrade()` contra un engine SQLite real vía
`Operations.context()`, igual que `TestPublishedDateMigration` para 0005. Con
`recreate="auto"` (el default de `batch_alter_table`), en PostgreSQL
(producción) esto emite `ALTER TABLE` ordinario — el modo batch no es una
preferencia de estilo, es lo que mantiene usable el patrón de test de
migración existente sin debilitar el DDL de producción.

Gotcha a dejar registrado: la reflexión de SQLite en SQLAlchemy no devuelve
CHECK constraints, así que un `recreate` de batch contra una base SQLite
*real* borraría en silencio los tres CHECK preexistentes de `prices_history`.
Inofensivo acá — producción es PostgreSQL (sin recreate) y la tabla que arma
el test de migración no tiene ningún CHECK previo — pero es motivo para no
correr nunca estas migraciones contra SQLite fuera de ese test.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("prices_history") as batch_op:
        batch_op.add_column(sa.Column("unit", sa.String(length=10), nullable=True))
        batch_op.add_column(
            sa.Column("sale_format", sa.String(length=10), nullable=True)
        )
        batch_op.add_column(
            sa.Column("packaging", sa.String(length=64), nullable=True)
        )
        batch_op.create_check_constraint(
            "ck_prices_history_sale_format",
            "sale_format IS NULL OR sale_format IN ('retail', 'wholesale')",
        )


def downgrade() -> None:
    with op.batch_alter_table("prices_history") as batch_op:
        batch_op.drop_constraint("ck_prices_history_sale_format", type_="check")
        batch_op.drop_column("packaging")
        batch_op.drop_column("sale_format")
        batch_op.drop_column("unit")
