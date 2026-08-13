"""CLI de backfill idempotente de tasas de cambio pendientes.

    python -m saimc.exchange_rate.backfill [--apply] [--limit N]

Corre a mano, fuera del flujo de curaduría, contra filas que ya se guardaron
sin convertir (por ejemplo porque elTOQUE estaba caída al momento de curar,
o no tenía dato para esa fecha todavía). Por defecto es dry-run: no escribe
nada — ni en `prices_history` ni en `exchange_rate_cache` — porque `.env`
apunta a producción y no existe base de desarrollo, así que la invocación
olvidadiza (sin flags) tiene que ser la inofensiva (design D8). Escribir
exige el flag explícito `--apply`.

Reusa la MISMA cadena de resolución (`RateResolver`) y la MISMA regla de
ancla (`anchor_date`) que el hook de curaduría (`web/routers/curation.py`):
la política de conversión vive en un solo lugar, nunca se bifurca acá.

Idempotente por construcción, vía el filtro de la consulta
(`currency <> 'CUP' AND exchange_rate IS NULL`): una fila que ya resolvió
con éxito deja de aparecer en la consulta de la próxima corrida, así que
nunca se reintenta ni se sobreescribe. Una fila que resolvió a "sin tasa"
sigue con `exchange_rate IS NULL`, así que SÍ vuelve a aparecer — se
reintenta a propósito, porque la tasa pudo aparecer después.
"""

import argparse
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from saimc.config import get_settings
from saimc.db import session_scope
from saimc.exchange_rate.eltoque import fetch_rate
from saimc.exchange_rate.service import Fetcher, RateResolver, anchor_date, apply_resolution
from saimc.models import Currency, PriceHistory, RawIngestion

SleepFn = Callable[[float], None]

# Límite del 1 req/s de la API TRMI de elTOQUE (design D4) — sólo se aplica
# acá, en el backfill: la resolución al curar colapsa por memoización a lo
# sumo una llamada por moneda distinta en el request, así que nunca pega el
# límite; el backfill sí puede recorrer muchas filas de golpe.
_PAUSA_ENTRE_FETCHES = 1.0


@dataclass
class BackfillReport:
    """Resumen de una corrida, para el operador — no se persiste."""

    examined: int = 0
    resolved: int = 0
    unresolved: int = 0


def _pending_rows(
    db: Session, limit: int | None
) -> list[tuple[PriceHistory, RawIngestion]]:
    """Filas objetivo: moneda extranjera, todavía sin tasa. Trae también el
    `RawIngestion` de cada una en la misma consulta (join, no N+1) — el
    ancla necesita `captured_at` como fallback de `published_date`."""
    consulta = (
        select(PriceHistory, RawIngestion)
        .join(RawIngestion, PriceHistory.source_id == RawIngestion.id)
        .where(
            PriceHistory.currency != Currency.CUP.value,
            PriceHistory.exchange_rate.is_(None),
        )
        .order_by(PriceHistory.created_at)
    )
    if limit is not None:
        consulta = consulta.limit(limit)
    return [(fila.PriceHistory, fila.RawIngestion) for fila in db.execute(consulta)]


def _con_pausa(fetcher: Fetcher, sleep: SleepFn) -> Fetcher:
    """Envuelve `fetcher` para pausar DESPUÉS de cada llamada real a la red.

    El `RateResolver` sólo invoca el fetcher en el paso 2 de su cadena (cache
    miss): un acierto de cache o una tasa vieja de respaldo (paso 1 o 3)
    nunca pasan por acá, así que la pausa separa fetches reales entre sí,
    nunca demora un acierto de cache."""

    def envuelto(key: str, day: date) -> Decimal | None:
        resultado = fetcher(key, day)
        sleep(_PAUSA_ENTRE_FETCHES)
        return resultado

    return envuelto


def run_backfill(
    db: Session,
    fetcher: Fetcher,
    *,
    apply: bool,
    limit: int | None = None,
    sleep: SleepFn = time.sleep,
) -> BackfillReport:
    """Corre una pasada de backfill sobre `db`.

    No decide cómo se abre/cierra la sesión — eso es responsabilidad de
    `main()` (`session_scope`) o, en los tests, de la fixture `session`. En
    dry-run (`apply=False`) nunca se muta `PriceHistory` ni se escribe en la
    cache (`write_through=False`); en `--apply` cada fila resuelta se
    commitea individualmente, así que una corrida interrumpida a mitad de
    camino no pierde lo ya convertido.
    """
    report = BackfillReport()
    resolver = RateResolver(db, _con_pausa(fetcher, sleep), write_through=apply)

    for entry, ingestion in _pending_rows(db, limit):
        report.examined += 1
        anchor = anchor_date(entry.published_date, ingestion.captured_at)
        resolution = resolver.resolve(entry.currency, anchor)

        if resolution.rate is None:
            report.unresolved += 1
            print(f"  sin tasa: fila {entry.id} ({entry.currency}, ancla {anchor})")
            continue

        report.resolved += 1
        if apply:
            apply_resolution(entry, resolution)
            db.commit()
        else:
            print(
                f"  resolvería: fila {entry.id} ({entry.currency}, ancla {anchor}) "
                f"-> tasa {resolution.rate} ({resolution.source})"
            )

    return report


def main() -> None:
    settings = get_settings()

    parser = argparse.ArgumentParser(
        description="Backfill idempotente de tasas de cambio pendientes"
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
    args = parser.parse_args()

    def fetcher(key: str, day: date) -> Decimal | None:
        return fetch_rate(
            key, day, api_key=settings.el_toque_api, timeout=settings.el_toque_timeout
        )

    with session_scope() as db:
        report = run_backfill(db, fetcher, apply=args.apply, limit=args.limit)

    print(f"Examinadas:    {report.examined}")
    print(f"Resueltas:     {report.resolved}")
    print(f"Sin convertir: {report.unresolved}")


if __name__ == "__main__":
    main()
