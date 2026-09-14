"""Constructores de los gráficos del rediseño de la home pública.

Mismo reparto que `series_view`, `card_view` y `map_view`: acá se agrupa, se
resume y se calculan las coordenadas; la plantilla solo escupe el SVG. Es el
invariante 2 del ADR 001: si el número se arma en Jinja, no se puede testear.

Las formas salen del muestrario que el operador aprobó el 2026-09-10
(docs/decisions.md): cifra con su muestra y rango por período para el panel de
producto; las demás llegan en los slices siguientes.

Dos reglas atraviesan el módulo:

- **Escala robusta, sin borrar.** Un precio mal cargado —una caja tomada como
  libra— queda a ×40 o ×700 de su celda y aplasta cualquier eje lineal. Cada
  serie se escala con los precios que caen dentro de un cerco robusto en escala
  logarítmica; los de afuera no se borran ni se promedian: se cuentan y se
  marcan en el borde. No es un umbral de publicación de los que §12.6 prohíbe:
  no decide qué se publica ni qué es válido, solo qué entra en el eje.
- **Nada que nadie observó.** Rangos y medianas por período, con los períodos
  sin anuncios a la vista; ninguna línea entre ellos (mismo criterio que
  `series_view`).
"""

from __future__ import annotations

import math
import statistics
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from saimc.cells import RetailObservation
from saimc.formats import format_label as _etiqueta_de_formato
from saimc.web.card_view import format_price

#: Cerco robusto: un precio queda fuera de escala si se aleja de la mitad
#: central de su celda más de `FENCE_IQR` anchos de caja, medidos en escala
#: logarítmica porque el error de carga es multiplicativo (×40, ×99, ×700), y
#: nunca con un margen menor a ×`FENCE_MIN_FACTOR`: con precios casi iguales la
#: caja no tiene ancho y sin ese piso cualquier variación normal saldría del eje.
#: Es de dibujo, no de publicación (ver docstring del módulo).
FENCE_IQR = 3
FENCE_MIN_FACTOR = 2

#: Lienzo del mini gráfico de rango del panel, en unidades de `viewBox`. El SVG
#: escala al ancho del panel: esto es proporción, no tamaño.
WIDTH = 360.0
HEIGHT = 132.0
PAD_LEFT = 42.0
PAD_RIGHT = 8.0
PAD_TOP = 10.0
PAD_BOTTOM = 22.0


def _quantile(ordered: Sequence[float], q: float) -> float:
    """Cuantil con interpolación lineal (el método por defecto de numpy)."""
    i = (len(ordered) - 1) * q
    lo, hi = math.floor(i), math.ceil(i)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (i - lo)


def robust_fence(prices: Iterable[Decimal]) -> tuple[float, float]:
    """Límites inferior y superior del cerco robusto de un conjunto de precios."""
    logs = sorted(math.log(float(p)) for p in prices if p > 0)
    if not logs:
        return 0.0, math.inf
    q1, q3 = _quantile(logs, 0.25), _quantile(logs, 0.75)
    margin = max(FENCE_IQR * (q3 - q1), math.log(FENCE_MIN_FACTOR))
    return math.exp(q1 - margin), math.exp(q3 + margin)


def split_outliers(
    observations: Sequence[RetailObservation],
) -> tuple[list[RetailObservation], list[RetailObservation]]:
    """`(dentro, fuera)` del cerco robusto calculado sobre las propias filas."""
    if not observations:
        return [], []
    lo, hi = robust_fence(o.unit_price for o in observations)
    dentro = [o for o in observations if lo <= float(o.unit_price) <= hi]
    fuera = [o for o in observations if not lo <= float(o.unit_price) <= hi]
    return dentro, fuera


def bin_days(window_days: int) -> int:
    """Tamaño del período del rango: semanas para 30 y 90 días, días para 7.

    Con una ventana de 7 días, una sola barra semanal no mostraría ningún
    movimiento. No es un umbral: es el grano de lectura de cada ventana.
    """
    return 7 if window_days >= 28 else 1


def _nice_ticks(lo: float, hi: float, count: int = 3) -> list[float]:
    """Marcas de eje en números redondos (1, 2 o 5 × 10ⁿ) dentro de `lo..hi`."""
    span = hi - lo
    if span <= 0:
        return [lo]
    raw = span / count
    magnitude = 10 ** math.floor(math.log10(raw))
    err = raw / magnitude
    step = magnitude * (1 if err < 1.5 else 2 if err < 3 else 5 if err < 7 else 10)
    ticks: list[float] = []
    value = math.ceil(lo / step) * step
    while value <= hi + 1e-9:
        ticks.append(value)
        value += step
    return ticks


_Key = tuple[str, str, str, str, str | None]


def observation_format_label(o: RetailObservation) -> str | None:
    """La etiqueta del formato de una observación, o `None` sin formato
    establecido — misma regla que `_FormatPriced.format_label` en `cells.py`,
    repetida acá porque `RetailObservation` no trae la propiedad (es la fila
    ya aplanada para series, no una celda agregada)."""
    if o.format_packaging is None or o.format_quantity is None or o.format_unit is None:
        return None
    return _etiqueta_de_formato(o.format_packaging, o.format_quantity, o.format_unit)


def _group(observations: Iterable[RetailObservation]) -> dict[_Key, list[RetailObservation]]:
    """Por producto × provincia × moneda × unidad × formato (2026-09-14: el
    formato se sumó al eje de agrupado — antes una botella y una garrafa del
    mismo producto se fundían en una sola cifra/serie, perdiendo justo la
    pregunta «¿cómo varía el precio según el envase?» que `/control` ya
    contestaba desde la matriz). Descarta lo mismo que `series_view` y por el
    mismo motivo: sin provincia (la vista es por provincia) y `unconverted`
    (mezclarla afirmaría una equivalencia que nadie calculó)."""
    grupos: dict[_Key, list[RetailObservation]] = {}
    for o in observations:
        if o.province_name is None or o.unconverted:
            continue
        clave = (o.product_name, o.province_name, o.currency, o.unit, observation_format_label(o))
        grupos.setdefault(clave, []).append(o)
    return grupos


def _short_day(d: date) -> str:
    return f"{d:%d/%m}"


# --- rango por período ------------------------------------------------------


@dataclass(frozen=True)
class RangeBin:
    """Un período (semana o día) de la ventana, con o sin anuncios."""

    start: date
    end: date
    #: Anuncios del período, fuera de escala incluidos: la muestra es la que es.
    count: int
    minimum: Decimal | None
    median: Decimal | None
    maximum: Decimal | None
    x: float
    y_min: float | None
    y_median: float | None
    y_max: float | None

    @property
    def has_range(self) -> bool:
        return self.minimum is not None

    @property
    def start_text(self) -> str:
        return _short_day(self.start)

    @property
    def minimum_text(self) -> str:
        return "" if self.minimum is None else format_price(self.minimum)

    @property
    def median_text(self) -> str:
        return "" if self.median is None else format_price(self.median)

    @property
    def maximum_text(self) -> str:
        return "" if self.maximum is None else format_price(self.maximum)


@dataclass(frozen=True)
class OutlierMark:
    """Un precio fuera de escala, marcado en el borde de su período."""

    x: float
    above: bool
    price: Decimal
    day: date

    @property
    def price_text(self) -> str:
        return format_price(self.price)

    @property
    def day_text(self) -> str:
        return _short_day(self.day)


@dataclass(frozen=True)
class Tick:
    y: float
    label: str


@dataclass(frozen=True)
class RangeSeries:
    """El rango por período de una celda producto × provincia × formato."""

    product_name: str
    province_name: str
    currency: str
    unit: str
    bins: tuple[RangeBin, ...]
    outliers: tuple[OutlierMark, ...]
    ticks: tuple[Tick, ...]
    bar_width: float
    bin_days: int
    #: Formato de venta de la celda («bolsa de 25 kg»), o `None` sin formato
    #: establecido (2026-09-14, ver `_group`).
    format_label: str | None = None

    @property
    def count(self) -> int:
        return sum(b.count for b in self.bins)

    @property
    def outlier_count(self) -> int:
        return len(self.outliers)

    @property
    def viewbox(self) -> str:
        return f"0 0 {WIDTH:g} {HEIGHT:g}"

    @property
    def baseline_y(self) -> float:
        return HEIGHT - PAD_BOTTOM

    @property
    def x_left(self) -> float:
        return PAD_LEFT

    @property
    def x_right(self) -> float:
        return WIDTH - PAD_RIGHT

    @property
    def period_name(self) -> str:
        return "semana" if self.bin_days == 7 else "día"

    @property
    def first_label(self) -> str:
        return self.bins[0].start_text

    @property
    def last_label(self) -> str:
        return _short_day(self.bins[-1].end)


def build_range_series(
    observations: Sequence[RetailObservation], *, window_days: int, hoy: date
) -> list[RangeSeries]:
    """Una serie de rango por período por celda producto × provincia.

    Los períodos se cuentan hacia atrás desde `hoy` —el último siempre termina
    hoy—, así que el más reciente es completo y el más viejo puede quedar corto.
    Cada serie tiene su propia escala vertical, como en `series_view`: responde
    «¿cómo se movió ESTE producto acá?», y una escala común aplastaría las
    celdas baratas contra el borde.
    """
    size = bin_days(window_days)
    n_bins = math.ceil((window_days + 1) / size)
    plot_w = WIDTH - PAD_LEFT - PAD_RIGHT
    plot_h = HEIGHT - PAD_TOP - PAD_BOTTOM
    slot = plot_w / n_bins

    def index(day: date) -> int:
        return (hoy - day).days // size  # 0 = el período más reciente

    series: list[RangeSeries] = []
    for (product, province, currency, unit, formato), rows in _group(observations).items():
        rows = [r for r in rows if 0 <= index(r.day) < n_bins]
        dentro, fuera = split_outliers(rows)
        if not dentro:
            continue

        prices = [r.unit_price for r in dentro]
        top_price = max(prices)
        lo, hi = float(min(prices)), float(top_price)
        if lo == hi:
            lo, hi = lo * 0.9, hi * 1.1
        else:
            pad = (hi - lo) * 0.08
            lo, hi = max(0.0, lo - pad), hi + pad

        def to_y(value: Decimal | float, *, _lo: float = lo, _hi: float = hi) -> float:
            return round(PAD_TOP + (1 - (float(value) - _lo) / (_hi - _lo)) * plot_h, 1)

        por_periodo: dict[int, list[Decimal]] = {}
        cuenta: dict[int, int] = {}
        for r in rows:
            cuenta[index(r.day)] = cuenta.get(index(r.day), 0) + 1
        for r in dentro:
            por_periodo.setdefault(index(r.day), []).append(r.unit_price)

        bins: list[RangeBin] = []
        for j in range(n_bins):
            i = n_bins - 1 - j
            end = hoy - timedelta(days=i * size)
            start = end - timedelta(days=size - 1)
            x = round(PAD_LEFT + (j + 0.5) * slot, 1)
            vals = sorted(por_periodo.get(i, []))
            if vals:
                mn, md, mx = vals[0], statistics.median(vals), vals[-1]
                bins.append(RangeBin(start, end, cuenta.get(i, 0), mn, md, mx, x, to_y(mn), to_y(md), to_y(mx)))
            else:
                bins.append(RangeBin(start, end, cuenta.get(i, 0), None, None, None, x, None, None, None))

        marcas = tuple(
            OutlierMark(
                x=bins[n_bins - 1 - index(r.day)].x,
                above=r.unit_price > top_price,
                price=r.unit_price,
                day=r.day,
            )
            for r in sorted(fuera, key=lambda r: r.day)
        )
        ticks = tuple(
            Tick(y=to_y(v), label=format_price(Decimal(str(round(v, 2))))) for v in _nice_ticks(lo, hi)
        )
        series.append(
            RangeSeries(
                product_name=product,
                province_name=province,
                currency=currency,
                unit=unit,
                bins=tuple(bins),
                outliers=marcas,
                ticks=ticks,
                bar_width=round(min(slot * 0.45, 16.0), 1),
                bin_days=size,
                format_label=formato,
            )
        )

    series.sort(key=lambda s: (-s.count, s.product_name, s.province_name))
    return series


# --- cifra con su muestra ---------------------------------------------------


@dataclass(frozen=True)
class Headline:
    """La cifra de una celda en la ventana, con su muestra y su comparación.

    §9.0: nunca un valor puntual suelto. La mediana va con el rango y la
    cantidad de anuncios al lado, y la comparación con el período anterior del
    mismo largo es un número —sin flecha ni color de alarma: §12.6 no deja
    inventar el umbral que diría cuándo un cambio «importa»—.
    """

    product_name: str
    province_name: str
    currency: str
    unit: str
    count: int
    outliers: int
    median: Decimal
    minimum: Decimal
    maximum: Decimal
    previous_median: Decimal | None
    previous_count: int
    last_day: date
    #: Formato de venta de la celda («bolsa de 25 kg»), o `None` sin formato
    #: establecido (2026-09-14, ver `price_charts._group`).
    format_label: str | None = None

    @property
    def is_single_value(self) -> bool:
        return self.minimum == self.maximum

    @property
    def change_pct(self) -> int | None:
        if self.previous_median is None or self.previous_median == 0:
            return None
        return round((float(self.median) / float(self.previous_median) - 1) * 100)

    @property
    def change_text(self) -> str:
        pct = self.change_pct
        if pct is None:
            return ""
        if pct > 0:
            return f"+{pct}%"
        if pct < 0:
            return f"−{abs(pct)}%"
        return "0%"

    @property
    def median_text(self) -> str:
        return format_price(self.median)

    @property
    def minimum_text(self) -> str:
        return format_price(self.minimum)

    @property
    def maximum_text(self) -> str:
        return format_price(self.maximum)

    @property
    def previous_median_text(self) -> str:
        return "" if self.previous_median is None else format_price(self.previous_median)

    @property
    def last_day_text(self) -> str:
        return _short_day(self.last_day)


def build_headlines(
    current: Sequence[RetailObservation], previous: Sequence[RetailObservation]
) -> list[Headline]:
    """Una cifra por celda con anuncios en la ventana actual.

    `previous` son las observaciones del período anterior del mismo largo; la
    comparación usa la misma celda y el mismo cerco, cada período con el suyo.
    """
    anteriores = _group(previous)
    cifras: list[Headline] = []
    for key, rows in _group(current).items():
        dentro, fuera = split_outliers(rows)
        if not dentro:
            continue
        prices = sorted(r.unit_price for r in dentro)
        prev_rows = anteriores.get(key, [])
        prev_dentro, _ = split_outliers(prev_rows)
        product, province, currency, unit, formato = key
        cifras.append(
            Headline(
                product_name=product,
                province_name=province,
                currency=currency,
                unit=unit,
                count=len(rows),
                outliers=len(fuera),
                median=statistics.median(prices),
                minimum=prices[0],
                maximum=prices[-1],
                previous_median=(
                    statistics.median([r.unit_price for r in prev_dentro]) if prev_dentro else None
                ),
                previous_count=len(prev_rows),
                format_label=formato,
                last_day=max(r.day for r in rows),
            )
        )
    cifras.sort(key=lambda c: (-c.count, c.product_name, c.province_name))
    return cifras


# --- provincias lado a lado -------------------------------------------------

#: Lienzo de la comparación de provincias: una fila por provincia, nombre a la
#: izquierda, cantidad de anuncios a la derecha y el eje de precio abajo.
COMPARE_WIDTH = 360.0
COMPARE_ROW = 34.0
COMPARE_LEFT = 108.0
COMPARE_RIGHT = 74.0
COMPARE_TOP = 8.0
COMPARE_BOTTOM = 22.0


@dataclass(frozen=True)
class CompareRow:
    """Una provincia en la comparación: rango, mitad central, mediana y muestra."""

    province_name: str
    count: int
    outliers: int
    minimum: Decimal
    q1: Decimal
    median: Decimal
    q3: Decimal
    maximum: Decimal
    x_min: float
    x_q1: float
    x_median: float
    x_q3: float
    x_max: float
    y: float

    @property
    def minimum_text(self) -> str:
        return format_price(self.minimum)

    @property
    def q1_text(self) -> str:
        return format_price(self.q1)

    @property
    def median_text(self) -> str:
        return format_price(self.median)

    @property
    def q3_text(self) -> str:
        return format_price(self.q3)

    @property
    def maximum_text(self) -> str:
        return format_price(self.maximum)

    @property
    def box_width(self) -> float:
        return max(round(self.x_q3 - self.x_q1, 1), 2.0)


@dataclass(frozen=True)
class XTick:
    x: float
    label: str


@dataclass(frozen=True)
class ProvinceComparison:
    """Un producto (en una moneda y una unidad) con sus provincias en un eje común."""

    product_name: str
    currency: str
    unit: str
    rows: tuple[CompareRow, ...]
    ticks: tuple[XTick, ...]

    @property
    def height(self) -> float:
        return COMPARE_TOP + len(self.rows) * COMPARE_ROW + COMPARE_BOTTOM

    @property
    def viewbox(self) -> str:
        return f"0 0 {COMPARE_WIDTH:g} {self.height:g}"

    @property
    def x_left(self) -> float:
        return COMPARE_LEFT

    @property
    def x_right(self) -> float:
        return COMPARE_WIDTH - COMPARE_RIGHT

    @property
    def top(self) -> float:
        return COMPARE_TOP

    @property
    def axis_y(self) -> float:
        return self.height - COMPARE_BOTTOM


def _as_price(value: float) -> Decimal:
    return Decimal(str(round(value, 2)))


def build_province_comparison(
    observations: Sequence[RetailObservation],
) -> list[ProvinceComparison]:
    """Una comparación por producto × moneda × unidad, con una fila por provincia.

    Cada provincia tiene su propio cerco robusto (es su celda la que dice qué
    precio es imposible), pero todas comparten el eje: la pregunta es cuál está
    más cara, y eso solo se lee en una escala común. La más cara —por mediana—
    va primero. Unidades distintas no comparten eje: comparar libra con
    kilogramo afirmaría una equivalencia que nadie calculó.
    """
    # Acá se vuelve a fundir el formato (2026-09-14): la pregunta de esta
    # vista es «¿dónde está más cara?», entre provincias — no «¿cómo varía
    # según el envase?», que ya contesta el panel de producto. `unit_price`
    # ya es comparable entre formatos, así que blindar el resultado de antes
    # del eje de formato es correcto acá, no una regresión.
    por_producto: dict[tuple[str, str, str], dict[str, list[RetailObservation]]] = {}
    for (product, province, currency, unit, _formato), rows in _group(observations).items():
        por_producto.setdefault((product, currency, unit), {}).setdefault(province, []).extend(rows)

    plot_w = COMPARE_WIDTH - COMPARE_LEFT - COMPARE_RIGHT
    comparaciones: list[ProvinceComparison] = []
    for (product, currency, unit), provincias in por_producto.items():
        resumen = []
        for province, rows in provincias.items():
            dentro, fuera = split_outliers(rows)
            if not dentro:
                continue
            vals = sorted(float(r.unit_price) for r in dentro)
            resumen.append((province, rows, fuera, dentro, vals))
        if not resumen:
            continue

        lo = min(v[4][0] for v in resumen)
        hi = max(v[4][-1] for v in resumen)
        if lo == hi:
            lo, hi = lo * 0.9, hi * 1.1
        else:
            pad = (hi - lo) * 0.06
            lo, hi = max(0.0, lo - pad), hi + pad

        def to_x(value: float, *, _lo: float = lo, _hi: float = hi) -> float:
            return round(COMPARE_LEFT + (value - _lo) / (_hi - _lo) * plot_w, 1)

        filas = []
        for province, rows, fuera, dentro, vals in resumen:
            precios = sorted(r.unit_price for r in dentro)
            filas.append(
                {
                    "province_name": province,
                    "count": len(rows),
                    "outliers": len(fuera),
                    "minimum": precios[0],
                    "q1": _as_price(_quantile(vals, 0.25)),
                    "median": statistics.median(precios),
                    "q3": _as_price(_quantile(vals, 0.75)),
                    "maximum": precios[-1],
                    "x_min": to_x(vals[0]),
                    "x_q1": to_x(_quantile(vals, 0.25)),
                    "x_median": to_x(float(statistics.median(precios))),
                    "x_q3": to_x(_quantile(vals, 0.75)),
                    "x_max": to_x(vals[-1]),
                }
            )
        filas.sort(key=lambda f: (-f["median"], f["province_name"]))
        rows_out = tuple(
            CompareRow(y=round(COMPARE_TOP + (i + 0.5) * COMPARE_ROW, 1), **f)
            for i, f in enumerate(filas)
        )
        ticks = tuple(XTick(x=to_x(v), label=format_price(_as_price(v))) for v in _nice_ticks(lo, hi, 4))
        comparaciones.append(
            ProvinceComparison(
                product_name=product, currency=currency, unit=unit, rows=rows_out, ticks=ticks
            )
        )

    comparaciones.sort(key=lambda c: (-sum(r.count for r in c.rows), c.product_name, c.unit))
    return comparaciones


# --- piezas comunes de «Cómo se movió» --------------------------------------

#: Separación mínima entre dos etiquetas de texto en el mismo borde.
_LABEL_GAP = 14.0


def _spread_labels(ys: Sequence[float | None]) -> list[float | None]:
    """Separa etiquetas que caerían encimadas: de arriba abajo, cada una al
    menos `_LABEL_GAP` debajo de la anterior. Respeta los `None` y el orden."""
    out = list(ys)
    orden = sorted((i for i, v in enumerate(ys) if v is not None), key=lambda i: ys[i])
    previa: float | None = None
    for i in orden:
        if previa is not None and out[i] - previa < _LABEL_GAP:
            out[i] = previa + _LABEL_GAP
        previa = out[i]
    return [None if v is None else round(v, 1) for v in out]


def _province_slots(*groups: Iterable[RetailObservation]) -> dict[str, int]:
    """Índice de color de cada provincia, por orden alfabético sobre TODAS las
    observaciones: el color sigue a la provincia (dataviz: nunca a su rango),
    así que Santiago es el mismo tono aunque en un producto falte La Habana."""
    nombres = sorted(
        {o.province_name for g in groups for o in g if o.province_name and not o.unconverted}
    )
    return {n: i for i, n in enumerate(nombres)}


def _by_product(
    grupos: dict[_Key, list[RetailObservation]],
) -> dict[tuple[str, str, str], dict[str, list[RetailObservation]]]:
    # Funde el formato (2026-09-14, mismo motivo que `build_province_comparison`):
    # las vistas que usan esto comparan provincias, no formatos.
    out: dict[tuple[str, str, str], dict[str, list[RetailObservation]]] = {}
    for (product, province, currency, unit, _formato), rows in grupos.items():
        out.setdefault((product, currency, unit), {}).setdefault(province, []).extend(rows)
    return out


def _domain(values: Sequence[float], pad_share: float) -> tuple[float, float]:
    lo, hi = min(values), max(values)
    if lo == hi:
        return lo * 0.9, hi * 1.1
    pad = (hi - lo) * pad_share
    return max(0.0, lo - pad), hi + pad


# --- mes contra mes ---------------------------------------------------------

SLOPE_WIDTH = 360.0
SLOPE_HEIGHT = 176.0
SLOPE_TOP = 44.0
SLOPE_BOTTOM = 14.0
SLOPE_X_BEFORE = 165.0
SLOPE_X_NOW = 255.0


@dataclass(frozen=True)
class SlopeLine:
    """Una provincia: la mediana del período anterior y la de ahora."""

    province_name: str
    slot: int
    before_median: Decimal | None
    now_median: Decimal | None
    before_count: int
    now_count: int
    y_before: float | None
    y_now: float | None
    label_y_before: float | None
    label_y_now: float | None

    @property
    def has_line(self) -> bool:
        return self.before_median is not None and self.now_median is not None

    @property
    def change_pct(self) -> int | None:
        if not self.has_line or self.before_median == 0:
            return None
        return round((float(self.now_median) / float(self.before_median) - 1) * 100)

    @property
    def change_text(self) -> str:
        pct = self.change_pct
        if pct is None:
            return ""
        return f"+{pct}%" if pct > 0 else (f"−{abs(pct)}%" if pct < 0 else "0%")

    @property
    def before_text(self) -> str:
        return "" if self.before_median is None else format_price(self.before_median)

    @property
    def now_text(self) -> str:
        return "" if self.now_median is None else format_price(self.now_median)


@dataclass(frozen=True)
class SlopeChart:
    product_name: str
    currency: str
    unit: str
    lines: tuple[SlopeLine, ...]
    before_label: str
    now_label: str

    @property
    def viewbox(self) -> str:
        return f"0 0 {SLOPE_WIDTH:g} {SLOPE_HEIGHT:g}"

    @property
    def x_before(self) -> float:
        return SLOPE_X_BEFORE

    @property
    def x_now(self) -> float:
        return SLOPE_X_NOW

    @property
    def top(self) -> float:
        return SLOPE_TOP

    @property
    def bottom_y(self) -> float:
        return SLOPE_HEIGHT - SLOPE_BOTTOM


def _robust_median(rows: Sequence[RetailObservation]) -> Decimal | None:
    dentro, _ = split_outliers(rows)
    return statistics.median([r.unit_price for r in dentro]) if dentro else None


def build_month_vs_month(
    current: Sequence[RetailObservation],
    previous: Sequence[RetailObservation],
    *,
    window_days: int,
    hoy: date,
) -> list[SlopeChart]:
    """Por producto: la mediana de cada provincia en el período anterior y en el
    actual, del mismo largo. Responde «¿subió o bajó?» con dos números y su
    muestra —el cambio en porcentaje, sin flecha ni color de alarma (§12.6)—.
    Una provincia sin anuncios en uno de los dos períodos queda como punto
    suelto, sin línea: no hay contra qué comparar.
    """
    slots = _province_slots(current, previous)
    ahora, antes = _group(current), _group(previous)
    # Fundido a propósito (2026-09-14, mismo motivo que
    # `build_province_comparison`): «¿subió o bajó?» es una pregunta de
    # provincia, no de formato.
    por_producto: dict[tuple[str, str, str], dict[str, tuple[list, list]]] = {}
    for key in set(ahora) | set(antes):
        product, province, currency, unit, _formato = key
        entrada = por_producto.setdefault((product, currency, unit), {}).setdefault(
            province, ([], [])
        )
        entrada[0].extend(antes.get(key, []))
        entrada[1].extend(ahora.get(key, []))

    plot_h = SLOPE_HEIGHT - SLOPE_TOP - SLOPE_BOTTOM
    before_label = (
        f"{_short_day(hoy - timedelta(days=2 * window_days))} – "
        f"{_short_day(hoy - timedelta(days=window_days + 1))}"
    )
    now_label = f"{_short_day(hoy - timedelta(days=window_days))} – {_short_day(hoy)}"

    charts: list[SlopeChart] = []
    for (product, currency, unit), provincias in por_producto.items():
        datos = []
        for province, (rows_b, rows_n) in provincias.items():
            mb, mn = _robust_median(rows_b), _robust_median(rows_n)
            if mb is None and mn is None:
                continue
            datos.append((province, mb, mn, len(rows_b), len(rows_n)))
        if not datos:
            continue
        lo, hi = _domain([float(v) for d in datos for v in (d[1], d[2]) if v is not None], 0.1)

        def to_y(value: Decimal | None, *, _lo: float = lo, _hi: float = hi) -> float | None:
            if value is None:
                return None
            return round(SLOPE_TOP + (1 - (float(value) - _lo) / (_hi - _lo)) * plot_h, 1)

        datos.sort(key=lambda d: (-float(d[2] if d[2] is not None else d[1]), d[0]))
        ys_b = [to_y(d[1]) for d in datos]
        ys_n = [to_y(d[2]) for d in datos]
        lab_b, lab_n = _spread_labels(ys_b), _spread_labels(ys_n)
        lines = tuple(
            SlopeLine(
                province_name=d[0],
                slot=slots.get(d[0], 0),
                before_median=d[1],
                now_median=d[2],
                before_count=d[3],
                now_count=d[4],
                y_before=ys_b[i],
                y_now=ys_n[i],
                label_y_before=lab_b[i],
                label_y_now=lab_n[i],
            )
            for i, d in enumerate(datos)
        )
        charts.append(SlopeChart(product, currency, unit, lines, before_label, now_label))

    charts.sort(
        key=lambda c: (-sum(l.now_count + l.before_count for l in c.lines), c.product_name, c.unit)
    )
    return charts


# --- las provincias en el tiempo --------------------------------------------

STEPS_WIDTH = 360.0
STEPS_HEIGHT = 150.0
STEPS_LEFT = 42.0
STEPS_RIGHT = 70.0
STEPS_TOP = 10.0
STEPS_BOTTOM = 20.0


@dataclass(frozen=True)
class StepPoint:
    """La mediana de una provincia en un período."""

    x: float
    y: float
    median: Decimal
    count: int
    start: date

    @property
    def median_text(self) -> str:
        return format_price(self.median)

    @property
    def start_text(self) -> str:
        return _short_day(self.start)


@dataclass(frozen=True)
class StepLine:
    province_name: str
    slot: int
    #: Trazo en escalones: horizontal a lo ancho de cada período con dato,
    #: vertical entre períodos seguidos y cortado donde un período no tuvo
    #: anuncios (un `M` nuevo). Ningún tramo une períodos no contiguos.
    path: str
    points: tuple[StepPoint, ...]
    label_y: float | None

    @property
    def short_name(self) -> str:
        return self.province_name if len(self.province_name) <= 10 else self.province_name.split()[0]


@dataclass(frozen=True)
class StepChart:
    product_name: str
    currency: str
    unit: str
    lines: tuple[StepLine, ...]
    ticks: tuple[Tick, ...]
    bin_days: int
    first_label: str
    last_label: str

    @property
    def viewbox(self) -> str:
        return f"0 0 {STEPS_WIDTH:g} {STEPS_HEIGHT:g}"

    @property
    def x_left(self) -> float:
        return STEPS_LEFT

    @property
    def x_right(self) -> float:
        return STEPS_WIDTH - STEPS_RIGHT

    @property
    def label_x(self) -> float:
        return STEPS_WIDTH - STEPS_RIGHT + 6

    @property
    def baseline_y(self) -> float:
        return STEPS_HEIGHT - STEPS_BOTTOM

    @property
    def period_name(self) -> str:
        return "semana" if self.bin_days == 7 else "día"

    @property
    def periods_with_data(self) -> int:
        """Períodos distintos con al menos un anuncio, sumando provincias. Con
        uno solo no hay evolución que dibujar."""
        return len({p.start for l in self.lines for p in l.points})


def build_median_steps(
    observations: Sequence[RetailObservation], *, window_days: int, hoy: date
) -> list[StepChart]:
    """Por producto, la mediana de cada provincia por período, en un solo eje.

    Mismos períodos que `build_range_series` (hacia atrás desde hoy). Cada
    provincia usa su propio cerco robusto; el eje es común porque la pregunta
    es si se separan o se acercan.
    """
    size = bin_days(window_days)
    n_bins = math.ceil((window_days + 1) / size)
    plot_w = STEPS_WIDTH - STEPS_LEFT - STEPS_RIGHT
    plot_h = STEPS_HEIGHT - STEPS_TOP - STEPS_BOTTOM
    slot_w = plot_w / n_bins
    slots = _province_slots(observations)

    def index(day: date) -> int:
        return (hoy - day).days // size

    charts: list[StepChart] = []
    for (product, currency, unit), provincias in _by_product(_group(observations)).items():
        medianas: dict[str, dict[int, tuple[Decimal, int]]] = {}
        for province, rows in provincias.items():
            rows = [r for r in rows if 0 <= index(r.day) < n_bins]
            dentro, _ = split_outliers(rows)
            por_periodo: dict[int, list[Decimal]] = {}
            for r in dentro:
                por_periodo.setdefault(n_bins - 1 - index(r.day), []).append(r.unit_price)
            if por_periodo:
                medianas[province] = {
                    j: (statistics.median(v), len(v)) for j, v in por_periodo.items()
                }
        if not medianas:
            continue
        lo, hi = _domain([float(m) for p in medianas.values() for m, _ in p.values()], 0.08)

        def to_y(value: Decimal, *, _lo: float = lo, _hi: float = hi) -> float:
            return round(STEPS_TOP + (1 - (float(value) - _lo) / (_hi - _lo)) * plot_h, 1)

        lineas = []
        for province in sorted(medianas):
            trazo = ""
            puntos = []
            previa = None
            for j in sorted(medianas[province]):
                med, n = medianas[province][j]
                x0 = round(STEPS_LEFT + j * slot_w, 1)
                x1 = round(STEPS_LEFT + (j + 1) * slot_w, 1)
                y = to_y(med)
                trazo += f" L{x0},{y}" if previa == j - 1 else f" M{x0},{y}"
                trazo += f" L{x1},{y}"
                previa = j
                start = hoy - timedelta(days=(n_bins - 1 - j) * size + size - 1)
                puntos.append(StepPoint(round((x0 + x1) / 2, 1), y, med, n, start))
            lineas.append((province, trazo.strip(), tuple(puntos)))

        etiquetas = _spread_labels([pts[-1].y for _, _, pts in lineas])
        lines = tuple(
            StepLine(province, slots.get(province, 0), trazo, pts, etiquetas[i])
            for i, (province, trazo, pts) in enumerate(lineas)
        )
        ticks = tuple(
            Tick(y=to_y(Decimal(str(round(v, 2)))), label=format_price(Decimal(str(round(v, 2)))))
            for v in _nice_ticks(lo, hi)
        )
        first = hoy - timedelta(days=n_bins * size - 1)
        charts.append(
            StepChart(
                product, currency, unit, lines, ticks, size, _short_day(first), _short_day(hoy)
            )
        )

    charts.sort(key=lambda c: (-sum(p.count for l in c.lines for p in l.points), c.product_name))
    return charts


# --- calendario de anuncios -------------------------------------------------

CAL_CELL = 16.0
CAL_GAP = 3.0
CAL_LEFT = 18.0
CAL_TOP = 16.0
_WEEKDAYS = ("L", "M", "X", "J", "V", "S", "D")


@dataclass(frozen=True)
class CalendarDay:
    day: date
    count: int
    column: int
    row: int
    x: float
    y: float

    @property
    def level(self) -> int:
        """Tono del cuadro: 0 sin anuncios, 1 uno, 2 dos o tres, 3 cuatro o más.
        Cortes de lectura, no umbrales de publicación (§12.6)."""
        if self.count == 0:
            return 0
        if self.count == 1:
            return 1
        return 2 if self.count <= 3 else 3

    @property
    def day_text(self) -> str:
        return _short_day(self.day)


@dataclass(frozen=True)
class CalendarChart:
    product_name: str
    days: tuple[CalendarDay, ...]
    week_labels: tuple[XTick, ...]
    columns: int

    @property
    def cell(self) -> float:
        return CAL_CELL

    @property
    def left(self) -> float:
        return CAL_LEFT

    @property
    def width(self) -> float:
        return CAL_LEFT + self.columns * (CAL_CELL + CAL_GAP)

    @property
    def height(self) -> float:
        return CAL_TOP + 7 * (CAL_CELL + CAL_GAP)

    @property
    def viewbox(self) -> str:
        return f"0 0 {self.width:g} {self.height:g}"

    @property
    def weekday_labels(self) -> tuple[tuple[float, str], ...]:
        return tuple(
            (CAL_TOP + i * (CAL_CELL + CAL_GAP), letra) for i, letra in enumerate(_WEEKDAYS)
        )


def build_calendar(
    observations: Sequence[RetailObservation], *, window_days: int, hoy: date
) -> list[CalendarChart]:
    """Por producto, un cuadro por día de la ventana: cuántos anuncios hubo,
    sumando provincias. Semanas en columnas (de lunes a domingo) y días en
    filas. Responde «¿qué tan fresco y parejo es el dato?»: muestra cada día sin
    anuncios en vez de esconderlo. No dice precios."""
    inicio = hoy - timedelta(days=window_days)
    lunes = inicio - timedelta(days=inicio.weekday())
    columnas = (hoy - lunes).days // 7 + 1

    por_producto: dict[str, dict[date, int]] = {}
    for o in observations:
        if o.province_name is None or o.unconverted or not inicio <= o.day <= hoy:
            continue
        dias = por_producto.setdefault(o.product_name, {})
        dias[o.day] = dias.get(o.day, 0) + 1

    charts: list[CalendarChart] = []
    for product, cuenta in por_producto.items():
        dias = []
        for d in range(window_days + 1):
            dia = inicio + timedelta(days=d)
            col, fila = (dia - lunes).days // 7, dia.weekday()
            dias.append(
                CalendarDay(
                    day=dia,
                    count=cuenta.get(dia, 0),
                    column=col,
                    row=fila,
                    x=CAL_LEFT + col * (CAL_CELL + CAL_GAP),
                    y=CAL_TOP + fila * (CAL_CELL + CAL_GAP),
                )
            )
        etiquetas = tuple(
            XTick(
                x=CAL_LEFT + c * (CAL_CELL + CAL_GAP),
                label=_short_day(max(inicio, lunes + timedelta(days=7 * c))),
            )
            for c in range(0, columnas, 2)
        )
        charts.append(CalendarChart(product, tuple(dias), etiquetas, columnas))

    charts.sort(key=lambda c: (-sum(d.count for d in c.days), c.product_name))
    return charts


# --- la ventana «Cómo se movió» ---------------------------------------------


@dataclass(frozen=True)
class AdRow:
    """Un anuncio de la ventana, con el precio tal como quedó —fuera de escala
    incluido— y, si su evidencia es pública, el enlace a él (§6.5)."""

    day: date
    province_name: str
    price: Decimal
    currency: str
    unit: str
    evidence_id: uuid.UUID
    evidence_is_public: bool

    @property
    def day_text(self) -> str:
        return _short_day(self.day)

    @property
    def price_text(self) -> str:
        return format_price(self.price)


@dataclass(frozen=True)
class EvolutionBlock:
    """Lo que la ventana «Cómo se movió» dibuja de un producto: mes contra mes,
    las provincias en el tiempo (solo si hay al menos dos períodos con dato),
    el calendario de anuncios y la tabla de esos anuncios, que es también la
    vista en tabla de los gráficos. Uno por producto con anuncios en la
    ventana."""

    product_name: str
    slopes: tuple[SlopeChart, ...]
    steps: tuple[StepChart, ...]
    calendar: CalendarChart
    ads: tuple[AdRow, ...] = ()

    @property
    def legend(self) -> tuple[tuple[int, str], ...]:
        """(color, provincia) de las provincias que aparecen en los gráficos,
        en el orden de sus colores: la identidad nunca queda solo en el color."""
        vistas = {
            (l.slot, l.province_name)
            for chart in (*self.slopes, *self.steps)
            for l in chart.lines
        }
        return tuple(sorted(vistas))


def build_evolution_blocks(
    current: Sequence[RetailObservation],
    previous: Sequence[RetailObservation],
    *,
    window_days: int,
    hoy: date,
) -> list[EvolutionBlock]:
    """Arma un bloque por producto con anuncios en la ventana, en el orden del
    calendario (más anuncios primero)."""
    slopes = build_month_vs_month(current, previous, window_days=window_days, hoy=hoy)
    steps = build_median_steps(current, window_days=window_days, hoy=hoy)
    inicio = hoy - timedelta(days=window_days)
    anuncios: dict[str, list[AdRow]] = {}
    for o in current:
        # Mismo recorte que el calendario, para que la tabla cuente lo mismo.
        if o.province_name is None or o.unconverted or not inicio <= o.day <= hoy:
            continue
        anuncios.setdefault(o.product_name, []).append(
            AdRow(
                o.day, o.province_name, o.unit_price, o.currency, o.unit,
                o.evidence_id, o.evidence_is_public,
            )
        )
    for filas in anuncios.values():
        filas.sort(key=lambda a: (-a.day.toordinal(), a.province_name, a.price))
    return [
        EvolutionBlock(
            product_name=cal.product_name,
            slopes=tuple(s for s in slopes if s.product_name == cal.product_name),
            steps=tuple(
                s for s in steps if s.product_name == cal.product_name and s.periods_with_data >= 2
            ),
            calendar=cal,
            ads=tuple(anuncios.get(cal.product_name, [])),
        )
        for cal in build_calendar(current, window_days=window_days, hoy=hoy)
    ]
