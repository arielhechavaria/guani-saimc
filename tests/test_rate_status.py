"""Visibilidad del estado de la tasa de cambio — casos sintéticos.

Responde dos preguntas distintas que el operador hace como si fueran una:

1. **¿Qué tasa tiene el sistema disponible ahora?** (`compute_rate_status`) —
   estado del cache, por moneda.
2. **¿Qué tasa quedó aplicada en lo ya cargado?** (`compute_applied_rates`) —
   lo que efectivamente se escribió en `prices_history`.

Una tasa cacheada vieja no significa que lo curado esté mal: significa que lo
que se cure hoy va a salir con esa tasa si elTOQUE no responde.

**Ninguna de las dos emite veredicto de vigencia.** `spec.md` §12.6 lista la
«ventana de validez de la tasa de cambio» como parámetro pendiente de
calibración, sin valor asignado — igual que `service.py` no inventa una
ventana al degradar al paso 3, acá se reporta la antigüedad en días y nada
más.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from saimc.exchange_rate.status import compute_applied_rates, compute_rate_status
from saimc.models import (
    Currency,
    ExchangeRateCache,
    PriceHistory,
    Product,
    Province,
    RawIngestion,
)
from saimc.models.raw_ingestion import ContentType, ProcessingStatus, SourceType


def _cachear(
    session: Session,
    *,
    currency: str,
    rate_date: date,
    rate: str,
    source: str = "elTOQUE",
    fetched_at: datetime | None = None,
) -> None:
    session.add(
        ExchangeRateCache(
            currency=currency,
            rate_date=rate_date,
            rate=Decimal(rate),
            source=source,
            fetched_at=fetched_at or datetime(2026, 8, 13, 13, 52, tzinfo=UTC),
        )
    )
    session.commit()


class TestRateStatus:
    def test_reporta_la_ultima_tasa_por_moneda_con_su_antiguedad(
        self, session: Session
    ) -> None:
        """670 del 12/08 leída el 13/08: un día de antigüedad."""
        _cachear(session, currency="USD", rate_date=date(2026, 8, 12), rate="670")

        status = {s.currency: s for s in compute_rate_status(session, hoy=date(2026, 8, 13))}

        usd = status["USD"]
        assert usd.rate == Decimal("670")
        assert usd.rate_date == date(2026, 8, 12)
        assert usd.source == "elTOQUE"
        assert usd.age_days == 1

    def test_marca_si_hay_tasa_del_dia_pedido(self, session: Session) -> None:
        """Con tasa del día, curar no dispara fetch (paso 1 de `RateResolver`);
        sin ella, el primer anuncio del día sale a la red."""
        _cachear(session, currency="USD", rate_date=date(2026, 8, 13), rate="675")

        status = {s.currency: s for s in compute_rate_status(session, hoy=date(2026, 8, 13))}

        assert status["USD"].has_today is True
        assert status["USD"].age_days == 0

    def test_sin_tasa_del_dia_lo_dice(self, session: Session) -> None:
        _cachear(session, currency="USD", rate_date=date(2026, 8, 12), rate="670")

        status = {s.currency: s for s in compute_rate_status(session, hoy=date(2026, 8, 13))}

        assert status["USD"].has_today is False

    def test_toma_la_mas_reciente_por_fecha_no_por_lectura(
        self, session: Session
    ) -> None:
        """Mismo criterio que `cache.latest`: un backfill que escribe hoy una
        tasa vieja no debe pasar por «la última conocida»."""
        _cachear(
            session,
            currency="USD",
            rate_date=date(2026, 8, 12),
            rate="670",
            fetched_at=datetime(2026, 8, 12, 10, 0, tzinfo=UTC),
        )
        _cachear(
            session,
            currency="USD",
            rate_date=date(2026, 8, 1),
            rate="600",
            fetched_at=datetime(2026, 8, 13, 23, 0, tzinfo=UTC),
        )

        status = {s.currency: s for s in compute_rate_status(session, hoy=date(2026, 8, 13))}

        assert status["USD"].rate == Decimal("670")
        assert status["USD"].rate_date == date(2026, 8, 12)

    def test_devuelve_las_monedas_sin_cache_tambien(self, session: Session) -> None:
        """El hueco es el dato: una moneda sin tasa alguna significa que un
        anuncio en esa moneda se guardaría sin convertir si elTOQUE falla."""
        _cachear(session, currency="USD", rate_date=date(2026, 8, 12), rate="670")

        status = {s.currency: s for s in compute_rate_status(session, hoy=date(2026, 8, 13))}

        # Las tres monedas extranjeras del enum, nunca CUP (no se convierte
        # a sí misma).
        assert set(status) == {"USD", "EUR", "MLC"}
        assert status["EUR"].rate is None
        assert status["EUR"].rate_date is None
        assert status["EUR"].age_days is None
        assert status["EUR"].has_today is False

    def test_base_vacia_no_rompe(self, session: Session) -> None:
        status = compute_rate_status(session, hoy=date(2026, 8, 13))

        assert len(status) == 3
        assert all(s.rate is None for s in status)


class TestAppliedRates:
    def _fila(
        self,
        session: Session,
        *,
        price: str,
        currency: str,
        rate: str | None,
        rate_date: date | None,
        source: str | None,
    ) -> None:
        producto = session.query(Product).first()
        if producto is None:
            producto = Product(name="Arroz", category="granos", unit="lb", aliases=[])
            provincia = Province(code="scu", name="Santiago de Cuba")
            origen = RawIngestion(
                source_type=SourceType.INBOX_TEXT,
                file_path="raw/tasa.txt",
                content_hash="hash-tasa",
                content_type=ContentType.TEXT,
                processing_status=ProcessingStatus.CURATED,
                captured_at=datetime(2026, 8, 1, tzinfo=UTC),
            )
            session.add_all([producto, provincia, origen])
            session.commit()

        origen = session.query(RawIngestion).first()
        provincia = session.query(Province).first()
        session.add(
            PriceHistory(
                provenance="human",
                product_id=producto.id,
                province_id=provincia.id,
                price=Decimal(price),
                currency=currency,
                source_id=origen.id,
                exchange_rate=Decimal(rate) if rate is not None else None,
                exchange_rate_date=rate_date,
                exchange_rate_source=source,
                price_cup=Decimal(price) * Decimal(rate) if rate is not None else None,
            )
        )
        session.commit()

    def test_agrupa_lo_aplicado_por_tasa_fecha_y_fuente(self, session: Session) -> None:
        for _ in range(3):
            self._fila(
                session,
                price="10",
                currency=Currency.USD.value,
                rate="670",
                rate_date=date(2026, 8, 12),
                source="elTOQUE",
            )
        self._fila(
            session,
            price="10",
            currency=Currency.USD.value,
            rate="600",
            rate_date=date(2026, 8, 1),
            source="elTOQUE (cache)",
        )

        aplicadas = compute_applied_rates(session)

        assert [(a.rate, a.rate_date, a.source, a.rows) for a in aplicadas] == [
            (Decimal("670"), date(2026, 8, 12), "elTOQUE", 3),
            (Decimal("600"), date(2026, 8, 1), "elTOQUE (cache)", 1),
        ]

    def test_ignora_las_filas_sin_conversion(self, session: Session) -> None:
        """Una fila en CUP no tiene tasa aplicada, y una sin resolver tampoco:
        ninguna de las dos describe «qué tasa usó el sistema»."""
        self._fila(
            session,
            price="500",
            currency=Currency.CUP.value,
            rate=None,
            rate_date=None,
            source=None,
        )
        self._fila(
            session,
            price="10",
            currency=Currency.USD.value,
            rate=None,
            rate_date=None,
            source=None,
        )

        assert compute_applied_rates(session) == []

    def test_base_vacia_no_rompe(self, session: Session) -> None:
        assert compute_applied_rates(session) == []
