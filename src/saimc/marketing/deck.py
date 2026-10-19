"""Láminas de la presentación «Guani para instituciones» como publicación
educativa diaria (`daily.EDU_SLOT`).

La fuente es `docs/presentacion-instituciones/`: `slides/<id>.html` (1920×1080,
estilos en línea) y los PNG que las láminas llaman `/_blob/<id>`. Se muestran
tal cual, con dos ajustes de renderizado que no tocan el contenido:

* Se quita el contador escrito a mano («12 / 16»): suelta en un carrusel no
  tiene sentido.
* Las etiquetas del runtime de Slides (`<x-icon>`, `<x-connector>`) que un
  navegador no sabe pintar se cambian por SVG en línea. Cualquier otra
  etiqueta `x-*` hace fallar el armado: mejor que pintar a medias.

Tres secciones rotan una por día, igual que `daily.candidate_order`
(`day.toordinal() % 3`). Las láminas `hoy` (cifras fijas del 18-10) y `cierre`
(marcador sin rellenar) no se publican. El texto del post es fijo por sección y
sin cifras. Cambiar una lámina en `docs/` cambia lo que se publica mañana; lo
ya aprobado queda congelado en el snapshot de la diapositiva.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

DECK_CARD = "deck-slide"

_DECK_DIR = Path(__file__).resolve().parents[3] / "docs" / "presentacion-instituciones"

#: `/_blob/<id>` → archivo. Deducido por el `alt` y la proporción de cada uso:
#: el avatar (cover, 180×180), el anuncio (pregunta, 460×320) y la página del
#: arroz (respuesta, 1920×695).
_BLOBS: dict[str, str] = {
    "1937bd5faa6c22845a2a3c30b2518dfa": "avatar.png",
    "4f15636e6388c756c6cb022fdc2a5b37": "anuncio.png",
    "f1d41e3892d9a0cd7cdfbffb56d71008": "arroz-habana.png",
}


@dataclass(frozen=True)
class DeckSection:
    slug: str
    #: Ids de lámina (`slides/<id>.html`), en orden.
    slides: tuple[str, ...]
    #: Texto del post (sin hashtags: los pone `with_hashtags`). Fijo, sin cifras.
    summary: str


SECTIONS: tuple[DeckSection, ...] = (
    DeckSection(
        "idea",
        ("cover", "orden", "pregunta", "quienvende", "quees"),
        "La idea de Guani: el mercado cubano parece un relajo, pero tiene su orden, y los "
        "anuncios de quienes venden lo hacen visible. Desliza.\n\nGuarda esto. guani.app",
    ),
    DeckSection(
        "como-funciona",
        ("flujo", "embudo", "vara", "respuesta", "cuentas"),
        "Cómo funciona Guani: de un mensaje de WhatsApp a una cifra que puedes comprobar. "
        "Desliza para ver cada paso.\n\nGuarda esto. guani.app",
    ),
    DeckSection(
        "a-quien-le-habla",
        ("noopina", "publico", "viene", "miralo"),
        "A quién le habla Guani y cómo verlo tú mismo: muestra el precio y no opina sobre él. "
        "Desliza.\n\nMándaselo a quien hace las compras. guani.app",
    ),
)

#: Las láminas que se publican, en el orden de la presentación.
PUBLISHABLE: tuple[str, ...] = tuple(s for sec in SECTIONS for s in sec.slides)


def section_for_day(day: date) -> DeckSection:
    """La sección que toca el `day`: rota una por día, determinista."""
    return SECTIONS[day.toordinal() % len(SECTIONS)]


def section_of(first_slide: str) -> DeckSection | None:
    """La sección que abre con la lámina `first_slide`."""
    return next((s for s in SECTIONS if s.slides[0] == first_slide), None)


# --- Renderizado -------------------------------------------------------------

#: Íconos de línea (24×24) de los nombres que usa la presentación.
_ICONS: dict[str, str] = {
    "Users": (
        '<circle cx="9" cy="7" r="4"/><path d="M3 21v-2a4 4 0 0 1 4-4h4a4 4 0 0 1 4 4v2"/>'
        '<path d="M16 3.13a4 4 0 0 1 0 7.75"/><path d="M21 21v-2a4 4 0 0 0-3-3.85"/>'
    ),
    "Database": (
        '<ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v6a8 3 0 0 0 16 0V6"/>'
        '<path d="M4 12v6a8 3 0 0 0 16 0v-6"/>'
    ),
    "Lock": (
        '<rect x="5" y="11" width="14" height="10" rx="2"/><circle cx="12" cy="16" r="1"/>'
        '<path d="M8 11V7a4 4 0 1 1 8 0v4"/>'
    ),
    "Tool": '<path d="M7 10h3V7L6.5 3.5a6 6 0 0 1 8 8l6 6a2 2 0 0 1-3 3l-6-6a6 6 0 0 1-8-8L7 10"/>',
    "Chart": (
        '<rect x="3" y="12" width="6" height="8" rx="1"/><rect x="9" y="8" width="6" height="12" '
        'rx="1"/><rect x="15" y="4" width="6" height="16" rx="1"/>'
    ),
    "Search": '<circle cx="10" cy="10" r="7"/><path d="M21 21l-6-6"/>',
    "Globe": (
        '<circle cx="12" cy="12" r="9"/><path d="M3.6 9h16.8M3.6 15h16.8"/>'
        '<path d="M11.5 3a17 17 0 0 0 0 18M12.5 3a17 17 0 0 1 0 18"/>'
    ),
    "CheckCircle": '<circle cx="12" cy="12" r="9"/><path d="M9 12l2 2 4-4"/>',
    "Activity": '<path d="M3 12h4l3 8 4-16 3 8h4"/>',
    "Clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/>',
}

_SVG_OPEN = (
    'viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" '
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"'
)

_ICON = re.compile(r'<x-icon name="(\w+)" style="([^"]*)"></x-icon>')
_CONNECTOR = re.compile(r'<x-connector head="end" style="([^"]*)"></x-connector>')
#: Un párrafo que es solo «12 / 16».
_COUNTER = re.compile(r"<p[^>]*>\s*\d+ / \d+\s*</p>\n?")
_BLOB = re.compile(r"/_blob/(\w+)")


def _icon(match: re.Match[str]) -> str:
    nombre = match.group(1)
    if nombre not in _ICONS:
        raise ValueError(f"Ícono sin equivalente en la presentación: {nombre}")
    return f'<svg {_SVG_OPEN} style="{match.group(2)}">{_ICONS[nombre]}</svg>'


def _connector(match: re.Match[str]) -> str:
    # Flecha de izquierda a derecha, de 40 px de ancho como el conector original.
    return (
        '<svg viewBox="0 0 40 24" fill="none" stroke="currentColor" stroke-width="3" '
        'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" '
        f'style="{match.group(1)};height:24px"><path d="M4 12h30M26 4l8 8-8 8"/></svg>'
    )


@lru_cache(maxsize=len(_BLOBS))
def _blob_uri(archivo: str) -> str:
    datos = base64.b64encode((_DECK_DIR / archivo).read_bytes()).decode("ascii")
    return f"data:image/png;base64,{datos}"


def _blob(match: re.Match[str]) -> str:
    archivo = _BLOBS.get(match.group(1))
    if archivo is None:
        raise ValueError(f"Imagen de la presentación sin archivo: {match.group(1)}")
    return _blob_uri(archivo)


def render_slide(slide_id: str) -> str | None:
    """El `<section>` de la lámina listo para pintar en un navegador suelto, o
    `None` si no es una de las que se publican."""
    if slide_id not in PUBLISHABLE:
        return None
    html = (_DECK_DIR / "slides" / f"{slide_id}.html").read_text(encoding="utf-8")
    html = _COUNTER.sub("", html)
    html = _ICON.sub(_icon, html)
    html = _CONNECTOR.sub(_connector, html)
    html = _BLOB.sub(_blob, html)
    restantes = sorted(set(re.findall(r"<(x-[a-z-]+)", html)))
    if restantes:
        raise ValueError(f"Etiquetas de Slides sin resolver en «{slide_id}»: {restantes}")
    return html
