"""Presentación de la matriz de celdas: agrupado por producto y barra de rango.

`cells.py` decide **qué** es una celda; este módulo decide **cómo se lee**. La
separación importa: la identidad de la celda (producto × provincia × unidad ×
formato) es dominio y no se toca desde una decisión visual.

Dos cosas, las dos con geometría calculada en Python y no en Jinja, para que
sean testeables:

1. `group_cells` junta las series del mismo producto en la misma provincia bajo
   un encabezado. Antes «Arroz» se repetía tres veces y se leía como tres
   productos en vez de un producto con tres series.
2. `build_range_bar` dibuja el rango mínimo–mediana–máximo de cada serie.

**La barra es codificación proporcional, no veredicto.** `spec.md` §12.6
prohíbe fijar un umbral de dispersión, así que ninguna celda se pinta como
buena o mala: la barra dibuja el rango que la celda ya tiene y quien juzga es
el operador — lo mismo que ya hacía la columna de amplitud, pero en una forma
que salta a la vista en vez de quedar como un número al final de la fila.

**La escala es logarítmica** porque la amplitud es una razón (máximo ÷ mínimo),
no una diferencia: en escala lineal una celda de 44× y una de 103× se verían
casi iguales contra un máximo de 103, y una de 2× sería invisible. En
logarítmica las tres se distinguen. Por el mismo motivo la mediana se ubica en
escala logarítmica dentro de su propio rango: es la posición que responde
«¿dónde está la masa de las observaciones?».

La normalización es contra **toda la tabla**, no contra el grupo: si cada
producto se escalara solo, la amplitud de Azúcar se vería igual de ancha que
la de Arroz y la comparación entre productos mentiría.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal

from saimc.cells import Cell

# Piso de ancho visible. Sin él, una amplitud apenas mayor a 1 contra un
# máximo grande da una barra de fracción de píxel, que se lee como «no hay
# barra» y no como «el rango es chiquito» — que son cosas distintas.
_MIN_VISIBLE_WIDTH_PCT = 4.0


@dataclass(frozen=True)
class RangeBar:
    """Geometría de la barra de una serie, en porcentajes listos para CSS."""

    # Largo de la barra: la amplitud de esta celda contra la mayor de la tabla.
    width_pct: float
    # Posición del marcador de la mediana dentro de la barra.
    median_pct: float
    # `False` cuando no hay rango que dibujar (mínimo == máximo, una sola
    # observación, o un mínimo en cero que haría explotar la razón).
    has_range: bool


def build_range_bar(
    *, minimum: Decimal, median: Decimal, maximum: Decimal, max_spread: Decimal
) -> RangeBar:
    """Barra de una serie, escalada contra `max_spread` (el de toda la tabla)."""
    sin_barra = RangeBar(width_pct=0.0, median_pct=0.0, has_range=False)

    if minimum <= 0 or maximum <= minimum:
        return sin_barra

    spread = math.log(float(maximum) / float(minimum))
    techo = math.log(float(max_spread)) if max_spread > 1 else 0.0
    if techo <= 0:
        return sin_barra

    width_pct = max(_MIN_VISIBLE_WIDTH_PCT, min(100.0, spread / techo * 100.0))

    # Dónde cae la mediana dentro del propio rango de la celda. Pegada al
    # borde izquierdo significa que la masa de las observaciones está abajo y
    # el máximo es un extremo suelto — la forma típica de una celda que mezcla
    # precio minorista con un saco mayorista.
    median_pct = min(100.0, max(0.0, math.log(float(median) / float(minimum)) / spread * 100.0))

    return RangeBar(width_pct=width_pct, median_pct=median_pct, has_range=True)


@dataclass(frozen=True)
class CellWithBar:
    """Una celda con su barra ya calculada, para no hacer geometría en Jinja."""

    cell: Cell
    bar: RangeBar


@dataclass(frozen=True)
class CellGroup:
    """Todas las series de un producto en una provincia."""

    product_name: str
    province_name: str | None
    cells: list[CellWithBar]
    total: int


def group_cells(cells: list[Cell]) -> list[CellGroup]:
    """Agrupa por producto × provincia, con los grupos de mayor muestra primero.

    El orden de las series dentro del grupo se hereda de `compute_cells` —que
    ya ordena por tamaño de muestra— así que acá no se reordena.

    La provincia entra en la clave del grupo: fundir el Arroz de dos provincias
    bajo un mismo encabezado afirmaría una comparación que nadie pidió, y es
    justamente la separación que la matriz existe para sostener.
    """
    if not cells:
        return []

    # La amplitud mayor de la tabla fija la escala de todas las barras.
    amplitudes = [
        Decimal(cell.maximum) / Decimal(cell.minimum) for cell in cells if cell.minimum > 0
    ]
    max_spread = max(amplitudes, default=Decimal(1))

    grouped: dict[tuple[str, str | None], list[CellWithBar]] = {}
    for cell in cells:
        clave = (cell.product_name, cell.province_name)
        grouped.setdefault(clave, []).append(
            CellWithBar(
                cell=cell,
                bar=build_range_bar(
                    minimum=cell.minimum,
                    median=cell.median,
                    maximum=cell.maximum,
                    max_spread=max_spread,
                ),
            )
        )

    grupos = [
        CellGroup(
            product_name=product_name,
            province_name=province_name,
            cells=celdas,
            total=sum(c.cell.count for c in celdas),
        )
        for (product_name, province_name), celdas in grouped.items()
    ]

    # Mismo criterio que `compute_cells`: lo que tiene muestra primero, con
    # desempates explícitos para que el orden no lo decida el hash del dict.
    grupos.sort(key=lambda g: (-g.total, g.product_name, g.province_name or ""))
    return grupos
