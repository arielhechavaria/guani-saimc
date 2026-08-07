"""`saimc.extraction.ocr` — transcripción OCR de evidencia capturada como imagen.

Los casos con mock no dependen del binario de Tesseract: prueban que nuestro
código propaga o degrada correctamente, no la calidad del OCR en sí. El
único test que corre Tesseract de verdad está marcado `skipif` y se salta
si el binario no está instalado (igual criterio que el resto del proyecto:
no depender de un binario del sistema para que la suite pase en cualquier
lado).
"""

import shutil
from pathlib import Path

import pytest
import pytesseract
from PIL import Image, ImageDraw, ImageFont

from saimc.extraction import ocr


class TestTranscribeMockeado:
    def test_propaga_el_texto_reconocido_tal_cual(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        imagen = tmp_path / "foto.png"
        Image.new("RGB", (10, 10), "white").save(imagen)

        monkeypatch.setattr(
            pytesseract, "image_to_string", lambda img, lang: "pollo a 500 habana"
        )

        assert ocr.transcribe(imagen) == "pollo a 500 habana"

    def test_binario_de_tesseract_ausente_devuelve_string_vacio(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        imagen = tmp_path / "foto.png"
        Image.new("RGB", (10, 10), "white").save(imagen)

        def _raise(img, lang):
            raise pytesseract.TesseractNotFoundError()

        monkeypatch.setattr(pytesseract, "image_to_string", _raise)

        assert ocr.transcribe(imagen) == ""

    def test_archivo_inexistente_devuelve_string_vacio(self, tmp_path: Path) -> None:
        assert ocr.transcribe(tmp_path / "no-existe.png") == ""

    def test_imagen_corrupta_devuelve_string_vacio(self, tmp_path: Path) -> None:
        corrupta = tmp_path / "corrupta.png"
        corrupta.write_bytes(b"esto no es una imagen")

        assert ocr.transcribe(corrupta) == ""

    def test_error_no_previsto_se_propaga(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Degradar agraciadamente fallos conocidos no es tragarse cualquier error."""
        imagen = tmp_path / "foto.png"
        Image.new("RGB", (10, 10), "white").save(imagen)

        def _raise(img, lang):
            raise ValueError("boom")

        monkeypatch.setattr(pytesseract, "image_to_string", _raise)

        with pytest.raises(ValueError):
            ocr.transcribe(imagen)


@pytest.mark.skipif(
    shutil.which("tesseract") is None, reason="tesseract no está instalado"
)
def test_smoke_ocr_real_reconoce_texto_grande(tmp_path: Path) -> None:
    imagen = tmp_path / "hola.png"
    lienzo = Image.new("RGB", (400, 150), "white")
    dibujo = ImageDraw.Draw(lienzo)
    fuente = ImageFont.load_default(size=80)
    dibujo.text((20, 30), "HOLA", fill="black", font=fuente)
    lienzo.save(imagen)

    texto = ocr.transcribe(imagen)

    assert "hola" in texto.lower()
