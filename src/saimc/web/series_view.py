"""Gráfico de evolución de precio, como SVG generado en el servidor.

Mismo reparto que `matrix.py`, `map_view.py` y `card_view.py`: acá se calculan
las coordenadas y la plantilla solo las escupe dentro de un `<svg>`. Es el
invariante 2 del ADR 001 — si el número se arma en Jinja no se puede testear.

**Por qué SVG en servidor y no Chart.js.** El ADR 001 dejó esa decisión abierta
a propósito, «para medirla cuando el mapa ya exista». Medida el 2026-08-23: la
serie más densa —Arroz/La Habana— son 17 observaciones repartidas en 14 días de
los últimos 30. Con esa cantidad de puntos y sin ninguna interacción, los ~60 KB
de Chart.js compran zoom, tooltips y animación que este gráfico no usa, y serían
la única dependencia JS de una página que hoy no tiene ninguna. El mismo
argumento que descartó MapLibre para el mapa, ahora con el número al lado.

**Por qué no hay una línea que una los puntos.** Es la decisión de fondo de este
módulo, y no es estética. Arroz/La Habana tiene dato en 14 de 30 días;
Aceite/La Habana, en **3**. Una línea continua entre el 14/08 y el 17/08 dibuja
tres precios que nadie observó, y con Aceite uniría tres puntos sueltos en un mes
afirmando treinta días de precios inventados. §12.6 prohíbe inventar umbrales; el
mismo criterio vale para inventar observaciones. El gráfico muestra **dónde hubo
anuncios y a cuánto**, con los huecos a la vista, que es exactamente lo que la
muestra sostiene.

La banda mín–máx y la línea de la mediana sí son horizontales y abarcan todo el
ancho: describen la muestra entera del período, no una interpolación temporal.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from saimc.cells import RetailObservation
from saimc.web.card_view import format_price

#: Lienzo del gráfico, en unidades de `viewBox`. Fijo y sin unidades físicas: el
#: SVG escala al ancho del contenedor, así que esto es proporción, no tamaño.
WIDTH = 600.0
HEIGHT = 170.0
PAD_LEFT = 6.0
PAD_RIGHT = 6.0
PAD_TOP = 12.0
PAD_BOTTOM = 26.0

#: Mínimo de días distintos con observación para que haya algo que graficar. Con
#: uno solo no hay evolución: hay un punto. No es un umbral de publicación de los
#: que §12.6 prohíbe —no decide si el dato es válido ni si se publica el precio,
#: que la tarjeta ya muestra— es la condición aritmética para que un eje temporal
#: signifique algo.
MIN_DAYS = 2


@dataclass(frozen=True)
class SeriesPoint:
    """Una observación ubicada en el lienzo."""

    day: date
    price: Decimal
    x: float
    y: float

    @property
    def price_text(self) -> str:
        return format_price(self.price)

    @property
    def day_text(self) -> str:
        return f"{self.day:%d/%m}"


@dataclass(frozen=True)
class PriceSeries:
    """La evolución de una celda producto × provincia dentro de la ventana."""

    product_name: str
    province_name: str
    currency: str
    unit: str
    points: tuple[SeriesPoint, ...]
    days_with_data: int
    window_days: int
    first_day: date
    last_day: date
    #: Coordenadas verticales de la banda y de la mediana, ya en el lienzo.
    band_top: float
    band_bottom: float
    median_y: float
    minimum: Decimal
    median: Decimal
    maximum: Decimal

    @property
    def count(self) -> int:
        return len(self.points)

    @property
    def viewbox(self) -> str:
        return f"0 0 {WIDTH:g} {HEIGHT:g}"

    @property
    def minimum_text(self) -> str:
        return format_price(self.minimum)

    @property
    def median_text(self) -> str:
        return format_price(self.median)

    @property
    def maximum_text(self) -> str:
        return format_price(self.maximum)

    @property
    def first_day_text(self) -> str:
        return f"{self.first_day:%d/%m}"

    @property
    def last_day_text(self) -> str:
        return f"{self.last_day:%d/%m}"

    @property
    def empty_days(self) -> int:
        """Días de la ventana sin ninguna observación. Se muestra, no se esconde."""
        return self.window_days - self.days_with_data


def build_series(
    observations: list[RetailObservation],
    *,
    window_days: int,
    hoy: date,
) -> list[PriceSeries]:
    """Arma una serie por celda producto × provincia.

    Descarta lo mismo que `card_view.build_cards` y por los mismos motivos: filas
    sin provincia (la vista es por provincia) y filas `unconverted` (mezclarlas
    con las de CUP afirmaría una equivalencia que nadie calculó). Y descarta
    además las celdas con menos de `MIN_DAYS` días distintos, porque un solo día
    no es una evolución.

    El eje X abarca la ventana completa —de `hoy - window_days` a `hoy`— y no el
    rango de los datos: si las observaciones se apelotonan en cuatro días de
    agosto, eso tiene que **verse** como cuatro días apelotonados dentro del mes,
    no estirarse hasta ocupar todo el ancho fingiendo cobertura pareja.
    """
    grouped: dict[tuple[str, str, str, str], list[RetailObservation]] = {}
    for observation in observations:
        if observation.province_name is None or observation.unconverted:
            continue
        key = (
            observation.product_name,
            observation.province_name,
            observation.currency,
            observation.unit,
        )
        grouped.setdefault(key, []).append(observation)

    start = hoy - timedelta(days=window_days)
    span_days = max((hoy - start).days, 1)
    plot_width = WIDTH - PAD_LEFT - PAD_RIGHT
    plot_height = HEIGHT - PAD_TOP - PAD_BOTTOM

    series: list[PriceSeries] = []
    for (product_name, province_name, currency, unit), rows in grouped.items():
        days = {row.day for row in rows}
        if len(days) < MIN_DAYS:
            continue

        prices = [row.unit_price for row in rows]
        minimum, maximum = min(prices), max(prices)
        median = statistics.median(prices)

        # Escala vertical con el rango de la propia celda, no una global: cada
        # gráfico responde «¿cómo se movió ESTE producto acá?», y una escala
        # compartida entre Aceite (miles por litro) y Azúcar (cientos por libra)
        # aplastaría las dos series contra el borde.
        spread = maximum - minimum

        def to_y(value: Decimal, *, _spread: Decimal = spread, _min: Decimal = minimum) -> float:
            if _spread == 0:
                # Todas las observaciones al mismo precio: la línea va al medio
                # en vez de dividir por cero o pegarse a un borde.
                return PAD_TOP + plot_height / 2
            share = float((value - _min) / _spread)
            return PAD_TOP + (1 - share) * plot_height  # y crece hacia abajo

        def to_x(day: date) -> float:
            share = (day - start).days / span_days
            return PAD_LEFT + min(max(share, 0.0), 1.0) * plot_width

        points = tuple(
            sorted(
                (
                    SeriesPoint(
                        day=row.day,
                        price=row.unit_price,
                        x=round(to_x(row.day), 1),
                        y=round(to_y(row.unit_price), 1),
                    )
                    for row in rows
                ),
                key=lambda p: (p.day, p.price),
            )
        )

        series.append(
            PriceSeries(
                product_name=product_name,
                province_name=province_name,
                currency=currency,
                unit=unit,
                points=points,
                days_with_data=len(days),
                window_days=window_days,
                first_day=min(days),
                last_day=max(days),
                band_top=round(to_y(maximum), 1),
                band_bottom=round(to_y(minimum), 1),
                median_y=round(to_y(median), 1),
                minimum=minimum,
                median=median,
                maximum=maximum,
            )
        )

    series.sort(key=lambda s: (-s.count, s.product_name, s.province_name))
    return series
