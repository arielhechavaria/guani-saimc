"""Política de resolución de tasa de cambio (`spec.md` §6.3 E, design.md D4/D5/D6).

`RateResolver` implementa la cadena de degradación de cuatro pasos: cache del
día pedido → fetch fresco a elTOQUE (con write-through) → última tasa
cacheada (fuera de ventana) → sin tasa. Nunca bloquea el guardado: cualquier
falla del fetcher (excepción incluida — una elTOQUE caída que levanta en vez
de devolver `None`) degrada al siguiente paso, nunca propaga.

`anchor_date` decide la fecha que se pide, no el motor de resolución: eso
vive acá porque ambos router paths (`submit_curation`/`submit_edit`) la
necesitan antes de llamar a `resolve()`.

`apply_resolution` es el único escritor de las cuatro columnas de
`PriceHistory` (`price_cup`, `exchange_rate`, `exchange_rate_date`,
`exchange_rate_source`), compartido por ambos router paths y por el backfill
(Commit 3) — así ambos CHECK de `prices_history` se satisfacen por
construcción sin repetir la lógica de redondeo/overflow en cada llamador.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

from saimc.exchange_rate import cache
from saimc.exchange_rate.eltoque import _ELTOQUE_KEYS
from saimc.models import PriceHistory

# `(clave elTOQUE, día)` -> tasa, o `None` si no se pudo resolver. Nunca debe
# levantar: `RateResolver` igual atrapa cualquier excepción por las dudas
# (un fetcher mal comportado no debe tumbar un guardado).
Fetcher = Callable[[str, date], Decimal | None]

_HAVANA = "America/Havana"
_ELTOQUE_SOURCE = "elTOQUE"
_ELTOQUE_CACHE_SOURCE = "elTOQUE (cache)"

# `prices_history.price_cup` es `Numeric(12, 2)`: 10 dígitos enteros como
# máximo (9999999999.99). Un monto convertido que llegue a 10**10 no entra
# en la columna — degrada a `None` en vez de tumbar el insert (design D4).
_PRICE_CUP_LIMIT = Decimal(10) ** 10


@dataclass(frozen=True)
class RateResolution:
    rate: Decimal | None
    # La fecha SOLICITADA (paso 1/2) o la fecha propia de la fila cacheada
    # más reciente (paso 3) — nunca la fecha que devuelve la respuesta de
    # elTOQUE, que miente (D3).
    rate_date: date | None
    source: str | None  # "elTOQUE" | "elTOQUE (cache)" | None


NO_RATE = RateResolution(None, None, None)


def anchor_date(published: date | None, captured_at: datetime) -> date:
    """Día calendario contra el que se pide la tasa (design D5).

    `published_date` manda cuando existe. Si no, se deriva de `captured_at`
    (que es UTC) convertido al calendario de La Habana — una captura de
    21:00 hora de Cuba es 01:00 UTC del día siguiente, y un `.date()`
    ingenuo sobre ese instante UTC pediría la tasa del día equivocado.
    Un `captured_at` sin zona (SQLite, los tests) se lee como UTC, que es lo
    que se guarda: sin eso `astimezone` lo interpretaría en la hora local de
    la máquina. Si el sistema no tiene la base de datos de zonas horarias
    instalada, degrada a la fecha UTC en vez de romper (mismo criterio de
    "nunca bloquear" que el resto del módulo).

    Es también el día de La Habana contra el que se fecha un anuncio sin fecha
    propia (`curation_rows.default_published_date`, T2): hay un solo lugar que
    sabe qué día es en Cuba.
    """
    if published is not None:
        return published
    if captured_at.tzinfo is None:
        captured_at = captured_at.replace(tzinfo=UTC)
    try:
        habana = ZoneInfo(_HAVANA)
    except ZoneInfoNotFoundError:
        return captured_at.date()
    return captured_at.astimezone(habana).date()


def apply_resolution(entry: PriceHistory, res: RateResolution) -> None:
    """Escribe las cuatro columnas de conversión en `entry` a partir de `res`.

    Único escritor de estas columnas (design D4): tanto `submit_curation`
    como `submit_edit` como el backfill (Commit 3) pasan por acá, así que el
    redondeo y el guardia de overflow se aplican una sola vez.
    """
    entry.exchange_rate = res.rate
    entry.exchange_rate_date = res.rate_date
    entry.exchange_rate_source = res.source

    if res.rate is None:
        entry.price_cup = None
        return

    convertido = (entry.price * res.rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    entry.price_cup = None if convertido >= _PRICE_CUP_LIMIT else convertido


class RateResolver:
    """Cadena de degradación de cuatro pasos, con memoización por request.

    Instanciar uno por request HTTP (no compartir entre requests): la
    memoización asume que todas las filas de un mismo submit comparten
    ancla y catálogo de monedas, así que N filas de la misma moneda
    colapsan a lo sumo una llamada HTTP — y si esa llamada falla, el fallo
    también se memoiza, para que una elTOQUE caída cueste un timeout por
    request, no uno por fila.

    `stale_fallback=False` corta la cadena en el paso 2: sin tasa del día,
    `NO_RATE` en vez de la última cacheada. Lo usa el backfill, que no debe
    volver a rellenar (decisión del 2026-10-16); el curado sigue con el
    default hasta que el resolutor falle cerrado en su propio cambio.
    """

    def __init__(
        self,
        db: Session,
        fetcher: Fetcher,
        *,
        write_through: bool = True,
        stale_fallback: bool = True,
    ) -> None:
        self._db = db
        self._fetcher = fetcher
        self._write_through = write_through
        self._stale_fallback = stale_fallback
        self._memo: dict[tuple[str, date], RateResolution] = {}

    def resolve(self, currency: str, anchor: date) -> RateResolution:
        key = (currency, anchor)
        if key in self._memo:
            return self._memo[key]

        resolution = self._resolve_uncached(currency, anchor)
        self._memo[key] = resolution
        return resolution

    def _resolve_uncached(self, currency: str, anchor: date) -> RateResolution:
        # Paso 1: cache exacto para el día pedido.
        cached = cache.get_for_date(self._db, currency, anchor)
        if cached is not None:
            return RateResolution(cached, anchor, _ELTOQUE_SOURCE)

        # Paso 2: fetch fresco. Cualquier excepción del fetcher (una
        # elTOQUE caída que levanta en vez de devolver `None`) degrada
        # igual que un `None` limpio — nunca propaga.
        eltoque_key = _ELTOQUE_KEYS.get(currency, currency)
        try:
            fetched = self._fetcher(eltoque_key, anchor)
        except Exception:
            fetched = None

        if fetched is not None:
            if self._write_through:
                cache.store(self._db, currency, anchor, fetched, _ELTOQUE_SOURCE)
            return RateResolution(fetched, anchor, _ELTOQUE_SOURCE)

        if not self._stale_fallback:
            return NO_RATE

        # Paso 3: última tasa cacheada, explícitamente fuera de ventana
        # (nunca se inventa una ventana de validez, spec.md §12.6).
        stale = cache.latest(self._db, currency)
        if stale is not None:
            rate, rate_date = stale
            return RateResolution(rate, rate_date, _ELTOQUE_CACHE_SOURCE)

        # Paso 4: nada resolvible.
        return NO_RATE
