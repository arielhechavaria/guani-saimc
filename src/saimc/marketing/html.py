"""El HTML de una tarjeta, fuera de la app web.

Lo usan el editor de publicaciones, la aprobación (que rasteriza desde el
snapshot) y cualquier script: un entorno Jinja propio con el mismo cargador y
el mismo autoescape que Starlette, así la plantilla sale igual que en
`/marketing/preview` sin levantar FastAPI (`docs/decisions.md`, 2026-10-18,
tarjetas diarias).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import jinja2

import saimc.web
from saimc.marketing.context import SITE_URL_PLACEHOLDER, CardType
from saimc.marketing.fonts import font_face_css
from saimc.marketing.options import CardOptions, parse_options

_TEMPLATES = Path(saimc.web.__file__).parent / "templates"


@lru_cache(maxsize=1)
def _environment() -> jinja2.Environment:
    return jinja2.Environment(loader=jinja2.FileSystemLoader(_TEMPLATES), autoescape=True)


def card_html(
    kind: CardType,
    context: dict[str, Any],
    options: CardOptions | None = None,
    slide_counter: str | None = None,
) -> str:
    """La tarjeta `kind` pintada con `context`. Las opciones solo deciden qué
    bloques y textos se ven; ninguna llega a una cifra. `slide_counter`
    («2 / 5») es el lugar de la diapositiva en su carrusel: sin él, las
    plantillas no muestran contador."""
    opciones = options or parse_options(kind, {})
    plantilla = _environment().get_template(kind.template)
    # Las fuentes viajan dentro del HTML: Playwright rasteriza sin acceso a /static.
    return plantilla.render(
        site_url=SITE_URL_PLACEHOLDER,
        font_css=font_face_css(),
        show=opciones.show,
        subtitle=opciones.subtitle,
        cta=opciones.cta,
        free=opciones.free,
        slide_counter=slide_counter,
        **context,
    )
