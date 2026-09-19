"""Lista de productos del tablero (dirección C, 2026-09-18).

El operador eligió, entre tres prototipos, un tablero con forma de lista de
seguimiento: una fila por producto piloto, una celda por provincia con su
mediana de la ventana y el cambio contra el período anterior del mismo largo.
Tocar una fila elige ese producto —solo ese— y el resto de la página se
enfoca en él; «Todos los productos» vuelve a la vista completa.

No calcula ninguna cifra nueva: las celdas salen tal cual de
`price_charts.build_month_vs_month`, el mismo «¿subió o bajó?» que dibuja la
ventana «Cómo se movió» del mapa. Así la lista y el gráfico nunca pueden
contradecirse. El cambio va sin flecha ni color (§12.6): una baja es buena
noticia para quien compra y mala para quien vende.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from saimc.web.price_charts import SlopeChart, SlopeLine
from saimc.web.product_nav import product_icon, product_slug, products_query


@dataclass(frozen=True)
class WatchCell:
    """Un producto en una provincia: mediana de la ventana y su cambio."""

    province_name: str
    median_text: str
    count: int
    #: «+3%», «−7%», «0%», o vacío si no hubo anuncios en el período
    #: anterior: sin contra qué comparar, no se muestra ningún cambio.
    change_text: str
    previous_count: int
    #: La de mediana más baja entre las provincias con dato del producto. Solo
    #: hay una si se puede comparar (dos o más provincias) y sin empate. Es lo
    #: que antes contestaba la tira «dónde está más barato».
    is_cheapest: bool = False


@dataclass(frozen=True)
class WatchRow:
    product_name: str
    slug: str
    icon: str | None
    #: Moneda y unidad del gráfico del que salieron las celdas; vacías si el
    #: producto no tiene anuncios en la ventana.
    currency: str
    unit: str
    #: Una por columna de `Watchlist.provinces`, en ese orden; `None` donde
    #: la provincia no tiene anuncios de este producto en la ventana.
    cells: tuple[WatchCell | None, ...]
    query: str
    is_active: bool
    #: Con una provincia elegida (S8), las celdas de las demás provincias que
    #: sí tienen dato, para verlas en chico junto a la principal. Vacío sin
    #: provincia elegida: ahí todas son columnas.
    otras: tuple[WatchCell, ...] = ()
    #: Una por cada provincia de `Watchlist.all_provinces`, con o sin foco: el
    #: detalle de un producto compara todas las provincias aunque la lista
    #: esté enfocada en una. `None` donde no hay anuncios.
    todas: tuple[WatchCell | None, ...] = ()
    #: La provincia de mediana más baja y las demás con dato, para la frase
    #: «X es la más barata: 298 contra 318 en Y». `None` y `()` si no hay
    #: con qué comparar (menos de dos provincias con dato, o empate).
    barata: WatchCell | None = None
    contra: tuple[WatchCell, ...] = ()


@dataclass(frozen=True)
class Watchlist:
    provinces: tuple[str, ...]
    rows: tuple[WatchRow, ...]
    all_query: str
    all_is_active: bool
    #: Todas las provincias con dato, sin importar el foco (`provinces` queda
    #: en la elegida cuando hay foco).
    all_provinces: tuple[str, ...] = ()
    #: Anuncios en la ventana que respaldan las medianas de la lista: la suma
    #: de todas las celdas de todos los productos. Es lo que dice la página
    #: en «sobre N anuncios verificados a mano».
    total_ads: int = 0


def _chart_for(product: str, slopes: Sequence[SlopeChart]) -> SlopeChart | None:
    """El gráfico del producto. Si hubiera más de uno (dos monedas o
    unidades para el mismo producto), el de más anuncios en la ventana: es
    la cifra que más gente respalda."""
    propios = [s for s in slopes if s.product_name == product]
    if not propios:
        return None
    return max(propios, key=lambda s: sum(line.now_count for line in s.lines))


def _mas_barata(lines: Sequence[SlopeLine]) -> str | None:
    """La provincia de mediana más baja, o `None` si no hay con qué comparar
    (menos de dos provincias con dato) o si la más baja está empatada."""
    if len(lines) < 2:
        return None
    medianas = sorted((line.now_median, line.province_name) for line in lines)
    if medianas[0][0] == medianas[1][0]:
        return None
    return medianas[0][1]


def build_watchlist(
    pilot_names: Sequence[str],
    active_products: Sequence[str],
    slopes: Sequence[SlopeChart],
    *,
    provinces: Sequence[str],
    selected_province: str,
    desde: date,
    hasta: date,
) -> Watchlist:
    """Arma la lista en el orden de `pilot_names`, con una columna por cada
    provincia de `provinces`.

    `selected_province` es el slug de la provincia elegida (o vacío): las
    queries la preservan, igual que el rango de calendario. Si coincide con
    una de `provinces`, la lista se enfoca en ella (S8): una sola columna, y
    las demás pasan a `WatchRow.otras`; «más barata» se sigue decidiendo entre
    todas. Si no coincide (una provincia sin dato piloto, una cookie vieja),
    no hay foco y se ven todas las columnas. Una URL heredada
    con varios productos (`?productos=arroz,pollo`) marca todos los activos;
    la lista en sí nunca arma una multi-selección nueva."""
    activos = set(active_products)
    foco = next(
        (p for p in provinces if selected_province and product_slug(p) == selected_province), None
    )
    columnas = (foco,) if foco else tuple(provinces)

    def query(slugs: list[str]) -> str:
        return products_query(
            slugs, provincia=selected_province, desde=desde, hasta=hasta, vista="tablero"
        )

    rows = []
    total_ads = 0
    for name in pilot_names:
        chart = _chart_for(name, slopes)
        por_provincia = {}
        if chart is not None:
            con_dato = [line for line in chart.lines if line.now_median is not None]
            barata = _mas_barata(con_dato)
            for line in con_dato:
                por_provincia[line.province_name] = WatchCell(
                    province_name=line.province_name,
                    median_text=line.now_text,
                    count=line.now_count,
                    change_text=line.change_text,
                    previous_count=line.before_count,
                    is_cheapest=line.province_name == barata,
                )
        total_ads += sum(c.count for c in por_provincia.values())
        mas_barata = next((c for c in por_provincia.values() if c.is_cheapest), None)
        todas = tuple(por_provincia.get(p) for p in provinces)
        slug = product_slug(name)
        rows.append(
            WatchRow(
                product_name=name,
                slug=slug,
                icon=product_icon(name),
                currency=chart.currency if chart else "",
                unit=chart.unit if chart else "",
                cells=tuple(por_provincia.get(p) for p in columnas),
                query=query([slug]),
                is_active=name in activos,
                otras=tuple(
                    por_provincia[p] for p in provinces if foco and p != foco and p in por_provincia
                ),
                todas=todas,
                barata=mas_barata,
                contra=tuple(c for c in todas if c and c is not mas_barata) if mas_barata else (),
            )
        )
    return Watchlist(
        provinces=columnas,
        rows=tuple(rows),
        all_query=query([]),
        all_is_active=not activos,
        all_provinces=tuple(provinces),
        total_ads=total_ads,
    )
