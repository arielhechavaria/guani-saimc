"""Resolución de la variante (kind/brand/origin) de un producto.

Punto único, como `formats.py::get_or_create_format`: lo llama el formulario
de curaduría (design.md D7). Que declarar una variante y elegirla sean el
mismo acto es lo que hace segura la creación al vuelo — el operador nunca
necesita saber si "Vitalín" ya existe, la segunda llamada con la misma
ortografía o una equivalente encuentra la fila que ya existe.
"""

import unicodedata
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from saimc.models import ProductVariant


def variant_display(raw: str | None) -> str:
    """Forma de exhibición: recorta bordes y colapsa espacios internos.

    Tildes y mayúsculas sobreviven tal como las escribió el operador —
    «Vitalín» se muestra «Vitalín» para siempre, aunque un valor posterior lo
    haya escrito distinto (design.md D3)."""
    if not raw:
        return ""
    return " ".join(raw.split())


def variant_key(raw: str | None) -> str:
    """Forma canónica para comparar: sin tildes, casefold, espacios colapsados.

    `""` cuando el eje no se declara — nunca `None`, porque `NULL` en una
    UNIQUE compuesta no deduplica (design.md D1). Mismo plegado que
    `engine._strip_accents`/`llm_assist._normalize`; `casefold()` en vez de
    `.lower()` porque acá el resultado es una clave de comparación, no texto
    para mostrar."""
    decomposed = unicodedata.normalize("NFD", variant_display(raw))
    sin_tildes = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    return sin_tildes.casefold()


def get_or_create_variant(
    session: Session, *, product_id: UUID, kind: str, brand: str, origin: str
) -> ProductVariant | None:
    """Resuelve los tres ejes a su variante, creándola si no existe.

    `None` cuando los tres quedan vacíos: eso es «sin variante declarada» y
    el llamador guarda `variant_id = NULL`. Idempotente por
    `uq_product_variants_identity` sobre las columnas `*_key`.
    """
    kind_key = variant_key(kind)
    brand_key = variant_key(brand)
    origin_key = variant_key(origin)
    if not kind_key and not brand_key and not origin_key:
        return None

    # `no_autoflush`: mismo guardarraíl que `get_or_create_format` — este
    # SELECT no debe forzar el autoflush de otros objetos sucios en la misma
    # sesión (bug real de producción, 2026-09-04, docs/decisions.md).
    with session.no_autoflush:
        existente = session.scalar(
            select(ProductVariant).where(
                ProductVariant.product_id == product_id,
                ProductVariant.kind_key == kind_key,
                ProductVariant.brand_key == brand_key,
                ProductVariant.origin_key == origin_key,
            )
        )
    if existente is not None:
        return existente

    variante = ProductVariant(
        product_id=product_id,
        kind=variant_display(kind),
        brand=variant_display(brand),
        origin=variant_display(origin),
        kind_key=kind_key,
        brand_key=brand_key,
        origin_key=origin_key,
    )
    session.add(variante)
    # Flush y no commit: el llamador decide la transacción. El flush hace que
    # una segunda llamada dentro de la misma transacción vea esta fila.
    session.flush()
    return variante
