"""Revalidación de `prices_history` (slice B1).

Corre `python -m saimc.validation.revalidate` (por defecto, en seco).

Recorre los anuncios que tienen filas en `prices_history` y a cada fila le
vuelve a calcular el estado, el puntaje y el detalle, con la versión del motor
y los parámetros vigentes (`validation.persist`). Sirve para tres cosas:

- validar las filas anteriores a B1, que quedaron con estado nulo;
- reaplicar un cambio de `MIN_CELL_N`, de los parámetros de la regla 4
  (`CHANNEL_MIN_HISTORY`, `CHANNEL_MAX_EDIT_RATE`) o de `ENGINE_VERSION`;
- refrescar después de un backfill de tasas o de base de precio, que cambian
  el número que el motor compara.

Lo que hace y lo que no:

- **En seco por defecto.** Sin `--execute` calcula todo y no escribe nada: solo
  informa cuántas filas cambiarían de estado.
- **Solo escribe la validación.** No toca `provenance` ni ninguna otra columna.
- **Reescribe el detalle entero**, así que también pone al día la clave `jev`
  (A6b): la anota si Jev está activo y el anuncio tiene señal, y la quita si
  Jev está inactivo. Es el relleno de las filas anteriores a la señal.
- **No depende del orden.** Cada fila se valida contra las referencias
  anteriores a SU anuncio, que no miran el estado de ninguna otra fila.
- **Un anuncio que falla no detiene la corrida.** Sus filas quedan como
  estaban, el error va al log y el resumen lo cuenta.
- No llama a ninguna API: no gasta nada.

Contrato: `docs/spec-slice-B1.md`, «Revalidación».
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from saimc.db import session_scope
from saimc.models import PriceHistory, RawIngestion
from saimc.models.base import utc_now
from saimc.validation import persist
from saimc.validation.context import load_catalog
from saimc.validation.engine import ENGINE_VERSION

logger = logging.getLogger(__name__)

_UNVALIDATED = "sin validar"


@dataclass
class Summary:
    """Qué hizo (o haría) una corrida."""

    #: Filas recorridas.
    rows: int = 0
    #: `(estado anterior, estado nuevo)` -> filas. Solo las que cambian;
    #: `None` es «sin validar».
    transitions: Counter[tuple[str | None, str]] = field(default_factory=Counter)
    #: Filas de anuncios cuya validación falló: quedan como estaban.
    failed_rows: int = 0

    @property
    def changed(self) -> int:
        return sum(self.transitions.values())


def select_ingestions(session: Session) -> list[RawIngestion]:
    """Los anuncios con filas en `prices_history`, el más viejo primero."""
    stmt = (
        select(RawIngestion)
        .where(RawIngestion.id.in_(select(PriceHistory.source_id)))
        .order_by(RawIngestion.captured_at, RawIngestion.id)
    )
    return list(session.execute(stmt).scalars())


def revalidate(
    session: Session, ingestions: Sequence[RawIngestion], *, execute: bool
) -> Summary:
    """Revalida las filas de esos anuncios. Con `execute=False` no escribe.

    No hace `commit`: es de quien llama.
    """
    catalog = load_catalog(session)
    moment = utc_now()
    summary = Summary()
    for ingestion in ingestions:
        rows = list(
            session.execute(
                select(PriceHistory)
                .where(PriceHistory.source_id == ingestion.id)
                .order_by(PriceHistory.created_at, PriceHistory.id)
            ).scalars()
        )
        summary.rows += len(rows)
        try:
            validations = persist.validate_rows(session, ingestion, rows, catalog=catalog)
        except Exception:
            # Amplio a propósito, igual que en el guardado. Las filas quedan
            # como estaban: acá no hay un valor nuevo que las desmienta.
            logger.exception("validation.revalidate_fallo ingestion_id=%s", ingestion.id)
            summary.failed_rows += len(rows)
            continue
        # El bloque `jev` solo hace falta si se va a escribir; `jev_blocks`
        # nunca lanza y no cambia el estado de ninguna fila.
        blocks = (
            persist.jev_blocks(session, ingestion, rows, catalog=catalog)
            if execute
            else [None] * len(rows)
        )
        for row, validation, jev in zip(rows, validations, blocks, strict=True):
            if row.validation_status != validation.status.value:
                summary.transitions[(row.validation_status, validation.status.value)] += 1
            if execute:
                persist.store(row, validation, moment, jev)
    return summary


# --- CLI --------------------------------------------------------------------


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m saimc.validation.revalidate",
        description=(
            "Vuelve a validar las filas de prices_history con el motor y los "
            "parámetros vigentes. Por defecto no escribe; --execute guarda."
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Solo informar (por defecto).")
    mode.add_argument(
        "--execute",
        action="store_true",
        help="Escribe estado, puntaje y detalle en cada fila. No toca nada más.",
    )
    return parser.parse_args(argv)


def print_summary(summary: Summary, *, executed: bool, out: Callable[[str], None]) -> None:
    out(f"Motor {ENGINE_VERSION}, min_cell_n = {persist.MIN_CELL_N}.")
    verbo = "Cambiaron" if executed else "Cambian"
    out(f"{verbo} de estado: {summary.changed} de {summary.rows} filas.")
    # Orden estable: primero por estado nuevo, después por el anterior.
    for (before, after), count in sorted(
        summary.transitions.items(), key=lambda item: (item[0][1], item[0][0] or "")
    ):
        out(f"  {before or _UNVALIDATED} → {after}: {count}")
    if summary.failed_rows:
        out(
            f"AVISO: {summary.failed_rows} filas no se pudieron validar y quedaron "
            "como estaban (el detalle está en el log)."
        )


def execute(
    args: argparse.Namespace, session: Session, *, out: Callable[[str], None] = print
) -> int:
    """Corre el modo pedido. Devuelve el código de salida."""
    if not args.execute:
        out("MODO EN SECO: no se escribe nada.")
    summary = revalidate(session, select_ingestions(session), execute=args.execute)
    print_summary(summary, executed=args.execute, out=out)
    if not args.execute:
        out("Para guardar: agregá --execute.")
    return 1 if summary.failed_rows else 0


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    with session_scope() as session:
        code = execute(args, session)
    sys.exit(code)


if __name__ == "__main__":
    main()
