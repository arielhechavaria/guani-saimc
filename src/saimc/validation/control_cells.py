"""Lectura de la base para los controles de §10.7 (slice C3). Solo lee.

`validation.controls` es puro; este módulo le arma las filas. La celda y el
precio comparable **no se definen acá**: salen de
`cells.compute_retail_observations` —la misma función que normaliza las filas
de las vistas públicas— agrupadas por `cells.retail_cell_key`, la clave de
`compute_retail_ranges`. Y el recorte es el de la capa pública: productos
piloto, precio ya en CUP y la ventana de `DEFAULT_WINDOW_DAYS` días, cortada
por `published_date` o, si falta, el día de alta de la fila.

Dos consultas además de esa, sin importar cuántas filas haya: las ingestas de
la ventana y los canales (`origin.origin_resolver`).

Contrato: `docs/spec-slice-C3.md`.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from saimc.cells import (
    RetailCellKey,
    RetailObservation,
    compute_retail_observations,
    retail_cell_key,
)
from saimc.formats import format_label
from saimc.ingestion.origin import origin_resolver
from saimc.models import Product, RawIngestion
from saimc.validation.context import as_utc
from saimc.validation.controls import CellControls, ControlRow, cell_controls
from saimc.validation.persist import MIN_CELL_N
from saimc.web.product_nav import DEFAULT_WINDOW_DAYS

#: Orden de la sección: primero lo que diverge, después lo que no tiene ancla.
_STATUS_ORDER = {"diverges": 0, "unanchored": 1, "anchored": 2}


@dataclass(frozen=True)
class ControlledCell:
    """Una celda pública con sus controles."""

    product_name: str
    province_name: str | None
    unit: str
    #: «caja de 40 lb», o `None` sin formato establecido.
    format_label: str | None
    #: «girasol Vitalín importado», o `None` sin variante declarada.
    variant_label: str | None
    controls: CellControls

    @property
    def status(self) -> str:
        """`diverges`, `unanchored` (sin ancla) o `anchored`."""
        if self.controls.diverges:
            return "diverges"
        return "anchored" if self.controls.anchored else "unanchored"


def window_cells(
    session: Session, *, hoy: date | None = None
) -> dict[RetailCellKey, list[RetailObservation]]:
    """Las celdas públicas de la ventana de 30 días, con sus observaciones.

    Lo comparten la sección de `/control` y `measure_n`, para que los dos
    midan sobre las mismas celdas.
    """
    pilot = frozenset(
        session.execute(select(Product.name).where(Product.is_pilot.is_(True))).scalars()
    )
    report = compute_retail_observations(session, days=DEFAULT_WINDOW_DAYS, hoy=hoy)
    cells: dict[RetailCellKey, list[RetailObservation]] = {}
    for observation in report.observations:
        if observation.product_name in pilot and not observation.unconverted:
            cells.setdefault(retail_cell_key(observation), []).append(observation)
    return cells


@dataclass(frozen=True)
class _Source:
    """Lo que los controles leen de la ingesta de una fila."""

    channel: uuid.UUID | None
    sender: str | None
    at: datetime
    text: str | None


def _sources(
    session: Session, observations: Sequence[RetailObservation]
) -> dict[uuid.UUID, _Source]:
    """Por ingesta: canal resuelto, emisor, hora y texto. Una sola consulta."""
    ids = {observation.evidence_id for observation in observations}
    if not ids:
        return {}
    resolve = origin_resolver(session)
    stmt = select(
        RawIngestion.id,
        RawIngestion.channel_id,
        RawIngestion.source_reference,
        RawIngestion.sender_hash,
        RawIngestion.captured_at,
        RawIngestion.original_text,
    ).where(RawIngestion.id.in_(ids))
    return {
        ingestion_id: _Source(
            channel=resolve(channel_id, source_reference),
            sender=sender,
            at=as_utc(captured_at),
            text=text,
        )
        for ingestion_id, channel_id, source_reference, sender, captured_at, text in (
            session.execute(stmt)
        )
    }


def _label(parts: Sequence[str | None]) -> str | None:
    present = [part for part in parts if part]
    return " ".join(present) if present else None


def _format_label(packaging: str | None, quantity: Decimal | None, unit: str | None) -> str | None:
    if packaging is None or quantity is None or unit is None:
        return None
    return format_label(packaging, quantity, unit)


def compute_cell_controls(
    session: Session, *, burst_window: timedelta, hoy: date | None = None
) -> list[ControlledCell]:
    """Los controles de cada celda pública de la ventana.

    Primero las que divergen, después las sin ancla y al final el resto; dentro
    de cada grupo, de mayor a menor muestra.
    """
    cells = window_cells(session, hoy=hoy)
    sources = _sources(session, [o for group in cells.values() for o in group])

    result: list[ControlledCell] = []
    for key, observations in cells.items():
        rows = []
        for observation in observations:
            source = sources[observation.evidence_id]
            rows.append(
                ControlRow(
                    price=observation.unit_price,
                    provenance=observation.provenance or "",
                    channel=source.channel,
                    sender=source.sender,
                    at=source.at,
                    text=source.text,
                )
            )
        first = observations[0]
        result.append(
            ControlledCell(
                product_name=key[0],
                province_name=key[1],
                unit=first.unit,
                format_label=_format_label(
                    first.format_packaging, first.format_quantity, first.format_unit
                ),
                variant_label=_label(
                    [first.variant_kind, first.variant_brand, first.variant_origin]
                ),
                controls=cell_controls(rows, min_control_n=MIN_CELL_N, burst_window=burst_window),
            )
        )
    result.sort(
        key=lambda cell: (
            _STATUS_ORDER[cell.status],
            -cell.controls.count,
            cell.product_name,
            cell.province_name or "",
            cell.format_label or "",
            cell.variant_label or "",
        )
    )
    return result
