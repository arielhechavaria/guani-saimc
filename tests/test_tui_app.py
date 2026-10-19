"""La app del TUI: navega el registro, corre y pide confirmación si escribe."""

import asyncio
import sys
from pathlib import Path

from textual.widgets import RichLog, Static, Tree
from textual_image.widget import Image

from saimc.tui.app import GuaniTUI, HelpScreen
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


# --- pares seco → real, búsqueda, ayuda y terminal baja ---------------------------

_PAIR_COMMANDS = (
    Command(
        id="par-seco",
        group="Grupo",
        title="Par en seco",
        argv=("echo", "salida-seco"),
        about="Cuenta lo que haría.",
        real="par-real",
    ),
    Command(
        id="par-real",
        group="Grupo",
        title="Par escribiendo",
        argv=("echo", "salida-real"),
        about="Crea borradores.",
        writes=True,
    ),
    *_COMMANDS,
)


def _pair_app(tmp_path: Path, loader=None) -> GuaniTUI:
    return GuaniTUI(commands=_PAIR_COMMANDS, logs=_LOGS, cwd=tmp_path, status_loader=loader)


def _leaf_ids(app: GuaniTUI) -> list[str]:
    return [leaf.data for node in app.query_one(Tree).root.children for leaf in node.children]


def test_a_pair_shares_one_menu_entry_and_the_real_one_is_not_listed(tmp_path: Path) -> None:
    async def scenario() -> tuple[list[str], str]:
        app = _pair_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            leaf = next(
                n for g in app.query_one(Tree).root.children for n in g.children if n.data == "par-seco"
            )
            return _leaf_ids(app), leaf.label.plain

    ids, label = asyncio.run(scenario())
    assert "par-seco" in ids
    assert "par-real" not in ids
    assert label.endswith("⚠e")


def test_enter_on_a_pair_runs_the_dry_version_without_asking(tmp_path: Path) -> None:
    async def scenario() -> list[str]:
        app = _pair_app(tmp_path)
        async with app.run_test() as pilot:
            await _select(app, pilot, "par-seco")
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
            return [r.key for r in app.book.runs]

    assert asyncio.run(scenario()) == ["par-seco"]


def test_e_runs_the_real_version_after_confirming(tmp_path: Path) -> None:
    async def scenario() -> tuple[list[str], list[str]]:
        app = _pair_app(tmp_path)
        async with app.run_test() as pilot:
            await _select(app, pilot, "par-seco")
            await pilot.press("e")
            await pilot.pause()
            assert app.screen.query("#confirm")
            await pilot.press("y")
            await app.workers.wait_for_complete()
            await pilot.pause()
            return [r.key for r in app.book.runs], list(app.book.runs[0].lines)

    keys, lines = asyncio.run(scenario())
    assert keys == ["par-real"]
    assert lines == ["salida-real"]


def test_the_confirmation_warns_when_the_dry_run_was_never_done(tmp_path: Path) -> None:
    async def scenario() -> str:
        app = _pair_app(tmp_path)
        async with app.run_test() as pilot:
            await _select(app, pilot, "par-seco")
            await pilot.press("e")
            await pilot.pause()
            body = str(app.screen.query_one("#confirm", Static).render())
            await pilot.press("n")
            return body

    assert "Todavía no corriste «Par en seco»" in asyncio.run(scenario())


def test_the_confirmation_shows_what_the_last_dry_run_said(tmp_path: Path) -> None:
    async def scenario() -> str:
        app = _pair_app(tmp_path)
        async with app.run_test() as pilot:
            await _select(app, pilot, "par-seco")
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
            await pilot.press("e")
            await pilot.pause()
            body = str(app.screen.query_one("#confirm", Static).render())
            await pilot.press("n")
            return body

    body = asyncio.run(scenario())
    assert "Último «Par en seco»" in body
    assert "salió bien" in body
    assert "salida-seco" in body


def test_e_on_a_command_without_a_real_version_starts_nothing(tmp_path: Path) -> None:
    async def scenario() -> int:
        app = _pair_app(tmp_path)
        async with app.run_test() as pilot:
            await _select(app, pilot, "leer")
            await pilot.press("e")
            await pilot.pause()
            return len(app.book.runs)

    assert asyncio.run(scenario()) == 0


def test_the_detail_of_a_pair_shows_both_lines(tmp_path: Path) -> None:
    async def scenario() -> str:
        app = _pair_app(tmp_path)
        async with app.run_test() as pilot:
            await _select(app, pilot, "par-seco")
            return str(app.query_one("#detail", Static).render())

    text = asyncio.run(scenario())
    assert "Enter  $ echo salida-seco" in text
    assert "e      $ echo salida-real" in text
    assert "[escribe]" in text


def test_searching_filters_the_menu_and_escape_restores_it(tmp_path: Path) -> None:
    async def scenario() -> tuple[list[str], list[str], str]:
        app = _pair_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("slash")
            await pilot.press(*"escrib")
            await pilot.pause()
            filtered = _leaf_ids(app)
            await pilot.press("escape")
            await pilot.pause()
            return filtered, _leaf_ids(app), app.query_one("#search").value

    filtered, restored, typed = asyncio.run(scenario())
    # «escrib» está en «Escribir» y en el par, a través de «Par escribiendo».
    assert sorted(filtered) == ["escribir", "par-seco"]
    assert "leer" in restored
    assert typed == ""


def test_typing_in_the_search_does_not_trigger_the_key_bindings(tmp_path: Path) -> None:
    async def scenario() -> tuple[int, str]:
        app = _pair_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("slash")
            await pilot.press(*"xcjq")
            await pilot.pause()
            return len(app.book.runs), app.query_one("#search").value

    runs, typed = asyncio.run(scenario())
    assert runs == 0
    assert typed == "xcjq"


def test_enter_in_the_search_hands_the_focus_back_to_the_menu_on_the_first_match(tmp_path: Path) -> None:
    async def scenario() -> tuple[bool, str | None]:
        app = _pair_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("slash")
            await pilot.press(*"leer")
            await pilot.press("enter")
            await pilot.pause()
            tree = app.query_one(Tree)
            return app.focused is tree, tree.cursor_node.data if tree.cursor_node else None

    focused, cursor = asyncio.run(scenario())
    assert focused
    assert cursor == "leer"


def test_a_search_with_no_match_leaves_an_empty_menu(tmp_path: Path) -> None:
    async def scenario() -> int:
        app = _pair_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("slash")
            await pilot.press(*"zzzz")
            await pilot.pause()
            return len(app.query_one(Tree).root.children)

    assert asyncio.run(scenario()) == 0


def test_m_hides_and_shows_the_menu(tmp_path: Path) -> None:
    async def scenario() -> tuple[bool, bool]:
        app = _pair_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("m")
            await pilot.pause()
            hidden = not app.query_one(Tree).display
            await pilot.press("m")
            await pilot.pause()
            return hidden, app.query_one(Tree).display

    assert asyncio.run(scenario()) == (True, True)


def test_question_mark_opens_the_help_and_escape_closes_it(tmp_path: Path) -> None:
    async def scenario() -> tuple[bool, bool]:
        app = _pair_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("question_mark")
            await pilot.pause()
            opened = isinstance(app.screen, HelpScreen)
            await pilot.press("escape")
            await pilot.pause()
            return opened, isinstance(app.screen, HelpScreen)

    assert asyncio.run(scenario()) == (True, False)


def test_a_short_terminal_replaces_the_header_with_a_one_line_summary(tmp_path: Path) -> None:
    stages = (
        Stage("Grupo", "err", "Atrasado", "5", (), ""),
        Stage("Otro", "ok", "Al día", "1", (), ""),
        Stage("Otro2", "ok", "Al día", "1", (), ""),
    )

    async def scenario() -> tuple[bool, str, bool, bool]:
        app = _pair_app(tmp_path, loader=lambda: Snapshot(stages=stages))
        async with app.run_test(size=(80, 24)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            text = str(app.query_one("#brief", Static).render())
            return (
                app.query_one("#header").display,
                text,
                app.query_one("#brief").display,
                app.query_one("Footer").display,
            )

    header, text, brief, footer = asyncio.run(scenario())
    assert not header
    assert brief
    assert "1 atrasado" in text
    assert "2 al día" in text
    assert footer


def test_a_tall_terminal_keeps_the_header_and_hides_the_summary(tmp_path: Path) -> None:
    async def scenario() -> tuple[bool, bool]:
        app = _pair_app(tmp_path)
        async with app.run_test(size=(120, 48)) as pilot:
            await pilot.pause()
            return app.query_one("#header").display, app.query_one("#brief").display

    assert asyncio.run(scenario()) == (True, False)
