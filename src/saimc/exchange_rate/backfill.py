"""CLI de backfill idempotente de tasas de cambio pendientes o de relleno.

    python -m saimc.exchange_rate.backfill [--apply] [--limit N] [--backup PATH]

Corre a mano, fuera del flujo de curaduría, contra dos clases de filas en
divisa:

- **Pendientes**: se guardaron sin convertir (por ejemplo porque elTOQUE
  estaba caída al momento de curar, o no tenía dato para esa fecha todavía).
- **De relleno**: se convirtieron con la última tasa guardada, de otro día,
  y quedaron marcadas `elTOQUE (cache)` (sin `SAIMC_EL_TOQUE_API` en el
  `.env`, el resolutor cae a `cache.latest()`). La decisión del 2026-10-16
  exige una tasa real de elTOQUE para el día ancla de cada fila.

Por defecto es dry-run: no escribe nada — ni en `prices_history` ni en
`exchange_rate_cache` — porque `.env` apunta a producción y no existe base
de desarrollo, así que la invocación olvidadiza (sin flags) tiene que ser
la inofensiva (design D8). Escribir exige el flag explícito `--apply`, y
antes de escribir se respaldan en un CSV los valores originales de cada
fila objetivo.

Reusa la MISMA cadena de resolución (`RateResolver`) y la MISMA regla de
ancla (`anchor_date`) que el hook de curaduría (`web/routers/curation.py`),
cortada en el paso 2 (`stale_fallback=False`): sin tasa del día la fila no
se toca y se reporta; nunca se rellena con otra fecha.

Idempotente por construcción, vía el filtro de la consulta: una fila que
resolvió con éxito queda con tasa y fuente `elTOQUE`, así que deja de
aparecer en la próxima corrida y nunca se sobreescribe. Una fila que no
resolvió conserva lo que tenía (nula o de relleno), así que SÍ vuelve a
aparecer — se reintenta a propósito, porque la tasa pudo aparecer después.
"""

import argparse
import csv
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from saimc.config import get_settings
from saimc.db import session_scope
from saimc.exchange_rate.eltoque import ElToqueUnavailable, fetch_rate_strict
from saimc.exchange_rate.service import (
    _ELTOQUE_CACHE_SOURCE,
    Fetcher,
    RateResolver,
    anchor_date,
    apply_resolution,
)
from saimc.models import Currency, PriceHistory, RawIngestion

SleepFn = Callable[[float], None]

# Límite del 1 req/s de la API TRMI de elTOQUE (design D4) — sólo se aplica
# acá, en el backfill: la resolución al curar colapsa por memoización a lo
# sumo una llamada por moneda distinta en el request, así que nunca pega el
# límite; el backfill sí puede recorrer muchas filas de golpe.
_PAUSA_ENTRE_FETCHES = 1.0

# Medido el 2026-10-18: tras unas 30 consultas espaciadas a 1 s, elTOQUE
# responde 429 unos 5 minutos, aunque sus encabezados digan que quedan
# peticiones; el 1 req/s de la documentación no es el único límite. Se
# reintenta 8 minutos y, si no vuelve, la corrida se corta (retoma al repetirla).
_REINTENTOS = 8
_ESPERA_REINTENTO = 60.0

# Columnas del respaldo: lo que `apply_resolution` reescribe, más lo que
# hace falta para identificar y recalcular la fila.
_BACKUP_COLUMNS = (
    "id",
    "currency",
    "price",
    "price_cup",
    "exchange_rate",
    "exchange_rate_date",
    "exchange_rate_source",
)


@dataclass
class BackfillReport:
    """Resumen de una corrida, para el operador — no se persiste."""

    examined: int = 0
    resolved: int = 0
    unresolved: int = 0
    # `(id, moneda, ancla)` de cada fila sin tasa del día: quedó como estaba.
    unresolved_rows: list[tuple[uuid.UUID, str, date]] = field(default_factory=list)
    # Por qué se cortó la corrida, si elTOQUE dejó de responder; si no, `None`.
    aborted: str | None = None


def _target_rows(
    db: Session, limit: int | None
) -> list[tuple[PriceHistory, RawIngestion]]:
    """Filas objetivo: moneda extranjera, sin tasa o con tasa de relleno.
    Trae también el `RawIngestion` de cada una en la misma consulta (join,
    no N+1) — el ancla necesita `captured_at` como fallback de
    `published_date`."""
    consulta = (
        select(PriceHistory, RawIngestion)
        .join(RawIngestion, PriceHistory.source_id == RawIngestion.id)
        .where(
            PriceHistory.currency != Currency.CUP.value,
            or_(
                PriceHistory.exchange_rate.is_(None),
                PriceHistory.exchange_rate_source == _ELTOQUE_CACHE_SOURCE,
            ),
        )
        .order_by(PriceHistory.created_at)
    )
    if limit is not None:
        consulta = consulta.limit(limit)
    return [(fila.PriceHistory, fila.RawIngestion) for fila in db.execute(consulta)]


def _write_backup(path: Path, entries: list[PriceHistory]) -> None:
    """Respalda los valores originales de `entries` antes de tocarlas. Si no
    se puede escribir, la excepción corta la corrida antes de cualquier
    cambio."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="") as archivo:
        writer = csv.writer(archivo)
        writer.writerow(_BACKUP_COLUMNS)
        for entry in entries:
            writer.writerow(
                "" if (valor := getattr(entry, columna)) is None else str(valor)
                for columna in _BACKUP_COLUMNS
            )


@dataclass
class _Outage:
    """Lo deja `_con_pausa` cuando la API no volvió tras los reintentos."""

    reason: str | None = None


def _con_pausa(
    fetcher: Fetcher, sleep: SleepFn, *, retries: int, retry_wait: float, outage: _Outage
) -> Fetcher:
    """Envuelve `fetcher` para pausar DESPUÉS de cada llamada real a la red, y
    reintentar `retries` veces, esperando `retry_wait`, si la API no respondió.

    El `RateResolver` sólo invoca el fetcher en el paso 2 de su cadena (cache
    miss): un acierto de cache nunca pasa por acá, así que la pausa separa
    fetches reales entre sí, nunca demora un acierto de cache. Si la API sigue
    sin responder, devuelve `None` y lo anota en `outage`: el resolutor no ve
    la diferencia, el bucle de `run_backfill` sí."""

    def envuelto(key: str, day: date) -> Decimal | None:
        for intento in range(retries + 1):
            try:
                resultado = fetcher(key, day)
            except ElToqueUnavailable as exc:
                if intento == retries:
                    outage.reason = f"{exc} al pedir {key} del {day}"
                    return None
                print(f"  elTOQUE no respondió ({exc}); reintento en {retry_wait:.0f} s")
                sleep(retry_wait)
                continue
            sleep(_PAUSA_ENTRE_FETCHES)
            return resultado
        return None  # inalcanzable: el bucle siempre devuelve

    return envuelto


def run_backfill(
    db: Session,
    fetcher: Fetcher,
    *,
    apply: bool,
    limit: int | None = None,
    backup_path: Path | None = None,
    sleep: SleepFn = time.sleep,
    retries: int = _REINTENTOS,
    retry_wait: float = _ESPERA_REINTENTO,
) -> BackfillReport:
    """Corre una pasada de backfill sobre `db`.

    No decide cómo se abre/cierra la sesión — eso es responsabilidad de
    `main()` (`session_scope`) o, en los tests, de la fixture `session`. En
    dry-run (`apply=False`) nunca se muta `PriceHistory`, ni se escribe en la
    cache (`write_through=False`), ni se escribe el respaldo; en `--apply`
    primero se respaldan las filas objetivo en `backup_path` (si se pasó) y
    después cada fila resuelta se commitea individualmente, así que una
    corrida interrumpida a mitad de camino no pierde lo ya convertido.

    Si elTOQUE no responde tras los reintentos, la corrida se corta en esa
    fila (sin tocarla) y `report.aborted` dice por qué: volver a correr
    retoma donde quedó.
    """
    report = BackfillReport()
    outage = _Outage()
    resolver = RateResolver(
        db,
        _con_pausa(fetcher, sleep, retries=retries, retry_wait=retry_wait, outage=outage),
        write_through=apply,
        stale_fallback=False,
    )
    rows = _target_rows(db, limit)

    if apply and backup_path is not None:
        _write_backup(backup_path, [entry for entry, _ in rows])

    for entry, ingestion in rows:
        report.examined += 1
        anchor = anchor_date(entry.published_date, ingestion.captured_at)
        resolution = resolver.resolve(entry.currency, anchor)
        before = f"{entry.exchange_rate} ({entry.exchange_rate_source})"

        if outage.reason is not None:
            report.aborted = outage.reason
            print(f"  corrida cortada en fila {entry.id}: elTOQUE no respondió ({outage.reason})")
            break

        if resolution.rate is None:
            report.unresolved += 1
            report.unresolved_rows.append((entry.id, entry.currency, anchor))
            print(f"  sin tasa: fila {entry.id} ({entry.currency}, ancla {anchor}), queda {before}")
            continue

        report.resolved += 1
        if apply:
            apply_resolution(entry, resolution)
            db.commit()
        else:
            print(
                f"  resolvería: fila {entry.id} ({entry.currency}, ancla {anchor}) "
                f"{before} -> tasa {resolution.rate} ({resolution.source})"
            )

    return report


def main() -> None:
    settings = get_settings()

    parser = argparse.ArgumentParser(
        description="Backfill idempotente de tasas de cambio pendientes o de relleno"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Escribe los cambios. Sin este flag, sólo imprime lo que haría (dry-run, default).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="No-op explícito: ya es el comportamiento por defecto sin --apply.",
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="Máximo de filas a procesar en esta corrida."
    )
    parser.add_argument(
        "--backup",
        type=Path,
        default=None,
        help="CSV con los valores originales, escrito antes de --apply. "
        "Default: backups/exchange_rate_backfill_<UTC>.csv.",
    )
    args = parser.parse_args()

    backup_path = args.backup or Path("backups") / (
        f"exchange_rate_backfill_{datetime.now(UTC):%Y%m%dT%H%M%SZ}.csv"
    )

    api_key = settings.el_toque_api
    if not api_key:
        print("Falta SAIMC_EL_TOQUE_API en el .env: sin token no hay tasa que consultar.")
        raise SystemExit(1)

    def fetcher(key: str, day: date) -> Decimal | None:
        return fetch_rate_strict(key, day, api_key=api_key, timeout=settings.el_toque_timeout)

    with session_scope() as db:
        report = run_backfill(
            db,
            fetcher,
            apply=args.apply,
            limit=args.limit,
            backup_path=backup_path if args.apply else None,
        )

    print(f"Examinadas:    {report.examined}")
    print(f"Resueltas:     {report.resolved}")
    print(f"Sin convertir: {report.unresolved}")
    if args.apply:
        print(f"Respaldo:      {backup_path}")
    if report.aborted is not None:
        print(f"Cortada:       {report.aborted}. Volvé a correr más tarde: retoma donde quedó.")
        raise SystemExit(2)


if __name__ == "__main__":
    main()
