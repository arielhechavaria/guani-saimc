"""Los colores del TUI salen de `design_tokens.css`, no de valores sueltos."""

import re
from pathlib import Path

import pytest

from saimc.tui import theme

_TOKENS = Path(theme.__file__).resolve().parents[1] / "web" / "design_tokens.css"


def _dark_value(name: str) -> str:
    css = _TOKENS.read_text(encoding="utf-8")
    block = css[css.index(':root[data-theme="dark"]') :]
    block = block[: block.index("}")]
    match = re.search(rf"--{name}:\s*(#[0-9a-fA-F]{{6}})\s*;", block)
    assert match, name
    return match.group(1).lower()


def test_every_tui_color_is_the_dark_token_of_the_same_name() -> None:
    palette = theme.load()
    assert palette
    for token, value in palette.items():
        assert value == _dark_value(token), token


def test_the_palette_covers_what_the_app_uses() -> None:
    palette = theme.load()
    for token in theme.REQUIRED:
        assert token in palette


def test_a_missing_token_fails_loudly(tmp_path: Path) -> None:
    css = tmp_path / "tokens.css"
    css.write_text(':root[data-theme="dark"] {\n  --m3-bg: #000000;\n}\n', encoding="utf-8")
    with pytest.raises(KeyError):
        theme.load(css)
