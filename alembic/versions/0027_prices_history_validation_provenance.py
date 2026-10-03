"""Persiste la validación en `prices_history` y ensancha `prompt_version` (B1, K6).

Sobre `prices_history` (`docs/spec-slice-B1.md`):

- `validation_status`: el estado que le da el motor a la fila. Nullable, con
  CHECK sobre los siete estados de `ValidationStatus`. Nulo = sin validar.
- `validation_detail`: JSON con las seis reglas, los motivos, la versión del
  motor, los parámetros y la fecha. Nullable.
- `provenance`: quién hizo la fila (`human`, `auto`, `audited`). NOT NULL.

`confidence_score` no cambia: ya existía desde la 0002 y pasa a llenarse.

**Las filas existentes quedan con `provenance = 'human'`** y sin estado: todas
salieron de `submit_curation`, con una persona delante. El estado se les
completa después con `python -m saimc.validation.revalidate --execute`.

La columna se agrega nullable, se completa con un `UPDATE` y recién entonces
pasa a NOT NULL. Así no queda ningún `server_default`: quien crea una fila tiene
que decir de dónde viene, y un valor por defecto en la base taparía el olvido.

Sobre `llm_proposals`: `prompt_version` pasa de 32 a 64 caracteres (K6). La
versión con glosario ya ocupaba 30 y PostgreSQL no trunca: rechaza el insert, y
el pre-cálculo se caería.

`batch_alter_table` por el mismo motivo que la 0008, la 0016 y la 0026: SQLite
(los tests) no sabe agregar un CHECK ni cambiar una columna en sitio. En
PostgreSQL se traduce a `ALTER TABLE` ordinario. Vale la advertencia de la
0016: no correr esto contra una base SQLite real.

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUS = "ck_prices_history_validation_status"
_PROVENANCE = "ck_prices_history_provenance"


def upgrade() -> None:
    with op.batch_alter_table("prices_history") as batch_op:
        batch_op.add_column(sa.Column("validation_status", sa.String(length=24), nullable=True))
        batch_op.add_column(sa.Column("validation_detail", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("provenance", sa.String(length=16), nullable=True))

    op.execute("UPDATE prices_history SET provenance = 'human'")

    with op.batch_alter_table("prices_history") as batch_op:
        batch_op.alter_column("provenance", existing_type=sa.String(length=16), nullable=False)
        batch_op.create_check_constraint(
            _STATUS,
            "validation_status IS NULL OR validation_status IN ("
            "'approved', 'incomplete', 'anomaly', 'inconsistent', "
            "'possible_duplicate', 'untrusted_source', 'unverified')",
        )
        batch_op.create_check_constraint(_PROVENANCE, "provenance IN ('human', 'auto', 'audited')")

    with op.batch_alter_table("llm_proposals") as batch_op:
        batch_op.alter_column(
            "prompt_version",
            existing_type=sa.String(length=32),
            type_=sa.String(length=64),
            existing_nullable=False,
        )


def downgrade() -> None:
    # Una `prompt_version` de más de 32 caracteres que ya exista bloquea la
    # vuelta atrás en PostgreSQL, a propósito: no se recorta un dato guardado
    # para que el tipo viejo encaje.
    with op.batch_alter_table("llm_proposals") as batch_op:
        batch_op.alter_column(
            "prompt_version",
            existing_type=sa.String(length=64),
            type_=sa.String(length=32),
            existing_nullable=False,
        )

    # Bajar pierde el estado, el detalle y la procedencia. El estado se
    # recalcula con `revalidate`; la procedencia, mientras solo exista `human`,
    # la vuelve a completar el `upgrade`.
    with op.batch_alter_table("prices_history") as batch_op:
        batch_op.drop_constraint(_STATUS, type_="check")
        batch_op.drop_constraint(_PROVENANCE, type_="check")
        batch_op.drop_column("validation_status")
        batch_op.drop_column("validation_detail")
        batch_op.drop_column("provenance")
