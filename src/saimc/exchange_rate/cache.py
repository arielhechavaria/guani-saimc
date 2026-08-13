"""Repositorio de `exchange_rate_cache` (design.md D2).

Write-through = leer y luego actualizar-o-insertar, nunca `ON CONFLICT`: la
suite de tests corre en SQLite y `ON CONFLICT` es sintaxis de PostgreSQL
(mismo motivo por el que `curation_history` no usa `string_agg`).

`store()` sólo hace `flush()`, nunca `commit()`: quien resuelva la tasa
dentro de un request de curaduría (Commit 2) comparte transacción con el
insert de `PriceHistory`, y el commit final es responsabilidad del router
("the single existing commit", design.md D4) — igual que `curation.py` usa
`db.flush()` para obtener un `product.id` antes de su propio commit.

`latest()` ordena por `rate_date DESC`, nunca por `fetched_at`: un backfill
que hoy escribe una tasa vieja no debe envenenar el "último conocido" con
una fecha vieja sólo porque se escribió más tarde (regresión explícita de
D2, cubierta en `tests/test_exchange_rate.py::TestCacheRepository`).
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from saimc.models import ExchangeRateCache


def get_for_date(db: Session, currency: str, rate_date: date) -> Decimal | None:
    """Tasa cacheada para el par exacto `(currency, rate_date)`, si existe."""
    fila = db.scalars(
        select(ExchangeRateCache).where(
            ExchangeRateCache.currency == currency,
            ExchangeRateCache.rate_date == rate_date,
        )
    ).first()
    return fila.rate if fila is not None else None


def latest(db: Session, currency: str) -> tuple[Decimal, date] | None:
    """Tasa cacheada más reciente para `currency`, por `rate_date`, no por
    `fetched_at`. Devuelve también la fecha propia de la fila: el llamador
    la necesita para marcar el paso 3 (fuera de ventana) con la fecha real
    del dato, no con el ancla que se pidió."""
    fila = db.scalars(
        select(ExchangeRateCache)
        .where(ExchangeRateCache.currency == currency)
        .order_by(ExchangeRateCache.rate_date.desc())
    ).first()
    if fila is None:
        return None
    return (fila.rate, fila.rate_date)


def store(
    db: Session, currency: str, rate_date: date, rate: Decimal, source: str
) -> None:
    """Escribe la tasa para `(currency, rate_date)`: actualiza si ya existe,
    inserta si no. Nunca `ON CONFLICT` — ver docstring del módulo."""
    fila = db.scalars(
        select(ExchangeRateCache).where(
            ExchangeRateCache.currency == currency,
            ExchangeRateCache.rate_date == rate_date,
        )
    ).first()

    if fila is not None:
        fila.rate = rate
        fila.source = source
    else:
        db.add(
            ExchangeRateCache(
                currency=currency, rate_date=rate_date, rate=rate, source=source
            )
        )

    db.flush()
