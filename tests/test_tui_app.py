"""La app del TUI: navega el registro, corre y pide confirmación si escribe."""

import asyncio
from pathlib import Path

from textual.widgets import RichLog, Static, Tree
from textual_image.widget import Image

from saimc.tui.app import GuaniTUI
from saimc.tui.registry import Command, LogSource

_COMMANDS = (
    Command(
        id="leer",
        group="Grupo",
        title="Leer",
        argv=("echo", "salida-leer"),
        about="Solo lee.",
    ),
    Command(
        id="escribir",
        group="Grupo",
        title="Escribir",
        argv=("echo", "salida-escribir"),
        about="Escribe.",
        writes=True,
    ),
)
_LOGS = (LogSource(id="log-x", title="Log X", unit="x.service"),)


def _app(tmp_path: Path) -> GuaniTUI:
    return GuaniTUI(commands=_COMMANDS, logs=_LOGS, cwd=tmp_path)


def _log_text(app: GuaniTUI) -> str:
    log = app.query_one(RichLog)
    return "\n".join(line.text for line in log.lines)


async def _select(app: GuaniTUI, pilot, command_id: str) -> None:
    tree = app.query_one(Tree)
    for node in tree.root.children:
        for leaf in node.children:
            if leaf.data == command_id:
                tree.move_cursor(leaf)
                await pilot.pause()
                return
    raise AssertionError(command_id)


def test_j_and_k_move_the_cursor(tmp_path: Path) -> None:
    async def scenario() -> tuple[str | None, str | None]:
        app = _app(tmp_path)
        async with app.run_test() as pilot:
            tree = app.query_one(Tree)
            await pilot.press("j", "j")
            down = tree.cursor_node.data if tree.cursor_node else None
            await pilot.press("k")
            up = tree.cursor_node.data if tree.cursor_node else None
            return down, up

    down, up = asyncio.run(scenario())
    # Arranca en el encabezado del grupo: j, j cae en el segundo comando; k vuelve al primero.
    assert down == "escribir"
    assert up == "leer"


def test_enter_on_a_command_runs_it(tmp_path: Path) -> None:
    async def scenario() -> str:
        app = _app(tmp_path)
        async with app.run_test() as pilot:
            await _select(app, pilot, "leer")
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
            return _log_text(app)

    assert "salida-leer" in asyncio.run(scenario())


def test_enter_on_a_group_folds_it_instead_of_running(tmp_path: Path) -> None:
    async def scenario() -> tuple[bool, str]:
        app = _app(tmp_path)
        async with app.run_test() as pilot:
            tree = app.query_one(Tree)
            group = tree.root.children[0]
            tree.move_cursor(group)
            await pilot.pause()
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
            return group.is_expanded, _log_text(app)

    expanded, text = asyncio.run(scenario())
    assert not expanded
    assert text == ""


def test_the_selected_command_carries_the_arrow_marker(tmp_path: Path) -> None:
    async def scenario() -> tuple[str, str]:
        app = _app(tmp_path)
        async with app.run_test() as pilot:
            tree = app.query_one(Tree)
            await _select(app, pilot, "leer")
            leaf = tree.cursor_node
            other = next(n for n in tree.root.children[0].children if n is not leaf)
            assert leaf is not None
            style = tree.get_component_rich_style("tree--label")
            return (
                tree.render_label(leaf, style, style).plain,
                tree.render_label(other, style, style).plain,
            )

    selected, other = asyncio.run(scenario())
    assert selected.startswith("▸ ")
    assert not other.startswith("▸")


def test_the_header_shows_the_avatar_as_an_image(tmp_path: Path) -> None:
    async def scenario() -> bool:
        app = _app(tmp_path)
        async with app.run_test(size=(110, 48)):
            return isinstance(app.query_one("#avatar"), Image)

    assert asyncio.run(scenario())


def test_the_menu_ends_with_quit_and_enter_on_it_exits(tmp_path: Path) -> None:
    async def scenario() -> tuple[str, list[bool]]:
        app = _app(tmp_path)
        exits: list[bool] = []
        async with app.run_test() as pilot:
            tree = app.query_one(Tree)
            last = tree.root.children[-1]
            tree.move_cursor(last)
            await pilot.pause()
            app.exit = lambda *args, **kwargs: exits.append(True)  # type: ignore[method-assign]
            await pilot.press("enter")
            await pilot.pause()
            return str(last.label), exits

    label, exits = asyncio.run(scenario())
    assert label.endswith("Salir")
    assert exits == [True]


def test_selecting_a_command_shows_its_exact_line(tmp_path: Path) -> None:
    async def scenario() -> str:
        app = _app(tmp_path)
        async with app.run_test() as pilot:
            await _select(app, pilot, "leer")
            return str(app.query_one("#detail", Static).render())

    assert "echo salida-leer" in asyncio.run(scenario())


def test_read_only_command_runs_without_confirmation(tmp_path: Path) -> None:
    async def scenario() -> str:
        app = _app(tmp_path)
        async with app.run_test() as pilot:
            await _select(app, pilot, "leer")
            await pilot.press("r")
            await app.workers.wait_for_complete()
            await pilot.pause()
            return _log_text(app)

    assert "salida-leer" in asyncio.run(scenario())


def test_writing_command_asks_first_and_declining_runs_nothing(tmp_path: Path) -> None:
    async def scenario() -> tuple[bool, str]:
        app = _app(tmp_path)
        async with app.run_test() as pilot:
            await _select(app, pilot, "escribir")
            await pilot.press("r")
            await pilot.pause()
            asked = app.screen.query("#confirm").first(Static) is not None
            await pilot.press("n")
            await app.workers.wait_for_complete()
            await pilot.pause()
            return asked, _log_text(app)

    asked, text = asyncio.run(scenario())
    assert asked
    assert "salida-escribir" not in text


def test_writing_command_runs_after_confirming(tmp_path: Path) -> None:
    async def scenario() -> str:
        app = _app(tmp_path)
        async with app.run_test() as pilot:
            await _select(app, pilot, "escribir")
            await pilot.press("r")
            await pilot.pause()
            await pilot.press("y")
            await app.workers.wait_for_complete()
            await pilot.pause()
            return _log_text(app)

    assert "salida-escribir" in asyncio.run(scenario())
