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
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from saimc.cells import RetailObservation
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


_Key = tuple[str, str, str, str]


def _group(observations: Iterable[RetailObservation]) -> dict[_Key, list[RetailObservation]]:
    """Por producto × provincia × moneda × unidad. Descarta lo mismo que
    `series_view` y por el mismo motivo: sin provincia (la vista es por
    provincia) y `unconverted` (mezclarla afirmaría una equivalencia que nadie
    calculó)."""
    grupos: dict[_Key, list[RetailObservation]] = {}
    for o in observations:
        if o.province_name is None or o.unconverted:
            continue
        grupos.setdefault((o.product_name, o.province_name, o.currency, o.unit), []).append(o)
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
    """El rango por período de una celda producto × provincia."""

    product_name: str
    province_name: str
    currency: str
    unit: str
    bins: tuple[RangeBin, ...]
    outliers: tuple[OutlierMark, ...]
    ticks: tuple[Tick, ...]
    bar_width: float
    bin_days: int

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
    for (product, province, currency, unit), rows in _group(observations).items():
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
        product, province, currency, unit = key
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
                last_day=max(r.day for r in rows),
            )
        )
    cifras.sort(key=lambda c: (-c.count, c.product_name, c.province_name))
    return cifras
