"""Contexto de las tarjetas para redes (slice de marketing, 2026-09-28/29).

Puro: recibe lo que ya calculan `cells.py`/`card_view.py`/`price_charts.py`
para la vista pública y arma lo que cada plantilla necesita pintar. Ninguna
función de este módulo consulta la base ni inventa un cómputo nuevo.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from saimc.cells import RetailObservation
from saimc.units import unit_noun
from saimc.web.card_view import ProductCard, format_price, province_color_slots
from saimc.web.price_charts import SlopeChart

#: Mismos valores que `--m3-prov-0/-1/-2` en `design_tokens.css` (modo claro):
#: la tarjeta es una imagen estática, no puede leer variables CSS del sitio.
_PROVINCE_COLORS: dict[int, str] = {0: "#3f5fd6", 1: "#7048e8", 2: "#0b7285"}
_OTHER_COLOR = "#6f7979"

#: Ancho mínimo de la barra en pantalla (%) para que un precio único (rango
#: min=max) siga siendo visible. No es un umbral de publicación (§12.6): es
#: una cota cosmética sobre un `<div>`, no decide qué se muestra.
_MIN_BAR_WIDTH_PCT = 4.0


@dataclass(frozen=True)
class ProvinceRow:
    name: str
    color: str
    has_data: bool
    is_cheapest: bool = False
    range_text: str = ""
    median_text: str = ""
    count: int = 0
    bar_left_pct: float = 0.0
    bar_width_pct: float = 0.0
    median_pct: float = 0.0


@dataclass(frozen=True)
class DatoDelDiaCard:
    product_name: str
    unit: str
    unit_noun: str
    currency: str
    week_from: str
    week_to: str
    rows: list[ProvinceRow]


def format_price_whole(value: Decimal) -> str:
    """El precio de una tarjeta de marketing: entero (mitad hacia arriba) y con
    punto de miles, sin decimales («2.529»).

    Guarda: un precio positivo que redondea a 0 (p. ej. 0,4) nunca se muestra
    «0»: cae en `format_price`, con dos decimales. Es solo presentación: no
    cambia ninguna medición, y el sitio público sigue con `format_price`."""
    entero = value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    if value > 0 and entero == 0:
        return format_price(value)
    return f"{int(entero):,}".replace(",", ".")


def _range_text(minimo: Decimal, maximo: Decimal) -> str:
    """El rango en enteros; una sola cifra si ambos extremos redondean igual."""
    lo, hi = format_price_whole(minimo), format_price_whole(maximo)
    return lo if lo == hi else f"{lo} – {hi}"


def _format_range(card: ProductCard) -> str:
    if card.is_single_observation:
        return format_price_whole(card.minimum)
    return _range_text(card.minimum, card.maximum)


def build_dato_del_dia(
    cards: Sequence[ProductCard],
    *,
    product_name: str,
    province_names: Sequence[str],
    week_from: date,
    week_to: date,
) -> DatoDelDiaCard:
    """Arma la tarjeta para un producto, una fila por provincia de `province_names`.

    Un producto puede traer varias `ProductCard` por provincia — una por
    formato o variante declarado (`cells.RetailRange`, con `format_id` en la
    clave desde el 2026-08-12). Acá se muestra UNA sola cifra por provincia:
    la del formato con más observaciones — `build_cards` ya ordena por
    conteo descendente, así que alcanza con quedarse con la primera. No hay
    agregación entre formatos distintos en ningún otro lugar del sitio, y
    sumarlos acá inventaría una unidad compuesta que nadie definió.

    Una provincia sin ninguna tarjeta queda con `has_data=False`: la
    plantilla decide cómo mostrar «sin datos esta semana», no esta función.
    """
    slots = province_color_slots(province_names)
    por_provincia: dict[str, ProductCard] = {}
    for card in cards:
        if card.product_name != product_name:
            continue
        por_provincia.setdefault(card.province_name, card)

    rows = [
        ProvinceRow(
            name=name,
            color=_PROVINCE_COLORS.get(slots.get(name, 9), _OTHER_COLOR),
            has_data=name in por_provincia,
            range_text=_format_range(por_provincia[name]) if name in por_provincia else "",
            median_text=format_price_whole(por_provincia[name].median) if name in por_provincia else "",
            count=por_provincia[name].count if name in por_provincia else 0,
        )
        for name in province_names
    ]

    con_dato = [r for r in rows if r.has_data]
    if con_dato:
        minimos = [por_provincia[r.name].minimum for r in con_dato]
        maximos = [por_provincia[r.name].maximum for r in con_dato]
        escala_min, escala_max = min(minimos), max(maximos)
        rango = escala_max - escala_min

        def _con_barra(row: ProvinceRow) -> ProvinceRow:
            origen = por_provincia[row.name]
            if not rango:
                return replace(row, bar_left_pct=0.0, bar_width_pct=100.0, median_pct=50.0)
            left = float((origen.minimum - escala_min) / rango * 100)
            width = float((origen.maximum - origen.minimum) / rango * 100)
            median_pct = float((origen.median - escala_min) / rango * 100)
            return replace(
                row,
                bar_left_pct=left,
                bar_width_pct=max(width, _MIN_BAR_WIDTH_PCT),
                median_pct=median_pct,
            )

        rows = [_con_barra(r) if r.has_data else r for r in rows]

        if len(con_dato) >= 2:
            mas_barata = min(con_dato, key=lambda r: por_provincia[r.name].median)
            rows = [
                replace(r, is_cheapest=True) if r.has_data and r.name == mas_barata.name else r
                for r in rows
            ]

    referencia = next(iter(por_provincia.values()), None)
    unidad_corta = referencia.unit if referencia else ""
    unidad = unit_noun(referencia.unit) if referencia else ""
    moneda = referencia.currency if referencia else "CUP"

    return DatoDelDiaCard(
        product_name=product_name,
        unit=unidad_corta,
        unit_noun=unidad or "",
        currency=moneda,
        week_from=f"{week_from.day:02d}/{week_from.month:02d}",
        week_to=f"{week_to.day:02d}/{week_to.month:02d}",
        rows=rows,
    )


# --- «Cómo se movió»: mediana de ahora contra la del período anterior ------

#: Coordenadas del gráfico de líneas dentro del SVG 872×380 de
#: `marketing-design/project/Subio.dc.html`. Propias de esta tarjeta —no las
#: mismas que `price_charts.SLOPE_*`, pensadas para el gráfico chico del
#: panel flotante (360×176)— porque una imagen para redes no necesita
#: coincidir en tamaño con eso, solo en el dato.
_CHART_X_BEFORE = 150.0
_CHART_X_NOW = 722.0
_CHART_TOP_Y = 60.0
_CHART_BOTTOM_Y = 350.0


@dataclass(frozen=True)
class ProvinceSlope:
    name: str
    color: str
    has_line: bool
    change_text: str = ""
    before_text: str = ""
    now_text: str = ""
    before_count: int = 0
    now_count: int = 0
    y_before: float = 0.0
    y_now: float = 0.0


@dataclass(frozen=True)
class ComoSeMovioCard:
    product_name: str
    unit: str
    unit_noun: str
    currency: str
    before_label: str
    now_label: str
    rows: list[ProvinceSlope]
    x_before: float = _CHART_X_BEFORE
    x_now: float = _CHART_X_NOW


def _chart_y(value: float, lo: float, hi: float) -> float:
    if hi <= lo:
        return (_CHART_TOP_Y + _CHART_BOTTOM_Y) / 2
    frac = (value - lo) / (hi - lo)
    return _CHART_BOTTOM_Y - frac * (_CHART_BOTTOM_Y - _CHART_TOP_Y)


def build_como_se_movio(
    slopes: Sequence[SlopeChart], *, product_name: str, province_names: Sequence[str]
) -> ComoSeMovioCard | None:
    """Arma «Cómo se movió» a partir de los `SlopeChart` que ya calcula
    `price_charts.build_month_vs_month` — el mismo «¿subió o bajó?» que
    muestran la tarjeta simple y el panel flotante del tablero.

    `None` si no hay ningún `SlopeChart` para este producto: sin una sola
    provincia con dato en alguno de los dos períodos no hay nada que dibujar,
    y una tarjeta vacía sería peor que no ofrecerla.
    """
    chart = next((s for s in slopes if s.product_name == product_name), None)
    if chart is None:
        return None

    por_provincia = {line.province_name: line for line in chart.lines}
    con_linea = [
        por_provincia[n] for n in province_names
        if por_provincia.get(n) and por_provincia[n].has_line
    ]
    valores = [
        float(v)
        for line in con_linea
        for v in (line.before_median, line.now_median)
        if v is not None
    ]
    lo, hi = (min(valores), max(valores)) if valores else (0.0, 1.0)
    # Un 10% de margen para que un punto extremo no quede pegado al borde.
    margen = (hi - lo) * 0.1 if hi > lo else 1.0
    lo, hi = lo - margen, hi + margen

    slots = province_color_slots(province_names)
    rows = []
    for name in province_names:
        color = _PROVINCE_COLORS.get(slots.get(name, 9), _OTHER_COLOR)
        line = por_provincia.get(name)
        if line is None or line.before_median is None or line.now_median is None:
            rows.append(ProvinceSlope(name=name, color=color, has_line=False))
            continue
        rows.append(
            ProvinceSlope(
                name=name,
                color=color,
                has_line=True,
                change_text=line.change_text,
                before_text=format_price_whole(line.before_median),
                now_text=format_price_whole(line.now_median),
                before_count=line.before_count,
                now_count=line.now_count,
                y_before=_chart_y(float(line.before_median), lo, hi),
                y_now=_chart_y(float(line.now_median), lo, hi),
            )
        )

    return ComoSeMovioCard(
        product_name=product_name,
        unit=chart.unit,
        unit_noun=unit_noun(chart.unit) or "",
        currency=chart.currency,
        before_label=chart.before_label,
        now_label=chart.now_label,
        rows=rows,
    )


# --- «De dónde sale este precio»: anuncios reales detrás de una cifra ------


@dataclass(frozen=True)
class AnuncioRow:
    day_text: str
    price_text: str


@dataclass(frozen=True)
class DeDondeSaleCard:
    product_name: str
    province_name: str
    unit: str
    currency: str
    range_text: str
    count: int
    rows: list[AnuncioRow]


def build_de_donde_sale(
    observations: Sequence[RetailObservation],
    *,
    product_name: str,
    province_name: str,
    max_rows: int = 4,
) -> DeDondeSaleCard | None:
    """Arma «De dónde sale este precio»: el rango y los anuncios más
    recientes detrás de un producto en una provincia.

    Cruza formatos y variantes a propósito —a diferencia de «Dato del día»,
    que se queda con el de más muestra—: esta tarjeta explica el método
    («varios anuncios, un rango»), no compara un formato puntual, así que
    mostrar más anuncios reales sirve mejor a esa explicación.

    El enlace de cada fila queda siempre en «captura», nunca un link real al
    anuncio: decidir si conviene exponer ese enlace en una pieza pública es
    una decisión aparte, sin tomar todavía (`docs/decisions.md`).
    """
    filtradas = [
        o for o in observations
        if o.product_name == product_name and o.province_name == province_name
    ]
    if not filtradas:
        return None

    precios = [o.unit_price for o in filtradas]
    minimo, maximo = min(precios), max(precios)
    range_text = _range_text(minimo, maximo)
    recientes = sorted(filtradas, key=lambda o: o.day, reverse=True)[:max_rows]

    return DeDondeSaleCard(
        product_name=product_name,
        province_name=province_name,
        unit=filtradas[0].unit,
        currency=filtradas[0].currency,
        range_text=range_text,
        count=len(filtradas),
        rows=[
            AnuncioRow(
                day_text=f"{o.day.day:02d}/{o.day.month:02d}",
                price_text=format_price_whole(o.unit_price),
            )
            for o in recientes
        ],
    )
