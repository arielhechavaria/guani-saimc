"""Regenera el subconjunto de Material Symbols Rounded que sirve el operador.

No es parte del paquete que se despliega: es herramienta de build, y corre a
mano cada vez que una plantilla suma o saca un icono. Existe para que el asset
sea **reproducible** y para que sumar un icono no dependa de acordarse de
regenerar la fuente (mismo criterio que `tools/build_cuba_map.py`).

Qué hace
--------
1. Lee todas las plantillas de `src/saimc/web/templates/` y junta cada nombre
   que aparece como `<span class="m3-icon">nombre</span>`.
2. Le pide a Google Fonts el subconjunto con exactamente esos iconos, vía el
   parámetro `icon_names`. El subsetting lo hace el servidor de Google: por eso
   no hace falta `fontTools` ni `brotli` en el venv (decisions.md, 2026-08-20).
3. Escribe el `.woff2` en `static/fonts/` y, al lado, un manifiesto de texto con
   la lista de iconos. `tests/test_static_assets.py` compara ese manifiesto
   contra lo que usan las plantillas: si alguien agrega un icono y no corre esto,
   la suite se pone roja en vez de romperse en silencio en pantalla.

Uso
---
    python tools/build_icon_font.py

Sin argumentos. Necesita red (una sola llamada a fonts.googleapis.com).
"""

from __future__ import annotations

import re
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = REPO_ROOT / "src" / "saimc" / "web" / "templates"
FONT_PATH = REPO_ROOT / "src" / "saimc" / "web" / "static" / "fonts" / "material-symbols-rounded.woff2"
MANIFEST_PATH = FONT_PATH.with_suffix(".icons.txt")

# `<span class="m3-icon ...">nombre</span>` — el `class` puede traer más de una
# clase (`m3-icon m3-icon-sm m3-dim`), así que se acepta cualquier cosa hasta el
# `">`. Todas comparten `font-family` y por lo tanto el mismo subconjunto.
_ICON_RE = re.compile(r'class="m3-icon[^"]*">([a-z0-9_]+)<')

# Axis fijos: opsz 20..48 cubre los tres tamaños del CSS (16/20/24 px), y
# wght/FILL/GRAD en su valor por defecto. Mismo formato que la URL con la que se
# vendorizó la fuente en 2026-08-20.
_CSS_URL = (
    "https://fonts.googleapis.com/css2"
    "?family=Material+Symbols+Rounded:opsz,wght,FILL,GRAD@20..48,400,0,0"
    "&icon_names={names}&display=block"
)

# Sin User-Agent de navegador, Google devuelve un CSS con la fuente completa en
# vez del subconjunto.
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def icon_names() -> list[str]:
    nombres: set[str] = set()
    for tpl in sorted(TEMPLATES_DIR.glob("*.html")):
        nombres.update(_ICON_RE.findall(tpl.read_text(encoding="utf-8")))
    # Los íconos de producto de los chips, y los de las ventanas del menú del
    # compás, salen de Python (`PRODUCT_ICONS`/`VIEW_ICONS`), no de una
    # plantilla: el regex no los ve.
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from saimc.web.product_nav import PRODUCT_ICONS, VIEW_ICONS

    nombres.update(PRODUCT_ICONS.values())
    nombres.update(VIEW_ICONS.values())
    if not nombres:
        sys.exit("No se encontró ningún icono en las plantillas — ¿cambió el marcado?")
    return sorted(nombres)


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 — host fijo
        return resp.read()


def main() -> None:
    nombres = icon_names()
    print(f"{len(nombres)} iconos en las plantillas: {', '.join(nombres)}")

    css = _fetch(_CSS_URL.format(names=",".join(nombres))).decode("utf-8")
    match = re.search(r"src:\s*url\((https://fonts\.gstatic\.com/[^)]+)\)", css)
    if match is None:
        sys.exit("La respuesta de Google no trae una URL de fuente — revisá el CSS a mano.")

    woff2 = _fetch(match.group(1))
    if woff2[:4] != b"wOF2":
        sys.exit("Lo que devolvió Google no es un WOFF2.")

    FONT_PATH.write_bytes(woff2)
    MANIFEST_PATH.write_text("\n".join(nombres) + "\n", encoding="utf-8")
    print(f"escrito {FONT_PATH.relative_to(REPO_ROOT)} ({len(woff2):,} B)")
    print(f"escrito {MANIFEST_PATH.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
