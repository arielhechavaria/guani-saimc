"""`saimc.web.health_strip` — la franja de estado que abre `/control`.

Lo que se prueba acá es el criterio: qué se pinta rojo, qué ámbar, qué queda
apagado sin alarmar, y que lo roto suba solo al principio. Es puro, sin base
ni red.
"""

from datetime import UTC, datetime, timedelta

from saimc.exchange_rate.status import RateStatus
from saimc.extraction.llm_assist import ConsumptionSummary
from saimc.ingestion.whatsapp_health import SessionHealth
from saimc.progress import IntakeHealth
from saimc.web import health_strip

AHORA = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def _assist(**campos: object) -> ConsumptionSummary:
    base: dict = {
        "calls": 3,
        "input_tokens": 100,
        "output_tokens": 10,
        "estimated_usd": 1.23,
        "untariffed_calls": 0,
        "failures": {},
        "out_of_credit": False,
    }
    base.update(campos)
    return ConsumptionSummary(**base)


def _health(pending: int = 10) -> IntakeHealth:
    return IntakeHealth(pending=pending, curated=100, discarded=50)


class TestWaha:
    """El indicador que motivó la franja (hueco N-02)."""

    def test_una_sesion_trabajando_esta_verde(self) -> None:
        ind = health_strip.waha_indicator(
            SessionHealth(configured=True, status="working")
        )

        assert ind.level == "ok"
        assert ind.value == "conectado"

    def test_una_sesion_caida_esta_roja_y_dice_que_hacer(self) -> None:
        ind = health_strip.waha_indicator(
            SessionHealth(configured=True, status="failed")
        )

        assert ind.level == "down"
        assert "reiniciala" in ind.hint.lower()

    def test_waha_que_no_responde_esta_rojo(self) -> None:
        ind = health_strip.waha_indicator(
            SessionHealth(configured=True, status="unreachable")
        )

        assert ind.level == "down"

    def test_sin_configurar_no_alarma(self) -> None:
        """No es una falla: es una capacidad que el operador no usa. Pintarla
        de rojo entrenaría a ignorar el rojo."""
        ind = health_strip.waha_indicator(
            SessionHealth(configured=False, status="unknown")
        )

        assert ind.level == "off"


class TestUltimaCaptura:
    def test_algo_de_hoy_esta_verde(self) -> None:
        ind = health_strip.intake_indicator(
            AHORA - timedelta(hours=3), _health(), now=AHORA
        )

        assert ind.level == "ok"
        assert ind.value == "hoy"

    def test_de_ayer_sigue_verde(self) -> None:
        ind = health_strip.intake_indicator(
            AHORA - timedelta(days=1), _health(), now=AHORA
        )

        assert ind.value == "ayer"
        assert ind.level == "ok"

    def test_varios_dias_sin_nada_avisa(self) -> None:
        ind = health_strip.intake_indicator(
            AHORA - timedelta(days=4), _health(), now=AHORA
        )

        assert ind.level == "warn"
        assert "4 días" in ind.value

    def test_sin_ninguna_captura_avisa(self) -> None:
        ind = health_strip.intake_indicator(None, _health(), now=AHORA)

        assert ind.level == "warn"
        assert ind.value == "nunca"

    def test_una_fecha_sin_zona_no_rompe(self) -> None:
        """SQLite devuelve datetimes naive en algunos caminos; asumirlos UTC
        es mejor que reventar la vista entera."""
        ind = health_strip.intake_indicator(
            datetime(2026, 9, 1, 9, 0), _health(), now=AHORA
        )

        assert ind.level == "ok"


class TestTasa:
    def _rate(self, currency: str, age: int | None) -> RateStatus:
        return RateStatus(
            currency=currency,
            rate=None,
            rate_date=None,
            source=None,
            fetched_at=None,
            age_days=age,
            has_today=age == 0,
        )

    def test_al_dia_esta_verde(self) -> None:
        ind = health_strip.rate_indicator([self._rate("USD", 0)], now=AHORA)

        assert ind.level == "ok"
        assert ind.value == "al día"

    def test_manda_la_tasa_mas_vieja(self) -> None:
        """Si una moneda está al día y otra tiene una semana, la franja tiene
        que mostrar el problema, no el promedio."""
        ind = health_strip.rate_indicator(
            [self._rate("USD", 0), self._rate("EUR", 7)], now=AHORA
        )

        assert ind.level == "warn"
        assert "EUR" in ind.hint

    def test_sin_ninguna_tasa_avisa(self) -> None:
        ind = health_strip.rate_indicator([], now=AHORA)

        assert ind.level == "warn"


class TestAsistente:
    def test_sin_clave_queda_apagado_sin_alarmar(self) -> None:
        ind = health_strip.assist_indicator(_assist(), configured=False)

        assert ind.level == "off"

    def test_sin_saldo_esta_rojo(self) -> None:
        ind = health_strip.assist_indicator(
            _assist(out_of_credit=True), configured=True
        )

        assert ind.level == "down"
        assert "saldo" in ind.hint.lower()

    def test_andando_muestra_el_gasto(self) -> None:
        ind = health_strip.assist_indicator(
            _assist(estimated_usd=2.5), configured=True
        )

        assert ind.level == "ok"
        assert "2.50" in ind.value


class TestOrdenYResumen:
    def _strip(self, **overrides: object) -> list[health_strip.Indicator]:
        base: dict = {
            "waha": SessionHealth(configured=True, status="working"),
            "last_capture": AHORA,
            "health": _health(),
            "rate_statuses": [
                RateStatus("USD", None, None, None, None, 0, True)
            ],
            "assist": _assist(),
            "assist_configured": True,
            "now": AHORA,
        }
        base.update(overrides)
        return health_strip.build_strip(**base)

    def test_lo_roto_sube_solo_al_principio(self) -> None:
        """El orden lo decide la gravedad, no el catálogo: en un día roto lo
        que hay que arreglar está arriba sin buscarlo."""
        franja = self._strip(
            waha=SessionHealth(configured=True, status="failed")
        )

        assert franja[0].key == "waha"
        assert franja[0].level == "down"

    def test_un_dia_sano_no_tiene_nada_rojo(self) -> None:
        franja = self._strip()

        assert health_strip.worst_level(franja) == "ok"

    def test_lo_apagado_va_al_final(self) -> None:
        franja = self._strip(assist_configured=False)

        assert franja[-1].key == "assist"

    def test_el_peor_nivel_resume_la_franja(self) -> None:
        franja = self._strip(waha=SessionHealth(configured=True, status="failed"))

        assert health_strip.worst_level(franja) == "down"

    def test_lo_no_configurado_no_cuenta_como_problema(self) -> None:
        """Una capacidad sin usar no puede hacer que el panel diga que algo
        anda mal: sería entrenar al operador a ignorar el aviso."""
        franja = self._strip(
            waha=SessionHealth(configured=False, status="unknown"),
            assist_configured=False,
        )

        assert health_strip.worst_level(franja) == "ok"

    def test_estan_los_cinco_indicadores(self) -> None:
        assert {i.key for i in self._strip()} == {
            "waha",
            "intake",
            "queue",
            "rate",
            "assist",
        }
