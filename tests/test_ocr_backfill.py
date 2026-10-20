"""`saimc.extraction.ocr_backfill` — transcribe las imágenes pendientes sin OCR guardado.

Caso real (2026-10-20): 53 imágenes pendientes tenían propuesta `ok` hecha antes de
K3, así que el pre-cálculo no las volvía a tocar y su OCR nunca se guardó. Los
valores esperados salen de ese diagnóstico, no del comportamiento del código.
"""

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from saimc.extraction import ocr, ocr_backfill
from saimc.models import (
    ContentType,
    OcrTranscription,
    ProcessingStatus,
    RawIngestion,
    SourceType,
)


def _ingestion(
    session: Session,
    nombre: str,
    *,
    tipo: ContentType = ContentType.IMAGE,
    estado: ProcessingStatus = ProcessingStatus.PENDING,
) -> RawIngestion:
    row = RawIngestion(
        source_type=SourceType.INBOX_IMAGE,
        file_path=f"{nombre}.jpg",
        content_hash=f"bf-{nombre}-{uuid.uuid4()}",
        content_type=tipo,
        original_text="hola" if tipo == ContentType.TEXT else None,
        processing_status=estado,
        captured_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    session.add(row)
    session.commit()
    return row


def _guardada(session: Session, row: RawIngestion, version: str = ocr.OCR_VERSION) -> None:
    session.add(
        OcrTranscription(
            ingestion_id=row.id, engine="tesseract", ocr_version=version, text="ya estaba"
        )
    )
    session.commit()


def test_select_missing_toma_solo_imagenes_pendientes_sin_ocr_vigente(session: Session) -> None:
    falta = _ingestion(session, "falta")
    con_ocr = _ingestion(session, "con_ocr")
    _guardada(session, con_ocr)
    vieja = _ingestion(session, "version_vieja")
    _guardada(session, vieja, version="k0-vieja")
    _ingestion(session, "texto", tipo=ContentType.TEXT)
    _ingestion(session, "curada", estado=ProcessingStatus.CURATED)

    ids = {r.id for r in ocr_backfill.select_missing(session)}

    assert ids == {falta.id, vieja.id}


def test_run_guarda_la_transcripcion_redactada_de_cada_imagen(
    session: Session, tmp_path: Path
) -> None:
    row = _ingestion(session, "uno")

    result = ocr_backfill.run(
        session, [row], tmp_path, transcribe=lambda p: "Arroz 1kg 350 CUP llamar 52345678"
    )

    guardado = session.execute(select(OcrTranscription)).scalar_one()
    assert guardado.ingestion_id == row.id
    assert "52345678" not in guardado.text
    assert "Arroz" in guardado.text
    assert (result.total, result.transcribed, result.empty) == (1, 1, 0)


def test_run_una_transcripcion_vacia_no_se_guarda_y_se_cuenta(
    session: Session, tmp_path: Path
) -> None:
    row = _ingestion(session, "vacia")

    result = ocr_backfill.run(session, [row], tmp_path, transcribe=lambda p: "   ")

    assert session.execute(select(OcrTranscription)).first() is None
    assert (result.total, result.transcribed, result.empty) == (1, 0, 1)


def test_run_no_repite_lo_ya_transcripto(session: Session, tmp_path: Path) -> None:
    row = _ingestion(session, "dos")
    llamadas: list[Path] = []

    def _t(path: Path) -> str:
        llamadas.append(path)
        return "texto"

    ocr_backfill.run(session, [row], tmp_path, transcribe=_t)
    ocr_backfill.run(session, [row], tmp_path, transcribe=_t)

    assert len(llamadas) == 1


def test_run_respeta_limit(session: Session, tmp_path: Path) -> None:
    filas = [_ingestion(session, f"n{i}") for i in range(3)]

    result = ocr_backfill.run(session, filas, tmp_path, transcribe=lambda p: "x", limit=2)

    assert result.total == 2
    assert len(session.execute(select(OcrTranscription)).all()) == 2


def test_cli_en_seco_cuenta_y_no_escribe(
    session: Session, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _ingestion(session, "a")
    _ingestion(session, "b")

    code = ocr_backfill.execute(
        ocr_backfill.parse_args([]), session=session, raw_store_dir=tmp_path
    )

    assert code == 0
    assert "2 imágenes" in capsys.readouterr().out
    assert session.execute(select(OcrTranscription)).first() is None


def test_cli_execute_transcribe(
    session: Session, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _ingestion(session, "a")

    code = ocr_backfill.execute(
        ocr_backfill.parse_args(["--execute"]),
        session=session,
        raw_store_dir=tmp_path,
        transcribe=lambda p: "pollo 300 CUP",
    )

    assert code == 0
    assert session.execute(select(OcrTranscription)).scalar_one().text == "pollo 300 CUP"
    assert "1 transcripta" in capsys.readouterr().out
