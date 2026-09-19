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
(`SlopeLine.change_text`, §12.6 — comparación como número, nunca semáforo), no
uno de los cuatro bloqueados. Responde «¿sube o baja?» sin inventar ningún
umbral; el operador lo pidió explícitamente para que la tarjeta simple
conteste las tres preguntas de §6.7 (caro/barato, sube/baja, con qué muestra)
sin tener que abrir el panel flotante.
"""

from __future__ import annotations

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
    from saimc.web.price_charts import SlopeChart

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
    #: (`SlopeLine.change_text`, p. ej. «+8%»); `""` sin comparación posible.
    #: Se pega con `attach_trends`, vacío hasta entonces.
    change_text: str = ""

    @property
    def format_label_display(self) -> str | None:
        """`format_label` con solo la primera letra en mayúscula, o `None` sin
        formato. El filtro `capitalize` de Jinja pone en minúscula todo el
        resto —«botella de 1 L» quedaría «Botella de 1 l», con la unidad
        rota—, así que se resuelve acá y no en la plantilla."""
        if self.format_label is None:
            return None
        return self.format_label[0].upper() + self.format_label[1:]

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


#: Slots de color con nombre propio en `design_tokens.css`
#: (`--m3-prov-0`/`-1`/`-2`); el resto cae en el neutral `9`. Mismo criterio
#: que `matrix._province_slot`, pero acotado a 3 en vez de a
#: `_PROVINCE_SLOTS = 4`: esa constante deja pasar un `% 4` que puede dar 3,
#: y no hay `--m3-prov-3` — no se toca matrix.py en este slice (no es lo que
#: se pidió), pero acá sí importa no repetir el mismo hueco.
_NAMED_PROVINCE_SLOTS = 3
_OTHER_PROVINCE_SLOT = 9


def province_color_slots(province_names: Sequence[str]) -> dict[str, int]:
    """Slot de color (0/1/2/9) por provincia, en el orden en que se reciben
    —ya alfabético, mismo orden que agrupa `group_cards_by_province`—.

    Puramente posicional: no mira precio ni cantidad de anuncios, solo el
    orden de la lista que le pasan. Sin paleta para dieciséis provincias
    distintas: las primeras tres tienen color propio, el resto comparte el
    neutral — forzar una paleta más grande inventaría una identidad que
    nadie decidió.

    Qué lista pasarle es decisión de quien llama, no de esta función. El
    tablero (2026-09-16) la llamó primero sobre el catálogo completo de 16
    —identidad estable entre cargas—, pero con datos reales solo en dos
    provincias eso las dejaba a las dos en el mismo slot neutral: la tabla
    se veía de un solo color (2026-09-18, ver `routers/public.py`). Ahí se
    cambió a llamarla sobre las provincias con dato EN LA VISTA ACTUAL, a
    costa de la estabilidad entre cargas."""
    return {
        name: i if i < _NAMED_PROVINCE_SLOTS else _OTHER_PROVINCE_SLOT
        for i, name in enumerate(province_names)
    }


def attach_trends(cards: list[ProductCard], slopes: Sequence[SlopeChart]) -> list[ProductCard]:
    """Pega a cada tarjeta el cambio vs. el período anterior, si hay uno.

    El cambio es el del producto en esa provincia —el «¿subió o bajó?» de
    `price_charts.build_month_vs_month`, el mismo número sin color que
    muestran la lista de productos del tablero y la ventana «Cómo se
    movió»—, emparejado por producto + provincia + moneda + unidad. Una
    tarjeta de formato o variante comparte ese cambio agregado: no hay
    comparación previa a nivel de formato/variante todavía, y mostrar el
    agregado es preferible a no mostrar nada.

    Hasta el 2026-09-18 salía de un titular por formato (`build_headlines`)
    y un diccionario que se quedaba con el último: arroz en La Habana
    mostraba −12% en las once tarjetas, el de «saco de 50 kg» —un solo
    anuncio—, cuando el cambio del producto era −3%."""
    cambio_de = {
        (s.product_name, line.province_name, s.currency, s.unit): line.change_text
        for s in slopes
        for line in s.lines
    }
    return [
        replace(
            c,
            change_text=cambio_de.get((c.product_name, c.province_name, c.currency, c.unit), ""),
        )
        for c in cards
    ]
