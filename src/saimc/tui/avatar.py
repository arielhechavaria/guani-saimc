"""Avatar de Guani para la terminal: la imagen real, no un dibujo aparte.

La fuente es `apple-touch-icon.png`, que `tools/build_seo_assets.py` genera
desde la misma foto que usa la web (`_guani_avatar.html`); si el avatar cambia
allá, se regenera ese PNG y la terminal lo toma sola. Se recorta en círculo
sobre el fondo de la terminal porque el ícono trae las esquinas oscuras, y no
todos los protocolos de imagen respetan transparencia.
"""

from pathlib import Path

from PIL import Image, ImageDraw

SOURCE = Path(__file__).resolve().parents[1] / "web" / "static" / "seo" / "apple-touch-icon.png"


def load(background: str) -> Image.Image:
    """El avatar en RGB, con todo lo que queda fuera del círculo pintado de `background`."""
    with Image.open(SOURCE) as source:
        avatar = source.convert("RGB")
    mask = Image.new("L", avatar.size, 0)
    ImageDraw.Draw(mask).ellipse((0, 0, avatar.width - 1, avatar.height - 1), fill=255)
    canvas = Image.new("RGB", avatar.size, background)
    canvas.paste(avatar, mask=mask)
    return canvas
