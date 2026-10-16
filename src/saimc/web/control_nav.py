"""Navegación de `/control`: las cinco subpáginas, sus secciones y las insignias.

`/control` era una sola página con ~20 secciones apiladas y sin índice. Se
partió en cinco vistas reales (`/control`, `/control/ingesta`, ...) y este
módulo es la única fuente de qué hay en cada una: el menú lateral, las
tarjetas de «Hoy», la traducción de los enlaces viejos con ancla
(`/control#tasa` → `/control/mercado#tasa`) y las insignias salen de acá.

Las insignias no consultan nada nuevo: cuentan los indicadores de la franja de
estado que el router ya calcula (`health_strip.build_strip`) y la alerta de
aceptación sin revisar.
"""

from __future__ import annotations

from dataclasses import dataclass

from saimc.web.health_strip import Indicator


@dataclass(frozen=True)
class NavSection:
    """Una sección dentro de una subpágina: su rótulo y el `id` del ancla."""

    label: str
    anchor: str


@dataclass(frozen=True)
class ControlPage:
    key: str
    url: str
    # Rótulo del menú y título de la vista.
    title: str
    heading: str
    description: str
    icon: str
    sections: tuple[NavSection, ...]


@dataclass(frozen=True)
class Badge:
    """Cuántas cosas piden atención en una subpágina, y cuán grave es la peor."""

    count: int
    level: str  # "down" | "warn"


PAGES: tuple[ControlPage, ...] = (
    ControlPage(
        key="hoy",
        url="/control",
        title="Hoy",
        heading="Control de curaduría",
        description="Qué exige acción hoy, y a dónde ir a mirar el resto.",
        icon="calendar_month",
        sections=(
            NavSection("Estado del sistema", "estado"),
            NavSection("Hoy", "hoy"),
            NavSection("Lo que falta responder", "falta"),
            NavSection("Ritmo de carga", "ritmo"),
        ),
    ),
    ControlPage(
        key="ingesta",
        url="/control/ingesta",
        title="Ingesta",
        heading="Control · Ingesta",
        description="Qué entra, de dónde, cuánto se descarta y cuánto cuesta procesarlo.",
        icon="inbox",
        sections=(
            NavSection("Salud de la ingesta", "ingesta"),
            NavSection("Descarte por canal", "canales"),
            NavSection("Horas y automatización", "horas"),
            NavSection("Disponibilidad", "disponibilidad"),
        ),
    ),
    ControlPage(
        key="calidad",
        url="/control/calidad",
        title="Calidad",
        heading="Control · Calidad",
        description="Si lo que se carga sirve: validación, aceptación automática y controles.",
        icon="task_alt",
        sections=(
            NavSection("Validación", "validacion"),
            NavSection("Carril 1", "carril-1"),
            NavSection("Aceptación sin revisar", "rubber-stamp"),
            NavSection("Jev (señal en sombra)", "jev"),
            NavSection("Controles de manipulación", "controles"),
            NavSection("Matriz de los pilotos", "matriz"),
        ),
    ),
    ControlPage(
        key="fase0",
        url="/control/fase-0",
        title="Fase 0",
        heading="Control · Fase 0",
        description="Cuánto falta para la meta y cómo va H0.",
        icon="flag",
        sections=(
            NavSection("Meta de Fase 0", "meta"),
            NavSection("Métrica de la Fase 0", "metrica-fase-0"),
            NavSection("Historial de H0", "historial-h0"),
            NavSection("Cobertura de la meta", "cobertura"),
            NavSection("Consumo del asistente", "asistente"),
        ),
    ),
    ControlPage(
        key="mercado",
        url="/control/mercado",
        title="Mercado",
        heading="Control · Mercado",
        description="Tasa de cambio y rango de precios minoristas por producto.",
        icon="sell",
        sections=(
            NavSection("Tasa de cambio", "tasa"),
            NavSection("Rango minorista por producto", "rango"),
        ),
    ),
)

PAGE_BY_KEY: dict[str, ControlPage] = {page.key: page for page in PAGES}


def anchor_urls() -> dict[str, str]:
    """`«#tasa»` → `«/control/mercado#tasa»`, para cada ancla de cada subpágina.

    Sirve a los enlaces de la franja de estado (que llevan el ancla vieja, sin
    página) y a la redirección de los marcadores viejos de `/control#...`.
    """
    return {
        f"#{section.anchor}": f"{page.url}#{section.anchor}" for page in PAGES for section in page.sections
    }


def _page_of_anchor(anchor: str) -> str | None:
    for page in PAGES:
        if any(f"#{section.anchor}" == anchor for section in page.sections):
            return page.key
    return None


def compute_badges(strip: list[Indicator], *, rubber_stamp_alert: bool) -> dict[str, Badge]:
    """Las insignias del menú: una por subpágina que tenga algo para mirar.

    Un indicador en `down` o `warn` cuenta en la subpágina a la que apunta su
    ancla; «Hoy» suma todos, porque es el centro. `off` y `ok` no cuentan (el
    mismo criterio que `worst_level`). La alerta de aceptación sin revisar
    suma a Calidad como `warn`.
    """
    tally: dict[str, list[str]] = {}

    def add(page_key: str, level: str) -> None:
        tally.setdefault(page_key, []).append(level)

    for indicator in strip:
        if indicator.level not in ("down", "warn"):
            continue
        add("hoy", indicator.level)
        page_key = _page_of_anchor(indicator.anchor)
        if page_key is not None and page_key != "hoy":
            add(page_key, indicator.level)
    if rubber_stamp_alert:
        add("hoy", "warn")
        add("calidad", "warn")

    return {
        key: Badge(count=len(levels), level="down" if "down" in levels else "warn")
        for key, levels in tally.items()
    }
