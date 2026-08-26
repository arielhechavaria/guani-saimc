"""Filtro por producto para la vista pública.

Mismo reparto que `map_view` / `card_view` / `series_view`: acá se arma la lista
de opciones y cuál está activa; la plantilla solo la pinta. Que el slug y la
resolución vivan en Python y no en Jinja es lo que deja testear que
«azucar-blanca» resuelve a «Azúcar blanca» sin parsear HTML.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass


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


@dataclass(frozen=True)
class ProductChoice:
    """Una opción de la barra de filtro."""

    label: str
    #: Slug del producto, o cadena vacía para «Todos».
    slug: str
    is_selected: bool
    #: Destino del enlace sin ancla; la plantilla le agrega `#precios`.
    query: str


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


def build_product_nav(
    pilot_names: Iterable[str], selected_name: str | None
) -> list[ProductChoice]:
    """«Todos» + un ítem por producto piloto, marcando el activo."""
    choices = [
        ProductChoice(
            label="Todos",
            slug="",
            is_selected=selected_name is None,
            query="/",
        )
    ]
    for name in pilot_names:
        slug = product_slug(name)
        choices.append(
            ProductChoice(
                label=name,
                slug=slug,
                is_selected=name == selected_name,
                query=f"/?producto={slug}",
            )
        )
    return choices
