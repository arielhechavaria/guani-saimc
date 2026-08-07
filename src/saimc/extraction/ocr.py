"""Transcripción OCR de la evidencia capturada como imagen.

El whitepaper (§6.1, «Rol del OCR») ubica esta etapa en la cola de curaduría,
no en la ingesta: la imagen entra cruda a `raw_ingestions` y el texto se
transcribe recién cuando el operador la está revisando. Este módulo solo
transcribe; confirmar o corregir el resultado es responsabilidad del router
de curaduría.
"""

from pathlib import Path

import pytesseract
from PIL import Image, UnidentifiedImageError


def transcribe(path: Path, lang: str = "spa") -> str:
    """Transcribe el texto de una imagen con Tesseract.

    Degradación agraciada ante fallos esperables (binario de Tesseract
    ausente, archivo inexistente, imagen corrupta): la cola de curaduría no
    debe romperse porque el OCR falló en una captura puntual, el operador
    sigue pudiendo completar el formulario a mano igual que hoy. Cualquier
    otro error (por ejemplo de programación) se propaga: tragarlo escondería
    bugs reales detrás de un "" indistinguible de "no había texto".
    """
    try:
        return pytesseract.image_to_string(Image.open(path), lang=lang)
    except (
        pytesseract.TesseractNotFoundError,
        FileNotFoundError,
        UnidentifiedImageError,
    ):
        return ""
