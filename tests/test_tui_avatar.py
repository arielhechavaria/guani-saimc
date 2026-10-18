"""El avatar del TUI es la imagen real de Guani, recortada en círculo sobre el fondo."""

from saimc.tui import avatar, theme


def test_the_source_is_the_icon_built_from_the_svg_avatar() -> None:
    assert avatar.SOURCE.name == "apple-touch-icon.png"
    assert avatar.SOURCE.is_file()


def test_corners_take_the_terminal_background() -> None:
    background = theme.load()["m3-bg"]
    image = avatar.load(background)
    expected = tuple(int(background[i : i + 2], 16) for i in (1, 3, 5))
    width, height = image.size
    for corner in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)):
        assert image.getpixel(corner) == expected


def test_the_center_keeps_the_avatar() -> None:
    background = theme.load()["m3-bg"]
    image = avatar.load(background)
    width, height = image.size
    expected = tuple(int(background[i : i + 2], 16) for i in (1, 3, 5))
    assert image.mode == "RGB"
    assert image.getpixel((width // 2, height // 2)) != expected
