"""Agregación por celda producto × provincia × moneda × unidad × formato.

Es el instrumento de control del operador: responde «¿lo que estoy cargando
sirve?» sin tener que consultar la base a mano. La matriz producto × provincia
es la salida que la especificación define como núcleo de la propuesta (§5, «objeto
medido»), así que esta agregación es también el primer paso hacia el dashboard
público de la Fase 1.

**Reporta mediciones, no veredictos.** No hay umbral de muestra mínima ni
marca de «dispersión alta»: §12.6 es explícito en que un umbral
asignado por intuición y presentado como metodología es peor que no tener
umbral. N se calibra con datos reales —«análisis de estabilidad de la mediana
frente al tamaño de muestra»— y esta vista es justamente lo que permite
hacerlo. Quien juzga es el operador.

La moneda entra en la clave de la celda a propósito. `price_cup` existe pero
todavía no lo llena nadie (falta la integración con la tasa de cambio, §6.3 E):
promediar CUP con USD acá sería inventar una conversión que el sistema no tiene.

`unit` y `sale_format` entran por el mismo motivo. Un precio por libra y uno por
kilo no son la misma serie, y un saco mayorista no es comparable con la libra
suelta aunque ambos digan `lb`. Fundirlos produce una mediana que no describe
nada — el caso real del 2026-08-12: `Arroz` en Santiago mezclaba 730 por libra,
750 por kilo y dos sacos de 110 lb en una sola celda.

Ambas columnas son nulables, y el nulo **no se funde con nada**: «sin responder»
es su propia celda. Tratarlo como un valor más afirmaría una equivalencia que
nadie declaró, que es el mismo criterio por el que `canonical_unit()` devuelve
`None` en vez de adivinar. La contracara es que mientras el operador no responda
`sale_format` la cola de nulos crece — y eso es justamente lo que la vista debe
dejar ver, no esconder.

El envase (`packaging`) **no** entra en la clave: es texto libre sin vocabulario
cerrado («saco de 25 kg», «Saco de arroz De 110 lb»), así que agrupar por él
produciría una celda por variante de redacción. Si `unit` + `sale_format` no
alcanzan para separar, la decisión de darle vocabulario cerrado se toma con los
datos de esta misma vista, no antes (§12.6).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from saimc.models import PriceHistory, Product, Province


@dataclass(frozen=True)
class Cell:
    """Una celda de la matriz, con su muestra ya resumida."""

    product_name: str
    province_name: str | None
    currency: str
    unit: str | None
    sale_format: str | None
    count: int
    median: Decimal
    minimum: Decimal
    maximum: Decimal

    @property
    def spread(self) -> Decimal | None:
        """Cuántas veces el máximo contiene al mínimo.

        Es la señal que delata una celda que mezcla cosas distintas —precio
        minorista por libra contra saco mayorista, por ejemplo— sin necesidad
        de que el sistema decida qué es «demasiado». `None` cuando el mínimo
        es cero, que no debería pasar pero no vale la pena que rompa la vista.
        """
        if self.minimum == 0:
            return None
        return self.maximum / self.minimum


def compute_cells(session: Session) -> list[Cell]:
    """Resume `prices_history` en celdas, de mayor a menor tamaño de muestra.

    El orden es deliberado: lo que tiene muestra aparece primero, y la cola
    larga de celdas con una sola observación queda a la vista al final. Esa
    cola es en sí misma el diagnóstico de dispersión de productos.
    """
    stmt = (
        select(
            Product.name,
            Province.name,
            PriceHistory.currency,
            PriceHistory.unit,
            PriceHistory.sale_format,
            PriceHistory.price,
        )
        .join(Product, Product.id == PriceHistory.product_id)
        .outerjoin(Province, Province.id == PriceHistory.province_id)
    )

    CellKey = tuple[str, str | None, str, str | None, str | None]
    grouped: dict[CellKey, list[Decimal]] = {}
    for product_name, province_name, currency, unit, sale_format, price in session.execute(
        stmt
    ):
        key: CellKey = (product_name, province_name, currency, unit, sale_format)
        grouped.setdefault(key, []).append(price)

    cells = [
        Cell(
            product_name=product_name,
            province_name=province_name,
            currency=currency,
            unit=unit,
            sale_format=sale_format,
            count=len(prices),
            # La mediana se calcula en Python y no en SQL a propósito:
            # `percentile_cont` es de PostgreSQL y los tests corren sobre
            # SQLite (ver tests/conftest.py). Con el volumen de la Fase 0 la
            # diferencia de costo es irrelevante.
            median=statistics.median(prices),
            minimum=min(prices),
            maximum=max(prices),
        )
        for (
            product_name,
            province_name,
            currency,
            unit,
            sale_format,
        ), prices in grouped.items()
    ]

    # Los desempates son explícitos porque partir la celda multiplica los
    # empates de `count`: sin ellos el orden lo decidiría el hash del dict y la
    # vista cambiaría de forma entre recargas sin que nada haya cambiado.
    cells.sort(
        key=lambda cell: (
            -cell.count,
            cell.product_name,
            cell.province_name or "",
            cell.unit or "",
            cell.sale_format or "",
        )
    )
    return cells
