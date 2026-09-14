"""Barras de filtro de la vista pública: por producto, por provincia y por
ventana de días.

Mismo reparto que `map_view` / `card_view` / `series_view`: acá se arma la lista
de opciones y cuál está activa, con su enlace ya compuesto; la plantilla solo la
pinta. Que el slug, la resolución y la composición del query string vivan en
Python y no en Jinja es lo que deja testear que «azucar-blanca» resuelve a
«Azúcar blanca», y que elegir una provincia preserva el producto y la ventana
elegidos, sin parsear HTML.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

#: Ventana por defecto de tarjetas y series (días). 30 y no los 7 de `/control`:
#: esa vista responde «¿cómo se movió esta semana?» para el operador, y esta
#: «¿cuánto cuesta?» para alguien que compra. Medido el 2026-08-22, a 7 días
#: entran 5 celdas y a 30 entran 6, con muestras bastante más grandes
#: (Arroz/La Habana pasa de n=2 a n=17). No es un umbral de publicación: es el
#: período que la tarjeta declara describir, y lo dice en pantalla.
DEFAULT_WINDOW_DAYS = 30

#: Las tres ventanas que ofrece la barra. No son umbrales de los de §12.6 —no
#: deciden qué se publica— son tres cortes de lectura: la semana, el mes y el
#: trimestre.
WINDOW_CHOICES: tuple[int, ...] = (7, 30, 90)

#: Capas del mapa (2026-09-11): «publicación» es la de siempre (§9.0,
#: vinculante); «densidad» es puramente presentacional —sombrea por
#: `MapProvince.density_ratio`, relativo al máximo actual, sin ningún umbral
#: de los que §12.6 prohíbe inventar—. La clave viaja en `?capa=`.
LAYER_CHOICES: tuple[str, ...] = ("publicacion", "densidad")
DEFAULT_LAYER = "publicacion"


def product_slug(name: str) -> str:
    """Nombre → segmento de URL estable. Sirve para producto y para provincia.

    Quita las tildes con `unicodedata`: `NFKD` descompone «á» en «a» + el
    acento como carácter combinante aparte, y `combining()` deja tirar solo
    ese. Es la vía idiomática en Python y cubre cualquier acento, no solo los
    que se nos ocurran a mano. «Azúcar blanca» → «azucar-blanca»,
    «Santiago de Cuba» → «santiago-de-cuba».
    """
    sin_tildes = "".join(
        c
        for c in unicodedata.normalize("NFKD", name)
        if not unicodedata.combining(c)
    )
    return "-".join(sin_tildes.lower().split())


def _resolve(names: Sequence[str], slug: str | None) -> str | None:
    """El nombre cuyo slug coincide con `slug`, o `None`.

    `None` tanto si `slug` viene vacío como si no matchea ninguno: una vista
    pública no responde 404 por un query string raro, muestra todo.
    """
    if not slug:
        return None
    for name in names:
        if product_slug(name) == slug:
            return name
    return None


def resolve_product(pilot_names: Sequence[str], slug: str | None) -> str | None:
    """El nombre piloto cuyo slug coincide con `slug`, o `None`."""
    return _resolve(pilot_names, slug)


def resolve_products(pilot_names: Sequence[str], raw: str | None) -> list[str]:
    """`?productos=aceite,arroz` → nombres piloto, en orden de aparición, sin
    repetir, ignorando los slugs que no resuelven.

    Cadena vacía o `None` → lista vacía (la vista muestra todo), mismo criterio
    que `resolve_product`: un query string raro no da 404. El orden se conserva
    porque es el de activación de los chips, y los slices siguientes lo usan
    para decidir cuál panel entra primero.
    """
    if not raw:
        return []
    out: list[str] = []
    for chunk in raw.split(","):
        name = _resolve(pilot_names, chunk.strip())
        if name is not None and name not in out:
            out.append(name)
    return out


def resolve_province(province_names: Sequence[str], slug: str | None) -> str | None:
    """El nombre de provincia cuyo slug coincide con `slug`, o `None`."""
    return _resolve(province_names, slug)


def resolve_days(raw: str | int | None) -> int:
    """Query string → una de `WINDOW_CHOICES`, o el default ante cualquier cosa.

    Mismo criterio que `resolve_product`: un `?dias=` inválido no da 404 ni
    error, cae a la ventana por defecto.
    """
    try:
        value = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_WINDOW_DAYS
    return value if value in WINDOW_CHOICES else DEFAULT_WINDOW_DAYS


def resolve_layer(raw: str | None) -> str:
    """Query string → una de `LAYER_CHOICES`, o el default ante cualquier cosa.

    Mismo criterio que `resolve_days`: un `?capa=` inválido no da 404 ni
    error, cae a la capa por defecto.
    """
    return raw if raw in LAYER_CHOICES else DEFAULT_LAYER


def _parse_iso_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def default_window(hoy: date | None = None) -> tuple[date, date]:
    """El rango de siempre: últimos `DEFAULT_WINDOW_DAYS` días hasta hoy."""
    hoy = hoy or date.today()
    return hoy - timedelta(days=DEFAULT_WINDOW_DAYS), hoy


def resolve_window(
    desde_raw: str | None,
    hasta_raw: str | None,
    dias_raw: str | int | None,
    *,
    hoy: date | None = None,
) -> tuple[date, date]:
    """Rango de calendario efectivo para el date picker (2026-09-14, reporte
    de mejoras no consideradas — pedido del operador después de esa idea).

    `desde`/`hasta` explícitos ganan si son fechas ISO válidas y
    `desde <= hasta`. Si no —ausentes, mal formadas, o al revés— cae al
    `?dias=` de siempre (`resolve_days`): un enlace viejo sigue funcionando,
    y un query string roto no da 404 ni error, mismo criterio que el resto
    de los filtros de esta vista.
    """
    hoy = hoy or date.today()
    desde = _parse_iso_date(desde_raw)
    hasta = _parse_iso_date(hasta_raw)
    if desde is not None and hasta is not None and desde <= hasta:
        return desde, hasta
    dias = resolve_days(dias_raw)
    return hoy - timedelta(days=dias), hoy


def _query(
    *,
    producto: str = "",
    productos: Sequence[str] = (),
    provincia: str = "",
    desde: date,
    hasta: date,
    ver: Sequence[str] = (),
    capa: str = DEFAULT_LAYER,
    path: str = "/",
) -> str:
    """Compone la URL con los ejes activos, en orden fijo
    producto/provincia/desde/hasta/ver/capa.

    `productos` (lista de slugs, multi-selección) gana sobre `producto` (slug
    único, alias histórico) cuando trae algo: se emite `productos=a,b`. `ver`
    son las ventanas flotantes abiertas desde el menú del header. `capa` es la
    capa del mapa (§ mapa, 2026-09-11).

    `desde`/`hasta` van SIEMPRE explícitos (2026-09-14) —a diferencia de
    `capa`, que se omite si es el default—: son la respuesta directa al
    pedido de que el rango en uso se vea siempre, y una URL que dice
    exactamente qué ventana mira es más clara para compartir, no menos. La
    descarga usa `path="/datos.csv"`.
    """
    params = []
    if productos:
        params.append("productos=" + ",".join(productos))
    elif producto:
        params.append(f"producto={producto}")
    if provincia:
        params.append(f"provincia={provincia}")
    params.append(f"desde={desde.isoformat()}")
    params.append(f"hasta={hasta.isoformat()}")
    if ver:
        params.append("ver=" + ",".join(ver))
    if capa != DEFAULT_LAYER:
        params.append(f"capa={capa}")
    return f"{path}?" + "&".join(params)


def products_query(
    product_slugs: Sequence[str],
    *,
    provincia: str = "",
    desde: date,
    hasta: date,
    ver: Sequence[str] = (),
    capa: str = DEFAULT_LAYER,
    path: str = "/",
) -> str:
    """URL con `?productos=<slugs>` (o sin el parámetro si la lista está vacía),
    preservando provincia, rango de calendario, ventanas abiertas y capa del
    mapa. La usan el riel de chips, los paneles flotantes, el mapa y la hoja
    de provincia."""
    return _query(
        productos=list(product_slugs),
        provincia=provincia,
        desde=desde,
        hasta=hasta,
        ver=list(ver),
        capa=capa,
        path=path,
    )


def light_product_query(slug: str, *, provincia: str = "", desde: date, hasta: date) -> str:
    """Enlace a la vista liviana de un producto (`/producto/<slug>`, sin
    mapa, 2026-09-14) desde el panel flotante. Preserva provincia y rango de
    calendario si están puestos —esa vista los sigue aceptando aunque no
    tenga mapa ni ventanas que perder—; no lleva `ver` ni `capa` porque
    ninguno de los dos significa nada ahí."""
    return _query(provincia=provincia, desde=desde, hasta=hasta, path=f"/producto/{slug}")


def download_href(
    *,
    producto: str = "",
    productos: Sequence[str] = (),
    provincia: str = "",
    desde: date,
    hasta: date,
) -> str:
    """URL de `/datos.csv` con los mismos filtros que la vista, para que la
    descarga traiga exactamente lo que se está viendo."""
    return _query(
        producto=producto,
        productos=productos,
        provincia=provincia,
        desde=desde,
        hasta=hasta,
        path="/datos.csv",
    )


def _product_link(slug: str, *, provincia: str, desde: date, hasta: date, hoy: date | None = None) -> str:
    """Enlace de un chip de producto: la URL limpia `/producto/<slug>` cuando no
    hay otro filtro activo, y el query string en cuanto lo hay.

    La ruta limpia solo sabe de un producto; provincia y rango viajan como
    query. Mantener las dos formas —y no forzar un redirect— es lo que pide el
    ítem del board: `/producto/arroz` para compartir, `?producto=` como alias.
    """
    if not provincia and (desde, hasta) == default_window(hoy):
        return f"/producto/{slug}"
    return _query(producto=slug, provincia=provincia, desde=desde, hasta=hasta)


@dataclass(frozen=True)
class NavChoice:
    """Una opción de cualquiera de las tres barras de filtro."""

    label: str
    #: Slug del ítem, o cadena vacía para «Todos» / «Todas».
    slug: str
    is_selected: bool
    #: Destino del enlace sin ancla; la plantilla le agrega `#precios`.
    query: str


@dataclass(frozen=True)
class WindowChoice:
    """Una opción de la barra de ventana de días."""

    label: str
    days: int
    is_selected: bool
    query: str


def build_product_nav(
    pilot_names: Iterable[str],
    selected_name: str | None,
    selected_desde: date,
    selected_hasta: date,
    *,
    selected_province: str = "",
) -> list[NavChoice]:
    """«Todos» + un ítem por producto piloto, marcando el activo.

    Cada enlace preserva `selected_province` y el rango de calendario:
    cambiar de producto no debería resetear la provincia ni la ventana que
    se venía mirando.
    """
    prov_slug = product_slug(selected_province) if selected_province else ""
    choices = [
        NavChoice(
            label="Todos",
            slug="",
            is_selected=selected_name is None,
            query=_query(producto="", provincia=prov_slug, desde=selected_desde, hasta=selected_hasta),
        )
    ]
    for name in pilot_names:
        slug = product_slug(name)
        choices.append(
            NavChoice(
                label=name,
                slug=slug,
                is_selected=name == selected_name,
                query=_product_link(
                    slug, provincia=prov_slug, desde=selected_desde, hasta=selected_hasta
                ),
            )
        )
    return choices


def build_province_nav(
    province_names: Iterable[str],
    selected_name: str | None,
    *,
    selected_product: str = "",
    selected_desde: date,
    selected_hasta: date,
    active_products: Sequence[str] = (),
    ver: Sequence[str] = (),
    capa: str = DEFAULT_LAYER,
) -> list[NavChoice]:
    """«Todas» + un ítem por provincia con dato piloto, marcando la activa.

    Simétrica a `build_product_nav`; cada enlace preserva el producto (o el
    conjunto `active_products` de los chips), el rango de calendario, las
    ventanas flotantes abiertas (`ver`) y la capa del mapa (`capa`): la barra
    vive dentro de la ventana «Precios», y cambiar de provincia no puede
    cerrarla ni resetear ninguna de las dos.
    """
    prod_slug = product_slug(selected_product) if selected_product else ""
    active_slugs = [product_slug(n) for n in active_products]
    choices = [
        NavChoice(
            label="Todas",
            slug="",
            is_selected=selected_name is None,
            query=_query(
                producto=prod_slug,
                productos=active_slugs,
                provincia="",
                desde=selected_desde,
                hasta=selected_hasta,
                ver=ver,
                capa=capa,
            ),
        )
    ]
    for name in province_names:
        slug = product_slug(name)
        choices.append(
            NavChoice(
                label=name,
                slug=slug,
                is_selected=name == selected_name,
                query=_query(
                    producto=prod_slug,
                    productos=active_slugs,
                    provincia=slug,
                    desde=selected_desde,
                    hasta=selected_hasta,
                    ver=ver,
                    capa=capa,
                ),
            )
        )
    return choices


#: Ícono de cada producto piloto en los chips de la home, por slug: el nombre
#: de la ligadura de Material Symbols Rounded (el proyecto sigue Material). Solo
#: presentación: vive en código y no en `products` para no tocar el esquema
#: (CLAUDE.md: un cambio de esquema pide el flujo de especificación completo).
#: Un producto sin entrada sigue mostrando su nombre.
#:
#: Material Symbols no tiene íconos propios para casi ningún alimento básico:
#: solo `rice_bowl` es exacto. Los demás son la aproximación más cercana del
#: catálogo (verificado contra los 4.239 íconos el 2026-09-10): pollo →
#: `yakitori` (el único etiquetado «grilled chicken»), picadillo → `skillet`,
#: azúcar → `grain` (granulado), aceite → `water_drop`. El nombre del producto
#: sale igual como tooltip y como etiqueta accesible.
#:
#: Cada nombre tiene que estar en el subconjunto vendorizado de la fuente —lo
#: suma `tools/build_icon_font.py` y lo verifica `test_static_assets.py`—, o el
#: chip mostraría la palabra en vez del ícono.
PRODUCT_ICONS: dict[str, str] = {
    "arroz": "rice_bowl",
    "pollo": "yakitori",
    "picadillo": "skillet",
    "azucar-blanca": "grain",
    "aceite": "water_drop",
}


def product_icon(name: str) -> str | None:
    """El ícono del producto, o `None` si no tiene uno asignado."""
    return PRODUCT_ICONS.get(product_slug(name))


#: Productos piloto con ícono ilustrado propio en el panel flotante de
#: producto (`templates/_product_illustration.html`), a diferencia de
#: `PRODUCT_ICONS` de arriba —ligaduras de Material Symbols, para los
#: chips—. Se agregó porque la aproximación de Material era demasiado
#: genérica para el panel (aceite → una gota cualquiera, no una botella) y
#: el operador la pidió explícitamente el 2026-09-13. Un SVG dibujado a
#: mano por producto piloto, sin color propio (usa `currentColor`, hereda
#: `--ph-accent` vía `.ph-fpanel-illustration`) para no abrir una paleta
#: fija nueva. Un producto piloto sin entrada acá cae a `product_icon`.
PRODUCT_ILLUSTRATIONS: frozenset[str] = frozenset(
    {"arroz", "pollo", "picadillo", "azucar-blanca", "aceite"}
)


def has_product_illustration(name: str) -> bool:
    """Si el producto tiene ícono ilustrado propio para el panel."""
    return product_slug(name) in PRODUCT_ILLUSTRATIONS


@dataclass(frozen=True)
class ProductChip:
    """Un interruptor de producto del riel flotante sobre el mapa.

    `query` prende el producto si está apagado; si ya está prendido, `query`
    es el estado actual sin cambios —clic en un chip activo no apaga nada—,
    porque su panel ya tiene su propia × para cerrarse (`ProductPanel.close_query`
    en `panel_view.py`). Antes el chip también apagaba, y era fácil cerrar un
    panel sin querer al pasar por el riel; ver `docs/decisions.md` 2026-09-11.
    `icon`, si el producto tiene uno, reemplaza al nombre en el chip; el
    nombre queda como etiqueta accesible y como tooltip al pasar por encima.

    El chip SIEMPRE se queda en el mapa (`?productos=`, nunca la ruta limpia
    `/producto/<slug>`): el operador lo pidió explícito el 2026-09-14 —«no
    me gusta que vaya directo a esa vista»— después de una vuelta donde el
    riel sí saltaba a la vista liviana. La vista liviana se alcanza desde el
    panel flotante (`ProductPanel.light_query`, `panel_view.py`), no desde
    acá.
    """

    label: str
    slug: str
    is_active: bool
    query: str
    icon: str | None = None


def build_product_chips(
    pilot_names: Iterable[str],
    active_names: Sequence[str],
    *,
    selected_province: str = "",
    selected_desde: date,
    selected_hasta: date,
    ver: Sequence[str] = (),
    capa: str = DEFAULT_LAYER,
) -> list[ProductChip]:
    """Un chip por producto piloto, en el orden del catálogo.

    El conjunto activo (`active_names`) viaja como `?productos=` en el orden en
    que se fueron prendiendo. Un chip apagado agrega su slug al final al
    clickearlo; uno ya prendido no hace nada —cerrar su panel es cosa de la ×
    del panel, no del chip—. `ver`, `capa` y el rango de calendario se
    preservan: prender un producto no cierra las ventanas abiertas, ni
    cambia la capa del mapa, ni resetea la ventana de fechas.
    """
    prov_slug = product_slug(selected_province) if selected_province else ""
    active_slugs = [product_slug(name) for name in active_names]
    chips: list[ProductChip] = []
    for name in pilot_names:
        slug = product_slug(name)
        is_active = slug in active_slugs
        resultado = (
            active_slugs
            if is_active
            else [*active_slugs, slug]
        )
        chips.append(
            ProductChip(
                label=name,
                slug=slug,
                is_active=is_active,
                icon=product_icon(name),
                query=products_query(
                    resultado,
                    provincia=prov_slug,
                    desde=selected_desde,
                    hasta=selected_hasta,
                    ver=ver,
                    capa=capa,
                ),
            )
        )
    return chips


def build_window_nav(
    selected_desde: date,
    selected_hasta: date,
    selected_name: str | None = None,
    *,
    selected_province: str = "",
    active_products: Sequence[str] = (),
    ver: Sequence[str] = (),
    capa: str = DEFAULT_LAYER,
    hoy: date | None = None,
) -> list[WindowChoice]:
    """Atajos rápidos del date picker (2026-09-14): «7/30/90 días» siguen
    siendo los mismos tres cortes de lectura de siempre, ahora expresados
    como el rango de calendario que representan (`hoy - N días` a `hoy`) en
    vez de un `?dias=N` aparte. Marca el activo solo si el rango elegido
    coincide EXACTO con uno de los tres —un rango personalizado no marca
    ninguno, que es la señal correcta: no es ninguno de los presets—.
    Preserva producto (o el conjunto de chips), provincia, ventanas
    flotantes abiertas y capa.
    """
    hoy = hoy or date.today()
    prod_slug = product_slug(selected_name) if selected_name else ""
    prov_slug = product_slug(selected_province) if selected_province else ""
    active_slugs = [product_slug(n) for n in active_products]
    return [
        WindowChoice(
            label=f"{days} días",
            days=days,
            is_selected=(selected_desde, selected_hasta) == (hoy - timedelta(days=days), hoy),
            query=_query(
                producto=prod_slug,
                productos=active_slugs,
                provincia=prov_slug,
                desde=hoy - timedelta(days=days),
                hasta=hoy,
                ver=ver,
                capa=capa,
            ),
        )
        for days in WINDOW_CHOICES
    ]


# --- ventanas flotantes del menú del header ----------------------------------

#: Las ventanas que abre el menú del header, en el orden del menú. La clave es
#: el valor que viaja en `?ver=` y el `id` de la ventana en la plantilla; la
#: etiqueta es lo que se lee en el menú. «Ventana» acá es la ventana flotante
#: sobre el mapa, no la ventana de días (`WINDOW_CHOICES`).
VIEWS: tuple[tuple[str, str], ...] = (
    ("cobertura", "Cobertura"),
    ("precios", "Precios"),
    ("evolucion", "Cómo se movió"),
)
_VIEW_KEYS = tuple(key for key, _ in VIEWS)

#: Ícono de Material Symbols por ventana del menú (2026-09-13, rediseño
#: «Brújula»): el menú pasó de texto a un dock de botones redondos, y un botón
#: redondo sin texto necesita un ícono para decir qué hace. Mismo patrón que
#: `PRODUCT_ICONS`: el nombre visible sigue siendo la fuente de verdad
#: (`aria-label` del botón y la etiqueta que sale al abrir la ventana), el
#: ícono es una ayuda visual, aproximada como los de producto.
VIEW_ICONS: dict[str, str] = {
    "cobertura": "map",
    "precios": "sell",
    "evolucion": "show_chart",
}


def resolve_views(raw: str | None) -> list[str]:
    """`?ver=precios,cobertura` → claves válidas, en orden de apertura, sin
    repetir. Una clave desconocida se ignora, mismo criterio que
    `resolve_products`: un query string raro no da 404."""
    if not raw:
        return []
    out: list[str] = []
    for chunk in raw.split(","):
        key = chunk.strip()
        if key in _VIEW_KEYS and key not in out:
            out.append(key)
    return out


@dataclass(frozen=True)
class ViewToggle:
    """Un ítem del menú del header: abre o cierra su ventana flotante.

    `query` es el estado tras togglearla —abrirla al final de `?ver=` o
    sacarla—, preservando productos, provincia y ventana de días. El × de la
    ventana usa el mismo `query` (con la ventana abierta, togglear = cerrar).
    """

    key: str
    label: str
    is_open: bool
    query: str
    icon: str


def build_view_menu(
    open_keys: Sequence[str],
    *,
    active_products: Sequence[str] = (),
    selected_province: str = "",
    selected_desde: date,
    selected_hasta: date,
    capa: str = DEFAULT_LAYER,
) -> list[ViewToggle]:
    """Un ítem por ventana, en el orden del menú. Varias pueden estar abiertas a
    la vez: se apilan sobre el mapa."""
    active_slugs = [product_slug(n) for n in active_products]
    prov_slug = product_slug(selected_province) if selected_province else ""
    items: list[ViewToggle] = []
    for key, label in VIEWS:
        is_open = key in open_keys
        resultado = (
            [k for k in open_keys if k != key] if is_open else [*open_keys, key]
        )
        items.append(
            ViewToggle(
                key=key,
                label=label,
                is_open=is_open,
                icon=VIEW_ICONS[key],
                query=products_query(
                    active_slugs,
                    provincia=prov_slug,
                    desde=selected_desde,
                    hasta=selected_hasta,
                    ver=resultado,
                    capa=capa,
                ),
            )
        )
    return items


def build_layer_nav(
    selected_layer: str,
    *,
    active_products: Sequence[str] = (),
    selected_province: str = "",
    selected_desde: date,
    selected_hasta: date,
    ver: Sequence[str] = (),
) -> list[NavChoice]:
    """Las dos capas del mapa, marcando la activa y preservando todo lo demás.

    `NavChoice.slug` es la clave de `?capa=` (o cadena vacía para la capa por
    defecto, mismo criterio que «Todos»/«Todas» en los otros ejes).
    """
    active_slugs = [product_slug(n) for n in active_products]
    prov_slug = product_slug(selected_province) if selected_province else ""
    etiquetas = {"publicacion": "Publicación", "densidad": "Densidad de anuncios"}
    return [
        NavChoice(
            label=etiquetas[layer],
            slug=layer,
            is_selected=layer == selected_layer,
            query=products_query(
                active_slugs,
                provincia=prov_slug,
                desde=selected_desde,
                hasta=selected_hasta,
                ver=ver,
                capa=layer,
            ),
        )
        for layer in LAYER_CHOICES
    ]
