"""La app del TUI: navega el registro, corre y pide confirmación si escribe."""

import asyncio
from pathlib import Path

from textual.widgets import RichLog, Static, Tree

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
