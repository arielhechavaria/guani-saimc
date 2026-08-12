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

`compute_retail_ranges` es la otra mitad del módulo: responde «¿en qué rango
se movió este producto en los últimos N días?», donde el envase deja de
importar porque el producto sigue siendo uno solo. Ver su docstring para los
tres filtros que hacen que esa pregunta tenga una respuesta comparable.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from saimc.models import PriceHistory, Product, Province, SaleFormat
from saimc.units import canonical_unit


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


@dataclass(frozen=True)
class RetailRange:
    """Rango de precio **minorista** de un producto, normalizado por unidad.

    A diferencia de `Cell`, esta fila ya no distingue envase ni formato: es
    justamente lo que colapsa `compute_retail_ranges` una vez que descarta lo
    mayorista y lo no comparable. `unit` queda igual en la salida porque es la
    unidad canónica del producto, no un eje de agrupación — todas las filas de
    una misma `RetailRange` ya están en esa unidad.
    """

    product_name: str
    province_name: str | None
    currency: str
    unit: str
    count: int
    median: Decimal
    minimum: Decimal
    maximum: Decimal


def compute_retail_ranges(
    session: Session, *, days: int = 7, hoy: date | None = None
) -> list[RetailRange]:
    """Rango de precio minorista por producto, últimos `days` días.

    Es la otra mitad de `compute_cells`: esa vista separa por formato para que
    el operador vea si está mezclando cosas distintas al cargar; esta vista
    asume que ya lo revisó y responde una pregunta distinta — «¿en qué rango
    se movió Arroz esta semana?» — donde el producto es uno solo aunque el
    envase varíe (2026-08-12).

    Tres filtros, todos deliberados y ninguno inventado:

    - **Solo `sale_format == "retail"`.** Normalizar por cantidad no vuelve
      comparable un saco con un paquete: el caso real medido el 2026-08-12 da
      ~2.5x de diferencia por libra entre mayorista y minorista, que es
      economía real, no ruido de carga. Mezclarlos reproduciría el mismo
      problema que separar por `sale_format` en `compute_cells` resolvió. El
      precio que le importa al hogar —el usuario que define spec.md §8.1
      Nivel 0— es el minorista.
    - **`unit` de la fila debe coincidir con la unidad canónica del producto**
      (`Product.unit`, pasada por `canonical_unit()` porque el seed la guarda
      sin canonicalizar: "litro", no "l"). Sin tabla de conversión lb↔kg: no
      hay una definida, y adivinar un factor es exactamente lo que §12.6
      prohíbe. Una fila en otra unidad queda fuera de este promedio pero
      sigue visible en `compute_cells`.
    - **Requiere `quantity`.** Sin cantidad no hay precio por unidad que
      calcular.

    La fecha de referencia es `published_date` cuando existe; si no, el día en
    que se cargó la fila (`created_at`) — mismo criterio de repliegue que ya
    usa `guess_published_date` en otras partes del sistema. Ambas quedan en
    huso UTC sin ajustar a hora de Cuba: al ritmo de curaduría de la Fase 0
    (~1h diaria) un corrimiento de horas no cambia a qué día cae la fila.
    """
    cutoff = (hoy or date.today()) - timedelta(days=days)

    stmt = (
        select(
            Product.name,
            Product.unit,
            Province.name,
            PriceHistory.currency,
            PriceHistory.unit,
            PriceHistory.quantity,
            PriceHistory.price,
            PriceHistory.published_date,
            PriceHistory.created_at,
        )
        .join(Product, Product.id == PriceHistory.product_id)
        .outerjoin(Province, Province.id == PriceHistory.province_id)
        .where(PriceHistory.sale_format == SaleFormat.RETAIL.value)
        .where(PriceHistory.unit.is_not(None))
        .where(PriceHistory.quantity.is_not(None))
    )

    RangeKey = tuple[str, str | None, str, str]
    grouped: dict[RangeKey, list[Decimal]] = {}
    for (
        product_name,
        product_unit,
        province_name,
        currency,
        row_unit,
        quantity,
        price,
        published_date,
        created_at,
    ) in session.execute(stmt):
        if quantity == 0:
            continue
        if row_unit != canonical_unit(product_unit):
            continue

        effective_date = published_date or created_at.date()
        if effective_date < cutoff:
            continue

        key: RangeKey = (product_name, province_name, currency, row_unit)
        grouped.setdefault(key, []).append(price / quantity)

    ranges = [
        RetailRange(
            product_name=product_name,
            province_name=province_name,
            currency=currency,
            unit=unit,
            count=len(unit_prices),
            median=statistics.median(unit_prices),
            minimum=min(unit_prices),
            maximum=max(unit_prices),
        )
        for (product_name, province_name, currency, unit), unit_prices in grouped.items()
    ]

    ranges.sort(
        key=lambda r: (-r.count, r.product_name, r.province_name or "")
    )
    return ranges
