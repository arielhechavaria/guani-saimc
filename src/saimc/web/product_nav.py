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


def _query(
    *,
    producto: str = "",
    productos: Sequence[str] = (),
    provincia: str = "",
    dias: int = DEFAULT_WINDOW_DAYS,
    ver: Sequence[str] = (),
    path: str = "/",
) -> str:
    """Compone la URL con los ejes activos, en orden fijo
    producto/provincia/días/ver.

    `productos` (lista de slugs, multi-selección) gana sobre `producto` (slug
    único, alias histórico) cuando trae algo: se emite `productos=a,b`. `ver`
    son las ventanas flotantes abiertas desde el menú del header. Omite `dias`
    cuando es el default para no ensuciar la URL compartible con un parámetro
    redundante. La descarga usa `path="/datos.csv"`.
    """
    params = []
    if productos:
        params.append("productos=" + ",".join(productos))
    elif producto:
        params.append(f"producto={producto}")
    if provincia:
        params.append(f"provincia={provincia}")
    if dias != DEFAULT_WINDOW_DAYS:
        params.append(f"dias={dias}")
    if ver:
        params.append("ver=" + ",".join(ver))
    return f"{path}?" + "&".join(params) if params else path


def products_query(
    product_slugs: Sequence[str],
    *,
    provincia: str = "",
    dias: int = DEFAULT_WINDOW_DAYS,
    ver: Sequence[str] = (),
    path: str = "/",
) -> str:
    """URL con `?productos=<slugs>` (o sin el parámetro si la lista está vacía),
    preservando provincia, ventana de días y ventanas abiertas. La usan el
    riel de chips, los paneles flotantes, el mapa y la hoja de provincia."""
    return _query(
        productos=list(product_slugs),
        provincia=provincia,
        dias=dias,
        ver=list(ver),
        path=path,
    )


def download_href(
    *,
    producto: str = "",
    productos: Sequence[str] = (),
    provincia: str = "",
    dias: int = DEFAULT_WINDOW_DAYS,
) -> str:
    """URL de `/datos.csv` con los mismos filtros que la vista, para que la
    descarga traiga exactamente lo que se está viendo."""
    return _query(
        producto=producto,
        productos=productos,
        provincia=provincia,
        dias=dias,
        path="/datos.csv",
    )


def _product_link(slug: str, *, provincia: str, dias: int) -> str:
    """Enlace de un chip de producto: la URL limpia `/producto/<slug>` cuando no
    hay otro filtro activo, y el query string en cuanto lo hay.

    La ruta limpia solo sabe de un producto; provincia y ventana viajan como
    query. Mantener las dos formas —y no forzar un redirect— es lo que pide el
    ítem del board: `/producto/arroz` para compartir, `?producto=` como alias.
    """
    if not provincia and dias == DEFAULT_WINDOW_DAYS:
        return f"/producto/{slug}"
    return _query(producto=slug, provincia=provincia, dias=dias)


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
    selected_days: int = DEFAULT_WINDOW_DAYS,
    *,
    selected_province: str = "",
) -> list[NavChoice]:
    """«Todos» + un ítem por producto piloto, marcando el activo.

    Cada enlace preserva `selected_province` y `selected_days`: cambiar de
    producto no debería resetear la provincia ni la ventana que se venía
    mirando.
    """
    prov_slug = product_slug(selected_province) if selected_province else ""
    choices = [
        NavChoice(
            label="Todos",
            slug="",
            is_selected=selected_name is None,
            query=_query(producto="", provincia=prov_slug, dias=selected_days),
        )
    ]
    for name in pilot_names:
        slug = product_slug(name)
        choices.append(
            NavChoice(
                label=name,
                slug=slug,
                is_selected=name == selected_name,
                query=_product_link(slug, provincia=prov_slug, dias=selected_days),
            )
        )
    return choices


def build_province_nav(
    province_names: Iterable[str],
    selected_name: str | None,
    *,
    selected_product: str = "",
    selected_days: int = DEFAULT_WINDOW_DAYS,
    active_products: Sequence[str] = (),
    ver: Sequence[str] = (),
) -> list[NavChoice]:
    """«Todas» + un ítem por provincia con dato piloto, marcando la activa.

    Simétrica a `build_product_nav`; cada enlace preserva el producto (o el
    conjunto `active_products` de los chips), la ventana de días y las
    ventanas flotantes abiertas (`ver`): la barra vive dentro de la ventana
    «Precios», y cambiar de provincia no puede cerrarla.
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
                dias=selected_days,
                ver=ver,
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
                    dias=selected_days,
                    ver=ver,
                ),
            )
        )
    return choices


#: Ícono de cada producto piloto en los chips de la home, por slug. Solo
#: presentación: vive en código y no en `products` para no tocar el esquema
#: (CLAUDE.md: un cambio de esquema pide el flujo de especificación completo).
#: Un producto sin entrada sigue mostrando su nombre. Cada emoji tiene que estar
#: en el subconjunto vendorizado de Noto Color Emoji —lo verifica
#: `test_static_assets.py` y lo suma `tools/build_emoji_font.py`—, o se vería
#: distinto en cada teléfono.
PRODUCT_ICONS: dict[str, str] = {
    "arroz": "🍚",
    "pollo": "🍗",
    "picadillo": "🥩",
    "azucar-blanca": "🍬",
    "aceite": "🫒",
}


def product_icon(name: str) -> str | None:
    """El ícono del producto, o `None` si no tiene uno asignado."""
    return PRODUCT_ICONS.get(product_slug(name))


@dataclass(frozen=True)
class ProductChip:
    """Un interruptor de producto del riel flotante sobre el mapa.

    `query` es el estado resultante de togglear ESTE chip —prenderlo si está
    apagado, apagarlo si está prendido—, preservando provincia y ventana.
    `icon`, si el producto tiene uno, reemplaza al nombre en el chip; el
    nombre queda como etiqueta accesible y como tooltip al pasar por encima.
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
    selected_days: int = DEFAULT_WINDOW_DAYS,
    ver: Sequence[str] = (),
) -> list[ProductChip]:
    """Un chip por producto piloto, en el orden del catálogo.

    El conjunto activo (`active_names`) viaja como `?productos=` en el orden en
    que se fueron prendiendo. Togglear un chip agrega su slug al final o lo
    quita, sin reordenar el resto. `ver` se preserva: prender un producto no
    cierra las ventanas abiertas.
    """
    prov_slug = product_slug(selected_province) if selected_province else ""
    active_slugs = [product_slug(name) for name in active_names]
    chips: list[ProductChip] = []
    for name in pilot_names:
        slug = product_slug(name)
        is_active = slug in active_slugs
        resultado = (
            [s for s in active_slugs if s != slug]
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
                    resultado, provincia=prov_slug, dias=selected_days, ver=ver
                ),
            )
        )
    return chips


def build_window_nav(
    selected_days: int,
    selected_name: str | None = None,
    *,
    selected_province: str = "",
    active_products: Sequence[str] = (),
    ver: Sequence[str] = (),
) -> list[WindowChoice]:
    """Las tres ventanas de días, marcando la activa y preservando producto (o
    el conjunto de chips), provincia y ventanas flotantes abiertas."""
    prod_slug = product_slug(selected_name) if selected_name else ""
    prov_slug = product_slug(selected_province) if selected_province else ""
    active_slugs = [product_slug(n) for n in active_products]
    return [
        WindowChoice(
            label=f"{days} días",
            days=days,
            is_selected=days == selected_days,
            query=_query(
                producto=prod_slug,
                productos=active_slugs,
                provincia=prov_slug,
                dias=days,
                ver=ver,
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


def build_view_menu(
    open_keys: Sequence[str],
    *,
    active_products: Sequence[str] = (),
    selected_province: str = "",
    selected_days: int = DEFAULT_WINDOW_DAYS,
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
                query=products_query(
                    active_slugs,
                    provincia=prov_slug,
                    dias=selected_days,
                    ver=resultado,
                ),
            )
        )
    return items
