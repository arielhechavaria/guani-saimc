"""Contexto de la tarjeta «Dato del día» (slice de marketing, 2026-09-28).

Puro: recibe las `ProductCard` que ya arma `card_view.build_cards` (el mismo
cómputo que usa la vista pública) y devuelve lo que la plantilla necesita
pintar. No consulta la base ni convierte monedas — eso ya pasó antes, en
`cells.compute_retail_ranges`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date

from saimc.units import unit_noun
from saimc.web.card_view import ProductCard, province_color_slots

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


def _format_range(card: ProductCard) -> str:
    lo, hi = card.minimum_text, card.maximum_text
    return lo if card.is_single_observation or lo == hi else f"{lo} – {hi}"


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
            median_text=por_provincia[name].median_text if name in por_provincia else "",
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
