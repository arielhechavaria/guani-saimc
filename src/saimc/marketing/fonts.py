"""CSS de las fuentes reales del sitio para las tarjetas de marketing.

Las tarjetas se rasterizan con Playwright desde HTML suelto
(`page.set_content`), sin origen: no pueden pedir `/static/fonts/...`. En vez
de bajar una tipografía de un CDN (DESIGN.md: los activos salen del repo), se
inyecta el mismo `inter.woff2` que sirve el sitio, como data URI.

«Guani Display» (`--ph-font-heading`) es ese mismo archivo declarado en peso
300; acá no se declara aparte: las plantillas piden `Inter` con
`font-weight: 300` y sale el mismo Light, sin duplicar 48 kB en base64.
"""

from __future__ import annotations

import base64
from functools import lru_cache
from pathlib import Path

import saimc.web

_INTER = Path(saimc.web.__file__).parent / "static" / "fonts" / "inter.woff2"


@lru_cache(maxsize=1)
def font_face_css() -> str:
    """`@font-face` de Inter (eje variable 100–900) con el archivo embebido."""
    datos = base64.b64encode(_INTER.read_bytes()).decode("ascii")
    return (
        '@font-face{font-family:"Inter";font-style:normal;font-weight:100 900;'
        f'src:url("data:font/woff2;base64,{datos}") format("woff2")}}'
    )


_AVATAR = Path(saimc.web.__file__).parent / "static" / "brand" / "guani-avatar.webp"


@lru_cache(maxsize=1)
def avatar_data_uri() -> str:
    """El avatar a color de las tarjetas (círculo con alpha) como data URI, por
    la misma razón que la tipografía: Playwright rasteriza sin acceso a /static."""
    datos = base64.b64encode(_AVATAR.read_bytes()).decode("ascii")
    return f"data:image/webp;base64,{datos}"
