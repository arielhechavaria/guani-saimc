"""Persistir la validación en `prices_history` (slice B1).

El motor es puro y `context` solo lee; este módulo junta los dos y **escribe en
las filas** el estado, el puntaje y el detalle. Lo llaman los dos endpoints que
guardan filas (`submit_curation` y `submit_edit`) y la revalidación
(`python -m saimc.validation.revalidate`). No hace `commit`: la transacción es
de quien llama.

Lo que queda en cada fila es una **foto**: describe la fila contra el mercado
conocido en ese momento, con la fecha, la versión del motor y los parámetros
con que se validó. Las altas, ediciones y borrados de OTROS anuncios no
revalidan filas ajenas; para refrescar está `revalidate`.

Nada de esto automatiza: ningún carril usa lo que se guarda acá.

Desde A6b, si Jev está activo y el anuncio tiene señal guardada, el detalle
suma una clave `jev` con lo que contestó (`validation.jev_signal`). Es una
anotación al costado: se calcula aparte y no entra a ninguna regla, así que el
estado, el puntaje y `provenance` salen iguales con y sin ella. Acá no se llama
a nadie: solo se lee la señal que dejó el pre-cálculo.

Contrato: `docs/spec-slice-B1.md`; la clave `jev`, `docs/spec-slice-A6b.md`.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from saimc.config import Settings, get_settings
from saimc.models import PriceHistory, Provenance, RawIngestion
from saimc.models.base import utc_now
from saimc.validation import jev_signal
from saimc.validation.context import Catalog, build_stored_rows_context, load_catalog
from saimc.validation.engine import ENGINE_VERSION, validate_stored_row
from saimc.validation.types import RowValidation

logger = logging.getLogger(__name__)

#: Referencias mínimas en la celda para que las reglas 2 y 6 opinen sobre una
#: fila guardada. Se fijó para PERSISTIR, no para automatizar: es una decisión
#: del operador para poder describir las filas, sin medición que la elija entre
#: 3, 5 y 10 y sin efecto sobre ningún carril (`docs/decisions.md`, 2026-10-03,
#: «B1»). Cada fila guarda el valor con el que se validó, así que cambiarlo no
#: deja filas sin explicación: se corre `revalidate`.
#:
#: Los dos parámetros de la regla 4 siguen SIN fijar (los decide G1b): la regla
#: da `unknown` en toda fila y ninguna sale `approved`.
MIN_CELL_N = 5


def validate_rows(
    session: Session,
    ingestion: RawIngestion,
    rows: Sequence[PriceHistory],
    *,
    catalog: Catalog | None = None,
) -> list[RowValidation]:
    """Valida las filas guardadas de UN anuncio, sin escribir nada.

    `rows` son todas del anuncio `ingestion`. Devuelve un resultado por fila,
    en el mismo orden. Lanza lo que lance el motor o la lectura del contexto:
    atajarlo es de quien llama.

    Una fila cuenta como verificada si la hizo una persona
    (`provenance = human`). Qué lleva una fila `auto` lo decide B2b.
    """
    catalog = catalog or load_catalog(session)
    ctx = build_stored_rows_context(session, ingestion, rows, catalog=catalog)
    pilot_ids = frozenset(p.id for p in catalog.pilot_products)
    province_ids = frozenset(p.id for p in catalog.provinces)
    return [
        validate_stored_row(
            stored.row,
            stored.context,
            published_at=ctx.published_at,
            channel=ctx.channel,
            pilot_ids=pilot_ids,
            province_ids=province_ids,
            recent_channel_rows=ctx.recent_channel_rows,
            verified=row.provenance == Provenance.HUMAN,
            min_cell_n=MIN_CELL_N,
        )
        for row, stored in zip(rows, ctx.rows, strict=True)
    ]


def jev_blocks(
    session: Session,
    ingestion: RawIngestion,
    rows: Sequence[PriceHistory],
    *,
    catalog: Catalog | None = None,
    settings: Settings | None = None,
) -> list[dict[str, Any] | None]:
    """El bloque `jev` de cada fila, o `None` donde no corresponde. Nunca lanza.

    `None` en todas si Jev está inactivo o el anuncio no tiene señal de la
    huella vigente. Si buscar o leer la señal falla, se ataja acá: la fila
    conserva su validación y queda sin `jev`. No llama a la red: solo lee
    `jev_signals`.
    """
    nothing: list[dict[str, Any] | None] = [None] * len(rows)
    if not rows or not jev_signal.is_active(settings or get_settings()):
        return nothing
    try:
        # Savepoint, por lo mismo que en `validate_and_store`: en PostgreSQL
        # una consulta que falla aborta toda la transacción del guardado.
        with session.begin_nested():
            pilots = (catalog or load_catalog(session)).pilot_products
            options = jev_signal.options_from_products(pilots)
            signal = jev_signal.current_signal(session, ingestion.id, options.questions_hash)
            if signal is None:
                return nothing
            return list(jev_signal.annotate(jev_signal.read_signal(signal), rows))
    except Exception:
        # Amplio a propósito: Jev es una señal en sombra y ningún fallo suyo
        # puede costarle nada al guardado. Solo el id de la ingesta va al log.
        logger.exception("jev.anotacion_fallo ingestion_id=%s", ingestion.id)
        return nothing


def validation_detail(
    validation: RowValidation, validated_at: datetime, jev: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """El JSON de `prices_history.validation_detail`.

    No repite el estado ni el puntaje, que tienen columna propia. Los motivos
    nombran todas las reglas que no pasaron, no solo la que decidió el estado.

    `jev` es el bloque de la señal en sombra (A6b). Sin señal la clave **no
    existe**: no va en `null`.
    """
    detail: dict[str, Any] = {
        "rules": validation.detail()["rules"],
        "reasons": list(validation.reasons),
        "engine_version": ENGINE_VERSION,
        "config": {"min_cell_n": MIN_CELL_N},
        "validated_at": validated_at.isoformat(),
    }
    if jev is not None:
        detail["jev"] = dict(jev)
    return detail


def store(
    row: PriceHistory,
    validation: RowValidation,
    validated_at: datetime,
    jev: Mapping[str, Any] | None = None,
) -> None:
    """Escribe el resultado en la fila. No toca `provenance` ni otra columna."""
    row.validation_status = validation.status.value
    row.confidence_score = validation.confidence_score
    row.validation_detail = validation_detail(validation, validated_at, jev)


def _clear(row: PriceHistory) -> None:
    row.validation_status = None
    row.confidence_score = None
    row.validation_detail = None


def validate_and_store(
    session: Session,
    ingestion: RawIngestion,
    rows: Sequence[PriceHistory],
    *,
    catalog: Catalog | None = None,
    validated_at: datetime | None = None,
) -> bool:
    """Valida las filas de un anuncio y les escribe el resultado. Nunca lanza.

    Es lo que llama el guardado. Si la validación falla, las filas quedan con
    estado, puntaje y detalle NULOS —una validación vieja describiría valores
    que ya no están— y el error va al log: el operador ya decidió, y un fallo
    de validación no puede costarle la curaduría. Devuelve si pudo validar.

    Quien llama tiene que haber hecho `flush` antes: así un error de escritura
    de las filas sale por su camino normal y no se confunde con uno de acá.
    """
    try:
        # Savepoint: en PostgreSQL una consulta que falla deja abortada toda la
        # transacción, y el `commit` del guardado fallaría igual aunque acá se
        # ataje la excepción. Con el savepoint solo se deshace este tramo, que
        # no escribe nada (`validate_rows` solo lee).
        with session.begin_nested():
            # El catálogo se lee una vez y sirve para validar y para Jev.
            catalog = catalog or load_catalog(session)
            validations = validate_rows(session, ingestion, rows, catalog=catalog)
    except Exception:
        # Amplio a propósito: cualquier fallo del motor o del armado del
        # contexto se ataja igual. Solo el id de la ingesta va al log.
        logger.exception("validation.persist_fallo ingestion_id=%s", ingestion.id)
        for row in rows:
            _clear(row)
        return False
    moment = validated_at or utc_now()
    blocks = jev_blocks(session, ingestion, rows, catalog=catalog)
    for row, validation, jev in zip(rows, validations, blocks, strict=True):
        store(row, validation, moment, jev)
    return True


# --- /control ------------------------------------------------------------------


@dataclass(frozen=True)
class StatusCounts:
    """Cuántas filas hay en cada estado. Es lo único de la validación que va a
    `/control`: solo conteos, sin canal, emisor ni detalle (criterio de cuando
    `/control` era público; hoy pide operador)."""

    #: Estado -> filas. Solo trae los estados que tienen alguna.
    by_status: Mapping[str, int]
    #: Filas con estado nulo: todavía sin validar.
    unvalidated: int

    @property
    def total(self) -> int:
        return sum(self.by_status.values()) + self.unvalidated


def status_counts(session: Session) -> StatusCounts:
    """El conteo de `prices_history` por `validation_status`."""
    stmt = select(PriceHistory.validation_status, func.count()).group_by(
        PriceHistory.validation_status
    )
    by_status: dict[str, int] = {}
    unvalidated = 0
    for status, count in session.execute(stmt):
        if status is None:
            unvalidated = count
        else:
            by_status[status] = count
    return StatusCounts(by_status=by_status, unvalidated=unvalidated)
