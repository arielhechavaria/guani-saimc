"""Barras de filtro de la vista pública: por producto y por ventana de días.

Mismo reparto que `map_view` / `card_view` / `series_view`: acá se arma la lista
de opciones y cuál está activa, con su enlace ya compuesto; la plantilla solo la
pinta. Que el slug, la resolución y la composición del query string vivan en
Python y no en Jinja es lo que deja testear que «azucar-blanca» resuelve a
«Azúcar blanca», y que elegir una ventana preserva el producto elegido, sin
parsear HTML.
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
    """Nombre de producto → segmento de URL estable.

    Quita las tildes con `unicodedata`: `NFKD` descompone «á» en «a» + el
    acento como carácter combinante aparte, y `combining()` deja tirar solo
    ese. Es la vía idiomática en Python y cubre cualquier acento, no solo los
    que se nos ocurran a mano. «Azúcar blanca» → «azucar-blanca».
    """
    sin_tildes = "".join(
        c
        for c in unicodedata.normalize("NFKD", name)
        if not unicodedata.combining(c)
    )
    return "-".join(sin_tildes.lower().split())


def resolve_product(
    pilot_names: Sequence[str], slug: str | None
) -> str | None:
    """El nombre piloto cuyo slug coincide con `slug`, o `None`.

    `None` tanto si `slug` viene vacío como si no matchea ninguno: una vista
    pública no responde 404 por un query string raro, muestra todo.
    """
    if not slug:
        return None
    for name in pilot_names:
        if product_slug(name) == slug:
            return name
    return None


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


def _query(*, producto: str, dias: int) -> str:
    """Compone `/`, `/?producto=x`, `/?dias=7` o `/?producto=x&dias=7`.

    Omite `dias` cuando es el default para no ensuciar la URL compartible con
    un parámetro redundante. La plantilla le agrega el ancla `#precios`.
    """
    params = []
    if producto:
        params.append(f"producto={producto}")
    if dias != DEFAULT_WINDOW_DAYS:
        params.append(f"dias={dias}")
    return "/?" + "&".join(params) if params else "/"


@dataclass(frozen=True)
class ProductChoice:
    """Una opción de la barra de filtro por producto."""

    label: str
    #: Slug del producto, o cadena vacía para «Todos».
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
) -> list[ProductChoice]:
    """«Todos» + un ítem por producto piloto, marcando el activo.

    Cada enlace preserva `selected_days`: cambiar de producto no debería
    resetear la ventana que el usuario venía mirando.
    """
    choices = [
        ProductChoice(
            label="Todos",
            slug="",
            is_selected=selected_name is None,
            query=_query(producto="", dias=selected_days),
        )
    ]
    for name in pilot_names:
        slug = product_slug(name)
        choices.append(
            ProductChoice(
                label=name,
                slug=slug,
                is_selected=name == selected_name,
                query=_query(producto=slug, dias=selected_days),
            )
        )
    return choices


def build_window_nav(
    selected_days: int, selected_name: str | None = None
) -> list[WindowChoice]:
    """Las tres ventanas, marcando la activa y preservando el producto elegido."""
    slug = product_slug(selected_name) if selected_name else ""
    return [
        WindowChoice(
            label=f"{days} días",
            days=days,
            is_selected=days == selected_days,
            query=_query(producto=slug, dias=days),
        )
        for days in WINDOW_CHOICES
    ]
