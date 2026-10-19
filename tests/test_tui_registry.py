"""El registro de comandos del TUI no se desincroniza del repo.

Lo que se prueba acá es que cada entrada apunte a algo que existe y que ningún
comando que escribe o gasta pueda correr sin confirmación. El texto de cada
entrada no se prueba: es presentación.
"""

import importlib.util
from pathlib import Path

from saimc.tui.registry import COMMANDS, GROUPS, LOGS, REPO_ROOT, Command, matches

_WRITE_FLAGS = {"--execute", "--apply"}


def _module_of(argv: tuple[str, ...]) -> str | None:
    if "-m" in argv:
        return argv[argv.index("-m") + 1]
    return None


def test_ids_are_unique() -> None:
    ids = [c.id for c in COMMANDS] + [log.id for log in LOGS]
    assert len(ids) == len(set(ids))


def test_every_command_belongs_to_a_known_group() -> None:
    assert {c.group for c in COMMANDS} <= set(GROUPS)


def test_python_modules_exist() -> None:
    for command in COMMANDS:
        module = _module_of(command.argv)
        if module is not None:
            assert importlib.util.find_spec(module) is not None, command.id


def test_scripts_exist() -> None:
    for command in COMMANDS:
        for part in command.argv:
            if part.startswith(("scripts/", "tools/")):
                assert (REPO_ROOT / part).is_file(), command.id


def test_commands_that_write_require_confirmation() -> None:
    for command in COMMANDS:
        if _WRITE_FLAGS & set(command.argv):
            assert command.needs_confirm, command.id


def test_read_only_commands_do_not_ask_for_confirmation() -> None:
    for command in COMMANDS:
        if not (command.writes or command.spends):
            assert not command.needs_confirm, command.id


def test_log_units_have_a_unit_file_or_are_the_app() -> None:
    systemd = REPO_ROOT / "systemd"
    for log in LOGS:
        assert log.unit == "saimc.service" or (systemd / log.unit).is_file(), log.id


def test_log_follow_argv_targets_the_unit() -> None:
    for log in LOGS:
        assert log.argv[0] == "journalctl"
        assert log.unit in log.argv
        assert "-f" in log.argv


def test_display_shows_the_venv_python_not_the_absolute_path() -> None:
    command = next(c for c in COMMANDS if _module_of(c.argv))
    assert command.display.startswith(".venv/bin/python ")
    assert str(Path(command.argv[0]).parent) not in command.display


# --- pares seco → real y búsqueda -------------------------------------------------

_BY_ID = {c.id: c for c in COMMANDS}


def test_every_real_counterpart_exists_writes_and_runs_the_same_module() -> None:
    pairs = [(c, _BY_ID[c.real]) for c in COMMANDS if c.real]
    assert pairs, "tiene que haber al menos un par"
    for dry, real in pairs:
        assert real.needs_confirm, real.id
        assert not dry.needs_confirm, dry.id
        assert _module_of(real.argv) == _module_of(dry.argv), dry.id


def test_a_real_command_belongs_to_one_dry_entry_only() -> None:
    reals = [c.real for c in COMMANDS if c.real]
    assert len(reals) == len(set(reals))


def test_a_pair_shares_its_group() -> None:
    for dry in (c for c in COMMANDS if c.real):
        assert _BY_ID[dry.real].group == dry.group, dry.id


def _cmd(title: str, about: str = "", group: str = "Carril 0") -> Command:
    return Command("x", group, title, ("echo",), about)


def test_search_ignores_case_and_accents() -> None:
    command = _cmd("Pre-cálculo en seco")
    assert matches(command, "PRE-CALCULO")
    assert matches(command, "calculo")


def test_search_needs_every_word_in_any_order() -> None:
    command = _cmd("Carril 0 en seco", "Cuántos pendientes descartaría")
    assert matches(command, "seco carril")
    assert not matches(command, "seco marketing")


def test_search_looks_at_the_group_and_the_description() -> None:
    command = _cmd("Revalidar en seco", "Cuántas filas cambiarían", group="Tablero")
    assert matches(command, "tablero")
    assert matches(command, "filas")


def test_search_also_finds_an_entry_through_its_real_counterpart() -> None:
    dry = _cmd("Marketing en seco", "No crea nada.")
    real = _cmd("Marketing escribiendo", "Crea los borradores de hoy.")
    assert not matches(dry, "borradores")
    assert matches(dry, "borradores", real)


def test_an_empty_search_matches_everything() -> None:
    assert matches(_cmd("Lo que sea"), "   ")
