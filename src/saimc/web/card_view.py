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

Lo que esta vista **no** hace todavía, y está en el GitHub Project (ver
`docs/backlog.md`): semáforo de tendencia (color/alarma), índice de
disponibilidad y recomendación de compra. Los tres necesitan un umbral sin
calibrar que §12.6 prohíbe inventar.

**El cambio vs. el período anterior sí se muestra** (`change_text`, desde
2026-09-14): es el mismo número sin color que ya usa el panel flotante
(`Headline.change_text`, §12.6 — comparación como número, nunca semáforo), no
uno de los cuatro bloqueados. Responde «¿sube o baja?» sin inventar ningún
umbral; el operador lo pidió explícitamente para que la tarjeta simple
conteste las tres preguntas de §6.7 (caro/barato, sube/baja, con qué muestra)
sin tener que abrir el panel flotante.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

from saimc.cells import RetailRange
from saimc.web.product_nav import has_product_illustration, product_icon, product_slug

if TYPE_CHECKING:
    # Bajo `TYPE_CHECKING` nomás: `price_charts.py` importa `format_price` de
    # este mismo módulo, así que un import normal acá sería circular en
    # runtime. `from __future__ import annotations` ya vuelve perezosa la
    # anotación de tipos, así que mypy lo resuelve igual sin ejecutarlo.
    from saimc.web.price_charts import Headline

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
    """Un formato en una provincia, con su rango y la muestra que lo respalda.

    `minimum`/`median`/`maximum` son el precio por unidad canónica del
    producto — el comparador entre formatos. `format_*` traen el precio del
    formato, que es el titular publicado cuando hay formato declarado.
    """

    product_name: str
    province_name: str
    currency: str
    unit: str
    count: int
    minimum: Decimal
    median: Decimal
    maximum: Decimal
    format_label: str | None = None
    format_minimum: Decimal | None = None
    format_median: Decimal | None = None
    format_maximum: Decimal | None = None
    # Variante declarada (product-variants): «girasol Vitalín importado»,
    # distinguiéndola de la genérica y de otras variantes del mismo producto
    # en la misma provincia. `None` sin variante declarada.
    variant_label: str | None = None
    #: Cambio vs. el período anterior del mismo largo, ya formateado
    #: (`Headline.change_text`, p. ej. «+8%»); `""` sin comparación posible.
    #: Se pega con `attach_trends`, vacío hasta entonces.
    change_text: str = ""

    @property
    def state(self) -> str:
        return SAMPLE_FORMING

    @property
    def slug(self) -> str:
        return product_slug(self.product_name)

    @property
    def icon(self) -> str | None:
        """El ícono Material Symbols del producto, o `None` si no tiene."""
        return product_icon(self.product_name)

    @property
    def has_illustration(self) -> bool:
        """Si el producto tiene ícono ilustrado propio (ver
        `templates/_product_illustration.html`) para el ancla visual de la
        tarjeta, en vez del glifo de Material Symbols del chip."""
        return has_product_illustration(self.product_name)

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
    def format_minimum_text(self) -> str | None:
        return None if self.format_minimum is None else format_price(self.format_minimum)

    @property
    def format_median_text(self) -> str | None:
        return None if self.format_median is None else format_price(self.format_median)

    @property
    def format_maximum_text(self) -> str | None:
        return None if self.format_maximum is None else format_price(self.format_maximum)

    @property
    def is_single_observation(self) -> bool:
        """Con una sola observación no hay rango, hay un dato suelto.

        No es un umbral de publicación: es la diferencia aritmética entre tener
        dispersión y no tenerla. Con n=1 el mínimo, la mediana y el máximo son
        el mismo número, y presentarlos como «rango» sugeriría una variación
        observada que no se observó.
        """
        return self.count == 1

    @property
    def median_pct(self) -> float | None:
        """Dónde cae la mediana dentro del propio [mínimo, máximo], en escala
        logarítmica — 2026-09-16, Dirección D (tablero) sobre las propuestas
        de diseño.

        Mismo criterio que `matrix.build_range_bar`, pero autocontenido: esa
        barra escala el ANCHO contra el resto de una tabla; esta no tiene con
        qué comparar, así que siempre ocupa el 100% de la tarjeta y lo único
        que varía es dónde cae la marca de la mediana. Log y no lineal por el
        mismo motivo que ahí: un saco mayorista mezclado con precio minorista
        estira el máximo mucho más de lo que separa al resto de las
        observaciones, y una escala lineal aplastaría la marca contra el
        borde izquierdo.

        Usa el rango de FORMATO cuando hay uno declarado —el mismo que
        `format_minimum_text`/`format_median_text`/`format_maximum_text` ya
        muestran como titular—, para que la barra describa el mismo número
        que el texto de al lado.

        `None` sin rango que dibujar: mínimo ≤ 0 (no debería pasar con
        precios, pero `build_range_bar` se cubre del mismo caso) o mínimo ==
        máximo —una sola observación, que la plantilla ya trata aparte
        (`is_single_observation`)."""
        minimo = self.format_minimum if self.format_minimum is not None else self.minimum
        maximo = self.format_maximum if self.format_maximum is not None else self.maximum
        mediana = self.format_median if self.format_median is not None else self.median
        if minimo <= 0 or maximo <= minimo:
            return None
        spread = math.log(float(maximo) / float(minimo))
        return min(100.0, max(0.0, math.log(float(mediana) / float(minimo)) / spread * 100.0))


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
            format_label=r.format_label,
            format_minimum=r.format_minimum,
            format_median=r.format_median,
            format_maximum=r.format_maximum,
            variant_label=r.variant_label,
        )
        for r in ranges
        if r.province_name is not None and not r.unconverted
    ]
    cards.sort(key=lambda c: (-c.count, c.product_name, c.province_name))
    return cards


def group_cards_by_province(
    cards: Sequence[ProductCard], province_names: Sequence[str]
) -> list[tuple[str, list[ProductCard]]]:
    """Las tarjetas agrupadas por provincia, en el orden de `province_names`
    (ya alfabético — lo resuelve `_render_home`, acá no se reordena nada).

    Para el modo «Tablero» (2026-09-16): mismas tarjetas que ya arma
    `build_cards`, mismo componente `.ph-card` de siempre, solo agrupadas en
    vez de en una sola lista plana. Una provincia sin ninguna tarjeta no
    entra —no hay nada que agrupar bajo su nombre."""
    por_provincia: dict[str, list[ProductCard]] = {}
    for card in cards:
        por_provincia.setdefault(card.province_name, []).append(card)
    return [
        (name, por_provincia[name]) for name in province_names if name in por_provincia
    ]


def attach_trends(cards: list[ProductCard], headlines: Sequence[Headline]) -> list[ProductCard]:
    """Pega a cada tarjeta el cambio vs. el período anterior, si hay uno.

    Reusa `Headline.change_text` tal cual —el mismo número sin color que ya
    muestra el panel flotante (`panel_view.attach_blocks`)—, emparejado por
    producto + provincia + moneda + unidad. Una tarjeta de formato o variante
    comparte el cambio agregado de todo el producto en esa provincia: no hay
    comparación previa a nivel de formato/variante todavía, y aproximarla con
    el agregado es preferible a no mostrar nada."""
    cambio_de = {
        (h.product_name, h.province_name, h.currency, h.unit): h.change_text for h in headlines
    }
    return [
        replace(
            c,
            change_text=cambio_de.get((c.product_name, c.province_name, c.currency, c.unit), ""),
        )
        for c in cards
    ]
