"""Paneles flotantes por producto activo, para la home pública.

Slice 4 del rediseño: cada producto que el visitante prende con un chip abre un
panel flotante sobre el mapa con su rango nacional y su evolución. Mismo reparto
que `card_view` / `series_view` / `map_view`: acá se agrega, la plantilla pinta.

El rango del panel es una **lectura rápida** —el mínimo de los mínimos y el
máximo de los máximos de las provincias con dato—, no una medición nueva: la
tabla por provincia de `#precios` sigue siendo la autoridad. Si un producto
tuviera unidades distintas entre provincias (no pasa hoy con los piloto), el
panel toma la unidad de la primera tarjeta y agrega igual; el detalle de abajo
desambigua.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Decimal

from saimc.web.card_view import ProductCard, format_price
from saimc.web.price_charts import Headline, RangeSeries
from saimc.web.product_nav import (
    DEFAULT_LAYER,
    DEFAULT_WINDOW_DAYS,
    has_product_illustration,
    product_icon,
    product_slug,
    products_query,
)
from saimc.web.series_view import PriceSeries


@dataclass(frozen=True)
class PanelBlock:
    """Una provincia dentro del panel: su cifra y su rango por período."""

    headline: Headline
    ranges: RangeSeries | None


@dataclass(frozen=True)
class ProductPanel:
    """Un producto activo, con su rango nacional y sus series."""

    name: str
    slug: str
    currency: str
    unit: str
    count: int
    provinces: int
    minimum: Decimal
    maximum: Decimal
    series: tuple[PriceSeries, ...]
    #: `?productos=` sin este producto: el destino del botón de cerrar.
    close_query: str
    #: Cifra con su muestra y rango por período, un bloque por provincia
    #: (`attach_blocks`). Vacío hasta que se le pegan.
    blocks: tuple[PanelBlock, ...] = ()

    @property
    def has_data(self) -> bool:
        return self.count > 0

    @property
    def minimum_text(self) -> str:
        return format_price(self.minimum)

    @property
    def maximum_text(self) -> str:
        return format_price(self.maximum)

    @property
    def is_single_value(self) -> bool:
        """Con una sola observación (o todas al mismo precio) no hay rango."""
        return self.has_data and self.minimum == self.maximum

    @property
    def has_series(self) -> bool:
        return bool(self.series)

    @property
    def icon(self) -> str | None:
        """El ícono del producto, el mismo del chip, o `None` si no tiene."""
        return product_icon(self.name)

    @property
    def has_illustration(self) -> bool:
        """Si el producto tiene ícono ilustrado propio para este panel
        (`templates/_product_illustration.html`), en vez del glifo de
        Material Symbols que usa el chip."""
        return has_product_illustration(self.name)


def build_product_panels(
    active_products: list[str],
    cards: list[ProductCard],
    series: list[PriceSeries],
    *,
    selected_province: str = "",
    selected_days: int = DEFAULT_WINDOW_DAYS,
    ver: Sequence[str] = (),
    capa: str = DEFAULT_LAYER,
) -> list[ProductPanel]:
    """Un panel por producto activo, en el orden de activación.

    Un producto activo sin ninguna tarjeta en la ventana entra igual, con
    `count=0`: el panel dice que no hay dato y ofrece cerrarlo, en vez de
    desaparecer sin explicación. `ver` (las ventanas abiertas del menú) y
    `capa` (la capa del mapa) viajan en el `close_query` para que cerrar un
    panel no cierre ventanas ni cambie la capa.
    """
    prov_slug = product_slug(selected_province) if selected_province else ""
    active_slugs = [product_slug(name) for name in active_products]

    cards_by_product: dict[str, list[ProductCard]] = {}
    for card in cards:
        cards_by_product.setdefault(card.product_name, []).append(card)
    series_by_product: dict[str, list[PriceSeries]] = {}
    for serie in series:
        series_by_product.setdefault(serie.product_name, []).append(serie)

    panels: list[ProductPanel] = []
    for name in active_products:
        this_slug = product_slug(name)
        pcards = cards_by_product.get(name, [])
        pseries = tuple(series_by_product.get(name, []))
        close_query = products_query(
            [s for s in active_slugs if s != this_slug],
            provincia=prov_slug,
            dias=selected_days,
            ver=ver,
            capa=capa,
        )
        if not pcards:
            panels.append(
                ProductPanel(
                    name=name,
                    slug=this_slug,
                    currency="",
                    unit="",
                    count=0,
                    provinces=0,
                    minimum=Decimal(0),
                    maximum=Decimal(0),
                    series=pseries,
                    close_query=close_query,
                )
            )
            continue
        panels.append(
            ProductPanel(
                name=name,
                slug=this_slug,
                currency=pcards[0].currency,
                unit=pcards[0].unit,
                count=sum(c.count for c in pcards),
                provinces=len({c.province_name for c in pcards}),
                minimum=min(c.minimum for c in pcards),
                maximum=max(c.maximum for c in pcards),
                series=pseries,
                close_query=close_query,
            )
        )
    return panels


def attach_blocks(
    panels: Sequence[ProductPanel],
    headlines: Sequence[Headline],
    ranges: Sequence[RangeSeries],
) -> list[ProductPanel]:
    """Pega a cada panel su cifra y su rango por período, un bloque por
    provincia, en el orden de las cifras (la provincia con más anuncios
    primero). Cifra y rango se emparejan por producto, provincia, moneda y
    unidad: una celda con dos unidades no mezcla el rango de una con la cifra
    de la otra."""
    rango_de = {(r.product_name, r.province_name, r.currency, r.unit): r for r in ranges}
    por_producto: dict[str, list[PanelBlock]] = {}
    for h in headlines:
        por_producto.setdefault(h.product_name, []).append(
            PanelBlock(
                headline=h,
                ranges=rango_de.get((h.product_name, h.province_name, h.currency, h.unit)),
            )
        )
    return [replace(p, blocks=tuple(por_producto.get(p.name, ()))) for p in panels]
