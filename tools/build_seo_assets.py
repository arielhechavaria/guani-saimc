"""Genera los íconos y la imagen de compartir del sitio (slice `seo-basico`).

No es parte del paquete que se despliega: es herramienta de build y corre a
mano cuando cambia el avatar o la tarjeta (mismo criterio que
`tools/build_css_tokens.py`). Los resultados se commitean: el servidor no
necesita Chromium para servirlos.

Qué escribe, en `src/saimc/web/static/seo/`
-------------------------------------------
- `og-guani.png`          1200×630, la tarjeta de marketing «og-compartir»
                          (`og:image` y `twitter:image` de todas las páginas).
- `favicon-32.png`,
  `favicon-48.png`        la foto del avatar (`static/brand/guani-avatar.webp`)
                          a 32 y 48 px, con alfa fuera del círculo.
- `apple-touch-icon.png`  180×180, el ícono de iOS (iOS pinta de negro el alfa,
                          así que va sobre un fondo oscuro opaco); también el
                          `logo` del JSON-LD de `Organization` y la fuente del
                          avatar de la terminal (`saimc.tui.avatar`).

Ya no hay `favicon.svg`: el avatar es una foto (docs/decisions.md, 2026-10-19).

Uso
---
    python tools/build_seo_assets.py

Los íconos salen de PIL; solo la imagen og necesita el extra `marketing`
(Playwright + Chromium, ver `pyproject.toml`). Sin red.
Es idempotente en el contenido, aunque el PNG puede variar unos bytes.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import jinja2
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from saimc.marketing.fonts import avatar_data_uri, font_face_css  # noqa: E402
from saimc.marketing.render import render_png  # noqa: E402

TEMPLATES = ROOT / "src" / "saimc" / "web" / "templates"
OUT = ROOT / "src" / "saimc" / "web" / "static" / "seo"

AVATAR = ROOT / "src" / "saimc" / "web" / "static" / "brand" / "guani-avatar.webp"

#: Fondo opaco del ícono de iOS (el borde de la foto es oscuro).
APPLE_BACKGROUND = (11, 11, 16)


def _environment() -> jinja2.Environment:
    return jinja2.Environment(loader=jinja2.FileSystemLoader(TEMPLATES), autoescape=True)


def paleta(png: bytes) -> bytes:
    """Reduce un PNG a 256 colores: pesa 3 veces menos y en un ícono o una
    tarjeta ilustrada no se nota (conexión lenta del lector, ver CLAUDE.md)."""
    with Image.open(io.BytesIO(png)) as src:
        q = src.convert("RGB").quantize(256, dither=Image.Dither.FLOYDSTEINBERG)
    buf = io.BytesIO()
    q.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def icon_png(size: int, *, background: tuple[int, int, int] | None = None) -> bytes:
    """La foto a `size` px; con `background` va opaca sobre ese color."""
    with Image.open(AVATAR) as src:
        foto = src.convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
    if background is not None:
        lienzo = Image.new("RGBA", (size, size), (*background, 255))
        lienzo.alpha_composite(foto)
        foto = lienzo.convert("RGB")
    buf = io.BytesIO()
    foto.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def og_png(env: jinja2.Environment) -> bytes:
    html = env.get_template("marketing_og_compartir.html").render(
        site_url="", font_css=font_face_css(), avatar_uri=avatar_data_uri()
    )
    return render_png(html, width=1200, height=630)


def main() -> int:
    env = _environment()
    OUT.mkdir(parents=True, exist_ok=True)
    escritos = {
        "favicon-32.png": icon_png(32),
        "favicon-48.png": icon_png(48),
        "apple-touch-icon.png": paleta(icon_png(180, background=APPLE_BACKGROUND)),
        "og-guani.png": paleta(og_png(env)),
    }
    for nombre, contenido in escritos.items():
        (OUT / nombre).write_bytes(contenido)
        print(f"{nombre}: {len(contenido):,} B")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
