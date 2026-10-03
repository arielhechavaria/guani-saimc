"""El único camino de lectura de la transcripción del OCR (slice K3).

`stored_transcription` devuelve el texto guardado de una imagen o, si no hay,
la transcribe, la redacta, la guarda y la devuelve. Lo usan el pre-cálculo, la
cola y el contexto del motor, así una imagen se transcribe una vez por versión
del OCR (`ocr.OCR_VERSION`) y no tres. `measure` y el backtest NO pasan por
acá: siguen transcribiendo de nuevo para medir el extractor de hoy.

La fila es un caché derivado: un fallo al guardarla nunca rompe a quien lee.
Ver `docs/spec-slice-K3.md`.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from saimc.extraction import ocr
from saimc.models import OcrTranscription, RawIngestion
from saimc.privacy import PHONE_PLACEHOLDER, redact_personal_data

logger = logging.getLogger(__name__)

# Lo que acepta como transcriptor: el real (`ocr.transcribe_detailed`) o el
# seam de los tests, que devuelve solo `str`.
Transcriber = Callable[[Path], "ocr.OcrResult | str"]


def _find(db: Session, ingestion_id: uuid.UUID) -> OcrTranscription | None:
    return db.execute(
        select(OcrTranscription).where(
            OcrTranscription.ingestion_id == ingestion_id,
            OcrTranscription.ocr_version == ocr.OCR_VERSION,
        )
    ).scalar_one_or_none()


def _as_result(raw: ocr.OcrResult | str) -> ocr.OcrResult:
    """Un transcriptor que devuelve `str` no declara motor ni cajas: se guarda
    como el respaldo (`tesseract`, sin cajas). No se inventa un tercer motor."""
    if isinstance(raw, str):
        return ocr.OcrResult(raw, ocr.ENGINE_TESSERACT)
    return raw


def _redact(result: ocr.OcrResult) -> tuple[str, list[dict[str, Any]] | None]:
    """El texto y las cajas sin teléfonos.

    El texto se redacta entero (como siempre); cada caja, por renglón, con
    `redacted` en verdadero si ese renglón traía un número. Un teléfono partido
    entre dos renglones lo ve la redacción del texto entero pero no la de cada
    renglón: en ese caso se tapan todos los renglones con dígitos, porque es
    preferible perder el texto de una caja que guardar un pedazo del número.
    """
    text = redact_personal_data(result.text) or ""
    if result.boxes is None:
        return text, None
    boxes: list[dict[str, Any]] = []
    for box in result.boxes:
        clean = redact_personal_data(box["text"]) or ""
        boxes.append({**box, "text": clean, "redacted": clean != box["text"]})
    if text != "\n".join(b["text"] for b in boxes):
        for raw, box in zip(result.boxes, boxes, strict=True):
            if any(ch.isdigit() for ch in raw["text"]):
                box["text"] = PHONE_PLACEHOLDER
                box["redacted"] = True
    return text, boxes


def stored_transcription(
    db: Session,
    ingestion: RawIngestion,
    raw_store_dir: Path,
    transcribe: Transcriber | None = None,
) -> str:
    """El texto del OCR de la imagen, redactado, transcribiéndola solo si hace falta.

    `transcribe` es el seam de test; por defecto `ocr.transcribe_detailed`,
    resuelto al llamar. Una transcripción vacía no se guarda: un archivo que
    todavía no está o un fallo puntual no quedan congelados como «sin texto».

    No hace commit: guarda dentro de un SAVEPOINT y deja la transacción del
    llamador como estaba. Si dos procesos transcriben a la vez, el que pierde la
    carrera de `UNIQUE (ingestion_id, ocr_version)` devuelve la fila del otro.
    """
    existing = _find(db, ingestion.id)
    if existing is not None:
        return existing.text

    if transcribe is None:
        transcribe = ocr.transcribe_detailed
    result = _as_result(transcribe(raw_store_dir / ingestion.file_path))
    text, boxes = _redact(result)
    if not text.strip():
        return text

    try:
        with db.begin_nested():
            db.add(
                OcrTranscription(
                    ingestion_id=ingestion.id,
                    engine=result.engine,
                    ocr_version=ocr.OCR_VERSION,
                    text=text,
                    boxes=boxes,
                )
            )
    except IntegrityError:
        # Otro proceso guardó la misma imagen y versión entre la lectura y el alta.
        winner = _find(db, ingestion.id)
        if winner is None:
            logger.warning("ocr_store.no_se_pudo_guardar ingestion=%s", ingestion.id)
            return text
        return winner.text
    return text
