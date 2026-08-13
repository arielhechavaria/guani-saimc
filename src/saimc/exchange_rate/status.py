"""Visibilidad del estado de la tasa de cambio, para `/control`.

Responde dos preguntas que se confunden fácil:

1. **¿Qué tasa tiene el sistema disponible?** (`compute_rate_status`) — mira
   `exchange_rate_cache`, por moneda. Describe lo que va a pasar con lo que se
   cure de acá en adelante.
2. **¿Qué tasa quedó aplicada?** (`compute_applied_rates`) — mira las columnas
   de conversión de `prices_history`. Describe lo ya cargado, que no cambia
   porque el cache se actualice.

**Ninguna emite veredicto de vigencia.** `spec.md` §12.6 lista la «ventana de
validez de la tasa de cambio» como parámetro pendiente de calibración, sin
valor asignado y explícitamente a no inventar. Por eso se reporta
`age_days` —una medición— y nunca una marca de «vencida». Es el mismo criterio
con el que `service.py` degrada al paso 3 «explícitamente fuera de ventana»
sin definir cuál es la ventana.

Todo es de solo lectura y sale de la base: cargar `/control` **no** golpea la
API de elTOQUE. Un fetch en un GET agregaría latencia de red a una vista de
consulta y además tendría efecto de escritura (el write-through del paso 2).
Quien sale a la red es la curaduría, que es cuando la tasa se necesita de
verdad.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from saimc.exchange_rate import cache
from saimc.models import Currency, ExchangeRateCache, PriceHistory

# Las monedas que requieren conversión. CUP queda fuera: no se convierte a sí
# misma, y `RateResolver` nunca la resuelve.
_FOREIGN_CURRENCIES: tuple[str, ...] = tuple(
    c.value for c in Currency if c is not Currency.CUP
)


@dataclass(frozen=True)
class RateStatus:
    """Lo que el cache sabe hoy sobre una moneda."""

    currency: str
    rate: Decimal | None
    rate_date: date | None
    source: str | None
    fetched_at: datetime | None
    # Días entre `rate_date` y hoy. Medición, no veredicto: no existe una
    # ventana de validez fijada (§12.6).
    age_days: int | None
    # `True` cuando hay tasa para el día de hoy: curar no dispara fetch
    # (paso 1 de `RateResolver`). `False` significa que el primer anuncio en
    # esta moneda va a salir a la red, y si elTOQUE no responde degrada a la
    # última conocida.
    has_today: bool


def compute_rate_status(session: Session, *, hoy: date | None = None) -> list[RateStatus]:
    """Estado del cache por moneda, incluidas las que no tienen ninguna tasa.

    Devolver la moneda sin cache es parte del punto: significa que un anuncio
    en esa moneda se guardaría sin convertir si elTOQUE no responde, y eso no
    se ve en ningún otro lado.
    """
    today = hoy or date.today()

    salida: list[RateStatus] = []
    for currency in _FOREIGN_CURRENCIES:
        # `cache.latest` ordena por `rate_date`, no por `fetched_at`: un
        # backfill que hoy escribe una tasa vieja no debe pasar por «la
        # última conocida». Se reusa en vez de repetir la consulta.
        ultima = cache.latest(session, currency)

        if ultima is None:
            salida.append(
                RateStatus(
                    currency=currency,
                    rate=None,
                    rate_date=None,
                    source=None,
                    fetched_at=None,
                    age_days=None,
                    has_today=False,
                )
            )
            continue

        rate, rate_date = ultima
        fila = session.scalars(
            select(ExchangeRateCache).where(
                ExchangeRateCache.currency == currency,
                ExchangeRateCache.rate_date == rate_date,
            )
        ).first()

        salida.append(
            RateStatus(
                currency=currency,
                rate=rate,
                rate_date=rate_date,
                source=fila.source if fila is not None else None,
                fetched_at=fila.fetched_at if fila is not None else None,
                age_days=(today - rate_date).days,
                has_today=cache.get_for_date(session, currency, today) is not None,
            )
        )

    return salida


@dataclass(frozen=True)
class AppliedRate:
    """Una tasa efectivamente escrita en `prices_history`, con su volumen."""

    rate: Decimal
    rate_date: date | None
    source: str | None
    rows: int


def compute_applied_rates(session: Session) -> list[AppliedRate]:
    """Qué tasas quedaron aplicadas en lo ya cargado, de mayor a menor volumen.

    Solo filas con `exchange_rate` escrito: una fila en CUP nunca tuvo tasa, y
    una sin resolver tampoco: ninguna de las dos describe qué tasa usó el
    sistema. Esas dos aparecen en otro lado —el hueco «sin convertir» de
    `/control`— y mezclarlas acá confundiría «no aplicaba» con «no se pudo».
    """
    filas = session.execute(
        select(
            PriceHistory.exchange_rate,
            PriceHistory.exchange_rate_date,
            PriceHistory.exchange_rate_source,
            func.count(),
        )
        .where(PriceHistory.exchange_rate.is_not(None))
        .group_by(
            PriceHistory.exchange_rate,
            PriceHistory.exchange_rate_date,
            PriceHistory.exchange_rate_source,
        )
    ).all()

    aplicadas = [
        AppliedRate(rate=rate, rate_date=rate_date, source=source, rows=rows)
        for rate, rate_date, source, rows in filas
    ]
    # Desempates explícitos para que el orden no dependa del motor: con dos
    # tasas del mismo volumen, la más reciente primero.
    aplicadas.sort(key=lambda a: (-a.rows, -(a.rate_date or date.min).toordinal()))
    return aplicadas
