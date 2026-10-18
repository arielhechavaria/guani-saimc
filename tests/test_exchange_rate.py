"""Servicio de conversión monetaria (`spec.md` §6.3 E).

Cada valor esperado de `TestParseRate` viene del sondeo real de la API TRMI de
elTOQUE hecho el 2026-08-13 (ver Engram `discovery/eltoque-trmi-api`), escrito
ANTES de que exista `eltoque.py`. Ningún caso de este archivo toca la red: el
socket real (`fetch_rate`) se prueba con `httpx.MockTransport`, nunca con un
host verdadero.
"""

import csv
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from saimc.exchange_rate.backfill import run_backfill
from saimc.exchange_rate.cache import get_for_date, latest, store
from saimc.exchange_rate.eltoque import (
    ElToqueUnavailable,
    fetch_rate,
    fetch_rate_strict,
    parse_rate,
)
from saimc.exchange_rate.service import (
    NO_RATE,
    RateResolution,
    RateResolver,
    anchor_date,
    apply_resolution,
)
from saimc.models import (
    ContentType,
    Currency,
    ExchangeRateCache,
    PriceHistory,
    Product,
    RawIngestion,
    SourceType,
)

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


class TestFetchRateStrict:
    """El backfill necesita separar «el día no tiene dato» (200 con `tasas`
    vacío → `None`) de «la API no respondió» (429, otro status, red → levanta).
    Medido el 2026-10-18: elTOQUE corta con 429 tras una ráfaga y `fetch_rate`
    lo leía como día sin dato."""

    def _fetch(self, handler) -> Decimal | None:
        return fetch_rate_strict(
            "USD",
            date(2026, 10, 3),
            api_key="token-de-prueba",
            timeout=1.0,
            transport=httpx.MockTransport(handler),
        )

    def test_429_levanta(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                429, text='{"error":"Has superado el límite de peticiones"}'
            )

        with pytest.raises(ElToqueUnavailable):
            self._fetch(handler)

    def test_error_de_red_levanta(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("sin red", request=request)

        with pytest.raises(ElToqueUnavailable):
            self._fetch(handler)

    def test_dia_sin_dato_devuelve_none(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text='{"tasas":{}}')

        assert self._fetch(handler) is None

    def test_dia_con_dato_devuelve_la_tasa(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=_BODY_CON_TASAS)

        assert self._fetch(handler) == Decimal("671.56")

    def test_fetch_rate_sigue_degradando_el_429_a_none(self) -> None:
        """El curado no cambia: `fetch_rate` nunca levanta."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, text="")

        assert (
            fetch_rate(
                "USD",
                date(2026, 10, 3),
                api_key="token-de-prueba",
                timeout=1.0,
                transport=httpx.MockTransport(handler),
            )
            is None
        )


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
    def test_una_fila_en_eur_le_pide_ecu_a_eltoque(self, session) -> None:
        """El euro se llama `ECU` en el JSON de elTOQUE, no `EUR` (sondeo del
        2026-08-13, Engram `discovery/eltoque-trmi-api`).

        `TestParseRate` no cubre esto: le pasa la clave `"ECU"` ya resuelta al
        parser, o sea prueba que el parser lee la clave que le dan, nunca que
        una fila en EUR pida la clave correcta. Sin este caso, cambiar el mapeo
        a `"EUR"` deja la suite verde y persiste euros convertidos a la tasa
        equivocada, con una procedencia §6.3 E que se ve impecable.

        La aserción es sobre la clave que viaja al fetcher, no sobre el
        resultado: es el único punto donde el mapeo es observable.
        """
        fetcher = _StubFetcher({("ECU", _ANCLA): Decimal("781.47")})
        resolver = RateResolver(session, fetcher)

        resultado = resolver.resolve("EUR", _ANCLA)

        assert fetcher.calls == [("ECU", _ANCLA)]
        assert resultado == RateResolution(Decimal("781.47"), _ANCLA, "elTOQUE")
        # La cache se indexa por la moneda del dominio, nunca por la clave
        # interna de elTOQUE: `price_history.currency` guarda "EUR".
        assert get_for_date(session, "EUR", _ANCLA) == Decimal("781.47")

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
            provenance="human",
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
            provenance="human",
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
            provenance="human",
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


class _BackfillFetcher:
    """Fetcher de prueba para el backfill: tasas fijas por `(clave, día)`,
    nunca toca la red, cuenta cada llamada — separado de `_StubFetcher` para
    no acoplar la prueba del CLI a la del resolver, que evolucionan aparte."""

    def __init__(self, rates: dict[tuple[str, date], Decimal] | None = None) -> None:
        self.rates = rates or {}
        self.calls: list[tuple[str, date]] = []

    def __call__(self, key: str, day: date) -> Decimal | None:
        self.calls.append((key, day))
        return self.rates.get((key, day))


def _crear_producto_backfill(session, *, name: str = "Arroz") -> Product:
    producto = Product(name=name, category="granos", unit="lb")
    session.add(producto)
    session.flush()
    return producto


def _crear_ingestion_backfill(session, *, hash_: str) -> RawIngestion:
    ingestion = RawIngestion(
        source_type=SourceType.INBOX_TEXT,
        file_path=f"raw/{hash_}.txt",
        content_hash=hash_,
        content_type=ContentType.TEXT,
        captured_at=datetime(2026, 8, 10, 12, 0, tzinfo=UTC),
    )
    session.add(ingestion)
    session.flush()
    return ingestion


def _crear_precio_pendiente(
    session,
    producto: Product,
    ingestion: RawIngestion,
    *,
    currency: str,
    exchange_rate: Decimal | None = None,
    exchange_rate_date: date | None = None,
    exchange_rate_source: str | None = None,
) -> PriceHistory:
    # `ck_prices_history_exchange_rate_provenance` exige fecha y fuente no
    # nulas cuando `exchange_rate` no es nulo — una fila "ya convertida" en
    # los tests debe traer las tres o el insert la rechaza.
    precio = PriceHistory(
        provenance="human",
        product_id=producto.id,
        price=Decimal("10.00"),
        currency=currency,
        source_id=ingestion.id,
        exchange_rate=exchange_rate,
        exchange_rate_date=exchange_rate_date,
        exchange_rate_source=exchange_rate_source,
    )
    session.add(precio)
    session.flush()
    return precio


class TestBackfill:
    """`run_backfill` es la función que el CLI (`backfill.py`, task 3.2) llama
    con una sesión ya abierta — separada de `main()` a propósito, para poder
    probarla sin `session_scope()` ni `.env` (design D8)."""

    def test_dry_run_no_escribe_nada(self, session) -> None:
        producto = _crear_producto_backfill(session)
        ingestion = _crear_ingestion_backfill(session, hash_="bf-1")
        fila = _crear_precio_pendiente(session, producto, ingestion, currency=Currency.USD)
        ancla = ingestion.captured_at.date()
        fetcher = _BackfillFetcher({("USD", ancla): Decimal("671.56")})

        reporte = run_backfill(session, fetcher, apply=False, sleep=lambda _: None)

        session.expire_all()
        recuperada = session.get(PriceHistory, fila.id)
        assert recuperada is not None
        assert recuperada.exchange_rate is None
        assert recuperada.price_cup is None
        # tampoco escribe la cache: un dry-run no debe dejar rastro alguno.
        assert get_for_date(session, "USD", ancla) is None
        assert reporte.examined == 1
        assert reporte.resolved == 1
        assert reporte.unresolved == 0

    def test_dry_run_no_persiste_ni_aunque_la_sesion_commitee(self, session) -> None:
        """Regresión de la guarda de dry-run, con el commit que hace `session_scope`.

        `test_dry_run_no_escribe_nada` no alcanza: siembra una sola fila, nadie
        consulta la sesión después de la mutación —así que no hay autoflush— y
        el `expire_all()` descarta el estado sucio antes de mirar. Una versión
        de `run_backfill` que mutara la fila fuera del `if apply:` pasaría ese
        test y aun así escribiría en producción, porque `session_scope`
        (`src/saimc/db.py`) commitea al salir sin excepción y `main()` corre el
        backfill adentro.

        Este caso reproduce esa salida: dos filas (para que el autoflush sea
        posible) y un `commit()` explícito después del dry-run. Si la mutación
        llegó al ORM, el commit la persiste y las aserciones caen.
        """
        producto = _crear_producto_backfill(session)
        ingestion_a = _crear_ingestion_backfill(session, hash_="bf-dry-a")
        ingestion_b = _crear_ingestion_backfill(session, hash_="bf-dry-b")
        fila_a = _crear_precio_pendiente(
            session, producto, ingestion_a, currency=Currency.USD
        )
        fila_b = _crear_precio_pendiente(
            session, producto, ingestion_b, currency=Currency.USD
        )
        ancla = ingestion_a.captured_at.date()
        fetcher = _BackfillFetcher({("USD", ancla): Decimal("671.56")})

        run_backfill(session, fetcher, apply=False, sleep=lambda _: None)

        # Lo que hace `session_scope` al salir normalmente. Sin esto el test no
        # ejerce el camino que realmente corre en producción.
        session.commit()

        session.expire_all()
        for fila in (fila_a, fila_b):
            recuperada = session.get(PriceHistory, fila.id)
            assert recuperada is not None
            assert recuperada.exchange_rate is None
            assert recuperada.exchange_rate_date is None
            assert recuperada.exchange_rate_source is None
            assert recuperada.price_cup is None
        assert get_for_date(session, "USD", ancla) is None

    def test_apply_resuelve_y_persiste(self, session) -> None:
        producto = _crear_producto_backfill(session)
        ingestion = _crear_ingestion_backfill(session, hash_="bf-2")
        fila = _crear_precio_pendiente(session, producto, ingestion, currency=Currency.USD)
        ancla = ingestion.captured_at.date()
        fetcher = _BackfillFetcher({("USD", ancla): Decimal("671.56")})

        reporte = run_backfill(session, fetcher, apply=True, sleep=lambda _: None)

        session.expire_all()
        recuperada = session.get(PriceHistory, fila.id)
        assert recuperada is not None
        assert recuperada.exchange_rate == Decimal("671.56")
        assert recuperada.exchange_rate_date == ancla
        assert recuperada.exchange_rate_source == "elTOQUE"
        assert recuperada.price_cup == Decimal("6715.60")
        assert reporte.resolved == 1
        assert reporte.unresolved == 0

    def test_fila_ya_resuelta_no_se_reintenta_ni_se_sobreescribe(self, session) -> None:
        """Idempotencia, caso 1: una fila que ya resolvió con éxito nunca
        vuelve a pedirse — ni siquiera si una corrida posterior traería una
        tasa distinta (design D8, spec: idempotent backfill)."""
        producto = _crear_producto_backfill(session)
        ingestion = _crear_ingestion_backfill(session, hash_="bf-3")
        fila = _crear_precio_pendiente(session, producto, ingestion, currency=Currency.USD)
        ancla = ingestion.captured_at.date()

        primer_fetcher = _BackfillFetcher({("USD", ancla): Decimal("671.56")})
        run_backfill(session, primer_fetcher, apply=True, sleep=lambda _: None)
        session.expire_all()
        assert session.get(PriceHistory, fila.id).exchange_rate == Decimal("671.56")

        segundo_fetcher = _BackfillFetcher({("USD", ancla): Decimal("999.99")})
        reporte = run_backfill(session, segundo_fetcher, apply=True, sleep=lambda _: None)

        session.expire_all()
        recuperada = session.get(PriceHistory, fila.id)
        assert recuperada.exchange_rate == Decimal("671.56")  # sobrevive, no 999.99
        assert segundo_fetcher.calls == []
        assert reporte.examined == 0

    def test_fila_sin_tasa_se_reintenta_en_la_proxima_corrida(self, session) -> None:
        """Idempotencia, caso 2: una fila que resolvió a "sin tasa" SÍ se
        reintenta en la próxima corrida — la tasa pudo aparecer después."""
        producto = _crear_producto_backfill(session)
        ingestion = _crear_ingestion_backfill(session, hash_="bf-4")
        fila = _crear_precio_pendiente(session, producto, ingestion, currency=Currency.USD)
        ancla = ingestion.captured_at.date()

        fetcher_sin_tasa = _BackfillFetcher()  # sin ninguna tasa configurada
        primer_reporte = run_backfill(session, fetcher_sin_tasa, apply=True, sleep=lambda _: None)
        assert primer_reporte.unresolved == 1
        assert fetcher_sin_tasa.calls == [("USD", ancla)]

        session.expire_all()
        assert session.get(PriceHistory, fila.id).exchange_rate is None

        fetcher_con_tasa = _BackfillFetcher({("USD", ancla): Decimal("671.56")})
        segundo_reporte = run_backfill(session, fetcher_con_tasa, apply=True, sleep=lambda _: None)

        assert segundo_reporte.resolved == 1
        assert fetcher_con_tasa.calls == [("USD", ancla)]  # sí se reintentó

    def test_limit_acota_filas_procesadas(self, session) -> None:
        producto = _crear_producto_backfill(session)
        ingestion = _crear_ingestion_backfill(session, hash_="bf-5")
        _crear_precio_pendiente(session, producto, ingestion, currency=Currency.USD)
        _crear_precio_pendiente(session, producto, ingestion, currency=Currency.EUR)
        fetcher = _BackfillFetcher()

        reporte = run_backfill(session, fetcher, apply=False, limit=1, sleep=lambda _: None)

        assert reporte.examined == 1

    def test_cup_y_fila_ya_convertida_quedan_fuera_del_filtro(self, session) -> None:
        producto = _crear_producto_backfill(session)
        ingestion = _crear_ingestion_backfill(session, hash_="bf-6")
        _crear_precio_pendiente(session, producto, ingestion, currency=Currency.CUP)
        _crear_precio_pendiente(
            session,
            producto,
            ingestion,
            currency=Currency.USD,
            exchange_rate=Decimal("650.00"),
            exchange_rate_date=ingestion.captured_at.date(),
            exchange_rate_source="elTOQUE",
        )
        fetcher = _BackfillFetcher()

        reporte = run_backfill(session, fetcher, apply=True, sleep=lambda _: None)

        assert reporte.examined == 0
        assert fetcher.calls == []

    def test_pausa_solo_tras_un_fetch_real_nunca_tras_un_acierto_de_cache(
        self, session
    ) -> None:
        producto = _crear_producto_backfill(session)
        ingestion = _crear_ingestion_backfill(session, hash_="bf-7")
        ancla = ingestion.captured_at.date()
        store(session, "USD", ancla, Decimal("671.56"), "elTOQUE")  # ya cacheada
        _crear_precio_pendiente(session, producto, ingestion, currency=Currency.USD)
        fetcher = _BackfillFetcher()  # no debería llamarse: acierto de cache
        pausas: list[float] = []

        run_backfill(session, fetcher, apply=True, sleep=pausas.append)

        assert fetcher.calls == []
        assert pausas == []


# ---------------------------------------------------------------------------
# Saneamiento de filas con tasa de relleno (`elTOQUE (cache)`): T1 de
# `docs/plan-bloque-mvp.md`, decisión del 2026-10-16. Los valores esperados
# se fijan a mano antes del código: precio 10 USD, relleno de 690 del
# 2026-09-13 (6900 CUP), ancla 2026-10-05, tasa real del día 760 (7600 CUP).
# ---------------------------------------------------------------------------

_RELLENO = "elTOQUE (cache)"
_ANCLA_RELLENO = date(2026, 10, 5)
_FECHA_RELLENO = date(2026, 9, 13)


def _crear_precio_relleno(
    session,
    producto: Product,
    ingestion: RawIngestion,
    *,
    currency: str = Currency.USD,
    published_date: date | None = _ANCLA_RELLENO,
) -> PriceHistory:
    """Una fila como las 266 de producción: convertida con la última tasa
    guardada (otro día) y marcada `elTOQUE (cache)`."""
    precio = PriceHistory(
        provenance="human",
        product_id=producto.id,
        price=Decimal("10.00"),
        currency=currency,
        source_id=ingestion.id,
        published_date=published_date,
        exchange_rate=Decimal("690.0000"),
        exchange_rate_date=_FECHA_RELLENO,
        exchange_rate_source=_RELLENO,
        price_cup=Decimal("6900.00"),
    )
    session.add(precio)
    session.flush()
    return precio


class TestResolverSinRelleno:
    def test_sin_tasa_del_dia_no_cae_a_la_ultima_cacheada(self, session) -> None:
        store(session, "USD", _FECHA_RELLENO, Decimal("690"), "elTOQUE")
        resolver = RateResolver(session, _BackfillFetcher(), stale_fallback=False)

        assert resolver.resolve(Currency.USD, _ANCLA_RELLENO) == NO_RATE

    def test_por_defecto_sigue_cayendo_a_la_ultima_cacheada(self, session) -> None:
        """El curado no cambia en este slice: la falla cerrada del resolutor
        es otro cambio, con sus propios tests (decisión del 2026-10-16)."""
        store(session, "USD", _FECHA_RELLENO, Decimal("690"), "elTOQUE")
        resolver = RateResolver(session, _BackfillFetcher())

        assert resolver.resolve(Currency.USD, _ANCLA_RELLENO) == RateResolution(
            Decimal("690"), _FECHA_RELLENO, _RELLENO
        )


class TestBackfillRelleno:
    def test_con_tasa_del_dia_reescribe_las_cuatro_columnas(self, session) -> None:
        producto = _crear_producto_backfill(session)
        ingestion = _crear_ingestion_backfill(session, hash_="rel-1")
        fila = _crear_precio_relleno(session, producto, ingestion)
        fetcher = _BackfillFetcher({("USD", _ANCLA_RELLENO): Decimal("760")})

        reporte = run_backfill(session, fetcher, apply=True, sleep=lambda _: None)

        session.expire_all()
        recuperada = session.get(PriceHistory, fila.id)
        assert recuperada.exchange_rate == Decimal("760")
        assert recuperada.exchange_rate_date == _ANCLA_RELLENO
        assert recuperada.exchange_rate_source == "elTOQUE"
        assert recuperada.price_cup == Decimal("7600.00")
        assert fetcher.calls == [("USD", _ANCLA_RELLENO)]
        assert (reporte.examined, reporte.resolved, reporte.unresolved) == (1, 1, 0)

    def test_ancla_sin_fecha_de_publicacion_es_la_captura_en_la_habana(
        self, session
    ) -> None:
        producto = _crear_producto_backfill(session)
        ingestion = _crear_ingestion_backfill(session, hash_="rel-2")
        # 2026-08-10 12:00 UTC = 08:00 en La Habana, mismo día.
        _crear_precio_relleno(session, producto, ingestion, published_date=None)
        fetcher = _BackfillFetcher()

        run_backfill(session, fetcher, apply=False, sleep=lambda _: None)

        assert fetcher.calls == [("USD", date(2026, 8, 10))]

    def test_sin_tasa_del_dia_no_se_toca_y_se_reporta(self, session) -> None:
        """Sin dato para el ancla no se rellena con otro día, ni siquiera con
        uno que la cache sí tiene: la fila queda como está y va al reporte."""
        producto = _crear_producto_backfill(session)
        ingestion = _crear_ingestion_backfill(session, hash_="rel-3")
        fila = _crear_precio_relleno(session, producto, ingestion)
        store(session, "USD", date(2026, 10, 4), Decimal("758"), "elTOQUE")
        fetcher = _BackfillFetcher()  # elTOQUE sin dato para el ancla

        reporte = run_backfill(session, fetcher, apply=True, sleep=lambda _: None)

        session.expire_all()
        recuperada = session.get(PriceHistory, fila.id)
        assert recuperada.exchange_rate == Decimal("690.0000")
        assert recuperada.exchange_rate_date == _FECHA_RELLENO
        assert recuperada.exchange_rate_source == _RELLENO
        assert recuperada.price_cup == Decimal("6900.00")
        assert (reporte.examined, reporte.resolved, reporte.unresolved) == (1, 0, 1)
        assert reporte.unresolved_rows == [(fila.id, "USD", _ANCLA_RELLENO)]

    def test_acierto_de_cache_del_dia_exacto_se_usa_sin_consultar(self, session) -> None:
        producto = _crear_producto_backfill(session)
        ingestion = _crear_ingestion_backfill(session, hash_="rel-4")
        fila = _crear_precio_relleno(session, producto, ingestion)
        store(session, "USD", _ANCLA_RELLENO, Decimal("760"), "elTOQUE")
        fetcher = _BackfillFetcher()

        run_backfill(session, fetcher, apply=True, sleep=lambda _: None)

        session.expire_all()
        assert session.get(PriceHistory, fila.id).price_cup == Decimal("7600.00")
        assert fetcher.calls == []

    def test_dry_run_no_toca_la_fila_ni_la_cache(self, session) -> None:
        producto = _crear_producto_backfill(session)
        ingestion = _crear_ingestion_backfill(session, hash_="rel-5")
        fila = _crear_precio_relleno(session, producto, ingestion)
        fetcher = _BackfillFetcher({("USD", _ANCLA_RELLENO): Decimal("760")})

        reporte = run_backfill(session, fetcher, apply=False, sleep=lambda _: None)
        session.commit()  # lo que hace `session_scope` al salir

        session.expire_all()
        recuperada = session.get(PriceHistory, fila.id)
        assert recuperada.exchange_rate_source == _RELLENO
        assert recuperada.price_cup == Decimal("6900.00")
        assert get_for_date(session, "USD", _ANCLA_RELLENO) is None
        assert reporte.resolved == 1

    def test_fila_saneada_no_vuelve_a_examinarse(self, session) -> None:
        producto = _crear_producto_backfill(session)
        ingestion = _crear_ingestion_backfill(session, hash_="rel-6")
        _crear_precio_relleno(session, producto, ingestion)
        fetcher = _BackfillFetcher({("USD", _ANCLA_RELLENO): Decimal("760")})
        run_backfill(session, fetcher, apply=True, sleep=lambda _: None)

        segundo = _BackfillFetcher({("USD", _ANCLA_RELLENO): Decimal("999")})
        reporte = run_backfill(session, segundo, apply=True, sleep=lambda _: None)

        assert reporte.examined == 0
        assert segundo.calls == []

    def test_cup_queda_fuera_aunque_traiga_la_marca_de_relleno(self, session) -> None:
        """Los CHECK admiten una fila CUP con `exchange_rate` nulo y fuente
        `elTOQUE (cache)`; igual nunca se le pide tasa a la moneda nacional."""
        producto = _crear_producto_backfill(session)
        ingestion = _crear_ingestion_backfill(session, hash_="rel-7")
        fila = PriceHistory(
            provenance="human",
            product_id=producto.id,
            price=Decimal("10.00"),
            currency=Currency.CUP,
            source_id=ingestion.id,
            exchange_rate_source=_RELLENO,
        )
        session.add(fila)
        session.flush()
        fetcher = _BackfillFetcher()

        reporte = run_backfill(session, fetcher, apply=True, sleep=lambda _: None)

        assert reporte.examined == 0
        assert fetcher.calls == []

    def test_pendiente_sin_tasa_del_dia_ya_no_se_rellena(self, session) -> None:
        """Cambio de comportamiento del backfill: una fila sin tasa tampoco
        recibe la última tasa guardada; sigue sin convertir y se reintenta."""
        producto = _crear_producto_backfill(session)
        ingestion = _crear_ingestion_backfill(session, hash_="rel-8")
        fila = _crear_precio_pendiente(session, producto, ingestion, currency=Currency.USD)
        store(session, "USD", _FECHA_RELLENO, Decimal("690"), "elTOQUE")

        reporte = run_backfill(session, _BackfillFetcher(), apply=True, sleep=lambda _: None)

        session.expire_all()
        recuperada = session.get(PriceHistory, fila.id)
        assert recuperada.exchange_rate is None
        assert recuperada.exchange_rate_source is None
        assert recuperada.price_cup is None
        assert reporte.unresolved == 1

    def test_lo_reescrito_cumple_los_check_de_la_tabla(self, session) -> None:
        producto = _crear_producto_backfill(session)
        ingestion = _crear_ingestion_backfill(session, hash_="rel-9")
        _crear_precio_relleno(session, producto, ingestion)
        _crear_precio_relleno(session, producto, ingestion, currency=Currency.EUR)
        _crear_precio_relleno(
            session, producto, ingestion, published_date=date(2026, 10, 6)
        )
        fetcher = _BackfillFetcher(
            {
                ("USD", _ANCLA_RELLENO): Decimal("760"),
                ("ECU", _ANCLA_RELLENO): Decimal("850"),
            }
        )

        run_backfill(session, fetcher, apply=True, sleep=lambda _: None)

        # El commit pasó por los CHECK; además se verifican fila por fila.
        session.expire_all()
        for fila in session.scalars(select(PriceHistory)):
            assert fila.currency != Currency.CUP or fila.exchange_rate is None
            assert fila.exchange_rate is None or (
                fila.exchange_rate_date is not None
                and fila.exchange_rate_source is not None
            )
        # Y los CHECK están activos en la base de prueba: no es un test vacío.
        with pytest.raises(IntegrityError):
            session.add(
                PriceHistory(
                    provenance="human",
                    product_id=producto.id,
                    price=Decimal("1"),
                    currency=Currency.USD,
                    source_id=ingestion.id,
                    exchange_rate=Decimal("760"),
                )
            )
            session.flush()
        session.rollback()

    def test_apply_respalda_los_valores_originales_antes_de_escribir(
        self, session, tmp_path
    ) -> None:
        producto = _crear_producto_backfill(session)
        ingestion = _crear_ingestion_backfill(session, hash_="rel-10")
        fila = _crear_precio_relleno(session, producto, ingestion)
        fetcher = _BackfillFetcher({("USD", _ANCLA_RELLENO): Decimal("760")})
        respaldo = tmp_path / "respaldo.csv"

        run_backfill(
            session, fetcher, apply=True, backup_path=respaldo, sleep=lambda _: None
        )

        with respaldo.open(encoding="utf-8") as archivo:
            filas = list(csv.DictReader(archivo))
        assert filas == [
            {
                "id": str(fila.id),
                "currency": "USD",
                "price": "10.00",
                "price_cup": "6900.00",
                "exchange_rate": "690.0000",
                "exchange_rate_date": "2026-09-13",
                "exchange_rate_source": _RELLENO,
            }
        ]

    def test_api_caida_reintenta_con_espera_y_resuelve(self, session) -> None:
        producto = _crear_producto_backfill(session)
        ingestion = _crear_ingestion_backfill(session, hash_="rel-12")
        fila = _crear_precio_relleno(session, producto, ingestion)
        respuestas: list[Decimal | Exception] = [
            ElToqueUnavailable("429"),
            Decimal("760"),
        ]

        def fetcher(key: str, day: date) -> Decimal | None:
            respuesta = respuestas.pop(0)
            if isinstance(respuesta, Exception):
                raise respuesta
            return respuesta

        pausas: list[float] = []
        reporte = run_backfill(
            session, fetcher, apply=True, sleep=pausas.append, retry_wait=90.0
        )

        session.expire_all()
        assert session.get(PriceHistory, fila.id).price_cup == Decimal("7600.00")
        assert reporte.resolved == 1
        assert reporte.aborted is None
        assert 90.0 in pausas

    def test_api_caida_sin_recuperarse_corta_la_corrida_sin_tocar_nada(
        self, session
    ) -> None:
        """Un 429 persistente no es «día sin dato»: la fila queda intacta, no
        entra al reporte de sin tasa y no se examinan las siguientes."""
        producto = _crear_producto_backfill(session)
        ingestion = _crear_ingestion_backfill(session, hash_="rel-13")
        fila = _crear_precio_relleno(session, producto, ingestion)
        _crear_precio_relleno(
            session, producto, ingestion, published_date=date(2026, 10, 6)
        )
        llamadas: list[tuple[str, date]] = []

        def fetcher(key: str, day: date) -> Decimal | None:
            llamadas.append((key, day))
            raise ElToqueUnavailable("429")

        reporte = run_backfill(
            session, fetcher, apply=True, sleep=lambda _: None, retries=2
        )

        session.expire_all()
        recuperada = session.get(PriceHistory, fila.id)
        assert recuperada.exchange_rate_source == _RELLENO
        assert recuperada.price_cup == Decimal("6900.00")
        assert reporte.aborted is not None
        assert reporte.unresolved_rows == []
        assert (reporte.examined, reporte.resolved, reporte.unresolved) == (1, 0, 0)
        assert llamadas == [("USD", _ANCLA_RELLENO)] * 3  # el intento y dos más

    def test_dry_run_no_escribe_respaldo(self, session, tmp_path) -> None:
        producto = _crear_producto_backfill(session)
        ingestion = _crear_ingestion_backfill(session, hash_="rel-11")
        _crear_precio_relleno(session, producto, ingestion)
        respaldo = tmp_path / "respaldo.csv"

        run_backfill(
            session,
            _BackfillFetcher(),
            apply=False,
            backup_path=respaldo,
            sleep=lambda _: None,
        )

        assert not respaldo.exists()
