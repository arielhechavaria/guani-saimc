"""Regenera el subconjunto de Noto Color Emoji que sirve la vista de cola.

Hermana de `tools/build_icon_font.py`: misma idea (asset reproducible, subsetting
del lado de Google, manifiesto con tripwire), pero para los emoji de
`/curation/cola` en vez de las ligaduras de Material Symbols.

Por qué existe
--------------
`saimc-material.css` autohospeda Roboto, Roboto Mono, Material Symbols y Alice.
Los emoji eran lo único que caía a lo que tuviera el sistema — y en el Linux del
operador `fc-list` no lista ninguna fuente de emoji, así que sólo renderizaban
por el navegador de Windows. Noto Color Emoji es la fuente de emoji de la propia
familia Material/Android. El subconjunto cubre las marcas de la interfaz más los
~230 emoji que traen los anuncios reales, así que pesa ~520 KB: es contenido de
usuario, no un puñado de íconos (decisión del operador, 2026-09-08).

Qué hace
--------
1. Junta los glifos a subsetear: `QUEUE_EMOJI` del router (el vocabulario de la
   interfaz) más lo que haya en el manifiesto `noto-color-emoji.chars.txt`, que
   `tools/scan_ad_emoji.py` mantiene con los emoji que traen los anuncios
   reales. El manifiesto está versionado, así que esta herramienta no necesita
   la base de datos.
2. Le pide a Google Fonts el subconjunto con exactamente esos glifos, vía el
   parámetro `text`. El subsetting lo hace el servidor de Google: no hace falta
   `fontTools` ni `brotli` en el venv (mismo motivo que `build_icon_font.py`).
3. Escribe el `.woff2` en `static/fonts/` y reescribe el manifiesto con la unión
   ordenada. `tests/test_static_assets.py` compara ese manifiesto contra
   `QUEUE_EMOJI` y contra las plantillas: si alguien suma un emoji de interfaz y
   no corre esto, la suite se pone roja.

Uso
---
    python tools/build_emoji_font.py

Sin argumentos. Necesita red (una sola llamada a fonts.googleapis.com). El
formato que devuelve Google depende del User-Agent, así que el archivo queda
pineado a lo que sirve Chrome — mismo caveat que `build_icon_font.py`.
"""

from __future__ import annotations

import sys
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from saimc.web.routers.curation import QUEUE_EMOJI  # noqa: E402

FONT_PATH = REPO_ROOT / "src" / "saimc" / "web" / "static" / "fonts" / "noto-color-emoji.woff2"
MANIFEST_PATH = FONT_PATH.with_suffix(".chars.txt")

_CSS_URL = "https://fonts.googleapis.com/css2?family=Noto+Color+Emoji&text={text}"

# Sin User-Agent de navegador, Google devuelve la fuente completa (~10 MB) en vez
# del subconjunto.
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 — host fijo
        return resp.read()


def main() -> None:
    # El manifiesto y el subsetting trabajan por code point, no por glifo: un
    # emoji con selector de variación (🖼️ = U+1F5BC U+FE0F) son dos entradas, y
    # la fuente se subsetea por su cmap igual.
    previos = (
        set(MANIFEST_PATH.read_text(encoding="utf-8").strip())
        if MANIFEST_PATH.exists()
        else set()
    )
    del_ui = set("".join(QUEUE_EMOJI))
    codepoints = sorted(previos | del_ui)
    print(
        f"{len(codepoints)} code points a subsetear "
        f"({len(del_ui)} de interfaz, {len(codepoints) - len(del_ui & set(codepoints))} solo de anuncios)"
    )

    texto = urllib.parse.quote("".join(codepoints))
    css = _fetch(_CSS_URL.format(text=texto)).decode("utf-8")

    inicio = css.find("url(")
    fin = css.find(")", inicio)
    if inicio == -1 or fin == -1 or "fonts.gstatic.com" not in css[inicio:fin]:
        sys.exit("La respuesta de Google no trae una URL de fuente — revisá el CSS a mano.")
    font_url = css[inicio + 4 : fin].strip("'\"")

    woff2 = _fetch(font_url)
    if woff2[:4] != b"wOF2":
        sys.exit("Lo que devolvió Google no es un WOFF2.")

    FONT_PATH.write_bytes(woff2)
    MANIFEST_PATH.write_text("".join(codepoints) + "\n", encoding="utf-8")
    print(f"escrito {FONT_PATH.relative_to(REPO_ROOT)} ({len(woff2):,} B)")
    print(f"escrito {MANIFEST_PATH.relative_to(REPO_ROOT)} ({len(codepoints)} code points)")


if __name__ == "__main__":
    main()
