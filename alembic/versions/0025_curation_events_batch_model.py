"""Amplía el CHECK de `curation_events.mode` con `batch_model` (slice A1b).

El spec de A2 (`docs/spec-slice-A2.md`) dejó `batch_model` para la migración del
slice que lo usa: el lote de imágenes con el veredicto del modelo. Solo cambia
el vocabulario permitido; no toca datos ni columnas.

`batch_alter_table` porque SQLite (los tests) no sabe cambiar un CHECK en
sitio; en PostgreSQL se traduce a un `DROP CONSTRAINT` y un `ADD CONSTRAINT`.
`auto` (B2) amplía el CHECK en su propia migración.

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-02
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CHECK = "ck_curation_events_mode"


def upgrade() -> None:
    with op.batch_alter_table("curation_events") as batch_op:
        batch_op.drop_constraint(_CHECK, type_="check")
        batch_op.create_check_constraint(
            _CHECK, "mode IN ('individual', 'batch_out_of_focus', 'batch_model')"
        )


def downgrade() -> None:
    # Las filas `batch_model` que existan bloquean la vuelta atrás a propósito:
    # la bitácora es de solo altas y no se reescribe para que el CHECK encaje.
    with op.batch_alter_table("curation_events") as batch_op:
        batch_op.drop_constraint(_CHECK, type_="check")
        batch_op.create_check_constraint(
            _CHECK, "mode IN ('individual', 'batch_out_of_focus')"
        )
