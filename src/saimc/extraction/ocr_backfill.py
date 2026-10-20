"""Transcribe las imágenes pendientes que no tienen OCR guardado.

El pre-cálculo solo transcribe las imágenes que le faltan de propuesta
(`prefill.select_candidates`). Las que ya tenían propuesta `ok` de antes de K3
nunca guardaron su transcripción, y el texto del OCR es lo que ahora entra al
flujo de texto. Este comando las recorre y llama a
`ocr_store.stored_transcription`, el único camino de lectura: redacta teléfonos,
guarda en `ocr_transcriptions` y no guarda una transcripción vacía.

No llama a ninguna API ni gasta: es OCR local. Solo agrega filas a
`ocr_transcriptions`; `raw_ingestions` no se toca.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from saimc.config import get_settings
from saimc.db import session_scope
from saimc.extraction import ocr, ocr_store
from saimc.models import ContentType, OcrTranscription, ProcessingStatus, RawIngestion


@dataclass
class BackfillResult:
    total: int = 0
    transcribed: int = 0
    empty: int = 0


def select_missing(session: Session) -> list[RawIngestion]:
    """Imágenes pendientes sin transcripción de la versión vigente del OCR."""
    has_ocr = exists().where(
        OcrTranscription.ingestion_id == RawIngestion.id,
        OcrTranscription.ocr_version == ocr.OCR_VERSION,
    )
    stmt = (
        select(RawIngestion)
        .where(
            RawIngestion.processing_status == ProcessingStatus.PENDING,
            RawIngestion.content_type == ContentType.IMAGE,
            ~has_ocr,
        )
        .order_by(RawIngestion.captured_at.desc())
    )
    return list(session.execute(stmt).scalars().all())


def run(
    session: Session,
    candidates: Sequence[RawIngestion],
    raw_store_dir: Path,
    *,
    transcribe: ocr_store.Transcriber | None = None,
    limit: int | None = None,
    out: Callable[[str], None] = print,
) -> BackfillResult:
    """Transcribe cada candidata y hace commit por imagen (lo hecho queda guardado)."""
    result = BackfillResult()
    for ingestion in candidates if limit is None else candidates[:limit]:
        result.total += 1
        text = ocr_store.stored_transcription(session, ingestion, raw_store_dir, transcribe)
        session.commit()
        if text.strip():
            result.transcribed += 1
        else:
            result.empty += 1
            out(f"{str(ingestion.id)[:8]}: sin texto (no se guarda)")
    return result


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m saimc.extraction.ocr_backfill",
        description=(
            "Transcribe con OCR las imágenes pendientes que no tienen transcripción guardada. "
            "Por defecto solo cuenta; --execute transcribe y guarda. No gasta API."
        ),
    )
    parser.add_argument("--execute", action="store_true", help="Transcribir y guardar.")
    parser.add_argument("--limit", type=int, default=None, help="Tope de imágenes.")
    return parser.parse_args(argv)


def execute(
    args: argparse.Namespace,
    *,
    session: Session,
    raw_store_dir: Path,
    transcribe: ocr_store.Transcriber | None = None,
    out: Callable[[str], None] = print,
) -> int:
    missing = select_missing(session)
    if not args.execute:
        out(f"{len(missing)} imágenes pendientes sin transcripción (versión {ocr.OCR_VERSION}).")
        out("En seco: no se transcribió nada. Con --execute se transcriben y se guardan.")
        return 0
    result = run(session, missing, raw_store_dir, transcribe=transcribe, limit=args.limit, out=out)
    out(
        f"{result.total} imágenes recorridas: {result.transcribed} transcriptas, "
        f"{result.empty} sin texto."
    )
    return 0


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    settings = get_settings()
    with session_scope() as session:
        code = execute(args, session=session, raw_store_dir=settings.raw_store_dir)
    sys.exit(code)


if __name__ == "__main__":
    main()
