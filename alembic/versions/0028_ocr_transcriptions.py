"""Crea `ocr_transcriptions`: la transcripción del OCR guardada (slice K3).

`docs/spec-slice-K3.md`. Tabla derivada y de solo altas, con el texto ya
redactado y, con RapidOCR, las cajas por renglón. `UNIQUE (ingestion_id,
ocr_version)`: una transcripción por imagen y por versión del OCR, así dos
procesos que leen la misma imagen a la vez no dejan dos filas.

No toca `raw_ingestions` ni hay backfill: las filas se llenan solas al pasar
la imagen por el pre-cálculo o la cola.

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ocr_transcriptions",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("ingestion_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("engine", sa.String(length=16), nullable=False),
        sa.Column("ocr_version", sa.String(length=32), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("boxes", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("engine IN ('rapidocr', 'tesseract')", name="ck_ocr_transcriptions_engine"),
        sa.ForeignKeyConstraint(
            ["ingestion_id"],
            ["raw_ingestions.id"],
            name="fk_ocr_transcriptions_ingestion_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ingestion_id", "ocr_version", name="uq_ocr_transcriptions_version"),
    )
    op.create_index("idx_ocr_transcriptions_ingestion_id", "ocr_transcriptions", ["ingestion_id"])


def downgrade() -> None:
    op.drop_index("idx_ocr_transcriptions_ingestion_id", table_name="ocr_transcriptions")
    op.drop_table("ocr_transcriptions")
