"""La app del TUI: navega el registro, corre y pide confirmación si escribe."""

import asyncio
import sys
from pathlib import Path

from textual.widgets import RichLog, Static, Tree
from textual_image.widget import Image

from saimc.tui.app import GuaniTUI
from saimc.tui.registry import Command, LogSource
from saimc.tui.status import Snapshot, Stage

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


# --- corridas independientes ------------------------------------------------------

_SLOW = "import time; time.sleep(1); print('fin-lento')"
_SLOW_COMMANDS = (
    Command(id="lento", group="Grupo", title="Lento", argv=(sys.executable, "-c", _SLOW), about="Tarda."),
    Command(
        id="lento-w",
        group="Grupo",
        title="Lento escribiendo",
        argv=(sys.executable, "-c", _SLOW),
        about="Tarda y escribe.",
        writes=True,
    ),
    Command(
        id="otro-w",
        group="Grupo",
        title="Otro escribiendo",
        argv=("echo", "otro"),
        about="Escribe.",
        writes=True,
    ),
    Command(
        id="color",
        group="Grupo",
        title="Con color",
        argv=("printf", "\\033[31mrojo\\033[0m\\n"),
        about="Imprime en rojo.",
    ),
    *_COMMANDS,
)


def _slow_app(tmp_path: Path) -> GuaniTUI:
    return GuaniTUI(commands=_SLOW_COMMANDS, logs=_LOGS, cwd=tmp_path)


def test_launching_another_command_does_not_cut_the_one_running(tmp_path: Path) -> None:
    async def scenario() -> list[tuple[str, str, list[str]]]:
        app = _slow_app(tmp_path)
        async with app.run_test() as pilot:
            await _select(app, pilot, "lento")
            await pilot.press("r")
            await _select(app, pilot, "leer")
            await pilot.press("r")
            await app.workers.wait_for_complete()
            await pilot.pause()
            return [(r.key, r.status, list(r.lines)) for r in app.book.runs]

    result = asyncio.run(scenario())
    assert result == [("lento", "ok", ["fin-lento"]), ("leer", "ok", ["salida-leer"])]


def test_launching_what_is_already_running_does_not_start_a_second_run(tmp_path: Path) -> None:
    async def scenario() -> int:
        app = _slow_app(tmp_path)
        async with app.run_test() as pilot:
            await _select(app, pilot, "lento")
            await pilot.press("r")
            await pilot.press("r")
            await pilot.pause()
            count = len(app.book.runs)
            await app.workers.wait_for_complete()
            return count

    assert asyncio.run(scenario()) == 1


def test_x_cuts_the_shown_run_and_kills_its_process(tmp_path: Path) -> None:
    async def scenario() -> tuple[str, str, list[str]]:
        app = _slow_app(tmp_path)
        async with app.run_test() as pilot:
            await _select(app, pilot, "lento")
            await pilot.press("r")
            await pilot.pause()
            await pilot.press("x")
            await app.workers.wait_for_complete()
            await pilot.pause()
            run = app.book.runs[0]
            return run.status, _log_text(app), list(run.lines)

    status, text, lines = asyncio.run(scenario())
    assert status == "cut"
    assert "[cortado" in text
    assert lines == []


def test_x_with_nothing_running_writes_nothing_to_the_output(tmp_path: Path) -> None:
    async def scenario() -> str:
        app = _app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("x")
            await pilot.pause()
            return _log_text(app)

    assert asyncio.run(scenario()) == ""


def test_the_output_keeps_the_colours_of_the_program(tmp_path: Path) -> None:
    async def scenario() -> tuple[str, str | None]:
        app = _slow_app(tmp_path)
        async with app.run_test() as pilot:
            await _select(app, pilot, "color")
            await pilot.press("r")
            await app.workers.wait_for_complete()
            await pilot.pause()
            line = next(s for s in app.query_one(RichLog).lines if s.text.strip() == "rojo")
            color = next(seg.style.color.name for seg in line if "rojo" in seg.text and seg.style)
            return line.text.strip(), color

    text, color = asyncio.run(scenario())
    assert text == "rojo"
    assert color == "color(1)"  # el rojo de la paleta ANSI


def test_choosing_another_run_in_the_list_shows_its_output(tmp_path: Path) -> None:
    async def scenario() -> tuple[str, str]:
        app = _slow_app(tmp_path)
        async with app.run_test() as pilot:
            for command_id in ("leer", "escribir"):
                await _select(app, pilot, command_id)
                await pilot.press("r")
                if command_id == "escribir":
                    await pilot.press("y")
                await app.workers.wait_for_complete()
                await pilot.pause()
            shown_last = _log_text(app)
            runs = app.query_one("#runs")
            runs.highlighted = 0
            await pilot.pause()
            return shown_last, _log_text(app)

    last, first = asyncio.run(scenario())
    assert "salida-escribir" in last
    assert "salida-leer" in first
    assert "salida-escribir" not in first


def test_a_second_writer_warns_that_the_first_is_still_writing(tmp_path: Path) -> None:
    async def scenario() -> tuple[str, int]:
        app = _slow_app(tmp_path)
        async with app.run_test() as pilot:
            await _select(app, pilot, "lento-w")
            await pilot.press("r")
            await pilot.press("y")
            await _select(app, pilot, "otro-w")
            await pilot.press("r")
            await pilot.pause()
            body = str(app.screen.query_one("#confirm", Static).render())
            await pilot.press("n")
            count = len(app.book.runs)
            await app.workers.wait_for_complete()
            return body, count

    body, count = asyncio.run(scenario())
    assert "Lento escribiendo" in body
    assert "a la vez" in body
    assert count == 1


def test_quitting_while_a_writer_runs_asks_first(tmp_path: Path) -> None:
    async def scenario() -> tuple[str, list[bool]]:
        app = _slow_app(tmp_path)
        exits: list[bool] = []
        async with app.run_test() as pilot:
            await _select(app, pilot, "lento-w")
            await pilot.press("r")
            await pilot.press("y")
            app.exit = lambda *args, **kwargs: exits.append(True)  # type: ignore[method-assign]
            await pilot.press("q")
            await pilot.pause()
            body = str(app.screen.query_one("#confirm", Static).render())
            asked_exits = list(exits)
            await pilot.press("n")
            await app.workers.wait_for_complete()
            return body, asked_exits + exits

    body, exits = asyncio.run(scenario())
    assert "sigue escribiendo" in body
    assert exits == []


def test_c_removes_finished_runs_and_keeps_the_running_one(tmp_path: Path) -> None:
    async def scenario() -> tuple[list[str], str]:
        app = _slow_app(tmp_path)
        async with app.run_test() as pilot:
            await _select(app, pilot, "leer")
            await pilot.press("r")
            await app.workers.wait_for_complete()
            await _select(app, pilot, "lento")
            await pilot.press("r")
            await pilot.pause()
            await pilot.press("c")
            await pilot.pause()
            keys = [r.key for r in app.book.runs]
            shown = _log_text(app)
            await app.workers.wait_for_complete()
            return keys, shown

    keys, shown = asyncio.run(scenario())
    assert keys == ["lento"]
    assert "salida-leer" not in shown


# --- estado de las etapas ---------------------------------------------------------

_STAGE = Stage(
    group="Grupo",
    health="err",
    health_label="Atrasado",
    summary="5 · 24 h",
    details=("Por WhatsApp: 5",),
    foot="hace 3 h",
)


def _status_app(tmp_path: Path, loader) -> GuaniTUI:
    return GuaniTUI(commands=_COMMANDS, logs=_LOGS, cwd=tmp_path, status_loader=loader)


def _group_node(app: GuaniTUI):
    return app.query_one(Tree).root.children[0]


def test_the_group_label_shows_the_health_and_the_figure(tmp_path: Path) -> None:
    async def scenario() -> str:
        app = _status_app(tmp_path, lambda: Snapshot(stages=(_STAGE,)))
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            return _group_node(app).label.plain

    assert asyncio.run(scenario()) == "● Grupo  5 · 24 h"


def test_highlighting_a_stage_shows_its_health_and_details(tmp_path: Path) -> None:
    async def scenario() -> str:
        app = _status_app(tmp_path, lambda: Snapshot(stages=(_STAGE,)))
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete()
            app.query_one(Tree).move_cursor(_group_node(app))
            await pilot.pause()
            return str(app.query_one("#detail", Static).render())

    text = asyncio.run(scenario())
    assert "Grupo — Atrasado" in text
    assert "Por WhatsApp: 5" in text
    assert "hace 3 h" in text


def test_when_the_status_cannot_be_read_the_menu_still_works_and_says_why(tmp_path: Path) -> None:
    async def scenario() -> tuple[str, str, int]:
        app = _status_app(tmp_path, lambda: Snapshot(error="OperationalError: sin base"))
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete()
            app.query_one(Tree).move_cursor(_group_node(app))
            await pilot.pause()
            detail = str(app.query_one("#detail", Static).render())
            return _group_node(app).label.plain, detail, len(app._notifications)

    label, detail, notices = asyncio.run(scenario())
    assert label == "Grupo"
    assert "sin base" in detail
    assert notices == 1


def test_u_reads_the_status_again(tmp_path: Path) -> None:
    calls: list[int] = []

    def loader() -> Snapshot:
        calls.append(1)
        return Snapshot(stages=(_STAGE,))

    async def scenario() -> None:
        app = _status_app(tmp_path, loader)
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete()
            await pilot.press("u")
            await app.workers.wait_for_complete()

    asyncio.run(scenario())
    assert len(calls) == 2


def test_the_status_is_read_again_when_a_writing_command_finishes(tmp_path: Path) -> None:
    calls: list[int] = []

    def loader() -> Snapshot:
        calls.append(1)
        return Snapshot(stages=(_STAGE,))

    async def scenario() -> None:
        app = _status_app(tmp_path, loader)
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete()
            await _select(app, pilot, "escribir")
            await pilot.press("r")
            await pilot.press("y")
            await app.workers.wait_for_complete()
            await pilot.pause()
            await app.workers.wait_for_complete()

    asyncio.run(scenario())
    assert len(calls) == 2


def test_a_read_only_command_does_not_reread_the_status(tmp_path: Path) -> None:
    calls: list[int] = []

    def loader() -> Snapshot:
        calls.append(1)
        return Snapshot(stages=(_STAGE,))

    async def scenario() -> None:
        app = _status_app(tmp_path, loader)
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete()
            await _select(app, pilot, "leer")
            await pilot.press("r")
            await app.workers.wait_for_complete()

    asyncio.run(scenario())
    assert len(calls) == 1


def test_without_a_status_loader_the_menu_keeps_plain_group_names(tmp_path: Path) -> None:
    async def scenario() -> str:
        app = _app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            return _group_node(app).label.plain

    assert asyncio.run(scenario()) == "Grupo"
