"""Tabla por provincia de la ventana con pestañas (prototipo B, 2026-09-21).

El operador eligió, entre cinco prototipos del mapa, una ventana con una
pestaña por producto activo; cada pestaña trae una tabla con una fila por
provincia —mediana, cambio, muestra y una sparkline— y el mapa se tiñe según
el producto de la pestaña abierta.

No calcula ninguna cifra que la aplicación no calcule ya en otro lado: la
mediana y el cambio salen tal cual de `price_charts.build_month_vs_month` (el
mismo «¿subió o bajó?» de la lista del tablero y de «Cómo se movió»), y la
sparkline dibuja las medianas por período de `price_charts.build_median_steps`
(las mismas de «Cómo se movió»). Por eso no hay una «mediana nacional»:
promediar medianas provinciales sería una estadística nueva; el titular es el
rango entre la provincia más barata y la más cara. El cambio va sin flecha ni
color (§12.6): una baja es buena noticia para quien compra y mala para quien
vende. El tinte del mapa tampoco decide qué es caro: es relativo a las
provincias con dato y no lleva umbral (§12.6).

Mismo reparto que `matrix.py` y `watchlist_view.py`: acá se arma la estructura
—incluido el trazo SVG— y la plantilla solo la recorre.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from saimc.units import unit_noun
from saimc.web.panel_view import PanelBlock
from saimc.web.price_charts import (
    AdRow,
    Headline,
    RangeSeries,
    SlopeChart,
    StepChart,
    StepLine,
    format_price,
)
from saimc.web.product_nav import product_slug
from saimc.web.watchlist_view import _alfabeto, _chart_for

#: Lienzo de la sparkline, en unidades del `viewBox`. Es el mismo en la tabla de
#: escritorio y en la de angosto: el CSS lo escala.
SPARK_W = 112.0
SPARK_H = 26.0
SPARK_PAD = 3.0
#: Ancho de la barra como fracción de su casilla, para que dos barras seguidas
#: no se toquen.
_BAR_FILL = 0.68


@dataclass(frozen=True)
class Sparkline:
    """Tres dibujos de la misma serie; la plantilla los lleva todos y el CSS
    muestra el que el visitante eligió (línea, área o barras)."""

    line: str
    area: str
    cols: str
    #: Extremo derecho de la serie, donde va el punto que marca «hoy».
    end: tuple[float, float]


@dataclass(frozen=True)
class GridRow:
    province_name: str
    slug: str
    median_text: str
    #: La mediana como número, para ordenar la columna sin volver a parsear el
    #: texto formateado (que lleva separador de miles).
    median: float
    #: «+3%», «−7%», «0%», o vacío si no hubo anuncios en el período anterior.
    change_text: str
    #: El cambio como número (puntos porcentuales) para ordenar; `None` si no
    #: hay cambio que mostrar. Las filas sin cambio se ordenan al final.
    change: int | None
    count: int
    #: 0 (la más barata con dato) a 1 (la más cara); ver `price_layer`.
    tint: float
    spark: Sparkline | None = None
    #: Lo que se ve al expandir la fila: un bloque por formato con su cifra, su
    #: rango por período y los anuncios detrás (`group_detail`). Vacío si no se
    #: pegó.
    details: tuple[PanelBlock, ...] = ()

    @property
    def has_data(self) -> bool:
        return True


@dataclass(frozen=True)
class ProductGrid:
    product_name: str
    slug: str
    currency: str
    unit: str
    #: Solo las provincias con anuncios en la ventana, de mayor a menor mediana.
    rows: tuple[GridRow, ...]
    #: Las demás, por nombre. Se dicen como ausencia de dato (§9.0), no como
    #: filas vacías ni como un cero.
    sin_datos: tuple[str, ...]
    #: «semana» o «día»: lo que dibuja cada trazo de la sparkline. Vacío sin
    #: anuncios, cuando no hay sparkline que rotular.
    period_name: str = ""

    @property
    def unit_noun(self) -> str:
        """«libra» para «lb»: la unidad escrita dentro de una frase. Fuera del
        vocabulario cae al valor canónico, que se lee peor pero no miente."""
        return unit_noun(self.unit) or self.unit

    @property
    def low_text(self) -> str:
        return min(self.rows, key=lambda r: r.median).median_text if self.rows else ""

    @property
    def high_text(self) -> str:
        return max(self.rows, key=lambda r: r.median).median_text if self.rows else ""

    @property
    def count(self) -> int:
        return sum(r.count for r in self.rows)

    @property
    def province_count(self) -> int:
        return len(self.rows)


def _n(value: float) -> str:
    """Un decimal como mucho y sin el «.0» de los enteros: el trazo viaja en
    el HTML de cada carga y cada byte cuenta."""
    return f"{round(value, 1):g}"


def _runs(indices: Sequence[int]) -> list[list[int]]:
    """Los índices agrupados en tramos de casillas consecutivas: un período sin
    anuncios corta la línea en vez de unir dos períodos que no se tocan."""
    tramos: list[list[int]] = []
    for pos, idx in enumerate(indices):
        if pos and idx == indices[pos - 1] + 1:
            tramos[-1].append(pos)
        else:
            tramos.append([pos])
    return tramos


def _sparkline(line: StepLine, first: date, size: int, slots: int) -> Sparkline:
    """El dibujo de una provincia, normalizado contra sí misma.

    Cada fila usa su propio mínimo y máximo: la pregunta de una sparkline es
    la forma —¿sube, baja, oscila?—, no el nivel, que ya dice la columna de la
    mediana. Una fila con un solo valor queda a media altura, sin pendiente.
    """
    puntos = sorted(line.points, key=lambda p: p.start)
    idx = [(p.start - first).days // size for p in puntos]
    valores = [float(p.median) for p in puntos]
    lo, hi = min(valores), max(valores)
    inner_w = SPARK_W - 2 * SPARK_PAD
    inner_h = SPARK_H - 2 * SPARK_PAD
    base = SPARK_H - SPARK_PAD

    def norm(v: float) -> float:
        return 0.5 if hi == lo else (v - lo) / (hi - lo)

    def x_of(i: int) -> float:
        return SPARK_W / 2 if slots <= 1 else SPARK_PAD + inner_w * i / (slots - 1)

    def y_of(v: float) -> float:
        return SPARK_PAD + inner_h * (1 - norm(v))

    xy = [(x_of(i), y_of(v)) for i, v in zip(idx, valores, strict=True)]
    linea = ""
    area = ""
    for tramo in _runs(idx):
        pts = [xy[k] for k in tramo]
        trazo = "M" + "L".join(f"{_n(x)},{_n(y)}" for x, y in pts)
        linea += trazo
        area += f"{trazo}L{_n(pts[-1][0])},{_n(base)}L{_n(pts[0][0])},{_n(base)}Z"
    ancho_barra = inner_w / slots * _BAR_FILL
    barras = ""
    for i, v in zip(idx, valores, strict=True):
        alto = 3 + (inner_h - 3) * norm(v)
        x = SPARK_PAD + inner_w * (i + (1 - _BAR_FILL) / 2) / slots
        barras += f"M{_n(x)},{_n(base)}v{_n(-alto)}h{_n(ancho_barra)}v{_n(alto)}z"
    return Sparkline(line=linea, area=area, cols=barras, end=(round(xy[-1][0], 1), round(xy[-1][1], 1)))


def _sparklines(step: StepChart | None) -> dict[str, Sparkline]:
    """Una sparkline por provincia, todas sobre el mismo eje x: la casilla 0 es
    el primer período con dato de cualquier provincia del producto."""
    if step is None or not step.lines:
        return {}
    starts = [p.start for line in step.lines for p in line.points]
    first, last = min(starts), max(starts)
    slots = (last - first).days // step.bin_days + 1
    return {
        line.province_name: _sparkline(line, first, step.bin_days, slots)
        for line in step.lines
        if line.points
    }


def _step_for(chart: SlopeChart | None, steps: Sequence[StepChart]) -> StepChart | None:
    """El gráfico de períodos del mismo producto, moneda y unidad que el de la
    mediana, para que la sparkline y el número describan la misma serie."""
    if chart is None:
        return None
    return next(
        (
            s
            for s in steps
            if (s.product_name, s.currency, s.unit) == (chart.product_name, chart.currency, chart.unit)
        ),
        None,
    )


def group_detail(
    headlines: Sequence[Headline],
    ranges: Sequence[RangeSeries],
    ads_by_cell: Mapping[tuple[str, str, str, str, str | None], Sequence[AdRow]],
) -> dict[tuple[str, str], tuple[PanelBlock, ...]]:
    """Los bloques de detalle agrupados por (producto, provincia), en el orden
    de las cifras (más anuncios primero), listos para pegar a cada fila.

    Es lo mismo que `panel_view.attach_blocks` arma para el panel del mapa, con
    dos diferencias: se agrupa por provincia y no por producto, y sale de las
    observaciones de TODAS las provincias, no de las que filtra la provincia
    elegida —cada fila de la tabla tiene que poder expandirse—. Cifra, rango y
    anuncios se emparejan por producto, provincia, moneda, unidad y formato:
    el formato no se funde acá aunque la fila sí lo funda para comparar
    provincias (`price_charts._by_product`).
    """
    rango_de = {(r.product_name, r.province_name, r.currency, r.unit, r.format_label): r for r in ranges}
    agrupado: dict[tuple[str, str], list[PanelBlock]] = {}
    for h in headlines:
        clave = (h.product_name, h.province_name, h.currency, h.unit, h.format_label)
        agrupado.setdefault((h.product_name, h.province_name), []).append(
            PanelBlock(
                headline=h,
                ranges=rango_de.get(clave),
                ads=tuple(ads_by_cell.get(clave, ())),
            )
        )
    return {k: tuple(v) for k, v in agrupado.items()}


def build_product_grids(
    active_products: Sequence[str],
    slopes: Sequence[SlopeChart],
    steps: Sequence[StepChart],
    provinces: Sequence[str],
    details: Mapping[tuple[str, str], Sequence[PanelBlock]] | None = None,
) -> tuple[ProductGrid, ...]:
    """Una tabla por producto activo, en el orden recibido, con las 16
    provincias repartidas entre las que tienen anuncios y las que no.
    `details` (`group_detail`) es opcional: sin él las filas no se expanden."""
    detalle = details or {}
    grillas = []
    for name in active_products:
        chart = _chart_for(name, slopes)
        # Cada línea con su mediana ya sin el `None`: solo cuentan las
        # provincias con anuncios en la ventana.
        con_dato = (
            [(line, line.now_median) for line in chart.lines if line.now_median is not None] if chart else []
        )
        step = _step_for(chart, steps)
        sparks = _sparklines(step)
        medianas = [float(m) for _, m in con_dato]
        lo, hi = (min(medianas), max(medianas)) if medianas else (0.0, 0.0)
        filas = []
        for line, decimal in con_dato:
            mediana = float(decimal)
            filas.append(
                GridRow(
                    province_name=line.province_name,
                    slug=product_slug(line.province_name),
                    median_text=format_price(decimal),
                    median=mediana,
                    change_text=line.change_text,
                    change=line.change_pct,
                    count=line.now_count,
                    tint=0.5 if hi == lo else round((mediana - lo) / (hi - lo), 3),
                    spark=sparks.get(line.province_name),
                    details=tuple(detalle.get((name, line.province_name), ())),
                )
            )
        filas.sort(key=lambda r: (-r.median, _alfabeto(r.province_name)))
        con_nombre = {r.province_name for r in filas}
        grillas.append(
            ProductGrid(
                product_name=name,
                slug=product_slug(name),
                currency=chart.currency if chart else "",
                unit=chart.unit if chart else "",
                rows=tuple(filas),
                sin_datos=tuple(sorted((p for p in provinces if p not in con_nombre), key=_alfabeto)),
                period_name=step.period_name if step else "",
            )
        )
    return tuple(grillas)


def price_layer(grids: Sequence[ProductGrid]) -> dict[str, dict[str, dict[str, float | str]]]:
    """Provincia → producto → `{"t": tinte, "m": mediana con su unidad}`, lo que
    el mapa lee para pintar según la pestaña abierta y para decir la cifra en
    el tooltip.

    El tinte va de 0 (la provincia más barata con dato del producto) a 1 (la
    más cara). Es relativo a las provincias con dato del propio producto y no
    lleva umbral (§12.6): mismo criterio que la capa de densidad, que se
    sombrea contra el máximo del momento. Con una sola provincia con dato no
    hay contra qué comparar y queda a medio tono. Sin verde ni rojo: ninguna
    es «buena» ni «mala». Una provincia sin dato del producto no aparece.
    """
    capa: dict[str, dict[str, dict[str, float | str]]] = {}
    for grid in grids:
        for row in grid.rows:
            capa.setdefault(row.slug, {})[grid.slug] = {
                "t": row.tint,
                "m": f"{row.median_text} {grid.currency}/{grid.unit}",
            }
    return capa
