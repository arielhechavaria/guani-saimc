"""Amplía los CHECK de `curation_events` para el carril 0 (slice B2a).

- `mode` suma `auto`: el descarte que hace el carril 0 desde el pre-cálculo,
  como ya anticipaba la 0025.
- `action` suma `rescued` y `audited`: las dos respuestas de la auditoría
  semanal (`/curation/auditoria`).

Solo cambia el vocabulario permitido; no toca datos ni columnas. Mismo patrón
que la 0025: `batch_alter_table`, porque SQLite (los tests) no sabe cambiar un
CHECK en sitio; en PostgreSQL se traduce a `DROP CONSTRAINT` y `ADD CONSTRAINT`.

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MODE = "ck_curation_events_mode"
_ACTION = "ck_curation_events_action"


def upgrade() -> None:
    with op.batch_alter_table("curation_events") as batch_op:
        batch_op.drop_constraint(_MODE, type_="check")
        batch_op.create_check_constraint(
            _MODE, "mode IN ('individual', 'batch_out_of_focus', 'batch_model', 'auto')"
        )
        batch_op.drop_constraint(_ACTION, type_="check")
        batch_op.create_check_constraint(
            _ACTION,
            "action IN ('curated', 'discarded', 'deleted', 'edited', 'rescued', 'audited')",
        )


def downgrade() -> None:
    # Las filas `auto`, `rescued` o `audited` que existan bloquean la vuelta
    # atrás a propósito: la bitácora es de solo altas y no se reescribe para
    # que el CHECK encaje.
    with op.batch_alter_table("curation_events") as batch_op:
        batch_op.drop_constraint(_MODE, type_="check")
        batch_op.create_check_constraint(
            _MODE, "mode IN ('individual', 'batch_out_of_focus', 'batch_model')"
        )
        batch_op.drop_constraint(_ACTION, type_="check")
        batch_op.create_check_constraint(
            _ACTION, "action IN ('curated', 'discarded', 'deleted', 'edited')"
        )
