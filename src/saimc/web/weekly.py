"""Serie semanal de medianas y su trazo, compartidos por «Explorar» y el tablero.

Vivían en `explore_view`; el tablero (celdas con sparkline, 2026-09-26) los
necesita y `explore_view` ya importa de `watchlist_view`, así que quedarían
circulares. Acá no dependen de ninguna de las dos vistas.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from saimc.cells import RetailObservation
from saimc.web.price_charts import split_outliers

#: Semanas de la tendencia.
WEEKS = 13
SPARK_PAD = 3.0
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


def _n(value: float) -> str:
    """Un decimal como mucho y sin el «.0» de los enteros: el trazo viaja en
    el HTML de cada carga y cada byte cuenta."""
    return f"{round(value, 1):g}"


def weekly_medians(rows: Sequence[RetailObservation], hoy: date) -> tuple[Decimal | None, ...]:
    """La mediana de cada una de las últimas 13 semanas (la casilla 12 termina
    hoy); `None` donde no hubo anuncios. El cerco se calcula sobre las 13
    semanas de la propia serie."""
    dentro, _ = split_outliers(rows)
    por_semana: dict[int, list[Decimal]] = defaultdict(list)
    for r in dentro:
        antes = (hoy - r.day).days // 7
        if 0 <= antes < WEEKS:
            por_semana[WEEKS - 1 - antes].append(r.unit_price)
    return tuple(statistics.median(por_semana[j]) if j in por_semana else None for j in range(WEEKS))


def sparkline_from_values(
    values: Sequence[Decimal | float | None], *, width: float, height: float, pad: float = SPARK_PAD
) -> Sparkline | None:
    """El trazo de una serie con huecos, normalizado contra sí misma.

    La forma —¿sube, baja, oscila?— es lo que dice una sparkline; el nivel ya lo
    dice la mediana de la fila. Cada valor cae en la posición de su semana, y un
    hueco corta la línea en vez de unir dos semanas que no se tocan. Con un solo
    dato queda a media altura, sin pendiente que inventar. `None` sin ningún
    dato.
    """
    puestos = [(i, float(v)) for i, v in enumerate(values) if v is not None]
    if not puestos:
        return None
    slots = len(values)
    valores = [v for _, v in puestos]
    lo, hi = min(valores), max(valores)
    inner_w, inner_h = width - 2 * pad, height - 2 * pad
    base = height - pad

    def norm(v: float) -> float:
        return 0.5 if hi == lo else (v - lo) / (hi - lo)

    def x_of(i: int) -> float:
        return width / 2 if slots <= 1 else pad + inner_w * i / (slots - 1)

    xy = [(x_of(i), pad + inner_h * (1 - norm(v))) for i, v in puestos]
    linea = ""
    area = ""
    tramo: list[tuple[float, float]] = []
    previo = -2
    for (i, _), punto in zip(puestos, xy, strict=True):
        if i != previo + 1 and tramo:
            linea, area = _cerrar(linea, area, tramo, base)
            tramo = []
        tramo.append(punto)
        previo = i
    linea, area = _cerrar(linea, area, tramo, base)
    ancho_barra = inner_w / slots * _BAR_FILL
    barras = ""
    for i, v in puestos:
        alto = 3 + (inner_h - 3) * norm(v)
        x = pad + inner_w * (i + (1 - _BAR_FILL) / 2) / slots
        barras += f"M{_n(x)},{_n(base)}v{_n(-alto)}h{_n(ancho_barra)}v{_n(alto)}z"
    return Sparkline(line=linea, area=area, cols=barras, end=(round(xy[-1][0], 1), round(xy[-1][1], 1)))


def _cerrar(linea: str, area: str, tramo: list[tuple[float, float]], base: float) -> tuple[str, str]:
    trazo = "M" + "L".join(f"{_n(x)},{_n(y)}" for x, y in tramo)
    return linea + trazo, area + f"{trazo}L{_n(tramo[-1][0])},{_n(base)}L{_n(tramo[0][0])},{_n(base)}Z"
