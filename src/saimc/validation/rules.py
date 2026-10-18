"""Las seis reglas de `spec.md` §6.2. Código puro: sin base, sin red, sin escribir.

Cada regla devuelve `pass`, `fail` o `unknown` (`Outcome`). Los umbrales llegan
todos por argumento: ninguna función de acá trae un valor por defecto, y un
parámetro en `None` (sin configuración) deja la regla en `unknown`.

Contrato: `docs/spec-motor-validacion.md`, «Las seis reglas».
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from saimc.cells import BasisReading, ResolvedRow, cell_sample, evaluate_basis, resolve_row
from saimc.extraction.engine import ExtractionProposal
from saimc.models import Currency
from saimc.validation.fence import robust_fence
from saimc.validation.types import CellReference, ChannelContext, Outcome, RecentRow

#: Ventana de la regla 5. No es un parámetro a calibrar: la fija la
#: especificación («en las 24 h previas»).
DUPLICATE_WINDOW = timedelta(hours=24)

_CURRENCIES = frozenset(c.value for c in Currency)


def check_structure(row: ExtractionProposal, *, pilot_ids: frozenset[uuid.UUID]) -> Outcome:
    """Regla 1: producto piloto del catálogo, precio > 0, moneda y provincia."""
    complete = (
        row.product is not None
        and row.product.id in pilot_ids
        and row.price is not None
        and row.price > 0
        and row.currency in _CURRENCIES
        and row.province is not None
    )
    return Outcome.PASS if complete else Outcome.FAIL


def _resolve(
    row: ExtractionProposal, *, price_cup: Decimal | None, price_basis: str | None
) -> ResolvedRow | None:
    """El precio de la fila en CUP por unidad base, con la normalización de
    `cells.compute_cells` (`resolve_row`). `None` si no se puede comparar con
    su celda: sin precio, sin tasa, sin cantidad o sin saber si dividir."""
    if row.product is None or row.price is None:
        return None
    resolved = resolve_row(
        currency=row.currency,
        price=row.price,
        price_cup=price_cup,
        quantity=row.quantity,
        price_basis=price_basis,
        unit=row.unit,
        product_unit=row.product.unit,
    )
    if resolved.unconverted or resolved.unquantified or resolved.unresolved_basis:
        return None
    return resolved


@dataclass(frozen=True)
class BasisCorrection:
    """La otra base de una fila que la regla 6 desmiente, con sus números.

    Es lo que el formulario de la cola muestra al operador: las dos lecturas
    del mismo precio, en CUP por unidad base, y la mediana de la celda contra
    la que se compararon.
    """

    #: La base que la celda prefiere (`unit` o `total`): la contraria a la declarada.
    flipped_basis: str
    #: Precio por `unit` leído como la fila declara.
    declared: Decimal
    #: Precio por `unit` leído con `flipped_basis`.
    other: Decimal
    reference: Decimal
    unit: str
    #: `True` si la mediana salió de todas las provincias y no de la propia.
    from_all_provinces: bool


def _basis_reading(
    row: ExtractionProposal,
    *,
    price_cup: Decimal | None,
    cell: CellReference,
    min_cell_n: int | None,
) -> tuple[ResolvedRow, ResolvedRow, BasisReading, str] | None:
    """Las dos lecturas del precio de la fila contra su celda, o `None` si no
    hay veredicto. Lo comparten la regla 6 y la corrección de base."""
    if min_cell_n is None:
        return None
    declared = _resolve(row, price_cup=price_cup, price_basis=row.price_basis)
    if declared is None:
        return None
    # Con cantidad 1 las dos lecturas son el mismo número y `price_basis` puede
    # faltar; para la «otra» lectura alcanza con cualquiera de las dos.
    flipped = "unit" if row.price_basis == "total" else "total"
    other = _resolve(row, price_cup=price_cup, price_basis=flipped)
    if other is None:
        return None
    reading = evaluate_basis(
        declared=declared.effective_price,
        other=other.effective_price,
        own_province=cell.own_province,
        all_provinces=cell.all_provinces,
        min_references=min_cell_n,
    )
    if reading is None:
        return None
    return declared, other, reading, flipped


def basis_correction(
    row: ExtractionProposal,
    *,
    price_cup: Decimal | None,
    cell: CellReference,
    min_cell_n: int | None,
) -> BasisCorrection | None:
    """La corrección de base de una fila, o `None` si no hay veredicto o la
    declarada es la que mejor encaja. Existe exactamente cuando la regla 6
    (`check_quantity_price`) sale `fail`: comparten el cálculo."""
    result = _basis_reading(row, price_cup=price_cup, cell=cell, min_cell_n=min_cell_n)
    if result is None or not result[2].other_fits_better:
        return None
    declared, other, reading, flipped = result
    return BasisCorrection(
        flipped_basis=flipped,
        declared=declared.effective_price,
        other=other.effective_price,
        reference=reading.reference,
        unit=declared.unit,
        from_all_provinces=reading.reference_from_all_provinces,
    )


def check_quantity_price(
    row: ExtractionProposal,
    *,
    price_cup: Decimal | None,
    cell: CellReference,
    min_cell_n: int | None,
) -> Outcome:
    """Regla 6: ¿la lectura que declara `price_basis` es la más cercana a la celda?

    Se calculan las dos lecturas del mismo número (por unidad y como total del
    lote) y `cells.evaluate_basis` dice cuál queda más cerca de la mediana de
    la celda: el mismo cálculo que `/control` usa para las bases sospechosas.
    """
    result = _basis_reading(row, price_cup=price_cup, cell=cell, min_cell_n=min_cell_n)
    if result is None:
        return Outcome.UNKNOWN
    return Outcome.FAIL if result[2].other_fits_better else Outcome.PASS


def check_price(
    row: ExtractionProposal,
    *,
    price_cup: Decimal | None,
    cell: CellReference,
    min_cell_n: int | None,
    quantity_price: Outcome,
) -> Outcome:
    """Regla 2: el precio normalizado cae dentro de la banda robusta de su celda.

    Recibe el resultado de la regla 6: si la base del precio quedó desmentida
    por la celda, no se sabe cuál es el precio por unidad —el mismo «no se sabe
    si dividir» de `resolve_row`— y la banda no tiene qué juzgar: `unknown`.
    Así el caso del saco marcado `unit` sale `inconsistent` y no `anomaly`
    (caso 14 de la especificación; `docs/decisions.md`, 2026-10-02, «A5b»).
    """
    if min_cell_n is None or quantity_price is Outcome.FAIL:
        return Outcome.UNKNOWN
    resolved = _resolve(row, price_cup=price_cup, price_basis=row.price_basis)
    if resolved is None:
        return Outcome.UNKNOWN
    picked = cell_sample(cell.own_province, cell.all_provinces, min_references=min_cell_n)
    if picked is None or not picked[0]:
        return Outcome.UNKNOWN
    low, high = robust_fence(picked[0])
    return Outcome.PASS if low <= float(resolved.effective_price) <= high else Outcome.FAIL


def check_location(
    row: ExtractionProposal,
    *,
    province_ids: frozenset[uuid.UUID],
    channel: ChannelContext | None,
) -> Outcome:
    """Regla 3: provincia del catálogo y que coincide con la del canal.

    Una provincia válida pero distinta de la del canal no es un error: es
    `unknown`, y va a revisión.
    """
    if row.province is None or row.province.id not in province_ids:
        return Outcome.FAIL
    if channel is None or channel.province_id is None:
        return Outcome.UNKNOWN
    return Outcome.PASS if row.province.id == channel.province_id else Outcome.UNKNOWN


def check_trust(
    channel: ChannelContext | None,
    *,
    min_history: int | None,
    max_edit_rate: float | None,
) -> Outcome:
    """Regla 4, a nivel canal: tiene historial y el operador casi no lo corrige.

    `unknown` si no hay canal, si faltan parámetros o si el historial no llega
    al mínimo: un canal nuevo no entra al carril 1 hasta juntarlo.
    """
    if channel is None or min_history is None or max_edit_rate is None:
        return Outcome.UNKNOWN
    if channel.decisions < min_history or channel.proposed_rows <= 0:
        return Outcome.UNKNOWN
    # Multiplicando en vez de dividir: misma comparación, sin división por cero.
    dirty = channel.changed_rows > max_edit_rate * channel.proposed_rows
    return Outcome.FAIL if dirty else Outcome.PASS


def check_duplicate(
    row: ExtractionProposal,
    *,
    published_at: datetime,
    recent_rows: Sequence[RecentRow],
) -> Outcome:
    """Regla 5: ¿hay una fila del mismo canal con el mismo producto, provincia
    y precio en las 24 h previas?

    La ventana incluye sus dos bordes: a las 24 h exactas todavía es un posible
    duplicado (lectura conservadora, va a revisión). Las filas posteriores al
    anuncio no cuentan.
    """
    if row.product is None or row.price is None:
        return Outcome.PASS  # sin producto o precio no hay qué repetir; la regla 1 ya falla
    province_id = None if row.province is None else row.province.id
    for recent in recent_rows:
        elapsed = published_at - recent.published_at
        if (
            timedelta(0) <= elapsed <= DUPLICATE_WINDOW
            and recent.product_id == row.product.id
            and recent.province_id == province_id
            and recent.price == row.price
        ):
            return Outcome.FAIL
    return Outcome.PASS
