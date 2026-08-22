"""Tarjetas de producto para la vista pública.

Convierte los `RetailRange` que ya calcula `cells.py` en algo mostrable, sin
consultar la base y sin decidir nada que la especificación no haya decidido.
Mismo reparto que `matrix.py` y `map_view.py`: acá se arma, la plantilla pinta.

**Qué se puede publicar hoy, según §9.0.** La sección es explícita y conviene
citarla porque es fácil leerla de más o de menos: «Ninguna celda muestra un
precio como titular sin respaldo de muestra. Por debajo del umbral, el dashboard
muestra el estado "muestra en formación" con el número de observaciones a la
vista, nunca un valor puntual presentado como medición consolidada».

O sea: **no prohíbe mostrar el precio**, prohíbe mostrarlo como titular sin
respaldo y como valor puntual consolidado. Y como la misma §9.0 dice que «las
Fases 0 y 1 son el período de calibración de N», hoy **todas** las celdas están
por debajo del umbral, porque el umbral todavía no existe. De ahí las tres
decisiones de esta vista:

1. Toda tarjeta va rotulada «muestra en formación». No hay estado alternativo
   todavía, y ponerlo condicional exigiría el umbral que §12.6 prohíbe inventar.
2. El titular es el **rango**, no la mediana sola. La mediana se muestra dentro
   del rango, nunca como cifra única: un valor puntual es justo lo que la
   sección llama «medición consolidada».
3. El **tamaño de muestra va siempre visible**, al lado del precio y no en una
   nota al pie.

Lo que esta vista **no** hace todavía, y está en `docs/backlog.md`: semáforo de
tendencia, alerta de variación, índice de disponibilidad y recomendación de
compra. Los cuatro necesitan o densidad de serie o un umbral sin calibrar.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from saimc.cells import RetailRange

#: Rótulo de §9.0 para una celda por debajo del umbral de publicación. Hoy son
#: todas, porque N está sin calibrar y las Fases 0-1 son su calibración.
SAMPLE_FORMING = "muestra en formación"


def format_price(value: Decimal) -> str:
    """Formatea un precio para lectura, en convención de separadores española.

    Redondea a entero por encima de 100 y a dos decimales por debajo. **No es un
    umbral de los de §12.6**: no decide qué se publica ni qué es válido, solo
    evita mostrar «806,0606060606060606060606061» y evita a la vez colapsar a
    cero un precio chico. Es presentación, y cambiarlo no cambiaría ninguna
    medición.
    """
    if value < 100:
        rounded = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        entero, _, decimales = f"{rounded:.2f}".partition(".")
        return f"{int(entero):,}".replace(",", ".") + "," + decimales
    entero = value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return f"{int(entero):,}".replace(",", ".")


@dataclass(frozen=True)
class ProductCard:
    """Un producto en una provincia, con su rango y la muestra que lo respalda."""

    product_name: str
    province_name: str
    currency: str
    unit: str
    count: int
    minimum: Decimal
    median: Decimal
    maximum: Decimal

    @property
    def state(self) -> str:
        return SAMPLE_FORMING

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
    def is_single_observation(self) -> bool:
        """Con una sola observación no hay rango, hay un dato suelto.

        No es un umbral de publicación: es la diferencia aritmética entre tener
        dispersión y no tenerla. Con n=1 el mínimo, la mediana y el máximo son
        el mismo número, y presentarlos como «rango» sugeriría una variación
        observada que no se observó.
        """
        return self.count == 1


def build_cards(ranges: list[RetailRange]) -> list[ProductCard]:
    """Arma las tarjetas publicables a partir de los rangos minoristas.

    Descarta dos clases de fila, ambas por no ser publicables y no por ser malas:

    - **Sin provincia.** La tarjeta responde «cuánto cuesta acá», y una fila sin
      provincia no contesta esa pregunta. Sigue contando en la matriz del
      operador.
    - **`unconverted`.** Es la fila en moneda extranjera cuya tasa no se
      resolvió. Mostrarla junto a las de CUP afirmaría una equivalencia que
      nadie calculó, que es exactamente lo que la bandera existe para evitar.

    Ordena por tamaño de muestra descendente: lo mejor respaldado primero. No es
    un ranking de precio ni de importancia — es cuánta evidencia hay detrás.
    """
    cards = [
        ProductCard(
            product_name=r.product_name,
            province_name=r.province_name,
            currency=r.currency,
            unit=r.unit,
            count=r.count,
            minimum=r.minimum,
            median=r.median,
            maximum=r.maximum,
        )
        for r in ranges
        if r.province_name is not None and not r.unconverted
    ]
    cards.sort(key=lambda c: (-c.count, c.product_name, c.province_name))
    return cards
