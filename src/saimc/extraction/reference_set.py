"""Conjunto de referencia congelado de H0 (slice B3-1).

Contrato: `docs/spec-slice-B3.md`, secciones 1 y 2.

Corre `python -m saimc.extraction.reference_set` (por defecto, en seco).

- **Quién entra** (`select_members`) es una regla, no una elección anuncio por
  anuncio: curado, con al menos una fila en `prices_history`, sin asistencia
  (el mismo `measure.assisted_ingestion_ids` de H0) y fuera de la lista de
  excluidos que se le pasa al comando.
- **Congelar** (`freeze`) inserta un miembro por anuncio con el `set_version`
  siguiente. Guarda las filas confirmadas de ese día (`rows`) y su hash. Solo
  altas: este módulo no modifica ni borra ninguna fila de la tabla, y una
  versión nueva no toca las anteriores.
- **Deriva** (`drift_report`): un miembro deriva cuando el hash de sus filas
  vivas ya no coincide con el congelado. Incluye el anuncio que se quedó sin
  filas. No lo saca del conjunto: la referencia solo cambia con una versión
  nueva.

La forma canónica (`canonical_rows`) no depende del motor de base: los tests
corren sobre SQLite y producción es PostgreSQL. Por eso cada valor se guarda
como texto o nulo, y los decimales sin ceros de más.

`rows` lleva el id de la fila y los seis campos de `measure.row_correctness`.
Nunca el texto del anuncio, el canal ni el emisor (`spec.md` §10.6).

El conjunto fija las etiquetas. No fija el catálogo del foco, la provincia
fija de cada canal ni el motor de OCR: un cambio ahí mueve H0 igual.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from saimc.config import Settings, get_settings
from saimc.db import session_scope
from saimc.extraction.measure import assisted_ingestion_ids
from saimc.models import (
    ContentType,
    PriceHistory,
    ProcessingStatus,
    RawIngestion,
    ReferenceSetMember,
)
from saimc.models.base import utc_now
from saimc.units import canonical_unit

COMMAND = "python -m saimc.extraction.reference_set"

#: Largo mínimo de un prefijo para `--exclude`: los 8 caracteres con los que
#: los comandos y `docs/decisions.md` nombran un anuncio.
MIN_PREFIX = 8

_TYPE_LABELS: tuple[tuple[str, str], ...] = (
    (ContentType.TEXT.value, "texto"),
    (ContentType.IMAGE.value, "imagen"),
)


# --- forma canónica y hashes ----------------------------------------------------


class ConfirmedRow(Protocol):
    """Lo que hace falta de una fila confirmada. `PriceHistory` lo cumple.

    Un `Protocol` es tipado estructural: alcanza con tener estos atributos,
    sin heredar de nada. Las propiedades de solo lectura dejan pasar tanto una
    fila del ORM como un objeto suelto de un test.
    """

    @property
    def id(self) -> uuid.UUID: ...
    @property
    def product_id(self) -> uuid.UUID: ...
    @property
    def province_id(self) -> uuid.UUID | None: ...
    @property
    def price(self) -> Decimal: ...
    @property
    def currency(self) -> str: ...
    @property
    def quantity(self) -> Decimal | None: ...
    @property
    def unit(self) -> str | None: ...


def _decimal_text(value: Decimal | None) -> str | None:
    """El decimal como texto, sin ceros de más ni notación científica.

    `340.00` y `340` dan `"340"`. `normalize()` solo no alcanza: escribe 340
    como `3.4E+2`. El formato `"f"` lo vuelve a notación de punto fijo.
    """
    if value is None:
        return None
    if value == 0:
        # `Decimal("-0.00").normalize()` conserva el signo.
        return "0"
    return format(value.normalize(), "f")


def _id_text(value: uuid.UUID | None) -> str | None:
    return None if value is None else str(value)


def canonical_rows(prices: Iterable[ConfirmedRow]) -> list[dict[str, str | None]]:
    """Las filas confirmadas en forma canónica, ordenadas por id de fila.

    Es lo que se guarda en `reference_set_members.rows`. Todo valor es texto o
    nulo, así vuelve idéntico del JSON de SQLite y del de PostgreSQL.
    """
    rows = [
        {
            "price_history_id": str(price.id),
            "product_id": str(price.product_id),
            "province_id": _id_text(price.province_id),
            "price": _decimal_text(price.price),
            "currency": price.currency,
            "quantity": _decimal_text(price.quantity),
            "unit": canonical_unit(price.unit),
        }
        for price in prices
    ]
    return sorted(rows, key=lambda row: str(row["price_history_id"]))


def values_hash(rows: Sequence[Mapping[str, Any]]) -> str:
    """SHA-256 de las filas canónicas. No depende del orden en que lleguen."""
    ordered = sorted(rows, key=lambda row: str(row["price_history_id"]))
    # Claves ordenadas y sin espacios: una sola escritura posible por valor.
    text = json.dumps(ordered, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def set_hash(pairs: Iterable[tuple[str, str]]) -> str:
    """La huella del conjunto: SHA-256 sobre las líneas `ingestion_id:values_hash`
    ordenadas, separadas por un salto de línea."""
    lines = sorted(f"{ingestion_id}:{row_hash}" for ingestion_id, row_hash in pairs)
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


# --- quién entra ------------------------------------------------------------------


@dataclass(frozen=True)
class Candidate:
    """Un anuncio que entra, con sus filas ya en forma canónica."""

    ingestion: RawIngestion
    rows: list[dict[str, str | None]]

    @property
    def values_hash(self) -> str:
        return values_hash(self.rows)


@dataclass(frozen=True)
class Selection:
    """Quién entra y cuántos curados quedan afuera por cada motivo.

    Cada curado cuenta en un solo motivo, en el orden de la regla: sin filas,
    asistido, excluido a mano. Así `excluded` son los que habrían entrado de
    no pasarlos con `--exclude`.
    """

    members: list[Candidate]
    curated: int
    without_rows: int
    assisted: int
    excluded: int
    #: Ids pasados con `--exclude` que ya quedaban afuera por otro motivo, o
    #: que no están curados.
    redundant_excluded: list[uuid.UUID]

    @property
    def set_hash(self) -> str:
        return set_hash((str(m.ingestion.id), m.values_hash) for m in self.members)


def _live_rows(
    session: Session, ingestion_ids: Collection[uuid.UUID] | None = None
) -> dict[uuid.UUID, list[PriceHistory]]:
    """Las filas vivas de `prices_history` agrupadas por anuncio."""
    stmt = select(PriceHistory)
    if ingestion_ids is not None:
        stmt = stmt.where(PriceHistory.source_id.in_(ingestion_ids))
    grouped: dict[uuid.UUID, list[PriceHistory]] = {}
    for row in session.execute(stmt).scalars():
        grouped.setdefault(row.source_id, []).append(row)
    return grouped


def select_members(
    session: Session,
    *,
    assist_log_path: Path | None,
    excluded: Collection[uuid.UUID] = frozenset(),
) -> Selection:
    """Aplica las cuatro condiciones de la especificación. No escribe."""
    curated = list(
        session.execute(
            select(RawIngestion)
            .where(RawIngestion.processing_status == ProcessingStatus.CURATED)
            .order_by(RawIngestion.id)
        ).scalars()
    )
    live = _live_rows(session)
    assisted_ids = assisted_ingestion_ids(session, assist_log_path)

    members: list[Candidate] = []
    without_rows = assisted = excluded_count = 0
    counted_excluded: set[uuid.UUID] = set()
    for ingestion in curated:
        rows = live.get(ingestion.id, [])
        if not rows:
            without_rows += 1
        elif str(ingestion.id) in assisted_ids:
            assisted += 1
        elif ingestion.id in excluded:
            excluded_count += 1
            counted_excluded.add(ingestion.id)
        else:
            members.append(Candidate(ingestion=ingestion, rows=canonical_rows(rows)))
    return Selection(
        members=members,
        curated=len(curated),
        without_rows=without_rows,
        assisted=assisted,
        excluded=excluded_count,
        redundant_excluded=sorted(set(excluded) - counted_excluded),
    )


class ExcludeError(ValueError):
    """Un `--exclude` que no nombra exactamente un anuncio."""


def resolve_excluded(session: Session, tokens: Sequence[str]) -> frozenset[uuid.UUID]:
    """Traduce cada `--exclude` a un id de `raw_ingestions`.

    Acepta el id completo o un prefijo de 8 caracteres o más, si es de un
    solo anuncio. El prefijo se compara en Python y no en SQL: un UUID se
    guarda distinto en SQLite y en PostgreSQL, y son unos miles de ids.
    """
    if not tokens:
        return frozenset()
    known = {
        ingestion_id.hex: ingestion_id
        for ingestion_id in session.execute(select(RawIngestion.id)).scalars()
    }
    resolved: set[uuid.UUID] = set()
    for token in tokens:
        prefix = token.strip().lower().replace("-", "")
        if len(prefix) < MIN_PREFIX:
            raise ExcludeError(
                f"«{token}»: hacen falta al menos {MIN_PREFIX} caracteres del id."
            )
        matches = [value for key, value in known.items() if key.startswith(prefix)]
        if not matches:
            raise ExcludeError(f"«{token}»: no es el id de ningún anuncio.")
        if len(matches) > 1:
            raise ExcludeError(
                f"«{token}»: el prefijo es de {len(matches)} anuncios. Pasá el id completo."
            )
        resolved.add(matches[0])
    return frozenset(resolved)


# --- congelar ---------------------------------------------------------------------


def current_version(session: Session) -> int | None:
    """La versión vigente (la mayor), o `None` si nunca se congeló."""
    return session.execute(select(func.max(ReferenceSetMember.set_version))).scalar_one()


def members_of(session: Session, version: int) -> list[ReferenceSetMember]:
    return list(
        session.execute(
            select(ReferenceSetMember)
            .where(ReferenceSetMember.set_version == version)
            .order_by(ReferenceSetMember.ingestion_id)
        ).scalars()
    )


def version_hash(session: Session, version: int) -> str:
    """La huella de una versión ya congelada."""
    return set_hash((str(m.ingestion_id), m.values_hash) for m in members_of(session, version))


def freeze(session: Session, selection: Selection, *, now: datetime | None = None) -> int:
    """Inserta la versión siguiente con los miembros de `selection`.

    No hace commit: los miembros van en la transacción de quien llama, así la
    versión entra entera o no entra. Devuelve el número de versión.
    """
    if not selection.members:
        raise ValueError("No se congela un conjunto vacío.")
    version = (current_version(session) or 0) + 1
    frozen_at = now or utc_now()
    session.add_all(
        ReferenceSetMember(
            set_version=version,
            ingestion_id=member.ingestion.id,
            rows=member.rows,
            values_hash=member.values_hash,
            frozen_at=frozen_at,
        )
        for member in selection.members
    )
    session.flush()
    return version


# --- deriva -----------------------------------------------------------------------


@dataclass(frozen=True)
class Drift:
    """Un miembro cuyas filas vivas ya no son las congeladas."""

    member: ReferenceSetMember
    #: Cuántas filas vivas le quedan. 0: se borró el anuncio o todas sus filas.
    live_rows: int


def drift_report(session: Session, version: int) -> list[Drift]:
    members = members_of(session, version)
    live = _live_rows(session, [member.ingestion_id for member in members])
    report: list[Drift] = []
    for member in members:
        rows = live.get(member.ingestion_id, [])
        if values_hash(canonical_rows(rows)) != member.values_hash:
            report.append(Drift(member=member, live_rows=len(rows)))
    return report


def drifted(session: Session, version: int) -> list[ReferenceSetMember]:
    """Los miembros con deriva de una versión."""
    return [drift.member for drift in drift_report(session, version)]


# --- el comando -------------------------------------------------------------------


def _count_lines(counts: Mapping[str, tuple[int, int]]) -> list[str]:
    """«N anuncios, M filas» en total y por tipo. `counts` va por `content_type`."""
    ads = sum(n for n, _ in counts.values())
    rows = sum(n for _, n in counts.values())
    lines = [f"{ads} anuncios, {rows} filas"]
    for content_type, label in _TYPE_LABELS:
        type_ads, type_rows = counts.get(content_type, (0, 0))
        lines.append(f"  {label}: {type_ads} anuncios, {type_rows} filas")
    return lines


def _counts(pairs: Iterable[tuple[str, int]]) -> dict[str, tuple[int, int]]:
    """Suma anuncios y filas por tipo a partir de pares `(content_type, filas)`."""
    counts: dict[str, tuple[int, int]] = {}
    for content_type, row_count in pairs:
        ads, rows = counts.get(content_type, (0, 0))
        counts[content_type] = (ads + 1, rows + row_count)
    return counts


def _status(session: Session, out: Callable[[str], None]) -> int:
    version = current_version(session)
    if version is None:
        out(f"No hay ningún conjunto de referencia congelado. Se congela con: {COMMAND} --execute")
        return 1
    members = members_of(session, version)
    content_types: dict[uuid.UUID, str] = {
        row[0]: row[1]
        for row in session.execute(
            select(RawIngestion.id, RawIngestion.content_type).where(
                RawIngestion.id.in_([member.ingestion_id for member in members])
            )
        )
    }
    total, *by_type = _count_lines(
        _counts((content_types[m.ingestion_id], len(m.rows)) for m in members)
    )
    frozen_at = min(member.frozen_at for member in members)
    out(f"Conjunto de referencia, versión {version} (congelada el {frozen_at:%Y-%m-%d}): {total}")
    for line in by_type:
        out(line)
    out(f"Hash del conjunto: {version_hash(session, version)}")
    report = drift_report(session, version)
    out(f"Miembros con deriva: {len(report)}")
    for drift in report:
        detail = (
            "sin filas vivas" if drift.live_rows == 0 else "las filas vivas ya no son las congeladas"
        )
        out(f"  {drift.member.ingestion_id}  {detail}")
    if report:
        out(
            "La deriva no cambia la referencia: H0 sigue midiendo contra las filas "
            "congeladas. Solo cambia con una versión nueva."
        )
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog=COMMAND,
        description=(
            "Congela el conjunto de referencia de H0: los anuncios curados sin "
            "asistencia, con sus filas. Por defecto, en seco."
        ),
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=["status"],
        help="`status`: versión vigente, su n, su hash y los miembros con deriva.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Escribe la versión siguiente. Sin esto no se escribe nada.",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="ID",
        help=(
            "Deja afuera un anuncio. Repetible. Acepta el id completo o su "
            f"prefijo de {MIN_PREFIX} caracteres, si es de un solo anuncio."
        ),
    )
    return parser.parse_args(argv)


def run(
    args: argparse.Namespace,
    session: Session,
    settings: Settings,
    *,
    out: Callable[[str], None] = print,
) -> int:
    """Corre el modo pedido. Devuelve el código de salida. No hace commit."""
    if args.command == "status":
        return _status(session, out)

    try:
        excluded = resolve_excluded(session, args.exclude)
    except ExcludeError as error:
        out(f"--exclude {error} No se escribió nada.")
        return 2

    selection = select_members(
        session, assist_log_path=settings.llm_assist_log_path, excluded=excluded
    )
    next_version = (current_version(session) or 0) + 1
    if not args.execute:
        out(f"EN SECO: no se escribe nada (con --execute se congela la versión {next_version}).")
    elif not selection.members:
        out("Ningún anuncio entra al conjunto: no se congela una versión vacía.")
        return 1
    else:
        version = freeze(session, selection)
        out(f"Congelada la versión {version} del conjunto de referencia.")

    label = "Entraron" if args.execute else "Entrarían"
    total, *by_type = _count_lines(
        _counts((m.ingestion.content_type, len(m.rows)) for m in selection.members)
    )
    out(f"{label}: {total}")
    for line in by_type:
        out(line)
    left_out = selection.curated - len(selection.members)
    out(f"Quedan afuera: {left_out} de {selection.curated} curados.")
    out(f"  asistidos: {selection.assisted}")
    out(f"  sin filas: {selection.without_rows}")
    out(f"  excluidos a mano: {selection.excluded}")
    for ingestion_id in selection.redundant_excluded:
        out(
            f"  --exclude {ingestion_id}: ya quedaba afuera (no está curado, no tiene "
            "filas o es asistido); no cuenta como excluido a mano."
        )
    out(f"Hash del conjunto: {selection.set_hash}")
    if args.execute:
        out(
            "Anotá en docs/decisions.md la versión, el n por tipo, el hash y los "
            "excluidos con su motivo."
        )
    return 0


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    settings = get_settings()
    # `session_scope` hace commit al salir: la versión entra entera o no entra.
    with session_scope() as session:
        code = run(args, session, settings)
    sys.exit(code)


if __name__ == "__main__":
    main()
