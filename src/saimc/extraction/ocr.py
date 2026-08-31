"""Transcripción OCR de la evidencia capturada como imagen.

La especificación (§6.1, «Rol del OCR») ubica esta etapa en la cola de curaduría,
no en la ingesta: la imagen entra cruda a `raw_ingestions` y el texto se
transcribe recién cuando el operador la está revisando. Este módulo solo
transcribe; confirmar o corregir el resultado es responsabilidad del router
de curaduría.

Hay dos motores, en cascada. **RapidOCR** primero —los modelos PP-OCR de
PaddleOCR corriendo sobre ONNX Runtime— y **Tesseract** como respaldo. La
cascada no es paranoia: `rapidocr` es una dependencia opcional (417 MB
instalados), así que un entorno sin ella tiene que seguir transcribiendo
igual, solo que peor.

Por qué se cambió el orden (medición del 2026-08-31 sobre las 98 filas de
imagen curadas, contra los precios que el operador confirmó):

    ¿el precio confirmado aparece en la transcripción?
        Tesseract   58,2%
        RapidOCR    78,6%   (+23 filas, −3)

En H0 completo el salto es mucho menor —43,5% → 44,2%— porque el cuello se
corre a las reglas: el número pasa a estar en el texto, pero el motor de
extracción todavía no siempre lo agarra. RapidOCR habilita ese trabajo, no lo
reemplaza (`docs/decisions.md`, 2026-08-31).

`paddleocr` propiamente dicho NO se puede usar acá: `paddlepaddle` publica
wheels hasta cp313 y el proyecto corre Python 3.14. `rapidocr` es un wheel
puro de Python sobre `onnxruntime`, que sí tiene cp314, y ejecuta exactamente
los mismos modelos.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytesseract
from PIL import Image, UnidentifiedImageError

logger = logging.getLogger(__name__)


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


# `_UNSET` distingue "todavía no intenté construir el motor" de "lo intenté y
# no se puede" (`None`). Sin ese tercer estado, cada imagen sin RapidOCR
# reintentaría el import y pagaría el costo de descubrir que no está.
_UNSET: Any = object()
_rapidocr_cache: Any = _UNSET


def _rapidocr_engine() -> Any:
    """El motor de RapidOCR, construido una sola vez, o `None` si no se puede.

    Construirlo cuesta ~0,7s y carga ~32 MB de modelos ONNX: se hace una vez
    por proceso y se reusa. Se construye tarde —en la primera transcripción y
    no al importar— para que arrancar el server no pague ese costo cuando
    nadie va a curar una imagen.

    Cualquier fallo al construirlo cae al respaldo en vez de propagarse, y esa
    amplitud es deliberada: acá "no se pudo construir" significa exactamente
    "este motor no es usable en esta máquina" —falta el paquete, faltan los
    modelos, la arquitectura no lo soporta—, que es la condición de respaldo.
    Un fallo al transcribir UNA imagen es otra cosa y se maneja aparte.
    """
    global _rapidocr_cache
    if _rapidocr_cache is _UNSET:
        try:
            from rapidocr import RapidOCR

            _rapidocr_cache = RapidOCR()
            logger.info("ocr.motor=rapidocr")
        except Exception:
            logger.info("ocr.motor=tesseract (rapidocr no disponible)")
            _rapidocr_cache = None
    return _rapidocr_cache


def _transcribe_rapidocr(path: Path) -> str | None:
    """Transcribe con RapidOCR, o `None` para que decida el respaldo.

    Devuelve `None` en los tres casos que significan "acá no hay respuesta
    de este motor": no está disponible, reventó con esta imagen, o no
    encontró ni un renglón. El último entra a propósito — una imagen donde
    RapidOCR no ve nada todavía puede darle texto a Tesseract, y el respaldo
    solo se paga en esas.
    """
    engine = _rapidocr_engine()
    if engine is None:
        return None

    try:
        resultado = engine(str(path))
    except Exception:
        # A diferencia del constructor, acá el fallo es de UNA imagen: se
        # registra con su ruta porque es una pérdida de captura concreta.
        logger.warning("ocr.rapidocr_fallo archivo=%s", path.name, exc_info=True)
        return None

    lineas = getattr(resultado, "txts", None)
    if not lineas:
        return None
    return "\n".join(lineas)


def _transcribe_tesseract(path: Path, lang: str) -> str:
    """Degradación agraciada ante fallos esperables (binario de Tesseract
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


def transcribe(path: Path, lang: str = "spa") -> str:
    """Transcribe el texto de una imagen: RapidOCR, y si no, Tesseract.

    El archivo inexistente y la imagen corrupta terminan en `""` como
    siempre: RapidOCR no los resuelve, devuelve `None`, y el respaldo aplica
    su degradación de siempre.
    """
    texto = _transcribe_rapidocr(path)
    if texto is not None:
        return texto
    return _transcribe_tesseract(path, lang)
