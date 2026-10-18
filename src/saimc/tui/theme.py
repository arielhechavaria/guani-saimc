"""Paleta del TUI: los tokens `--m3-` oscuros de `design_tokens.css`.

Es la herramienta del operador, así que usa la capa `--m3-` (`docs/DESIGN.md`,
«Dos capas, dos marcas»), y la versión oscura porque una terminal por SSH es
oscura. Se lee el CSS al arrancar en vez de copiar los hex: así un cambio de
token llega solo y no hay valores sueltos que se desincronicen. Si falta un
token, falla: un color inventado de respaldo es justo lo que DESIGN.md prohíbe.
"""

import re
from pathlib import Path

TOKENS_CSS = Path(__file__).resolve().parents[1] / "web" / "design_tokens.css"

REQUIRED = (
    "m3-bg",
    "m3-surface",
    "m3-on-surface",
    "m3-on-surface-variant",
    "m3-outline",
    "m3-outline-variant",
    "m3-primary",
    "m3-primary-container",
    "m3-tertiary",
    "m3-warn",
    "m3-error",
    "m3-success",
)

_DARK_BLOCK = re.compile(r':root\[data-theme="dark"\]\s*\{([^}]*)\}')
_TOKEN = re.compile(r"--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})\s*;")


def load(path: Path = TOKENS_CSS) -> dict[str, str]:
    """Devuelve `{token: hex}` para cada token de `REQUIRED`, del bloque oscuro."""
    block = _DARK_BLOCK.search(path.read_text(encoding="utf-8"))
    found = dict(_TOKEN.findall(block.group(1))) if block else {}
    return {token: found[token].lower() for token in REQUIRED}
