"""Las corridas del TUI: estado, duración y qué se conserva."""

from datetime import datetime, timedelta

import pytest

from saimc.tui import runs
from saimc.tui.runs import RunBook, format_duration

T0 = datetime(2026, 10, 19, 14, 2, 0)


def _start(book: RunBook, key: str = "a", *, writes: bool = False, kind: str = "command"):
    return book.start(key=key, title=key.upper(), display=f"cmd {key}", kind=kind, writes=writes, at=T0)


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0.2, "<1 s"), (3.9, "3 s"), (59, "59 s"), (65, "1 min 05 s"), (3725, "1 h 02 min")],
)
def test_format_duration(seconds: float, expected: str) -> None:
    assert format_duration(seconds) == expected


def test_a_new_run_is_running_and_has_no_footer() -> None:
    run = _start(RunBook())
    assert run.status == runs.RUNNING
    assert run.footer(T0) == ""


def test_finishing_with_zero_is_ok_and_with_other_code_is_failed() -> None:
    book = RunBook()
    ok, bad = _start(book, "a"), _start(book, "b")
    ok.finish(0, T0 + timedelta(seconds=3))
    bad.finish(2, T0 + timedelta(seconds=3))
    assert ok.status == runs.OK
    assert bad.status == runs.FAILED
    assert ok.footer(T0) == "[salió con código 0 · 3 s]"
    assert bad.footer(T0) == "[salió con código 2 · 3 s]"


def test_cutting_marks_it_cut_and_a_late_finish_does_not_change_it() -> None:
    run = _start(RunBook())
    run.cut(T0 + timedelta(seconds=5))
    run.finish(0, T0 + timedelta(seconds=9))
    assert run.status == runs.CUT
    assert run.footer(T0) == "[cortado · 5 s]"


def test_the_summary_shows_status_clock_title_and_duration() -> None:
    run = _start(RunBook(), "ingest")
    assert run.summary(T0 + timedelta(seconds=7)) == "● 14:02  INGEST · 7 s"
    run.finish(0, T0 + timedelta(seconds=7))
    assert run.summary(T0 + timedelta(hours=1)) == "✓ 14:02  INGEST · 7 s"


def test_a_log_run_is_labelled_as_log() -> None:
    run = _start(RunBook(), "app", kind="log")
    assert run.summary(T0).startswith("● 14:02  Log: APP")


def test_running_finds_only_unfinished_runs_by_key() -> None:
    book = RunBook()
    first = _start(book, "a")
    assert book.running("a") is first
    first.finish(0, T0)
    assert book.running("a") is None


def test_writing_reports_a_running_writer_only() -> None:
    book = RunBook()
    reader = _start(book, "r")
    writer = _start(book, "w", writes=True)
    assert book.writing() is writer
    writer.finish(0, T0)
    assert book.writing() is None
    assert reader.running


def test_clear_finished_keeps_the_ones_still_running() -> None:
    book = RunBook()
    done, live = _start(book, "a"), _start(book, "b")
    done.finish(0, T0)
    assert book.clear_finished() == [done]
    assert book.runs == (live,)


def test_trim_drops_the_oldest_finished_and_never_a_running_one() -> None:
    book = RunBook()
    live = _start(book, "live")
    for n in range(runs.MAX_RUNS + 2):
        _start(book, f"k{n}").finish(0, T0)
    removed = book.trim()
    assert len(book.runs) == runs.MAX_RUNS
    assert live in book.runs
    assert [r.key for r in removed] == ["k0", "k1", "k2"]


def test_lines_are_capped() -> None:
    run = _start(RunBook())
    for n in range(runs.MAX_LINES + 10):
        run.lines.append(str(n))
    assert len(run.lines) == runs.MAX_LINES
    assert run.lines[0] == "10"
