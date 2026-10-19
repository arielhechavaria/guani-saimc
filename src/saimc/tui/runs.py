"""Corridas del TUI: cada vez que se lanza algo queda una entrada propia.

Solo datos, sin Textual. Antes había una sola salida y lanzar un ítem cortaba el
anterior; ahora cada corrida tiene su estado, su duración y su salida, y varias
conviven (un log en vivo no se corta porque corras un comando).
"""

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

# Un log en vivo no termina: el tope evita que crezca sin límite.
MAX_LINES = 5000
# Corridas terminadas que se conservan en la lista; las más viejas se descartan.
MAX_RUNS = 30

RUNNING = "running"
OK = "ok"
FAILED = "failed"
CUT = "cut"

GLYPHS = {RUNNING: "●", OK: "✓", FAILED: "✗", CUT: "■"}


def format_duration(seconds: float) -> str:
    if seconds < 1:
        return "<1 s"
    total = int(seconds)
    if total < 60:
        return f"{total} s"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes} min {secs:02d} s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min"


@dataclass
class Run:
    number: int
    key: str  # id del comando o del log; sirve para no lanzar dos veces lo mismo
    title: str
    display: str
    kind: str  # "command" | "log"
    writes: bool
    started_at: datetime
    ended_at: datetime | None = None
    returncode: int | None = None
    was_cut: bool = False
    lines: deque[str] = field(default_factory=lambda: deque(maxlen=MAX_LINES))

    @property
    def status(self) -> str:
        if self.ended_at is None:
            return RUNNING
        if self.was_cut:
            return CUT
        return OK if self.returncode == 0 else FAILED

    @property
    def running(self) -> bool:
        return self.ended_at is None

    def finish(self, returncode: int, at: datetime) -> None:
        if self.ended_at is None:
            self.returncode = returncode
            self.ended_at = at

    def cut(self, at: datetime) -> None:
        if self.ended_at is None:
            self.was_cut = True
            self.ended_at = at

    def elapsed(self, now: datetime) -> float:
        return ((self.ended_at or now) - self.started_at).total_seconds()

    def summary(self, now: datetime) -> str:
        title = f"Log: {self.title}" if self.kind == "log" else self.title
        clock = self.started_at.strftime("%H:%M")
        return f"{GLYPHS[self.status]} {clock}  {title} · {format_duration(self.elapsed(now))}"

    def footer(self, now: datetime) -> str:
        """La línea que cierra la salida. Vacía mientras la corrida sigue."""
        took = format_duration(self.elapsed(now))
        if self.status == CUT:
            return f"[cortado · {took}]"
        if self.status == RUNNING:
            return ""
        return f"[salió con código {self.returncode} · {took}]"


class RunBook:
    """La lista de corridas, de la más vieja a la más nueva."""

    def __init__(self) -> None:
        self._runs: list[Run] = []
        self._next = 1

    @property
    def runs(self) -> tuple[Run, ...]:
        return tuple(self._runs)

    def start(self, *, key: str, title: str, display: str, kind: str, writes: bool, at: datetime) -> Run:
        run = Run(self._next, key, title, display, kind, writes, at)
        self._next += 1
        self._runs.append(run)
        return run

    def get(self, number: int) -> Run | None:
        return next((r for r in self._runs if r.number == number), None)

    def running(self, key: str) -> Run | None:
        return next((r for r in self._runs if r.running and r.key == key), None)

    def writing(self) -> Run | None:
        """Una corrida en curso que escribe, si hay."""
        return next((r for r in self._runs if r.running and r.writes), None)

    def active(self) -> list[Run]:
        return [r for r in self._runs if r.running]

    def clear_finished(self) -> list[Run]:
        removed = [r for r in self._runs if not r.running]
        self._runs = [r for r in self._runs if r.running]
        return removed

    def trim(self) -> list[Run]:
        """Descarta las terminadas más viejas cuando se pasa de `MAX_RUNS`."""
        excess = len(self._runs) - MAX_RUNS
        removed: list[Run] = []
        for run in self._runs:
            if excess <= 0:
                break
            if not run.running:
                removed.append(run)
                excess -= 1
        self._runs = [r for r in self._runs if r not in removed]
        return removed
