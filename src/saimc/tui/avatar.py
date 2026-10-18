"""Avatar de Guani para la terminal: el busto de `_guani_avatar.html`, en medios bloques.

Se rasteriza la misma geometría del SVG (viewBox 80×80) muestreando el centro
de cada píxel, y cada línea de texto lleva dos filas de píxeles con `▀`
(color de arriba como tinta, el de abajo como fondo). Los colores son los hex
fijos del SVG; si el SVG cambia, este archivo se cambia con él
(`tests/test_tui_avatar.py` lo vigila).
"""

from rich.style import Style
from rich.text import Text

COLORS = {
    "hair": "#191c1c",
    "face": "#6f7979",
    "feather": "#c4cfcf",
    "clothes": "#8a9494",
    "badge": "#ffffff",
}

# El grupo interior del SVG: translate(40,40) scale(.8) translate(-40,-40).
_SCALE = 0.8


def _in_ellipse(x: float, y: float, cx: float, cy: float, rx: float, ry: float) -> bool:
    return ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 <= 1


def _in_triangle(
    x: float, y: float, a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]
) -> bool:
    def side(p: tuple[float, float], q: tuple[float, float]) -> float:
        return (x - q[0]) * (p[1] - q[1]) - (p[0] - q[0]) * (y - q[1])

    d1, d2, d3 = side(a, b), side(b, c), side(c, a)
    has_neg = d1 < 0 or d2 < 0 or d3 < 0
    has_pos = d1 > 0 or d2 > 0 or d3 > 0
    return not (has_neg and has_pos)


def _near_mouth(x: float, y: float) -> bool:
    # Curva cuadrática M33,48 Q40,52 47,48 con trazo de 2,4: medio trazo de distancia.
    for step in range(31):
        t = step / 30
        px = (1 - t) ** 2 * 33 + 2 * (1 - t) * t * 40 + t**2 * 47
        py = (1 - t) ** 2 * 48 + 2 * (1 - t) * t * 52 + t**2 * 48
        if (x - px) ** 2 + (y - py) ** 2 <= 1.2**2:
            return True
    return False


def _inner(x: float, y: float) -> str:
    """Color del grupo interior, en sus coordenadas, pintando en el orden del SVG."""
    color = COLORS["badge"]
    if _in_ellipse(x, y, 40, 30, 34, 34):
        color = COLORS["hair"]
    if _in_ellipse(x, y, 40, 34, 27, 27):
        color = COLORS["face"]
    if 13 <= x <= 67 and 20 <= y <= 28:
        color = COLORS["hair"]
    if _in_triangle(x, y, (52, 22), (57, 3), (62, 22)):
        color = COLORS["feather"]
    if _in_ellipse(x, y, 33, 36, 3, 2.3) or _in_ellipse(x, y, 47, 36, 3, 2.3):
        color = COLORS["badge"]
    if _near_mouth(x, y):
        color = COLORS["badge"]
    if _in_ellipse(x, y, 40, 72, 40, 20):
        color = COLORS["clothes"]
    return color


def _sample(x: float, y: float) -> str | None:
    distance = ((x - 40) ** 2 + (y - 40) ** 2) ** 0.5
    if distance > 40:
        return None
    if distance >= 38:  # contorno de 2 sobre r=39
        return COLORS["feather"]
    ix, iy = (x - 40) / _SCALE + 40, (y - 40) / _SCALE + 40
    if (ix - 40) ** 2 + (iy - 40) ** 2 > 37**2:  # clipPath r=37 del grupo interior
        return COLORS["badge"]
    return _inner(ix, iy)


def pixels(size: int) -> list[list[str | None]]:
    """Grilla `size`×`size` de colores; `None` es transparente."""
    unit = 80 / size
    return [[_sample((col + 0.5) * unit, (row + 0.5) * unit) for col in range(size)] for row in range(size)]


def render(size: int) -> Text:
    """El avatar como texto: `size` columnas y `size // 2` líneas."""
    grid = pixels(size)
    text = Text()
    for row in range(0, size, 2):
        if row:
            text.append("\n")
        for top, bottom in zip(grid[row], grid[row + 1], strict=True):
            if top is None and bottom is None:
                text.append(" ")
            elif top is None:
                text.append("▄", Style(color=bottom))
            else:
                text.append("▀", Style(color=top, bgcolor=bottom))
    return text
