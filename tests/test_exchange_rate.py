"""Servicio de conversión monetaria (`spec.md` §6.3 E).

Cada valor esperado de `TestParseRate` viene del sondeo real de la API TRMI de
elTOQUE hecho el 2026-08-13 (ver Engram `discovery/eltoque-trmi-api`), escrito
ANTES de que exista `eltoque.py`. Ningún caso de este archivo toca la red: el
socket real (`fetch_rate`) se prueba con `httpx.MockTransport`, nunca con un
host verdadero.
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx

from saimc.exchange_rate.cache import get_for_date, latest, store
from saimc.exchange_rate.eltoque import fetch_rate, parse_rate
from saimc.exchange_rate.service import (
    NO_RATE,
    RateResolution,
    RateResolver,
    anchor_date,
    apply_resolution,
)
from saimc.models import ExchangeRateCache, PriceHistory

# Body real capturado en el sondeo del 2026-08-13, tasas del propio día.
_BODY_CON_TASAS = (
    '{"date":"2026-08-13","hour":7,"minutes":54,"seconds":16,'
    '"tasas":{"BTC":734.66,"ECU":781.47,"MLC":460.69,"USD":671.56,'
    '"USDT_TRC20":687.3}}'
)

# Respuesta real a un rango >= 24h: string plano, no JSON, HTTP 200.
_BODY_INTERVALO_INVALIDO = "El intervalo de tiempo debe ser menor a 24 horas"

# Día sin dato: HTTP 200 con `tasas` vacío (nunca 404).
_BODY_SIN_TASAS = (
    '{"date":"2026-08-13","hour":7,"minutes":54,"seconds":54,"tasas":{}}'
)


class TestParseRate:
    def test_usd_devuelve_la_tasa_del_dia(self) -> None:
        assert parse_rate(_BODY_CON_TASAS, "USD") == Decimal("671.56")

    def test_eur_se_mapea_a_la_clave_ecu(self) -> None:
        assert parse_rate(_BODY_CON_TASAS, "ECU") == Decimal("781.47")

    def test_mlc_devuelve_la_tasa_del_dia(self) -> None:
        assert parse_rate(_BODY_CON_TASAS, "MLC") == Decimal("460.69")

    def test_clave_ausente_devuelve_none(self) -> None:
        assert parse_rate(_BODY_CON_TASAS, "CUP") is None

    def test_body_no_json_no_rompe_y_devuelve_none(self) -> None:
        """La respuesta a un intervalo >= 24h es texto plano, no JSON."""
        assert parse_rate(_BODY_INTERVALO_INVALIDO, "USD") is None

    def test_tasas_vacio_es_ausencia_no_error(self) -> None:
        """Un día sin dato responde HTTP 200 con `tasas: {}` — la ausencia se
        detecta en el payload, nunca en el status code. `parse_rate` sólo ve
        el cuerpo como string, así que este caso también prueba que la
        función no tiene ningún canal para depender del status."""
        assert parse_rate(_BODY_SIN_TASAS, "USD") is None

    def test_valor_no_numerico_devuelve_none(self) -> None:
        body = '{"tasas":{"USD":"n/a"}}'
        assert parse_rate(body, "USD") is None

    def test_el_tipo_de_retorno_es_decimal_puro(self) -> None:
        """El campo `date` de la respuesta MIENTE (devuelve la fecha de la
        consulta, no la del dato) — D3 lo hace irrepresentable dejando que la
        firma sólo pueda devolver un `Decimal`, nunca una tupla/dict con
        fecha."""
        resultado = parse_rate(_BODY_CON_TASAS, "USD")
        assert type(resultado) is Decimal


class TestFetchRateTimeout:
    def test_timeout_degrada_a_none_sin_propagar(self) -> None:
        """`httpx.MockTransport` no aplica el timeout real del cliente (no
        hay socket de por medio), así que la forma correcta de simular un
        timeout es que el handler LEVANTE la excepción de transporte
        directamente, no que duerma más que el timeout configurado."""

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("tiempo agotado", request=request)

        transport = httpx.MockTransport(handler)

        resultado = fetch_rate(
            "USD",
            date(2026, 8, 13),
            api_key="token-de-prueba",
            timeout=0.05,
            transport=transport,
        )

        assert resultado is None

    def test_sin_api_key_devuelve_none_sin_llamar_transporte(self) -> None:
        llamado = False

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal llamado
            llamado = True
            return httpx.Response(200, text=_BODY_CON_TASAS)

        transport = httpx.MockTransport(handler)

        resultado = fetch_rate(
            "USD", date(2026, 8, 13), api_key=None, timeout=1.0, transport=transport
        )

        assert resultado is None
        assert llamado is False

    def test_respuesta_no_200_devuelve_none(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="error interno")

        transport = httpx.MockTransport(handler)

        resultado = fetch_rate(
            "USD",
            date(2026, 8, 13),
            api_key="token-de-prueba",
            timeout=1.0,
            transport=transport,
        )

        assert resultado is None

    def test_fetch_exitoso_usa_un_solo_dia_calendario(self) -> None:
        """`date_from`/`date_to` deben cubrir exactamente el día pedido,
        nunca un rango más ancho (el rango >= 24h devuelve el string de
        error, no JSON)."""
        capturado: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            capturado["date_from"] = request.url.params["date_from"]
            capturado["date_to"] = request.url.params["date_to"]
            return httpx.Response(200, text=_BODY_CON_TASAS)

        transport = httpx.MockTransport(handler)

        resultado = fetch_rate(
            "USD",
            date(2026, 8, 13),
            api_key="token-de-prueba",
            timeout=1.0,
            transport=transport,
        )

        assert resultado == Decimal("671.56")
        assert capturado["date_from"] == "2026-08-13 00:00:00"
        assert capturado["date_to"] == "2026-08-13 23:59:59"


class TestCacheRepository:
    def test_get_for_date_en_tabla_vacia_devuelve_none(self, session) -> None:
        assert get_for_date(session, "USD", date(2026, 8, 13)) is None

    def test_store_y_get_for_date_redondan_el_viaje(self, session) -> None:
        store(session, "USD", date(2026, 8, 13), Decimal("671.56"), "elTOQUE")

        assert get_for_date(session, "USD", date(2026, 8, 13)) == Decimal("671.56")

    def test_store_dos_veces_para_el_mismo_par_actualiza_sin_integrity_error(
        self, session
    ) -> None:
        """Write-through = leer y luego actualizar-o-insertar (D2). Nunca
        `ON CONFLICT`: PostgreSQL-only, la suite corre en SQLite."""
        store(session, "USD", date(2026, 8, 13), Decimal("671.56"), "elTOQUE")
        store(session, "USD", date(2026, 8, 13), Decimal("999.99"), "elTOQUE")

        assert get_for_date(session, "USD", date(2026, 8, 13)) == Decimal("999.99")

    def test_latest_ordena_por_rate_date_no_por_fetched_at(self, session) -> None:
        """Regresión explícita de D2: un backfill que hoy trae una tasa VIEJA
        no debe envenenar el "último conocido" con una fecha vieja sólo
        porque se escribió más tarde. `latest()` ordena por `rate_date DESC`,
        nunca por `fetched_at`."""
        store(session, "USD", date(2026, 4, 20), Decimal("525.00"), "elTOQUE")
        store(session, "USD", date(2026, 8, 10), Decimal("670.00"), "elTOQUE")

        # `store()` sella `fetched_at` con el reloj real (ahora). Para
        # simular el escenario del gotcha —la fila vieja (abril) escrita HOY
        # por un backfill, la más reciente (agosto) escrita hace días por una
        # captura en vivo anterior— se pisa `fetched_at` directo por ORM.
        session.query(ExchangeRateCache).filter_by(
            currency="USD", rate_date=date(2026, 4, 20)
        ).update({"fetched_at": datetime(2026, 8, 13, tzinfo=UTC)})
        session.query(ExchangeRateCache).filter_by(
            currency="USD", rate_date=date(2026, 8, 10)
        ).update({"fetched_at": datetime(2026, 8, 5, tzinfo=UTC)})
        session.commit()

        resultado = latest(session, "USD")

        assert resultado == (Decimal("670.00"), date(2026, 8, 10))

    def test_latest_en_tabla_vacia_devuelve_none(self, session) -> None:
        assert latest(session, "USD") is None


# Ancla compartida por los tests de política — un día calendario cualquiera
# del sondeo real, sin significado especial más allá de ser fijo y legible.
_ANCLA = date(2026, 8, 13)


class TestAnchorDate:
    def test_published_date_manda_sobre_captured_at(self) -> None:
        capturado = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
        assert anchor_date(date(2026, 4, 20), capturado) == date(2026, 4, 20)

    def test_captura_21h_habana_no_avanza_al_dia_siguiente(self) -> None:
        """01:00 UTC del 14/08 es 21:00 Habana del 13/08 (UTC-4 en agosto):
        un `.date()` ingenuo sobre el instante UTC pediría tasa del 14, que
        respondería `tasas: {}` y degradaría un anuncio convertible. El
        ancla debe seguir el calendario de Habana, no el de UTC (design D5).
        """
        capturado = datetime(2026, 8, 14, 1, 0, tzinfo=UTC)
        assert anchor_date(None, capturado) == date(2026, 8, 13)


class _StubFetcher:
    """Fetcher de prueba: nunca pega a la red, cuenta llamadas y puede
    configurarse para fallar (simula una elTOQUE caída, no sólo ausente)."""

    def __init__(
        self,
        rates: dict[tuple[str, date], Decimal] | None = None,
        *,
        raises: bool = False,
    ) -> None:
        self.rates = rates or {}
        self.raises = raises
        self.calls: list[tuple[str, date]] = []

    def __call__(self, key: str, day: date) -> Decimal | None:
        self.calls.append((key, day))
        if self.raises:
            raise RuntimeError("elTOQUE caído")
        return self.rates.get((key, day))


class TestRateResolver:
    def test_cache_hit_no_llama_al_fetcher(self, session) -> None:
        store(session, "USD", _ANCLA, Decimal("671.56"), "elTOQUE")
        fetcher = _StubFetcher()
        resolver = RateResolver(session, fetcher)

        resultado = resolver.resolve("USD", _ANCLA)

        assert resultado == RateResolution(Decimal("671.56"), _ANCLA, "elTOQUE")
        assert fetcher.calls == []

    def test_fetch_exitoso_escribe_a_traves_del_cache(self, session) -> None:
        fetcher = _StubFetcher({("USD", _ANCLA): Decimal("671.56")})
        resolver = RateResolver(session, fetcher)

        resultado = resolver.resolve("USD", _ANCLA)

        assert resultado == RateResolution(Decimal("671.56"), _ANCLA, "elTOQUE")
        # confirma el write-through leyendo directo del repositorio de cache,
        # no del resolver (que ya memoizó y no volvería a tocar la base).
        assert get_for_date(session, "USD", _ANCLA) == Decimal("671.56")

    def test_fetch_falla_usa_tasa_cacheada_mas_reciente_fuera_de_ventana(
        self, session
    ) -> None:
        store(session, "USD", date(2026, 8, 10), Decimal("670.00"), "elTOQUE")
        fetcher = _StubFetcher()  # sin tasas configuradas: el fetch falla
        resolver = RateResolver(session, fetcher)

        resultado = resolver.resolve("USD", _ANCLA)

        assert resultado == RateResolution(
            Decimal("670.00"), date(2026, 8, 10), "elTOQUE (cache)"
        )

    def test_sin_nada_resolvible_devuelve_no_rate(self, session) -> None:
        fetcher = _StubFetcher()
        resolver = RateResolver(session, fetcher)

        assert resolver.resolve("USD", _ANCLA) == NO_RATE

    def test_memoiza_exito_una_sola_llamada_para_cinco_filas(self, session) -> None:
        fetcher = _StubFetcher({("USD", _ANCLA): Decimal("671.56")})
        resolver = RateResolver(session, fetcher)

        for _ in range(5):
            resolver.resolve("USD", _ANCLA)

        assert len(fetcher.calls) == 1

    def test_memoiza_fallo_una_api_muerta_cuesta_un_timeout_no_cinco(
        self, session
    ) -> None:
        fetcher = _StubFetcher(raises=True)
        resolver = RateResolver(session, fetcher)

        primero = resolver.resolve("USD", _ANCLA)
        segundo = resolver.resolve("USD", _ANCLA)

        assert primero == NO_RATE
        assert segundo == NO_RATE
        assert len(fetcher.calls) == 1


class TestApplyResolution:
    def test_calcula_price_cup_redondeando_half_up(self) -> None:
        entry = PriceHistory(
            price=Decimal("100.00"),
            currency="USD",
            product_id=uuid.uuid4(),
            source_id=uuid.uuid4(),
        )
        resolution = RateResolution(Decimal("671.56"), _ANCLA, "elTOQUE")

        apply_resolution(entry, resolution)

        assert entry.price_cup == Decimal("67156.00")
        assert entry.exchange_rate == Decimal("671.56")
        assert entry.exchange_rate_date == _ANCLA
        assert entry.exchange_rate_source == "elTOQUE"

    def test_overflow_deja_price_cup_en_none(self) -> None:
        """Un monto convertido >= 10**10 no entra en `Numeric(12, 2)`: debe
        degradar a `None` en vez de tumbar el insert entero (design D4)."""
        entry = PriceHistory(
            price=Decimal("10000000000"),
            currency="USD",
            product_id=uuid.uuid4(),
            source_id=uuid.uuid4(),
        )
        resolution = RateResolution(Decimal("1"), _ANCLA, "elTOQUE")

        apply_resolution(entry, resolution)

        assert entry.price_cup is None
        # la tasa y su procedencia sí quedan registradas: sólo el monto se omite.
        assert entry.exchange_rate == Decimal("1")

    def test_sin_tasa_deja_price_cup_en_none(self) -> None:
        entry = PriceHistory(
            price=Decimal("100.00"),
            currency="USD",
            product_id=uuid.uuid4(),
            source_id=uuid.uuid4(),
        )

        apply_resolution(entry, NO_RATE)

        assert entry.price_cup is None
        assert entry.exchange_rate is None
        assert entry.exchange_rate_date is None
        assert entry.exchange_rate_source is None
