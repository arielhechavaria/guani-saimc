"""Estado del sistema para el TUI: las mismas siete etapas de `/control/flujo`.

No calcula nada propio: toma el `FlowView` que ya arma `saimc.web.control_flow`
y se queda con lo que cabe en una línea de menú y en un panel de texto. Así lo
que el operador ve en la terminal y en `/control/flujo` no puede discrepar.

Cada etapa se corresponde con un grupo del registro (`registry.GROUPS`): el menú
le pone al grupo la salud al lado.
"""

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from saimc.config import Settings, get_settings
from saimc.db import get_session_factory
from saimc.web import control_flow
from saimc.web.control_flow import FlowNode, FlowView
from saimc.web.strip_loader import load_strip

# Clave del nodo del flujo -> grupo del registro.
STAGE_GROUPS = {
    "in": "Ingesta",
    "pre": "Pre-cálculo",
    "c0": "Carril 0",
    "c1": "Carril 1",
    "c2": "Cola",
    "pub": "Tablero",
    "mkt": "Marketing",
}


@dataclass(frozen=True)
class Stage:
    group: str
    health: str  # ok | warn | err | off
    health_label: str
    summary: str  # la cifra principal, corta: «42 · 24 h»
    details: tuple[str, ...]  # una línea cada una, para el panel de detalle
    foot: str


# Lo que el menú sabe del estado: o las etapas, o por qué no se pudieron leer.
@dataclass(frozen=True)
class Snapshot:
    stages: tuple[Stage, ...] = ()
    error: str = ""

    def stage(self, group: str) -> Stage | None:
        return next((s for s in self.stages if s.group == group), None)

    def counts(self) -> dict[str, int]:
        """Cuántas etapas hay en cada nivel de salud, solo los que tienen alguna."""
        found: dict[str, int] = {}
        for stage in self.stages:
            found[stage.health] = found.get(stage.health, 0) + 1
        return {level: found[level] for level in ("err", "warn", "ok", "off") if level in found}


StatusLoader = Callable[[], Snapshot]


def _summary(node: FlowNode) -> str:
    if node.big is None:
        return ""
    return f"{node.big} · {node.unit}" if node.unit else node.big


def _details(node: FlowNode) -> tuple[str, ...]:
    lines = []
    chips = [f"{chip.title or chip.icon}: {chip.text}" for chip in node.chips]
    if chips:
        lines.append(" · ".join(chips))
    steps = [f"{step.count} {step.title}" for step in node.steps]
    if steps:
        lines.append(" · ".join(steps))
    return tuple(lines)


def stages_from_view(view: FlowView) -> tuple[Stage, ...]:
    return tuple(
        Stage(
            group=STAGE_GROUPS[node.key],
            health=node.health,
            health_label=node.health_label,
            summary=_summary(node),
            details=_details(node),
            foot=node.foot_text,
        )
        for node in view.nodes
    )


def read(db: Session, settings: Settings) -> Snapshot:
    """Lee el estado de la base y arma las etapas. Solo lectura."""
    snapshot = load_strip(db, settings)
    inputs = control_flow.load_inputs(db, settings, strip=snapshot.strip, prefill_run=snapshot.prefill_run)
    return Snapshot(stages=stages_from_view(control_flow.build_view(inputs)))


def load() -> Snapshot:
    """El lector de verdad. Bloquea (base y un chequeo corto a WAHA): se corre en un hilo.

    Si falla —base caída, `.env` sin configurar— no levanta: devuelve el motivo para
    mostrarlo en pantalla. El TUI tiene que abrir igual, sobre todo cuando algo anda mal.
    """
    try:
        with get_session_factory()() as db:
            return read(db, get_settings())
    except Exception as error:  # el motivo se muestra al operador, no se pierde
        return Snapshot(error=f"{type(error).__name__}: {error}")
