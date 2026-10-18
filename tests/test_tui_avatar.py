"""El avatar del TUI es el mismo busto de `_guani_avatar.html`, en medios bloques."""

from pathlib import Path

from saimc.tui import avatar

_TEMPLATE = Path(avatar.__file__).resolve().parents[1] / "web" / "templates" / "_guani_avatar.html"


def test_colors_are_the_ones_of_the_svg_avatar() -> None:
    svg = _TEMPLATE.read_text(encoding="utf-8").lower()
    for color in avatar.COLORS.values():
        assert color in svg, color


def test_grid_is_square_and_transparent_outside_the_badge() -> None:
    grid = avatar.pixels(24)
    assert len(grid) == 24
    assert all(len(row) == 24 for row in grid)
    assert grid[0][0] is None
    assert grid[23][23] is None


def test_center_is_the_face() -> None:
    grid = avatar.pixels(24)
    assert grid[11][12] == avatar.COLORS["face"]


def test_the_badge_rim_is_light() -> None:
    row = avatar.pixels(24)[12]
    first = next(c for c in row if c is not None)
    assert first in (avatar.COLORS["badge"], avatar.COLORS["feather"])


def test_the_face_has_eyes() -> None:
    grid = avatar.pixels(24)
    eyes = [c for row in grid[9:13] for c in row[6:18] if c == avatar.COLORS["badge"]]
    assert eyes


def test_render_packs_two_pixel_rows_per_line() -> None:
    lines = avatar.render(24).plain.split("\n")
    assert len(lines) == 12
    assert all(len(line) == 24 for line in lines)
