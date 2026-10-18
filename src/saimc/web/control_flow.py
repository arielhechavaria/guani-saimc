"""Vista de flujo de `/control` (`/control/flujo`): el lienzo de siete nodos.

El prototipo aprobado es `docs/prototipos/control-flujo-a-lienzo.html`
(decisión del 2026-10-18 en `docs/decisions.md`). Este módulo arma, para cada
nodo, lo que la plantilla pinta: cifra principal, gráfico propio, punto de
salud y pie. La plantilla no hace cuentas.

Dos capas, para poder probar la lógica sin base de datos:

- `load_inputs` lee lo que el sistema YA calcula (franja de estado, carriles,
  cola, cobertura, publicaciones) y lo junta en un `FlowInputs`. No agrega
  tablas ni columnas.
- `build_view` es pura: `FlowInputs` -> `FlowView`. Las coordenadas de los
  gráficos SVG salen de funciones puras (`hourly_chart`, `age_chart`, ...), no
  de aritmética en Jinja.

**Ningún umbral se inventa acá.** Cada uno sale de una constante o de la
configuración que ya existe, y donde no hay, el indicador se muestra sin él:

- el período del reloj del pre-cálculo: `health_strip.PREFILL_INTERVAL_HOURS`;
- las metas de falsos descartes del carril 0: `lane0.TARGETS`;
- el n del arranque del carril 1: `lane1.AUDIT_N0`;
- el límite de edad de la cola: `SAIMC_QUEUE_MAX_AGE_DAYS`
  (`Settings.queue_max_age_days`); en 0 —el valor por defecto— no hay límite,
  no se pinta ningún tramo de «vencido» y la antigüedad sola no cambia la salud.

**La salud** (`ok` / `warn` / `err` / `off`, que en pantalla son al día / mirar
/ atrasado / apagado) se deriva de las reglas que ya usa la franja de estado
(`health_strip`): `down` -> `err`, `warn` -> `warn`, `ok` -> `ok`, `off` -> `off`.
De dónde sale cada una está en el comentario de cada nodo. Un nodo cuya regla no
tiene indicador en la franja queda en `off`: sin regla no se afirma «al día».

Rojo y verde son salud operativa, nunca precio (`docs/DESIGN.md`, regla 3).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from saimc.config import Settings
from saimc.extraction.prefill_status import PrefillRun
from saimc.models import (
    CurationAction,
    CurationEvent,
    CurationMode,
    MarketingPost,
    PriceHistory,
    ProcessingStatus,
    Provenance,
    RawIngestion,
    SourceType,
)
from saimc.models.base import utc_now
from saimc.progress import CoverageCell, Last24h, compute_coverage, last_24h
from saimc.queue_expiry import EXPIRED_DETAIL_KEY
from saimc.validation import lane0, lane1
from saimc.validation.context import as_utc
from saimc.web.health_strip import PREFILL_INTERVAL_HOURS, Indicator

# --- salud ---------------------------------------------------------------------

HEALTH_LABELS = {"ok": "Al día", "warn": "Mirar", "err": "Atrasado", "off": "Apagado"}

# Niveles de la franja de estado -> niveles del nodo.
_LEVEL_TO_HEALTH = {"down": "err", "warn": "warn", "ok": "ok", "off": "off"}


def _health_from_strip(strip: Sequence[Indicator], *keys: str) -> str:
    """El peor nivel entre los indicadores de la franja con esas claves.

    Sin ninguno presente devuelve `off`: no hay regla que diga «al día».
    """
    levels = [_LEVEL_TO_HEALTH[i.level] for i in strip if i.key in keys and i.level in _LEVEL_TO_HEALTH]
    for health in ("err", "warn", "ok"):
        if health in levels:
            return health
    return "off"


def _indicator(strip: Sequence[Indicator], key: str) -> Indicator | None:
    return next((i for i in strip if i.key == key), None)


# --- formatos ------------------------------------------------------------------


def format_age(hours: float) -> str:
    """«12 min», «13 h», «2 d 4 h», «4 d»: una edad corta para un pie de nodo."""
    if hours < 1:
        minutes = int(hours * 60)
        return "menos de 1 min" if minutes < 1 else f"{minutes} min"
    whole = int(hours)
    if whole < 24:
        return f"{whole} h"
    days, rest = divmod(whole, 24)
    return f"{days} d" if rest == 0 else f"{days} d {rest} h"


def _percent_number(value: float) -> str:
    return f"{100 * value:.1f}".replace(".", ",")


def format_percent(value: float) -> str:
    """0,088 -> «8,8 %» (coma decimal, como el resto de la interfaz)."""
    return f"{_percent_number(value)} %"


def _hours_since(moment: datetime, now: datetime) -> float:
    return (now - as_utc(moment)).total_seconds() / 3600


# --- gráficos (SVG con coordenadas calculadas acá) -------------------------------


@dataclass(frozen=True)
class Shape:
    """Un elemento SVG: etiqueta, clase y atributos. La plantilla solo lo escribe."""

    tag: str
    cls: str = ""
    attrs: Mapping[str, Any] = field(default_factory=dict)
    text: str = ""


@dataclass(frozen=True)
class Chart:
    width: int
    height: int
    label: str
    shapes: tuple[Shape, ...]
    # Ocupa el ancho del nodo (con proporción fija) en vez de un tamaño en px.
    fluid: bool = False

    @property
    def view_box(self) -> str:
        return f"0 0 {self.width} {self.height}"


_C = "m3-flow-g-"
_RING_RADIUS = 22
_RING_LENGTH = 2 * math.pi * _RING_RADIUS
_HEALTH_CLASS = {"ok": f"{_C}ok", "warn": f"{_C}warn", "err": f"{_C}err", "off": f"{_C}line"}


def hourly_counts(timestamps: Sequence[datetime], now: datetime) -> list[int]:
    """Cuántas marcas cayeron en cada una de las últimas 24 h.

    El índice 23 es la hora más reciente (de hace 0 a 1 h) y el 0 la más vieja.
    Lo de hace 24 h o más, y lo futuro, queda fuera (mismo corte que `last_24h`).
    """
    counts = [0] * 24
    for moment in timestamps:
        age = _hours_since(moment, now)
        if 0 <= age < 24:
            counts[23 - int(age)] += 1
    return counts


def hourly_chart(counts: Sequence[int]) -> Chart:
    """24 barras de 3 px, una por hora; la última (la hora en curso) más oscura."""
    height = 40
    peak = max(counts) or 1
    shapes = []
    for index, count in enumerate(counts):
        bar = 1 if count == 0 else max(2, round(height * count / peak))
        last = index == len(counts) - 1
        shapes.append(
            Shape(
                "rect",
                f"{_C}bar is-now" if last else f"{_C}bar",
                {"x": index * 4, "y": height - bar, "width": 3, "height": bar},
            )
        )
    return Chart(96, height, "Mensajes por hora, últimas 24 h", tuple(shapes))


# Bordes de los tramos de edad de la cola, en horas: <6 h, 6–24 h, 1–2 d, 2–7 d, >7 d.
AGE_EDGES_HOURS = (6, 24, 48, 168)
_AGE_LOWER_HOURS = (0, *AGE_EDGES_HOURS)
_AGE_LABELS = ("6h", "1d", "2d", "7d", "+")


def age_buckets(ages_hours: Sequence[float]) -> list[int]:
    """Cuántos pendientes caen en cada tramo de edad. El borde pertenece al tramo de arriba."""
    counts = [0] * len(_AGE_LOWER_HOURS)
    for age in ages_hours:
        counts[sum(1 for edge in AGE_EDGES_HOURS if age >= edge)] += 1
    return counts


def age_chart(counts: Sequence[int], *, max_age_days: int) -> Chart:
    """Histograma de edades. Con tope de edad configurado, los tramos que ya
    empiezan en o después del tope van en rojo; sin tope no se pinta ninguno
    (no hay límite que marcar)."""
    peak = max(counts) or 1
    shapes: list[Shape] = []
    for index, count in enumerate(counts):
        bar = 1 if count == 0 else max(2, round(28 * count / peak))
        beyond = max_age_days > 0 and _AGE_LOWER_HOURS[index] >= max_age_days * 24
        x = index * 22
        shapes.append(
            Shape(
                "rect",
                f"{_C}err" if beyond else f"{_C}bar",
                {"x": x, "y": 32 - bar, "width": 18, "height": bar},
            )
        )
        shapes.append(
            Shape("text", f"{_C}label", {"x": x + 9, "y": 43, "text-anchor": "middle"}, _AGE_LABELS[index])
        )
    label = "Edad de la cola: " + ", ".join(str(c) for c in counts)
    return Chart(110, 44, label, tuple(shapes))


def _ring(fraction: float, health: str, center: str, label: str) -> Chart:
    shapes = [Shape("circle", f"{_C}track", {"cx": 28, "cy": 28, "r": _RING_RADIUS, "stroke-width": 6})]
    if fraction > 0:
        arc = f"{min(fraction, 1) * _RING_LENGTH:.1f} {_RING_LENGTH:.1f}"
        shapes.append(
            Shape(
                "circle",
                f"{_C}ring {_HEALTH_CLASS[health]}",
                {
                    "cx": 28,
                    "cy": 28,
                    "r": _RING_RADIUS,
                    "stroke-width": 6,
                    "stroke-dasharray": arc,
                    "transform": "rotate(-90 28 28)",
                },
            )
        )
    shapes.append(Shape("text", f"{_C}center", {"x": 28, "y": 33, "text-anchor": "middle"}, center))
    return Chart(56, 56, label, tuple(shapes))


def _ring_label(hours: float) -> str:
    if hours < 1:
        return f"{int(hours * 60)} min"
    if hours < 48:
        return f"{int(hours)} h"
    return f"{int(hours // 24)} d"


def clock_chart(*, elapsed_hours: float | None, period_hours: int, health: str) -> Chart:
    """El reloj del temporizador: tiempo desde el último disparo sobre su período.

    Una vuelta completa es el período; pasado el período el arco queda cerrado
    y el color (`health`) dice si ya es tarde. Sin registro, solo la pista.
    """
    if elapsed_hours is None:
        return _ring(0, health, "—", "Sin registro del último disparo")
    return _ring(
        elapsed_hours / period_hours,
        health,
        _ring_label(elapsed_hours),
        f"Último disparo hace {format_age(elapsed_hours)}, de un período de {period_hours} h",
    )


def progress_chart(done: int, total: int, *, label: str) -> Chart:
    """Anillo de «cuánto del n objetivo llevamos»: el centro dice el n."""
    return _ring(done / total if total else 0, "off", str(done), label)


@dataclass(frozen=True)
class BulletRow:
    """Una fila de la barra de error auditado contra su meta."""

    glyph: str
    rate: float | None
    target: float
    exceeded: bool


_BULLET_TRACK_X = 22
_BULLET_TRACK_W = 148
# El ancho completo de la pista vale este múltiplo de la meta: es la escala del
# dibujo, no una política. Deja la marca de la meta a un cuarto del ancho.
_BULLET_SCALE = 4


def bullet_chart(rows: Sequence[BulletRow], *, label: str) -> Chart:
    shapes: list[Shape] = []
    for index, row in enumerate(rows):
        top = 20 * index
        shapes.append(Shape("text", f"{_C}label", {"x": 0, "y": top + 11}, row.glyph))
        shapes.append(
            Shape(
                "rect",
                f"{_C}cell",
                {"x": _BULLET_TRACK_X, "y": top + 3, "width": _BULLET_TRACK_W, "height": 10, "rx": 2},
            )
        )
        if row.rate is not None:
            full = row.target * _BULLET_SCALE
            width = round(min(row.rate / full, 1) * _BULLET_TRACK_W, 1)
            shapes.append(
                Shape(
                    "rect",
                    f"{_C}bar {_C}err" if row.exceeded else f"{_C}bar {_C}ok",
                    {"x": _BULLET_TRACK_X, "y": top + 3, "width": width, "height": 10, "rx": 2},
                )
            )
        tick = round(_BULLET_TRACK_X + _BULLET_TRACK_W / _BULLET_SCALE, 1)
        shapes.append(Shape("line", f"{_C}meta", {"x1": tick, "y1": top, "x2": tick, "y2": top + 16}))
    return Chart(170, 20 * len(rows) - 4, label, tuple(shapes), fluid=True)


def coverage_chart(cells: Sequence[CoverageCell]) -> tuple[Chart | None, int]:
    """Cuadrícula producto (columnas) × provincia (filas); encendida si hay filas.

    Devuelve el gráfico (o `None` sin celdas) y cuántas celdas tienen dato.
    """
    if not cells:
        return None, 0
    products = list(dict.fromkeys(c.product_name for c in cells))
    provinces = list(dict.fromkeys(c.province_name for c in cells))
    rows = {(c.product_name, c.province_name): c.rows for c in cells}
    shapes: list[Shape] = []
    with_data = 0
    for col, product in enumerate(products):
        for row, province in enumerate(provinces):
            on = rows.get((product, province), 0) > 0
            with_data += on
            shapes.append(
                Shape(
                    "rect",
                    f"{_C}cell is-on" if on else f"{_C}cell",
                    {"x": col * 16, "y": row * 16, "width": 14, "height": 14, "rx": 2},
                )
            )
    label = f"{with_data} de {len(products) * len(provinces)} celdas producto × provincia con dato"
    return Chart(len(products) * 16 - 2, len(provinces) * 16 - 2, label, tuple(shapes)), with_data


# --- el nodo -------------------------------------------------------------------


@dataclass(frozen=True)
class Chip:
    icon: str
    text: str
    title: str = ""


@dataclass(frozen=True)
class Step:
    count: int
    on: bool
    title: str


@dataclass(frozen=True)
class FlowNode:
    key: str
    name: str
    icon: str
    health: str
    big: str | None = None
    unit: str = ""
    chart: Chart | None = None
    chips: tuple[Chip, ...] = ()
    steps: tuple[Step, ...] = ()
    foot_icon: str = "schedule"
    foot_text: str = ""

    @property
    def health_label(self) -> str:
        return HEALTH_LABELS[self.health]

    @property
    def foot_level(self) -> str:
        return self.health if self.health in ("warn", "err") else ""


@dataclass(frozen=True)
class Wire:
    key: str
    d: str
    flow: bool


@dataclass(frozen=True)
class Pill:
    key: str
    label: str
    x: float
    y: int
    off: bool


@dataclass(frozen=True)
class FlowView:
    nodes: tuple[FlowNode, ...]
    wires: tuple[Wire, ...]
    pills: tuple[Pill, ...]


_NO_24H = Last24h(entered=0, curated=0, discarded=0, pending=0, oldest_pending_hours=None)


@dataclass(frozen=True)
class FlowInputs:
    """Todo lo que el lienzo necesita, ya leído. Los defaults son «sin dato»."""

    now: datetime
    # Ingesta: (momento de entrada, source_type) de lo que entró en las últimas 24 h.
    intake: Sequence[tuple[datetime, str]] = ()
    last_ingest_at: datetime | None = None
    last_24h: Last24h = _NO_24H
    strip: Sequence[Indicator] = ()
    prefill_run: PrefillRun | None = None
    lane0_enabled_types: frozenset[str] = frozenset()
    lane0_bounds: Mapping[str, lane0.Bound] = field(default_factory=dict)
    lane1_enabled: bool = False
    lane1_audited: int = 0
    lane1_errors: int = 0
    lane1_brake_on: bool = False
    # De lo que entró en 24 h: cuántos anuncios resolvieron solos el carril 0
    # (descarte) y el carril 1 (aceptación).
    cohort_lane0: int = 0
    cohort_lane1: int = 0
    # Cola: cuándo entró cada pendiente.
    queue_created: Sequence[datetime] = ()
    queue_max_age_days: int = 0
    price_rows_total: int = 0
    price_rows_human_24h: int = 0
    last_price_row_at: datetime | None = None
    coverage: Sequence[CoverageCell] = ()
    # Publicaciones de marketing por estado (`draft`, `approved`, `published`).
    marketing: Mapping[str, int] = field(default_factory=dict)
    last_batch_at: datetime | None = None


def _ingesta(i: FlowInputs) -> FlowNode:
    # Salud: la peor entre WhatsApp y «última captura» de la franja
    # (`waha_indicator`, `intake_indicator`, que ya incluye el silencio por canal).
    whatsapp = sum(1 for _, source in i.intake if source == SourceType.WHATSAPP_CHANNEL)
    return FlowNode(
        key="in",
        name="Ingesta",
        icon="forum",
        health=_health_from_strip(i.strip, "waha", "intake"),
        big=str(len(i.intake)),
        unit="24 h",
        chart=hourly_chart(hourly_counts([m for m, _ in i.intake], i.now)),
        chips=(
            Chip("forum", str(whatsapp), "Por WhatsApp"),
            Chip("inbox", str(len(i.intake) - whatsapp), "Por la bandeja o pegado a mano"),
        ),
        foot_text=(
            "sin ingestas"
            if i.last_ingest_at is None
            else f"hace {format_age(_hours_since(i.last_ingest_at, i.now))}"
        ),
    )


def _pre_calculo(i: FlowInputs) -> FlowNode:
    # Salud: `prefill_indicator` (falló = atrasado; sin correr hace más de
    # `PREFILL_INTERVAL_HOURS` × 2 = mirar). La cifra es la tarjeta «Últimas 24 h».
    health = _health_from_strip(i.strip, "prefill")
    elapsed = None if i.prefill_run is None else _hours_since(i.prefill_run.finished_at, i.now)
    indicator = _indicator(i.strip, "prefill")
    chips: tuple[Chip, ...] = ()
    if i.last_24h.oldest_pending_hours is not None:
        chips = (
            Chip(
                "schedule",
                f"más viejo {format_age(i.last_24h.oldest_pending_hours)}",
                "El pendiente más viejo de las últimas 24 h",
            ),
        )
    return FlowNode(
        key="pre",
        name="Pre-cálculo",
        icon="schedule",
        health=health,
        big=str(i.last_24h.pending),
        unit="sin procesar",
        chart=clock_chart(elapsed_hours=elapsed, period_hours=PREFILL_INTERVAL_HOURS, health=health),
        chips=chips,
        foot_icon="error" if health == "err" else "schedule",
        foot_text=indicator.value if indicator is not None else "sin registro",
    )


def _carril_0(i: FlowInputs) -> FlowNode:
    # Salud: apagado si ningún tipo está encendido; atrasado si la cota de
    # Clopper-Pearson de un tipo encendido pasa su meta (`Bound.exceeded`, la
    # misma comparación que nombra el interruptor a apagar en la auditoría).
    enabled = [t for t in lane0.TYPES if t in i.lane0_enabled_types]
    bounds = {t: i.lane0_bounds.get(t) or lane0.Bound(t, 0, 0) for t in lane0.TYPES}
    if not enabled:
        health = "off"
    elif any(bounds[t].exceeded for t in enabled):
        health = "err"
    else:
        health = "ok"
    glyphs = {lane0.TEXT: "Aa", lane0.IMAGE: "▣"}
    names = {lane0.TEXT: "texto", lane0.IMAGE: "imagen"}
    rows = [BulletRow(glyphs[t], bounds[t].rate, lane0.TARGETS[t], bounds[t].exceeded) for t in lane0.TYPES]
    parts = [
        f"{names[t]} {format_percent(r)} (meta {format_percent(lane0.TARGETS[t])})"
        if (r := bounds[t].rate) is not None
        else f"{names[t]} sin auditar (meta {format_percent(lane0.TARGETS[t])})"
        for t in lane0.TYPES
    ]
    return FlowNode(
        key="c0",
        name="Carril 0",
        icon="filter_alt_off",
        health=health,
        big=str(i.cohort_lane0),
        unit="descartes",
        chart=bullet_chart(rows, label="Falsos descartes auditados: " + "; ".join(parts)),
        foot_icon="rule",
        foot_text="auditados " + " · ".join(str(bounds[t].n) for t in lane0.TYPES),
    )


def _carril_1(i: FlowInputs) -> FlowNode:
    # Salud: apagado si `SAIMC_LANE1_ENABLED` no está; si está, atrasado con el
    # freno puesto (`lane1.brake_for`) y mirar si la franja avisa auditoría
    # atrasada (`lane1_audit_indicator`). El anillo mide lo auditado contra
    # `lane1.AUDIT_N0`, el n con que termina el arranque.
    if not i.lane1_enabled:
        health = "off"
    elif i.lane1_brake_on:
        health = "err"
    else:
        health = _health_from_strip(i.strip, "lane1_audit")
        health = "warn" if health == "warn" else "ok"
    audited = i.lane1_audited
    return FlowNode(
        key="c1",
        name="Carril 1",
        icon="bolt",
        health=health,
        big=_percent_number(i.lane1_errors / audited) if audited else "—",
        unit="% error" if audited else "sin auditar",
        chart=progress_chart(
            audited,
            lane1.AUDIT_N0,
            label=f"Auditados {audited} de {lane1.AUDIT_N0} para dejar el arranque",
        ),
        foot_icon="toggle_on" if i.lane1_enabled else "toggle_off",
        foot_text=f"{'encendido' if i.lane1_enabled else 'apagado'} · n {audited}/{lane1.AUDIT_N0}",
    )


def _cola(i: FlowInputs) -> FlowNode:
    # Salud: la tarjeta «Últimas 24 h» de la franja (`last_24h_indicator`:
    # mirar si el pendiente más viejo de la jornada espera 12 h o más). Si hay
    # tope de edad (`queue_max_age_days` > 0) y queda un pendiente más viejo que
    # el tope, atrasado: el vencimiento automático no lo sacó.
    ages = [_hours_since(created, i.now) for created in i.queue_created]
    oldest = max(ages) if ages else None
    health = _health_from_strip(i.strip, "last_24h")
    if i.queue_max_age_days > 0 and oldest is not None and oldest > i.queue_max_age_days * 24:
        health = "err"
    return FlowNode(
        key="c2",
        name="Cola",
        icon="fact_check",
        health=health,
        big=str(len(ages)),
        chart=age_chart(age_buckets(ages), max_age_days=i.queue_max_age_days),
        foot_icon="warning" if health in ("warn", "err") else "schedule",
        foot_text="sin pendientes" if oldest is None else f"más viejo {format_age(oldest)}",
    )


def _tablero(i: FlowInputs) -> FlowNode:
    # Salud: la tarjeta «Tasa de cambio» de la franja (`rate_indicator`): el
    # tablero convierte con la tasa, y una tasa vieja lo deja desactualizado.
    chart, _ = coverage_chart(i.coverage)
    return FlowNode(
        key="pub",
        name="Tablero",
        icon="show_chart",
        health=_health_from_strip(i.strip, "rate"),
        big=str(i.price_rows_total),
        unit="filas",
        chart=chart,
        foot_text="sin filas"
        if i.last_price_row_at is None
        else f"hace {format_age(_hours_since(i.last_price_row_at, i.now))}",
    )


def _marketing(i: FlowInputs) -> FlowNode:
    # Salud: sin regla en la franja ni umbral configurado. Encendido (`ok`) si
    # hay publicaciones en curso, apagado si no hay ninguna.
    draft, approved, published = (i.marketing.get(k, 0) for k in ("draft", "approved", "published"))
    return FlowNode(
        key="mkt",
        name="Marketing",
        icon="campaign",
        health="ok" if draft + approved + published else "off",
        steps=(
            Step(draft, draft > 0, "borradores"),
            Step(approved, approved > 0, "aprobadas"),
            Step(published, published > 0, "publicadas"),
        ),
        foot_text="sin lotes"
        if i.last_batch_at is None
        else f"lote hace {format_age(_hours_since(i.last_batch_at, i.now))}",
    )


# Geometría de los cables de escritorio, la del prototipo: lienzo de 1000 × 560,
# nodos de 16 % de ancho y 160 px de alto, cable a media altura. Las pastillas
# van en HTML (x en %, y en px) para que el texto no se estire con el SVG.
_WIRES = (
    ("in-pre", "M170 280 C190 280 190 280 210 280"),
    ("pre-c0", "M370 280 C400 280 400 104 430 104"),
    ("pre-c1", "M370 280 H430"),
    ("pre-c2", "M370 280 C400 280 400 456 430 456"),
    ("c0-sink", "M590 104 H640"),
    ("c1-pub", "M590 280 C615 280 615 368 640 368"),
    ("c2-pub", "M590 456 C615 456 615 368 640 368"),
    ("pub-mkt", "M800 368 H830"),
)
_PILLS = (
    ("in-pre", 19, 280),
    ("pre-c0", 40, 180),
    ("pre-c1", 40, 280),
    ("pre-c2", 40, 380),
    ("c2-pub", 61.5, 420),
)


def build_view(i: FlowInputs) -> FlowView:
    """Los siete nodos, los cables y las pastillas con el caudal de 24 h."""
    nodes = (
        _ingesta(i),
        _pre_calculo(i),
        _carril_0(i),
        _carril_1(i),
        _cola(i),
        _tablero(i),
        _marketing(i),
    )
    entered = len(i.intake)
    # Una sola cohorte —lo que entró en 24 h—: lo que no resolvieron solos los
    # carriles 0 y 1 llega a la cola. Así los tres cables suman la entrada.
    to_queue = max(entered - i.cohort_lane0 - i.cohort_lane1, 0)
    lane1_flow = i.lane1_enabled and i.cohort_lane1 > 0
    marketing_active = sum(i.marketing.get(k, 0) for k in ("draft", "approved", "published")) > 0
    flows = {
        "in-pre": entered > 0,
        "pre-c0": i.cohort_lane0 > 0,
        "pre-c1": lane1_flow,
        "pre-c2": to_queue > 0,
        "c0-sink": i.cohort_lane0 > 0,
        "c1-pub": lane1_flow,
        "c2-pub": i.price_rows_human_24h > 0,
        "pub-mkt": marketing_active,
    }
    labels = {
        "in-pre": entered,
        "pre-c0": i.cohort_lane0,
        "pre-c1": i.cohort_lane1,
        "pre-c2": to_queue,
        "c2-pub": i.price_rows_human_24h,
    }
    return FlowView(
        nodes=nodes,
        wires=tuple(Wire(key, d, flows[key]) for key, d in _WIRES),
        pills=tuple(Pill(key, str(labels[key]), x, y, not flows[key]) for key, x, y in _PILLS),
    )


# --- lectura -------------------------------------------------------------------


def load_inputs(
    db: Session,
    settings: Settings,
    *,
    strip: Sequence[Indicator],
    prefill_run: PrefillRun | None,
    now: datetime | None = None,
) -> FlowInputs:
    """Junta lo que el sistema ya calcula. La franja y la última corrida del
    pre-cálculo llegan ya leídas (las trae `_shell_context`); el resto son
    consultas de solo lectura sobre tablas existentes."""
    moment = now or utc_now()
    since = moment - timedelta(hours=24)

    intake = [
        (created, str(source))
        for created, source in db.execute(
            select(RawIngestion.created_at, RawIngestion.source_type).where(RawIngestion.created_at > since)
        )
    ]
    last_ingest = db.execute(select(func.max(RawIngestion.captured_at))).scalar_one()

    # Cohorte de 24 h: qué anuncios que entraron en la ventana resolvió solo un
    # carril. Un descarte automático que es un vencimiento de la cola
    # (`queue_expiry`) no es del carril 0.
    auto_discarded: set[object] = set()
    auto_accepted: set[object] = set()
    auto_events = db.execute(
        select(RawIngestion.id, CurationEvent.action, CurationEvent.detail)
        .join(CurationEvent, CurationEvent.ingestion_id == RawIngestion.id)
        .where(RawIngestion.created_at > since, CurationEvent.mode == CurationMode.AUTO)
    )
    for ingestion_id, action, detail in auto_events:
        if isinstance(detail, dict) and EXPIRED_DETAIL_KEY in detail:
            continue
        if action == CurationAction.DISCARDED:
            auto_discarded.add(ingestion_id)
        elif action == CurationAction.CURATED:
            auto_accepted.add(ingestion_id)

    lane1_summary = lane1.summary(db, settings)
    marketing = dict(
        db.execute(select(MarketingPost.status, func.count()).group_by(MarketingPost.status)).all()
    )

    return FlowInputs(
        now=moment,
        intake=intake,
        last_ingest_at=last_ingest,
        last_24h=last_24h(db, now=moment),
        strip=strip,
        prefill_run=prefill_run,
        lane0_enabled_types=lane0.enabled_types(settings),
        lane0_bounds=lane0.audit_bounds(db, today=moment.date()),
        lane1_enabled=lane1_summary.enabled,
        lane1_audited=lane1_summary.state.audited,
        lane1_errors=lane1_summary.state.errors,
        lane1_brake_on=lane1_summary.brake.on,
        cohort_lane0=len(auto_discarded),
        cohort_lane1=len(auto_accepted),
        queue_created=list(
            db.execute(
                select(RawIngestion.created_at).where(
                    RawIngestion.processing_status == ProcessingStatus.PENDING
                )
            ).scalars()
        ),
        queue_max_age_days=settings.queue_max_age_days,
        price_rows_total=db.execute(select(func.count()).select_from(PriceHistory)).scalar_one(),
        price_rows_human_24h=db.execute(
            select(func.count())
            .select_from(PriceHistory)
            .where(PriceHistory.created_at > since, PriceHistory.provenance == Provenance.HUMAN)
        ).scalar_one(),
        last_price_row_at=db.execute(select(func.max(PriceHistory.created_at))).scalar_one(),
        coverage=compute_coverage(db),
        marketing=marketing,
        last_batch_at=db.execute(
            select(func.max(MarketingPost.created_at)).where(MarketingPost.origin == "batch")
        ).scalar_one(),
    )
