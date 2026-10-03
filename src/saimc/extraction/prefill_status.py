"""Registro de la última corrida real del pre-cálculo (slice C1).

El temporizador de systemd (`systemd/saimc-prefill.timer`) corre el pre-cálculo
sin nadie mirando. Para enterarse de que falló sin abrir una terminal, cada
corrida `--execute` deja acá cuándo terminó y cómo: `/control` lo lee y pinta
el indicador «Pre-cálculo» (`web/health_strip.py`).

No se reutiliza `llm_assist_log_path`: ese registro tiene una línea por
llamada y no sabe cuándo terminó una corrida ni con qué código de salida.

Es un solo JSON que se pisa en cada corrida (no un historial: el detalle vive
en `journalctl -u saimc-prefill.service`). Una corrida que muere antes de
escribirlo no deja nada nuevo, y eso se detecta por antigüedad en la franja.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class PrefillRun:
    """Cómo terminó la última corrida real."""

    finished_at: datetime
    exit_code: int
    done: int
    degraded: int
    skipped_no_text: int
    cost_usd: float
    stopped_by_max_cost: bool
    # `credit` o `auth` si la corrida paró por eso; vacío si no.
    aborted_failure: str


def write_run_status(path: Path, run: PrefillRun) -> None:
    """Pisa el registro. Escribe a un temporal y renombra: nunca queda a medias."""
    payload = asdict(run)
    payload["finished_at"] = run.finished_at.astimezone(UTC).isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def read_run_status(path: Path) -> PrefillRun | None:
    """El registro, o `None` si no existe o no se puede leer.

    Ilegible y ausente se tratan igual: `/control` no puede romperse por un
    archivo de estado, y los dos casos piden lo mismo del operador (mirar).
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["finished_at"] = datetime.fromisoformat(raw["finished_at"])
        return PrefillRun(**raw)
    except (OSError, ValueError, KeyError, TypeError):
        return None
