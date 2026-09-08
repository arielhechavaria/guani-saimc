"""Amplía el manifiesto de emoji con lo que aparece en los anuncios reales.

`build_emoji_font.py` subsetea Noto Color Emoji a una lista fija. Esa lista tiene
dos partes: el vocabulario de la interfaz (`QUEUE_EMOJI`, en el router) y los
emoji que traen los anuncios del mercado — 🤑, 🌟, 🚨, decenas más — que el
operador ve en el texto al curar. Esta herramienta junta la segunda parte.

A diferencia de las otras herramientas de `tools/`, ésta **necesita la base de
datos** (lee `raw_ingestions`). Por eso está separada del build de la fuente:
`build_emoji_font.py` sigue corriendo sólo con el repo, contra el manifiesto que
esta herramienta dejó versionado.

El manifiesto se AMPLÍA, nunca se recorta: un emoji que salió de un anuncio ya
descartado no desaparece de la fuente. Quitar glifos es un acto deliberado, a
mano.

Uso
---
    python tools/scan_ad_emoji.py

Lee la conexión de `SAIMC_DATABASE_URL` (el `.env` del proyecto). Después de
correr esto, correr `python tools/build_emoji_font.py` para regenerar el `.woff2`
con los glifos nuevos.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from sqlalchemy import select  # noqa: E402

from saimc.db import session_scope  # noqa: E402
from saimc.models import RawIngestion  # noqa: E402
from saimc.web.routers.curation import QUEUE_EMOJI  # noqa: E402

MANIFEST_PATH = (
    REPO_ROOT / "src" / "saimc" / "web" / "static" / "fonts" / "noto-color-emoji.chars.txt"
)

# Pictogramas, símbolos, dingbats, banderas y las flechas decorativas que el OCR
# arrastra ("➔"). No incluye el `→` de los enlaces de la interfaz (U+2192), que
# es tipografía y la cubre Roboto.
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF"  # pictogramas y suplementos
    "\U00002600-\U000027BF"  # símbolos misceláneos + dingbats
    "\U00002B00-\U00002BFF"  # flechas y símbolos varios
    "\U0001F1E6-\U0001F1FF"  # indicadores regionales (banderas)
    "\U0000FE0F"  # selector de variación emoji
    "\U0000200D"  # zero-width joiner (secuencias 👱‍♀️)
    "\U00002190-\U000021BF]"  # flechas — sin el tramo del `→` de enlaces
)


def main() -> None:
    with session_scope() as sesion:
        textos = [
            fila.original_text or ""
            for fila in sesion.scalars(select(RawIngestion))
        ]

    del_corpus = {glifo for texto in textos for glifo in _EMOJI_RE.findall(texto)}
    previos = set(MANIFEST_PATH.read_text(encoding="utf-8").strip()) if MANIFEST_PATH.exists() else set()

    union = previos | del_corpus | set("".join(QUEUE_EMOJI))
    nuevos = union - previos

    MANIFEST_PATH.write_text("".join(sorted(union)) + "\n", encoding="utf-8")
    print(f"anuncios leídos       : {len(textos)}")
    print(f"emoji en el corpus    : {len(del_corpus)}")
    print(f"manifiesto: {len(previos)} → {len(union)} glifos (+{len(nuevos)})")
    if nuevos:
        print(f"nuevos: {''.join(sorted(nuevos))}")
    print("\nahora: python tools/build_emoji_font.py")


if __name__ == "__main__":
    main()
