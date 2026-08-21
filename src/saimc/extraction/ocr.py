"""Transcripción OCR de la evidencia capturada como imagen.

La especificación (§6.1, «Rol del OCR») ubica esta etapa en la cola de curaduría,
no en la ingesta: la imagen entra cruda a `raw_ingestions` y el texto se
transcribe recién cuando el operador la está revisando. Este módulo solo
transcribe; confirmar o corregir el resultado es responsabilidad del router
de curaduría.
"""

from pathlib import Path

import pytesseract
from PIL import Image, UnidentifiedImageError


# La evidencia es una captura de chat, no una página escaneada. El modo por
# defecto de Tesseract (psm 3) segmenta buscando estructura de página —
# columnas, bloques, orientación— y sobre una captura de WhatsApp o Facebook
# esa búsqueda parte el texto de más. `--psm 6` la trata como lo que es: un
# bloque uniforme de texto.
#
# Medido sobre las 77 imágenes curadas del corpus (2026-08-21): producto
# 84.4% → 85.3%, provincia 76.8% → 79.1%, precio 53.1% → 53.6%,
# cantidad/unidad 53.6% → 53.1%. La extracción completa NO se mueve (40.8%):
# lo que mejora cae en filas que ya fallaban por otro lado. Se adopta igual
# porque cada campo bien prellenado es trabajo que el operador no hace, que
# es justamente lo que H0 mide como costo. Cuesta ~45% más de tiempo por
# imagen (~0.9s), imperceptible abriendo un anuncio por vez.
#
# Descartado en la misma medición: upscaling 2x + escala de grises, con o sin
# autocontraste. Sube producto pero BAJA el precio (27.3% → 25.0%).
_PAGE_SEGMENTATION_MODE = "--psm 6"


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
        return pytesseract.image_to_string(
            Image.open(path), lang=lang, config=_PAGE_SEGMENTATION_MODE
        )
    except (
        pytesseract.TesseractNotFoundError,
        FileNotFoundError,
        UnidentifiedImageError,
    ):
        return ""
