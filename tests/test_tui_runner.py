"""El ejecutor del TUI transmite la salida y se puede cortar."""

import asyncio
import contextlib
import sys
from pathlib import Path

from saimc.tui.runner import stream


async def _collect(argv: tuple[str, ...], cwd: Path) -> tuple[list[str], int | None]:
    lines: list[str] = []
    result = None
    async for item in stream(argv, cwd):
        if isinstance(item, str):
            lines.append(item)
        else:
            result = item.returncode
    return lines, result


def test_stream_yields_lines_then_exit_code(tmp_path: Path) -> None:
    lines, code = asyncio.run(_collect(("echo", "hola"), tmp_path))
    assert lines == ["hola"]
    assert code == 0


def test_stream_merges_stderr_and_reports_failure(tmp_path: Path) -> None:
    script = "import sys; print('a'); print('b', file=sys.stderr); sys.exit(3)"
    lines, code = asyncio.run(_collect((sys.executable, "-c", script), tmp_path))
    assert sorted(lines) == ["a", "b"]
    assert code == 3


def test_stream_runs_in_the_given_cwd(tmp_path: Path) -> None:
    lines, _ = asyncio.run(_collect(("pwd",), tmp_path))
    assert lines == [str(tmp_path)]


def test_stream_reports_a_missing_binary_instead_of_raising(tmp_path: Path) -> None:
    lines, code = asyncio.run(_collect(("no-such-binary-saimc",), tmp_path))
    assert code == 127
    assert "no-such-binary-saimc" in lines[0]


def test_cancelling_kills_the_process(tmp_path: Path) -> None:
    async def scenario() -> bool:
        seen: list[str] = []

        async def consume() -> None:
            async for item in stream(
                (sys.executable, "-u", "-c", "import time; print('up'); time.sleep(60)"),
                tmp_path,
            ):
                if isinstance(item, str):
                    seen.append(item)

        task = asyncio.create_task(consume())
        for _ in range(100):
            if seen:
                break
            await asyncio.sleep(0.05)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5)
        return seen == ["up"]

    assert asyncio.run(scenario())
