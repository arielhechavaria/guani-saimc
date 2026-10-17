"""`saimc.web.health_strip` — la franja de estado que abre `/control`.

Lo que se prueba acá es el criterio: qué se pinta rojo, qué ámbar, qué queda
apagado sin alarmar, y que lo roto suba solo al principio. Es puro, sin base
ni red.
"""

from datetime import UTC, datetime, timedelta

from saimc.exchange_rate.status import RateStatus
from saimc.extraction.llm_assist import ConsumptionSummary
from saimc.extraction.prefill_status import PrefillRun
from saimc.ingestion.whatsapp_health import SessionHealth
from saimc.progress import IntakeHealth
from saimc.validation import lane1
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


def _corrida(**campos: object) -> PrefillRun:
    base: dict = {
        "finished_at": AHORA - timedelta(hours=1),
        "exit_code": 0,
        "done": 5,
        "degraded": 0,
        "skipped_no_text": 0,
        "cost_usd": 0.04,
        "stopped_by_max_cost": False,
        "aborted_failure": "",
    }
    base.update(campos)
    return PrefillRun(**base)


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


class TestPrefill:
    """«Pre-cálculo»: el aviso de que el temporizador falló o dejó de correr."""

    def test_sin_registro_es_ambar_y_no_roja(self) -> None:
        ind = health_strip.prefill_indicator(None, now=AHORA)

        assert ind.level == "warn"
        assert ind.value == "sin registro"

    def test_la_ultima_corrida_bien_esta_verde(self) -> None:
        ind = health_strip.prefill_indicator(_corrida(), now=AHORA)

        assert ind.level == "ok"
        assert ind.value == "ok, hace 1 h"

    def test_una_corrida_con_codigo_distinto_de_cero_esta_roja_con_la_hora(self) -> None:
        ind = health_strip.prefill_indicator(
            _corrida(exit_code=1, aborted_failure="credit"), now=AHORA
        )

        assert ind.level == "down"
        assert ind.value == "falló (salida 1) el 01/09 11:00 UTC"
        assert "saldo" in ind.hint.lower()
        assert "journalctl -u saimc-prefill.service" in ind.hint

    def test_una_corrida_frenada_por_la_clave_manda_al_comando_de_comprobacion(self) -> None:
        ind = health_strip.prefill_indicator(
            _corrida(exit_code=1, aborted_failure="auth"), now=AHORA
        )

        assert "rechazó la clave" in ind.hint
        assert "python -m saimc.llm.provider" in ind.hint
        # La plantilla imprime texto plano: un backtick se vería literal.
        assert "`" not in ind.hint

    def test_la_falla_manda_aunque_la_corrida_sea_vieja(self) -> None:
        ind = health_strip.prefill_indicator(
            _corrida(exit_code=2, finished_at=AHORA - timedelta(hours=30)), now=AHORA
        )

        assert ind.level == "down"

    def test_justo_en_el_doble_del_intervalo_todavia_esta_verde(self) -> None:
        ind = health_strip.prefill_indicator(
            _corrida(finished_at=AHORA - timedelta(hours=6)), now=AHORA
        )

        assert ind.level == "ok"

    def test_pasado_el_doble_del_intervalo_esta_ambar_con_las_horas(self) -> None:
        ind = health_strip.prefill_indicator(
            _corrida(finished_at=AHORA - timedelta(hours=7, minutes=10)), now=AHORA
        )

        assert ind.level == "warn"
        assert ind.value == "sin correr hace 7 h"

    def test_un_registro_sin_zona_horaria_se_lee_como_utc(self) -> None:
        ind = health_strip.prefill_indicator(
            _corrida(finished_at=datetime(2026, 9, 1, 11, 0)), now=AHORA
        )

        assert ind.level == "ok"


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
            "prefill_run": _corrida(),
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

    def test_estan_los_seis_indicadores(self) -> None:
        assert {i.key for i in self._strip()} == {
            "waha",
            "intake",
            "queue",
            "rate",
            "assist",
            "prefill",
        }


class TestJev:
    """El indicador de la señal en sombra (A6b-1): aparece solo cuando Jev está
    activo y la última corrida cortó por algo que el operador tiene que
    arreglar. Es ámbar y nunca rojo: Jev no decide nada."""

    def test_un_corte_por_la_clave_es_ambar_y_lleva_a_la_seccion(self) -> None:
        ind = health_strip.jev_indicator(_corrida(jev_stopped="auth"), active=True)

        assert ind is not None
        assert (ind.key, ind.level, ind.anchor) == ("jev", "warn", "#jev")
        assert "SAIMC_VERCEL_API_KEY" in ind.hint

    def test_acceso_denegado_y_limite_de_pedidos_tambien_avisan(self) -> None:
        for corte in ("forbidden", "rate_limit"):
            ind = health_strip.jev_indicator(_corrida(jev_stopped=corte), active=True)

            assert ind is not None and ind.level == "warn"

    def test_el_tope_y_los_fallos_de_red_no_avisan(self) -> None:
        for corte in ("", "max_cost", "failures"):
            assert health_strip.jev_indicator(_corrida(jev_stopped=corte), active=True) is None

    def test_con_jev_inactivo_no_hay_indicador(self) -> None:
        assert health_strip.jev_indicator(_corrida(jev_stopped="auth"), active=False) is None

    def test_sin_registro_no_hay_indicador(self) -> None:
        assert health_strip.jev_indicator(None, active=True) is None

    def test_la_franja_lo_suma_solo_cuando_corresponde(self) -> None:
        base: dict = {
            "waha": SessionHealth(configured=True, status="working"),
            "last_capture": AHORA,
            "health": _health(),
            "rate_statuses": [RateStatus("USD", None, None, None, None, 0, True)],
            "assist": _assist(),
            "assist_configured": True,
            "now": AHORA,
        }

        sana = health_strip.build_strip(**base, prefill_run=_corrida(), jev_active=True)
        cortada = health_strip.build_strip(
            **base, prefill_run=_corrida(jev_stopped="auth"), jev_active=True
        )

        assert "jev" not in {i.key for i in sana}
        assert "jev" in {i.key for i in cortada}
        assert health_strip.worst_level(cortada) == "warn"


class TestAuditoriaDelCarril1:
    """Avisa cuando la medición se queda sin datos (decisions.md 2026-10-13: sin
    auditar, el freno nunca salta). El total sin auditar se muestra pero no
    alarma: es permanente. Alarma lo que TOCA auditar y espera."""

    def test_el_total_se_muestra_aparte_y_sin_nada_por_auditar_esta_ok(self) -> None:
        ind = health_strip.lane1_audit_indicator(
            lane1.AuditPending(total=225, due=0, starting=False, waiting_hours=None)
        )

        assert (ind.key, ind.level) == ("lane1_audit", "ok")
        assert "225 aceptados sin auditar" in ind.value

    def test_en_el_arranque_avisa_con_el_mas_viejo_sin_auditar_a_las_72_horas(self) -> None:
        def nivel(horas: int) -> str:
            return health_strip.lane1_audit_indicator(
                lane1.AuditPending(total=40, due=40, starting=True, waiting_hours=horas)
            ).level

        assert (nivel(71), nivel(72)) == ("ok", "warn")

    def test_despues_del_arranque_avisa_con_la_muestra_pendiente_a_las_48_horas(self) -> None:
        def nivel(horas: int) -> str:
            return health_strip.lane1_audit_indicator(
                lane1.AuditPending(total=225, due=30, starting=False, waiting_hours=horas)
            ).level

        assert (nivel(47), nivel(48)) == ("ok", "warn")

    def test_el_aviso_dice_donde_auditar_y_nunca_es_rojo(self) -> None:
        ind = health_strip.lane1_audit_indicator(
            lane1.AuditPending(total=225, due=30, starting=False, waiting_hours=500)
        )

        assert ind.level == "warn"
        assert "/curation/auditoria" in ind.hint

    def test_la_franja_lo_suma_solo_si_el_carril_tiene_algo_que_contar(self) -> None:
        base: dict = {
            "waha": SessionHealth(configured=True, status="working"),
            "last_capture": AHORA,
            "health": _health(),
            "rate_statuses": [RateStatus("USD", None, None, None, None, 0, True)],
            "assist": _assist(),
            "assist_configured": True,
            "prefill_run": _corrida(),
            "now": AHORA,
        }
        pendiente = lane1.AuditPending(total=5, due=5, starting=True, waiting_hours=100)

        sin = health_strip.build_strip(**base)
        con = health_strip.build_strip(**base, lane1_audit=pendiente)

        assert "lane1_audit" not in {i.key for i in sin}
        assert "lane1_audit" in {i.key for i in con}
