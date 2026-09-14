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

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal

from saimc.web.card_view import ProductCard, format_price
from saimc.web.price_charts import AdRow, Headline, RangeSeries
from saimc.web.product_nav import (
    DEFAULT_LAYER,
    DEFAULT_WINDOW_DAYS,
    has_product_illustration,
    light_product_query,
    product_icon,
    product_slug,
    products_query,
)
from saimc.web.series_view import PriceSeries


@dataclass(frozen=True)
class PanelBlock:
    """Una provincia —y, si el producto tiene formato declarado, un formato—
    dentro del panel: su cifra y su rango por período. Antes de 2026-09-14
    había uno por provincia a secas; ahora un producto con varios formatos
    en la misma provincia (botella y garrafa de aceite, por ejemplo) trae un
    bloque por cada uno, en vez de fundirlos en un solo rango ciego al
    envase (mismo eje que ya distinguía `/control` y la tabla enfocada de
    `#precios`, ver `docs/decisions.md`)."""

    headline: Headline
    ranges: RangeSeries | None
    #: Los anuncios detrás de esta celda exacta (§6.5, «¿de dónde salió?»),
    #: con su enlace a `/anuncio/{id}` cuando la evidencia es pública.
    #: Vacío si no se pegó (`attach_blocks` sin `ads_by_cell`).
    ads: tuple[AdRow, ...] = ()


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
    #: `/producto/<slug>`: la vista liviana sin mapa, para compartir
    #: (2026-09-14). El panel se queda en el mapa por defecto —el operador
    #: lo pidió explícito, «no me gusta que vaya directo a esa vista»—; esto
    #: es la puerta de salida hacia ella, no la entrada.
    light_query: str = ""
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
        light_query = light_product_query(this_slug, provincia=prov_slug, dias=selected_days)
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
                    light_query=light_query,
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
                light_query=light_query,
            )
        )
    return panels


def attach_blocks(
    panels: Sequence[ProductPanel],
    headlines: Sequence[Headline],
    ranges: Sequence[RangeSeries],
    ads_by_cell: Mapping[tuple[str, str, str, str, str | None], Sequence[AdRow]] | None = None,
) -> list[ProductPanel]:
    """Pega a cada panel su cifra y su rango por período, un bloque por
    provincia × formato, en el orden de las cifras (más anuncios primero).
    Cifra y rango se emparejan por producto, provincia, moneda, unidad y
    formato (2026-09-14: se sumó el formato al emparejado — antes una celda
    con dos formatos mezclaba el rango de uno con la cifra del otro apenas
    coincidían provincia+unidad).

    `ads_by_cell` (`price_charts.build_cell_ads`, misma clave de 5) es
    opcional: sin él, todo bloque queda con `ads=()` — el recorrido hacia
    atrás hasta el anuncio (§6.5, pedido el 2026-09-14 comparando con
    `/control`) es un agregado, no un requisito para armar el panel."""
    rango_de = {
        (r.product_name, r.province_name, r.currency, r.unit, r.format_label): r for r in ranges
    }
    ads_by_cell = ads_by_cell or {}
    por_producto: dict[str, list[PanelBlock]] = {}
    for h in headlines:
        clave = (h.product_name, h.province_name, h.currency, h.unit, h.format_label)
        por_producto.setdefault(h.product_name, []).append(
            PanelBlock(
                headline=h,
                ranges=rango_de.get(clave),
                ads=tuple(ads_by_cell.get(clave, ())),
            )
        )
    return [replace(p, blocks=tuple(por_producto.get(p.name, ()))) for p in panels]


def group_blocks_by_province(
    panels: Sequence[ProductPanel],
) -> dict[str, list[tuple[ProductPanel, PanelBlock]]]:
    """Bloques de todos los paneles (con dato) agrupados por provincia, para
    la insignia del mapa: una por provincia con dato de algún producto
    activo, sin depender de si hay una `selected_province` elegida —cuando
    la hay, `blocks` ya viene filtrado a esa sola provincia (`_render_home`),
    así que el resultado tiene una sola clave; sin elegir ninguna, una por
    cada provincia con dato.

    La insignia no desglosa por formato —ese detalle es del panel, acá es
    una lectura rápida—: con varios bloques del mismo producto en la misma
    provincia (uno por formato, desde 2026-09-14) se queda con el primero,
    que ya es el de más anuncios porque `panel.blocks` viene ordenado así."""
    agrupado: dict[str, list[tuple[ProductPanel, PanelBlock]]] = {}
    vistos: set[tuple[str, str]] = set()
    for panel in panels:
        for bloque in panel.blocks:
            slug = product_slug(bloque.headline.province_name)
            clave = (panel.name, slug)
            if clave in vistos:
                continue
            vistos.add(clave)
            agrupado.setdefault(slug, []).append((panel, bloque))
    return agrupado
