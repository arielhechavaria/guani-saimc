"""Cintillo de precios de las tres vistas (prototipo aprobado, 2026-09-26).

Una entrada por producto piloto con dato: su mediana entre las provincias que
lo tienen y el cambio contra el período anterior. Como la lista del tablero,
no calcula ninguna cifra nueva: sale de las mismas `SlopeLine` de
`price_charts.build_month_vs_month`, para que el cintillo y la lista no puedan
contradecirse.

Dos decisiones de honestidad, ambas visibles en el rótulo del cintillo:

- La cifra es la **mediana de las medianas de las provincias con dato**, no
  una mediana nacional: con dos provincias con dato, «nacional» sería una
  exageración. Es una cifra de resumen, no una medición nueva.
- El cambio compara solo provincias que tienen los dos períodos: mezclar una
  provincia con período anterior y otra sin él inventaría una variación. Va
  sin flecha ni color (§12.6, decisions.md 2026-09-21).
"""

from __future__ import annotations

import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal

from saimc.web.card_view import format_price
from saimc.web.price_charts import SlopeChart
from saimc.web.product_nav import product_slug
from saimc.web.watchlist_view import _alfabeto, _chart_for


@dataclass(frozen=True)
class TickerItem:
    product_name: str
    #: Mediana entre las provincias con dato, ya formateada.
    median_text: str
    #: «CUP/lb»: moneda y unidad del gráfico del que salió.
    unit: str
    #: «+3%», «−7%», «0%», o vacío si ninguna provincia tiene los dos períodos.
    change_text: str
    href: str


def _change_text(pct: int) -> str:
    return f"+{pct}%" if pct > 0 else (f"−{abs(pct)}%" if pct < 0 else "0%")


def build_ticker(
    pilot_names: Sequence[str],
    slopes: Sequence[SlopeChart],
    *,
    href: Callable[[str], str],
) -> tuple[TickerItem, ...]:
    """El cintillo, de más a menos anuncios en la ventana. `href` recibe el
    nombre del producto y devuelve el enlace: cada vista lo arma hacia sí
    misma, para que tocar un producto no cambie de vista."""
    armados: list[tuple[int, str, TickerItem]] = []
    for name in pilot_names:
        chart = _chart_for(name, slopes)
        if chart is None:
            continue
        con_dato = [line for line in chart.lines if line.now_median is not None]
        if not con_dato:
            continue
        ahora: list[Decimal] = [line.now_median for line in con_dato if line.now_median is not None]
        comparables = [line for line in con_dato if line.has_line]
        if comparables and all(line.before_median for line in comparables):
            antes = statistics.median(line.before_median for line in comparables if line.before_median)
            ahora_cmp = statistics.median(line.now_median for line in comparables if line.now_median)
            cambio = _change_text(round((float(ahora_cmp) / float(antes) - 1) * 100))
        else:
            cambio = ""
        unidad = f"{chart.currency}/{chart.unit}" if chart.unit else chart.currency
        item = TickerItem(
            product_name=name,
            median_text=format_price(statistics.median(ahora)),
            unit=unidad,
            change_text=cambio,
            href=href(product_slug(name)),
        )
        armados.append((sum(line.now_count for line in con_dato), name, item))
    armados.sort(key=lambda t: (-t[0], _alfabeto(t[1])))
    return tuple(item for _, _, item in armados)
