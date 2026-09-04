"""Proyecta los tokens Material 3 desde su fuente única a las dos hojas servidas.

No es parte del paquete que se despliega: es herramienta de build, y corre a
mano cada vez que se toca un token (mismo criterio que `tools/build_icon_font.py`
y `tools/build_cuba_map.py`).

Por qué existe
--------------
`static/saimc-material.css` y `static/saimc-public.css` declaraban cada una su
propio `:root`. La hoja pública tenía 13 tokens **copiados a mano** desde la del
operador, sin nada que detectara que se habían desincronizado — y se habían: las
plantillas citaban `var(--m3-primary, #006a60)` cuando el valor real es
`#00696e`, y `var(--m3-outline-variant, #cac4d0)` cuando el real es `#c4cfcf`
(ese `#cac4d0` es del esquema violáceo base de Material, ni siquiera de este
proyecto).

La alternativa obvia —un `tokens.css` aparte, enlazado por los dos shells— suma
un round-trip a cada vista, y CLAUDE.md fija que la conexión del operador es
lenta e intermitente. Así que el bloque se copia en tiempo de build y las hojas
siguen costando una sola descarga.

Qué hace
--------
1. Lee el bloque `:root { … }` de `src/saimc/web/design_tokens.css`.
2. Lo escribe dentro de cada hoja de `static/`, reemplazando lo que haya entre
   los centinelas `BEGIN/END tokens generados`.
3. Informa qué archivos cambió. Es idempotente: correrlo dos veces seguidas no
   toca nada la segunda vez.

`tests/test_static_assets.py` compara las hojas contra la fuente: editar un
token y no correr esto pone la suite en rojo con el nombre del token, en vez de
dejar la hoja pública con un color viejo que nadie mira.

Uso
---
    python tools/build_css_tokens.py

Sin argumentos y sin red. Devuelve 0 siempre que pueda escribir; devuelve 1 si
una hoja no tiene los centinelas.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = REPO_ROOT / "src" / "saimc" / "web"
SOURCE_PATH = WEB_DIR / "design_tokens.css"
TARGETS = [
    WEB_DIR / "static" / "saimc-material.css",
    WEB_DIR / "static" / "saimc-public.css",
]

BEGIN = "/* >>> BEGIN tokens generados — los edita tools/build_css_tokens.py, no vos */"
END = "/* <<< END tokens generados */"

# El bloque `:root` de la fuente, con los comentarios que lleve adentro. El
# `re.DOTALL` es a propósito: los comentarios explicativos de `--m3-tertiary` y
# `--m3-surface-variant` viven dentro del bloque y tienen que viajar con él.
_ROOT_RE = re.compile(r"^:root \{.*?^\}$", re.DOTALL | re.MULTILINE)


def extract_root_block(source: str) -> str:
    """Devuelve el `:root { … }` de la fuente, sin comentarios.

    El porqué de cada token vive en `design_tokens.css` y se lee ahí. Mandarlo
    también al navegador costaba ~1,1 KB gzipeados **en la hoja pública**, que
    es la que ve cualquiera con un teléfono y datos móviles: pesaba más que
    duplicar la hoja entera. Los comentarios se quedan en la fuente.
    """
    match = _ROOT_RE.search(source)
    if match is None:
        raise SystemExit(f"{SOURCE_PATH} no tiene un bloque `:root {{ … }}`")

    bloque = re.sub(r"[ \t]*/\*.*?\*/\n?", "", match.group(0), flags=re.DOTALL)
    # Los comentarios se llevan su línea, pero dejan huecos dobles donde
    # separaban dos grupos. Un solo renglón en blanco alcanza para agrupar.
    return re.sub(r"\n{3,}", "\n\n", bloque)


def render(target_text: str, block: str) -> str:
    """Reemplaza lo que haya entre los centinelas por `block`.

    Falla fuerte si los centinelas no están: preferimos un error ruidoso a
    escribir el bloque en un lugar arbitrario de una hoja de 582 líneas.
    """
    pattern = re.compile(
        re.escape(BEGIN) + r".*?" + re.escape(END),
        re.DOTALL,
    )
    if not pattern.search(target_text):
        raise LookupError("faltan los centinelas")
    return pattern.sub(f"{BEGIN}\n{block}\n{END}", target_text, count=1)


def main() -> int:
    block = extract_root_block(SOURCE_PATH.read_text(encoding="utf-8"))

    fallo = False
    for target in TARGETS:
        actual = target.read_text(encoding="utf-8")
        try:
            nuevo = render(actual, block)
        except LookupError:
            print(
                f"✗ {target.relative_to(REPO_ROOT)}: faltan los centinelas\n"
                f"    agregá estas dos líneas donde va el bloque de tokens:\n"
                f"      {BEGIN}\n      {END}",
                file=sys.stderr,
            )
            fallo = True
            continue

        if nuevo == actual:
            print(f"· {target.relative_to(REPO_ROOT)}: ya estaba al día")
            continue

        target.write_text(nuevo, encoding="utf-8")
        print(f"✓ {target.relative_to(REPO_ROOT)}: tokens actualizados")

    return 1 if fallo else 0


if __name__ == "__main__":
    raise SystemExit(main())
