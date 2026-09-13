"""`saimc.extraction.ocr` — transcripción OCR de evidencia capturada como imagen.

Los casos con mock no dependen del binario de Tesseract: prueban que nuestro
código propaga o degrada correctamente, no la calidad del OCR en sí. El
único test que corre Tesseract de verdad está marcado `skipif` y se salta
si el binario no está instalado (igual criterio que el resto del proyecto:
no depender de un binario del sistema para que la suite pase en cualquier
lado).

Desde que `transcribe` es una cascada (RapidOCR y, si no, Tesseract), los
casos del respaldo apagan RapidOCR explícitamente con la fixture
`sin_rapidocr`. Sin eso la suite daría resultados distintos según si
`rapidocr` —que es dependencia opcional— está instalado o no en la máquina
que la corre, que es exactamente la clase de test que miente.
"""

import shutil
from pathlib import Path

import pytesseract
import pytest
from PIL import Image, ImageDraw, ImageFont

from saimc.extraction import ocr


@pytest.fixture(autouse=True)
def _motor_sin_cachear(monkeypatch: pytest.MonkeyPatch) -> None:
    """El motor se cachea por proceso: sin limpiarlo, el primer test que lo
    construya (o que descubra que no está) decide por todos los demás."""
    monkeypatch.setattr(ocr, "_rapidocr_cache", ocr._UNSET)


@pytest.fixture
def sin_rapidocr(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fuerza el camino del respaldo, haya o no `rapidocr` instalado."""
    monkeypatch.setattr(ocr, "_rapidocr_engine", lambda: None)


class _MotorFalso:
    """Imita lo único que este módulo le pide a RapidOCR: ser llamable y
    devolver un objeto con `.txts`."""

    def __init__(self, txts: list[str] | None, error: Exception | None = None) -> None:
        self._txts = txts
        self._error = error
        self.llamadas = 0

    def __call__(self, ruta: str) -> object:
        self.llamadas += 1
        if self._error is not None:
            raise self._error
        return type("Resultado", (), {"txts": self._txts})()


class TestCascadaDeMotores:
    """RapidOCR primero; Tesseract cuando aquel no tiene una respuesta."""

    def test_usa_rapidocr_cuando_esta_disponible(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        imagen = tmp_path / "foto.png"
        Image.new("RGB", (10, 10), "white").save(imagen)
        motor = _MotorFalso(["Sal de cocina a 130 cup la libra", "Hoy, 9:21 a. m."])
        monkeypatch.setattr(ocr, "_rapidocr_engine", lambda: motor)

        def _no_deberia_llamarse(img, lang, config):
            raise AssertionError("Tesseract no debía correr")

        monkeypatch.setattr(pytesseract, "image_to_string", _no_deberia_llamarse)

        assert ocr.transcribe(imagen) == (
            "Sal de cocina a 130 cup la libra\nHoy, 9:21 a. m."
        )

    def test_sin_rapidocr_instalado_cae_a_tesseract(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sin_rapidocr: None
    ) -> None:
        imagen = tmp_path / "foto.png"
        Image.new("RGB", (10, 10), "white").save(imagen)
        monkeypatch.setattr(
            pytesseract, "image_to_string", lambda img, lang, config: "desde tesseract"
        )

        assert ocr.transcribe(imagen) == "desde tesseract"

    def test_si_rapidocr_revienta_con_la_imagen_cae_a_tesseract(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Un fallo con UNA imagen no puede dejar sin transcribir el resto."""
        imagen = tmp_path / "foto.png"
        Image.new("RGB", (10, 10), "white").save(imagen)
        monkeypatch.setattr(
            ocr, "_rapidocr_engine", lambda: _MotorFalso(None, error=RuntimeError("boom"))
        )
        monkeypatch.setattr(
            pytesseract, "image_to_string", lambda img, lang, config: "desde tesseract"
        )

        assert ocr.transcribe(imagen) == "desde tesseract"

    def test_si_rapidocr_no_encuentra_texto_cae_a_tesseract(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Una imagen donde RapidOCR no ve nada todavía puede darle texto a
        Tesseract; el respaldo solo se paga en esas."""
        imagen = tmp_path / "foto.png"
        Image.new("RGB", (10, 10), "white").save(imagen)
        monkeypatch.setattr(ocr, "_rapidocr_engine", lambda: _MotorFalso([]))
        monkeypatch.setattr(
            pytesseract, "image_to_string", lambda img, lang, config: "desde tesseract"
        )

        assert ocr.transcribe(imagen) == "desde tesseract"

    def test_el_motor_se_construye_una_sola_vez(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Construirlo cuesta ~0,7s y ~32 MB de modelos: una vez por proceso."""
        imagen = tmp_path / "foto.png"
        Image.new("RGB", (10, 10), "white").save(imagen)
        construcciones = {"n": 0}

        class _Fabrica:
            def __init__(self) -> None:
                construcciones["n"] += 1

            def __call__(self, ruta: str) -> object:
                return type("Resultado", (), {"txts": ["hola"]})()

        monkeypatch.setattr(ocr, "_rapidocr_cache", ocr._UNSET)
        monkeypatch.setitem(
            __import__("sys").modules, "rapidocr", type("m", (), {"RapidOCR": _Fabrica})
        )

        ocr.transcribe(imagen)
        ocr.transcribe(imagen)
        ocr.transcribe(imagen)

        assert construcciones["n"] == 1

    def test_rapidocr_ausente_no_rompe_y_lo_registra_una_vez(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """El import que falla se cachea: no se reintenta por cada imagen."""
        imagen = tmp_path / "foto.png"
        Image.new("RGB", (10, 10), "white").save(imagen)
        monkeypatch.setitem(__import__("sys").modules, "rapidocr", None)
        monkeypatch.setattr(
            pytesseract, "image_to_string", lambda img, lang, config: "desde tesseract"
        )

        assert ocr.transcribe(imagen) == "desde tesseract"
        assert ocr._rapidocr_cache is None


@pytest.mark.usefixtures("sin_rapidocr")
class TestTranscribeMockeado:
    def test_propaga_el_texto_reconocido_tal_cual(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        imagen = tmp_path / "foto.png"
        Image.new("RGB", (10, 10), "white").save(imagen)

        monkeypatch.setattr(
            pytesseract,
            "image_to_string",
            lambda img, lang, config: "pollo a 500 habana",
        )

        assert ocr.transcribe(imagen) == "pollo a 500 habana"

    def test_pide_el_modo_de_bloque_uniforme(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """La evidencia es una captura de chat, no una página con columnas.

        El modo por defecto de Tesseract (psm 3) segmenta buscando estructura
        de página; `--psm 6` la trata como un bloque uniforme de texto, que es
        lo que una captura de WhatsApp o Facebook realmente es. Medido contra
        las 77 imágenes curadas del corpus el 2026-08-21: producto 84.4% →
        85.3%, provincia 76.8% → 79.1%, precio 53.1% → 53.6%.
        """
        imagen = tmp_path / "foto.png"
        Image.new("RGB", (10, 10), "white").save(imagen)
        recibido: dict[str, object] = {}

        def _capturar(img, lang, config):
            recibido["lang"] = lang
            recibido["config"] = config
            return ""

        monkeypatch.setattr(pytesseract, "image_to_string", _capturar)
        ocr.transcribe(imagen)

        assert recibido["config"] == "--psm 6"
        assert recibido["lang"] == "spa"

    def test_binario_de_tesseract_ausente_devuelve_string_vacio(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        imagen = tmp_path / "foto.png"
        Image.new("RGB", (10, 10), "white").save(imagen)

        def _raise(img, lang, config):
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

        def _raise(img, lang, config):
            raise ValueError("boom")

        monkeypatch.setattr(pytesseract, "image_to_string", _raise)

        with pytest.raises(ValueError):
            ocr.transcribe(imagen)


def _rapidocr_instalado() -> bool:
    try:
        import rapidocr  # noqa: F401
    except ImportError:
        return False
    return True


@pytest.mark.skipif(
    shutil.which("tesseract") is None, reason="tesseract no está instalado"
)
def test_smoke_tesseract_real_reconoce_texto_grande(
    tmp_path: Path, sin_rapidocr: None
) -> None:
    imagen = tmp_path / "hola.png"
    lienzo = Image.new("RGB", (400, 150), "white")
    dibujo = ImageDraw.Draw(lienzo)
    fuente = ImageFont.load_default(size=80)
    dibujo.text((20, 30), "HOLA", fill="black", font=fuente)
    lienzo.save(imagen)

    texto = ocr.transcribe(imagen)

    assert "hola" in texto.lower()


@pytest.mark.skipif(not _rapidocr_instalado(), reason="rapidocr no está instalado")
def test_smoke_rapidocr_real_reconoce_texto_grande(tmp_path: Path) -> None:
    imagen = tmp_path / "hola.png"
    lienzo = Image.new("RGB", (400, 150), "white")
    dibujo = ImageDraw.Draw(lienzo)
    fuente = ImageFont.load_default(size=80)
    dibujo.text((20, 30), "HOLA", fill="black", font=fuente)
    lienzo.save(imagen)

    texto = ocr.transcribe(imagen)

    assert "hola" in texto.lower()
