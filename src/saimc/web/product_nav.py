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
    provincia: str = "",
    dias: int = DEFAULT_WINDOW_DAYS,
    path: str = "/",
) -> str:
    """Compone la URL con los ejes activos, en orden fijo producto/provincia/días.

    Omite `dias` cuando es el default para no ensuciar la URL compartible con
    un parámetro redundante. Las barras de filtro le agregan el ancla `#precios`;
    la descarga usa `path="/datos.csv"`.
    """
    params = []
    if producto:
        params.append(f"producto={producto}")
    if provincia:
        params.append(f"provincia={provincia}")
    if dias != DEFAULT_WINDOW_DAYS:
        params.append(f"dias={dias}")
    return f"{path}?" + "&".join(params) if params else path


def download_href(
    *, producto: str = "", provincia: str = "", dias: int = DEFAULT_WINDOW_DAYS
) -> str:
    """URL de `/datos.csv` con los mismos filtros que la vista, para que la
    descarga traiga exactamente lo que se está viendo."""
    return _query(producto=producto, provincia=provincia, dias=dias, path="/datos.csv")


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
) -> list[NavChoice]:
    """«Todas» + un ítem por provincia con dato piloto, marcando la activa.

    Simétrica a `build_product_nav`; cada enlace preserva el producto y la
    ventana elegidos.
    """
    prod_slug = product_slug(selected_product) if selected_product else ""
    choices = [
        NavChoice(
            label="Todas",
            slug="",
            is_selected=selected_name is None,
            query=_query(producto=prod_slug, provincia="", dias=selected_days),
        )
    ]
    for name in province_names:
        slug = product_slug(name)
        choices.append(
            NavChoice(
                label=name,
                slug=slug,
                is_selected=name == selected_name,
                query=_query(producto=prod_slug, provincia=slug, dias=selected_days),
            )
        )
    return choices


def build_window_nav(
    selected_days: int,
    selected_name: str | None = None,
    *,
    selected_province: str = "",
) -> list[WindowChoice]:
    """Las tres ventanas, marcando la activa y preservando producto y provincia."""
    prod_slug = product_slug(selected_name) if selected_name else ""
    prov_slug = product_slug(selected_province) if selected_province else ""
    return [
        WindowChoice(
            label=f"{days} días",
            days=days,
            is_selected=days == selected_days,
            query=_query(producto=prod_slug, provincia=prov_slug, dias=days),
        )
        for days in WINDOW_CHOICES
    ]
