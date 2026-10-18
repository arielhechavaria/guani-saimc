"""Genera los íconos y la imagen de compartir del sitio (slice `seo-basico`).

No es parte del paquete que se despliega: es herramienta de build y corre a
mano cuando cambia el avatar o la tarjeta (mismo criterio que
`tools/build_css_tokens.py`). Los resultados se commitean: el servidor no
necesita Chromium para servirlos.

Qué escribe, en `src/saimc/web/static/seo/`
-------------------------------------------
- `og-guani.png`          1200×630, la tarjeta de marketing «og-compartir»
                          (`og:image` y `twitter:image` de todas las páginas).
- `favicon.svg`           el avatar de Guani (`_guani_avatar.html`) como SVG.
- `favicon-32.png`        el mismo avatar a 32×32, para quien no entiende SVG.
- `apple-touch-icon.png`  180×180, el ícono de iOS; también el `logo` del
                          JSON-LD de `Organization`.

Uso
---
    python tools/build_seo_assets.py

Necesita el extra `marketing` (Playwright + Chromium, ver `pyproject.toml`). Sin
red. Es idempotente en el contenido, aunque el PNG puede variar unos bytes.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import jinja2

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from saimc.marketing.fonts import font_face_css  # noqa: E402
from saimc.marketing.render import render_png  # noqa: E402

TEMPLATES = ROOT / "src" / "saimc" / "web" / "templates"
OUT = ROOT / "src" / "saimc" / "web" / "static" / "seo"

#: Fondo del lienzo de los PNG chicos: el del avatar (`#ffffff`), para que la
#: esquina no quede transparente en un fondo oscuro de pestaña.
ICON_BACKGROUND = "#ffffff"


def _environment() -> jinja2.Environment:
    return jinja2.Environment(loader=jinja2.FileSystemLoader(TEMPLATES), autoescape=True)


def favicon_svg(env: jinja2.Environment) -> str:
    """El avatar como SVG suelto: con `xmlns` y sin las marcas de la página."""
    raw = env.get_template("_guani_avatar.html").render(avatar_clip_id="guani-favicon-clip")
    svg = raw.strip()
    svg = svg.replace(' class="ph-brand-avatar"', "")
    svg = re.sub(r'\s+(role|aria-label)="[^"]*"', "", svg)
    return svg.replace("<svg ", '<svg xmlns="http://www.w3.org/2000/svg" ', 1) + "\n"


def icon_png(svg: str, size: int) -> bytes:
    html = (
        f'<!doctype html><body style="margin:0;background:{ICON_BACKGROUND}">'
        f'<div style="width:{size}px;height:{size}px">'
        f"{svg.replace('<svg ', f'<svg width="{size}" height="{size}" ', 1)}"
        "</div></body>"
    )
    return render_png(html, width=size, height=size)


def og_png(env: jinja2.Environment) -> bytes:
    html = env.get_template("marketing_og_compartir.html").render(site_url="", font_css=font_face_css())
    return render_png(html, width=1200, height=630)


def main() -> int:
    env = _environment()
    OUT.mkdir(parents=True, exist_ok=True)
    svg = favicon_svg(env)
    escritos = {
        "favicon.svg": svg.encode("utf-8"),
        "favicon-32.png": icon_png(svg, 32),
        "apple-touch-icon.png": icon_png(svg, 180),
        "og-guani.png": og_png(env),
    }
    for nombre, contenido in escritos.items():
        (OUT / nombre).write_bytes(contenido)
        print(f"{nombre}: {len(contenido):,} B")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
