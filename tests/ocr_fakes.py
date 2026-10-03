"""Doble del OCR para las pruebas de la cola: reemplaza `ocr.transcribe_detailed`.

Desde K3 la cola, el pre-cálculo y el motor leen `ocr_store.stored_transcription`,
que transcribe con `ocr.transcribe_detailed`. Estos dobles devuelven solo texto,
como el seam de siempre; el resultado se envuelve como un `OcrResult` sin cajas.
"""

from collections.abc import Callable
from pathlib import Path

import pytest

from saimc.extraction import ocr
from saimc.extraction.ocr import ENGINE_TESSERACT, OcrResult


def as_detailed(texto: Callable[..., str]) -> Callable[..., OcrResult]:
    """Envuelve un transcriptor de `str` (`lambda path, lang="spa": ...`)."""

    def _detailed(path: Path, lang: str = "spa") -> OcrResult:
        return OcrResult(texto(path, lang), ENGINE_TESSERACT)

    return _detailed


def patch_ocr(monkeypatch: pytest.MonkeyPatch, texto: Callable[..., str]) -> None:
    """Reemplaza `ocr.transcribe_detailed` por un transcriptor de `str`."""
    monkeypatch.setattr(ocr, "transcribe_detailed", as_detailed(texto))
