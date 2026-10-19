"""Registro de comandos y logs que muestra el TUI.

Es la única fuente del menú. Solo datos: no importa Textual, así los tests lo
verifican contra el repo (módulos, scripts y unidades de systemd que existen).
Los comandos que piden `sudo` no están: el TUI no puede contestar la
contraseña, esos se siguen corriendo a mano desde `docs/comandos.md`.
"""

import shlex
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

# Los siete primeros son las etapas del flujo de `/control/flujo`, en su orden; el menú
# les pone al lado la salud de cada una (`saimc.tui.status`).
GROUPS = (
    "Ingesta",
    "Pre-cálculo",
    "Carril 0",
    "Carril 1",
    "Cola",
    "Tablero",
    "Marketing",
    "Medir y diagnosticar",
    "Temporizadores",
    "Mantenimiento",
)


@dataclass(frozen=True)
class Command:
    id: str
    group: str
    title: str
    argv: tuple[str, ...]
    about: str
    writes: bool = False  # cambia filas o archivos
    spends: bool = False  # gasta plata en una API
    # Id del comando que hace de verdad lo que este solo cuenta. Los dos comparten una
    # entrada del menú: Enter corre el seco y `e` el real.
    real: str | None = None

    @property
    def needs_confirm(self) -> bool:
        return self.writes or self.spends

    @property
    def display(self) -> str:
        """La línea tal como la escribirías a mano (`.venv/bin/python`, no la ruta absoluta)."""
        shown = (".venv/bin/python" if part == sys.executable else part for part in self.argv)
        return shlex.join(shown)


@dataclass(frozen=True)
class LogSource:
    id: str
    title: str
    unit: str

    @property
    def argv(self) -> tuple[str, ...]:
        return ("journalctl", "-u", self.unit, "-n", "100", "-f", "--no-pager")

    @property
    def display(self) -> str:
        return shlex.join(self.argv)


def _plain(text: str) -> str:
    """Minúsculas y sin tildes, para que «carril» encuentre «Carríl» y «pre-calculo» «Pre-cálculo»."""
    decomposed = unicodedata.normalize("NFD", text.casefold())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def matches(command: Command, query: str, real: Command | None = None) -> bool:
    """¿Entra `command` (y su versión real, si tiene) en la búsqueda? Cada palabra tiene que estar."""
    parts = [command.group, command.title, command.about]
    if real is not None:
        parts += [real.title, real.about]
    return matches_text(" ".join(parts), query)


def matches_text(text: str, query: str) -> bool:
    """¿Aparece cada palabra de `query` en `text`, sin mirar mayúsculas ni tildes?"""
    haystack = _plain(text)
    return all(word in haystack for word in _plain(query).split())


def _py(*args: str) -> tuple[str, ...]:
    return (sys.executable, *args)


COMMANDS = (
    Command(
        "ingest",
        "Ingesta",
        "Ingerir la bandeja de entrada",
        _py("-m", "saimc.ingestion"),
        "Toma los archivos de la bandeja y los guarda como registros crudos.",
        writes=True,
    ),
    Command(
        "prefill-dry",
        "Pre-cálculo",
        "Pre-cálculo en seco",
        _py("-m", "saimc.extraction.prefill", "--stale"),
        "Cuenta y estima el costo de rehacer propuestas viejas. No escribe.",
        real="prefill-run",
    ),
    Command(
        "prefill-run",
        "Pre-cálculo",
        "Pre-cálculo escribiendo",
        _py("-m", "saimc.extraction.prefill", "--stale", "--execute"),
        "Rehace propuestas viejas de verdad. Gasta, con tope.",
        writes=True,
        spends=True,
    ),
    Command(
        "lane0-dry",
        "Carril 0",
        "Carril 0 en seco",
        _py("-m", "saimc.validation.lane0"),
        "Cuántos pendientes descartaría el carril 0. No descarta.",
        real="lane0-run",
    ),
    Command(
        "lane0-run",
        "Carril 0",
        "Carril 0 escribiendo",
        _py("-m", "saimc.validation.lane0", "--execute"),
        "Descarta los pendientes que el carril 0 descarta, con evento automático.",
        writes=True,
    ),
    Command(
        "lane1-dry",
        "Carril 1",
        "Carril 1 en seco",
        _py("-m", "saimc.validation.lane1"),
        "Qué textos pendientes aceptaría solo el carril 1. No crea filas.",
    ),
    Command(
        "queue-expiry-dry",
        "Cola",
        "Tope de edad de la cola en seco",
        _py("-m", "saimc.queue_expiry"),
        "Cuántos pendientes vencerían. No descarta.",
    ),
    Command(
        "revalidate-dry",
        "Tablero",
        "Revalidar en seco",
        _py("-m", "saimc.validation.revalidate"),
        "Cuántas filas cambiarían de estado. No escribe.",
        real="revalidate-run",
    ),
    Command(
        "revalidate-run",
        "Tablero",
        "Revalidar escribiendo",
        _py("-m", "saimc.validation.revalidate", "--execute"),
        "Re-guarda el estado de las filas de prices_history.",
        writes=True,
    ),
    Command(
        "marketing-dry",
        "Marketing",
        "Tarjetas de marketing en seco",
        _py("-m", "saimc.marketing.daily"),
        "Dice qué borradores crearía hoy. No crea nada.",
        real="marketing-run",
    ),
    Command(
        "marketing-run",
        "Marketing",
        "Tarjetas de marketing escribiendo",
        _py("-m", "saimc.marketing.daily", "--execute"),
        "Crea los borradores de hoy y purga los vencidos.",
        writes=True,
    ),
    Command(
        "alerts-dry",
        "Medir y diagnosticar",
        "Avisos al celular en seco",
        _py("-m", "saimc.alerting"),
        "Muestra qué mandaría por Telegram. No manda.",
    ),
    Command(
        "h0",
        "Medir y diagnosticar",
        "Medir H0",
        _py("-m", "saimc.extraction.measure", "--no-record"),
        "Mide H0 contra el conjunto de referencia congelado, sin anexar a h0-runs.jsonl.",
    ),
    Command(
        "refset-status",
        "Medir y diagnosticar",
        "Conjunto de referencia: estado",
        _py("-m", "saimc.extraction.reference_set", "status"),
        "Versión vigente del conjunto de referencia y su deriva.",
    ),
    Command(
        "measure-n",
        "Medir y diagnosticar",
        "Estabilidad de la mediana por N",
        _py("-m", "saimc.validation.measure_n"),
        "Cuánto se mueve la mediana según el tamaño de muestra.",
    ),
    Command(
        "jev-dry",
        "Medir y diagnosticar",
        "Relleno de Jev en seco",
        _py("-m", "saimc.validation.jev_signal"),
        "Cuántos textos le faltan a Jev y cuánto costaría. No gasta.",
    ),
    Command(
        "instagram-dry",
        "Medir y diagnosticar",
        "Instagram en seco",
        _py("-m", "saimc.instagram.sync"),
        "Lee la cuenta y cuenta lo que guardaría. No escribe.",
    ),
    Command(
        "llm-provider",
        "Medir y diagnosticar",
        "Proveedor del asistente",
        _py("-m", "saimc.llm.provider"),
        "Por dónde llama el asistente (Anthropic o Vercel) y con qué modelo.",
    ),
    Command(
        "lint-drift",
        "Medir y diagnosticar",
        "Lint drift",
        _py("scripts/lint_drift.py", "--no-autofix"),
        "Hallazgos nuevos de ruff y mypy.",
    ),
    Command(
        "timers",
        "Temporizadores",
        "Cuándo toca cada temporizador",
        ("systemctl", "list-timers", "saimc-*.timer", "--no-pager"),
        "Próxima y última corrida de los temporizadores de Guani.",
    ),
    Command(
        "app-status",
        "Temporizadores",
        "Estado de la aplicación",
        ("systemctl", "status", "saimc.service", "--no-pager"),
        "Estado de saimc.service.",
    ),
    Command(
        "backup",
        "Mantenimiento",
        "Backup de la base",
        ("scripts/backup_db.sh",),
        "Vuelca la base a un archivo de respaldo.",
        writes=True,
    ),
)

LOGS = (
    LogSource("log-app", "Aplicación", "saimc.service"),
    LogSource("log-prefill", "Pre-cálculo", "saimc-prefill.service"),
    LogSource("log-alerts", "Avisos", "saimc-alerts.service"),
    LogSource("log-marketing", "Marketing", "saimc-marketing.service"),
    LogSource("log-instagram", "Instagram", "saimc-instagram.service"),
    LogSource("log-backup", "Backup", "saimc-backup.service"),
)
