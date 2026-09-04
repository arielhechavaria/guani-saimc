"""Resolución del triple `(envase, cantidad, unidad)` a un `ProductFormat`.

Punto único: lo llaman el formulario de curaduría y `/catalog` (design.md D7).
Que declarar un formato y elegirlo sean el mismo acto es lo que hace segura la
creación al vuelo — el operador nunca tipea un formato, elige envase de una
lista cerrada, escribe un número y elige unidad, así que "caja 40", "caja de
40" y "caja 40lb" colapsan al mismo triple y el segundo intento encuentra la
fila que ya existe.
"""

from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from saimc.models import ProductFormat
from saimc.units import PACKAGING, UNITS


def get_or_create_format(
    session: Session,
    *,
    product_id: UUID,
    packaging: str,
    quantity: Decimal,
    unit: str,
) -> ProductFormat:
    """Devuelve el formato del triple, creándolo si no existe.

    El vocabulario se canonicaliza en el llamador; acá se exige ya canónico y
    un valor fuera de `PACKAGING`/`UNITS` levanta `ValueError` **antes** de
    tocar la sesión. Idempotente por `uq_product_formats_identity`: dos
    llamadas con el mismo triple devuelven la misma fila sin insertar dos veces.
    """
    if packaging not in PACKAGING:
        raise ValueError(f"envase fuera del vocabulario: {packaging!r}")
    if unit not in UNITS:
        raise ValueError(f"unidad fuera del vocabulario: {unit!r}")

    # `no_autoflush`: este SELECT no debe forzar el autoflush de OTROS objetos
    # sucios en la misma sesión. Bug real de producción (2026-09-04,
    # docs/decisions.md): `curation.py::submit_edit` asigna `existing.currency
    # = "CUP"` y unas líneas más abajo llama acá para resolver `format_id`,
    # ANTES de limpiar `existing.exchange_rate` (eso pasa después, en
    # `apply_resolution`). Sin este bloque, el autoflush de ESTE SELECT
    # intentaba persistir esa fila a medio actualizar —CUP con una tasa
    # vieja todavía puesta— y violaba `ck_prices_history_exchange_rate_cup`.
    # El `session.flush()` explícito de abajo no lo afecta: sigue haciendo
    # visible el formato recién creado a una segunda llamada dentro del mismo
    # request.
    with session.no_autoflush:
        existente = session.scalar(
            select(ProductFormat).where(
                ProductFormat.product_id == product_id,
                ProductFormat.packaging == packaging,
                ProductFormat.quantity == quantity,
                ProductFormat.unit == unit,
            )
        )
    if existente is not None:
        return existente

    formato = ProductFormat(
        product_id=product_id,
        packaging=packaging,
        quantity=quantity,
        unit=unit,
    )
    session.add(formato)
    # Flush y no commit: el llamador decide la transacción. El flush hace que
    # una segunda llamada dentro de la misma transacción vea esta fila.
    session.flush()
    return formato
