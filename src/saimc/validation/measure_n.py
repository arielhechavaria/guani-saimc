"""Estabilidad de la mediana frente al tamaño de muestra (slice C3, control C1).

Corre `python -m saimc.validation.measure_n`. Solo lee: no escribe nada.

Es el análisis que pide `spec.md` §12.6 para calibrar N con datos reales. Toma
las celdas públicas de la ventana de 30 días que tienen 20 filas o más y, para
cada tamaño k de 2 a 15, saca 1.000 submuestras sin reposición de cada celda y
mide cuánto se aparta la mediana de la submuestra de la mediana de la celda
entera. Informa, por k, la mediana y el percentil 90 de ese error relativo
(juntando las submuestras de todas las celdas) y sobre cuántas celdas se midió.

**No fija N.** El operador elige el error que tolera y el dato da el N, en G1b.
La publicación no cambia: C1 sigue «informativa» como manda §10.7.

Los cuatro números de abajo son del análisis, no de la publicación.

Contrato: `docs/spec-slice-C3.md`, «C1 — Medir N».
"""

from __future__ import annotations

import random
import statistics
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from saimc.db import session_scope
from saimc.validation.control_cells import window_cells
from saimc.validation.fence import quantile
from saimc.web.product_nav import DEFAULT_WINDOW_DAYS

#: Filas mínimas para que una celda entre al análisis: hace falta bastante más
#: que el k más grande para que la submuestra no sea casi la celda entera.
MIN_CELL_ROWS = 20
#: Tamaños de submuestra que se miden.
SAMPLE_SIZES = range(2, 16)
#: Submuestras por celda y por tamaño.
SUBSAMPLES = 1000
#: Semilla fija: dos corridas sobre los mismos datos dan lo mismo.
SEED = 20261003


@dataclass(frozen=True)
class SizeResult:
    """El error relativo de la mediana con submuestras de tamaño `k`."""

    k: int
    median_error: float
    p90_error: float
    #: Sobre cuántas celdas se midió.
    cells: int


def measure(
    cells: Iterable[Sequence[Decimal]], *, seed: int = SEED, subsamples: int = SUBSAMPLES
) -> list[SizeResult]:
    """Mide el error de la mediana por tamaño de submuestra. Código puro.

    `cells` son los precios de cada celda. Las que no llegan a
    `MIN_CELL_ROWS` no entran; sin ninguna, devuelve la lista vacía.

    Los precios y las celdas se ordenan antes de sortear: el resultado depende
    de los datos y de la semilla, no del orden en que la base los devolvió.
    """
    eligible = sorted(
        sorted(float(price) for price in prices)
        for prices in cells
        if len(prices) >= MIN_CELL_ROWS
    )
    if not eligible:
        return []
    rng = random.Random(seed)  # noqa: S311 — sorteo estadístico, no criptografía
    results: list[SizeResult] = []
    for k in SAMPLE_SIZES:
        errors: list[float] = []
        for prices in eligible:
            full = statistics.median(prices)
            for _ in range(subsamples):
                errors.append(abs(statistics.median(rng.sample(prices, k)) - full) / full)
        errors.sort()
        results.append(
            SizeResult(
                k=k,
                median_error=quantile(errors, 0.5),
                p90_error=quantile(errors, 0.9),
                cells=len(eligible),
            )
        )
    return results


def load_cells(session: Session, *, hoy: date | None = None) -> list[list[Decimal]]:
    """Los precios de cada celda pública de la ventana de 30 días."""
    return [
        [observation.unit_price for observation in observations]
        for observations in window_cells(session, hoy=hoy).values()
    ]


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%".replace(".", ",")


def print_report(
    results: Sequence[SizeResult], *, total_cells: int, out: Callable[[str], None]
) -> None:
    out("Estabilidad de la mediana frente al tamaño de muestra (spec.md §12.6).")
    out(f"Ventana pública: últimos {DEFAULT_WINDOW_DAYS} días. Celdas en la ventana: {total_cells}.")
    if not results:
        out(
            f"Ninguna celda llega a {MIN_CELL_ROWS} filas en la ventana: "
            "todavía no hay con qué medir."
        )
        return
    out(
        f"Celdas con {MIN_CELL_ROWS} filas o más: {results[0].cells}. "
        f"{SUBSAMPLES} submuestras sin reposición por celda y por tamaño, semilla {SEED}."
    )
    out("Error = distancia entre la mediana de la submuestra y la de la celda, sobre la de la celda.")
    out("")
    out(f"{'k':>3}  {'error mediano':>13}  {'error p90':>9}  {'celdas':>6}")
    for result in results:
        out(
            f"{result.k:>3}  {_pct(result.median_error):>13}  "
            f"{_pct(result.p90_error):>9}  {result.cells:>6}"
        )
    out("")
    out("No fija N: elegí el error que tolerás y leé a qué k corresponde. Se decide en G1b.")


def execute(session: Session, *, out: Callable[[str], None] = print) -> int:
    """Corre la medición y la imprime. Devuelve el código de salida."""
    cells = load_cells(session)
    print_report(measure(cells), total_cells=len(cells), out=out)
    return 0


def main() -> None:
    with session_scope() as session:
        code = execute(session)
    sys.exit(code)


if __name__ == "__main__":
    main()
