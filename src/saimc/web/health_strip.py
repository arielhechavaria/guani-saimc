"""La franja de estado que abre `/control`: qué está roto AHORA.

`/control` creció sección por sección hasta tener once, todas con el mismo
peso visual. Eso lo vuelve un informe largo, no un panel: para saber si algo
se rompió hay que leerlo entero. Esta franja invierte el orden — arriba lo que
exige acción hoy, abajo el detalle de siempre.

Cada indicador dice tres cosas y ninguna más: **qué** se mide, **cómo está**
(un nivel, que la vista pinta) y **qué hacer** si está mal. Un semáforo sin
instrucción obliga a adivinar, que es el problema que vino a resolver.

Los niveles, de peor a mejor:

- `down`  — roto ahora, y algo dejó de funcionar por eso.
- `warn`  — todavía anda, pero envejece o se acerca a un límite.
- `ok`    — sano.
- `off`   — no configurado. **No es una falla**: distinguirlo evita pintar de
            rojo una capacidad que el operador todavía no usa.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from saimc.exchange_rate.status import RateStatus
from saimc.extraction.llm_assist import ConsumptionSummary
from saimc.extraction.prefill_status import PrefillRun
from saimc.ingestion.channel_gaps import GapReport
from saimc.ingestion.whatsapp_health import SessionHealth
from saimc.progress import IntakeHealth

# Cuántos días sin que entre nada empiezan a ser sospechosos. No es un umbral
# de los que §12.6 prohíbe —no decide si un dato es válido ni si se publica—:
# solo elige cuándo llamarle la atención al operador. Dos días cubre un fin de
# semana sin captura sin gritar por nada.
_STALE_INTAKE_DAYS = 2

# Lo mismo para la tasa: §12.6 prohíbe fijar una ventana de validez por
# intuición y presentarla como metodología, así que esto NO decide si la tasa
# sirve. Decide cuándo mostrarla en ámbar.
_STALE_RATE_DAYS = 2

# Cada cuántas horas corre el temporizador del pre-cálculo. DEBE coincidir con
# `OnCalendar` en `systemd/saimc-prefill.timer`; si se cambia uno, se cambia el
# otro. La franja pasa a ámbar cuando la última corrida es más vieja que
# `_PREFILL_STALE_FACTOR` intervalos: una corrida que se salteó se tolera, dos
# seguidas (o un proceso que murió sin dejar registro) ya son aviso.
PREFILL_INTERVAL_HOURS = 3
_PREFILL_STALE_FACTOR = 2


@dataclass(frozen=True)
class Indicator:
    """Una tarjeta de la franja."""

    key: str
    label: str
    value: str
    level: str
    # Qué hacer si está mal. Vacío cuando no hay nada que hacer.
    hint: str = ""
    # Ancla a la sección con el detalle, para que la franja no sea un callejón
    # sin salida.
    anchor: str = ""


def _days_since(moment: datetime | None, *, now: datetime) -> int | None:
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return (now - moment).days


def waha_indicator(health: SessionHealth) -> Indicator:
    """Estado de la ingesta automática por WhatsApp (hueco N-02).

    Es el indicador que motivó la franja: la sesión ya se cayó una vez y la
    ingesta paró en silencio, sin más síntoma que una cola que dejaba de
    crecer.
    """
    if not health.configured:
        return Indicator(
            key="waha",
            label="WhatsApp",
            value="sin configurar",
            level="off",
            hint="No hay canales automatizados. La bandeja y el pegado siguen igual.",
        )
    return Indicator(
        key="waha",
        label="WhatsApp",
        value=health.label,
        level="ok" if health.healthy else "down",
        hint="" if health.healthy else health.hint,
        anchor="#ingesta",
    )


def _gap_hint(gaps: GapReport) -> str:
    """El texto del aviso por canal (K4), para la tarjeta de ingesta."""
    if gaps.all_silent:
        return f"{gaps.headline}. {gaps.hint}"
    detalle = "; ".join(
        f"{c.name} ({int(c.silence_hours)} h, aviso a las {round(c.threshold_hours)} h)"
        for c in gaps.silent
    )
    return f"Canales sin mensajes: {detalle}."


def intake_indicator(
    last_capture: datetime | None,
    health: IntakeHealth,
    *,
    now: datetime,
    gaps: GapReport | None = None,
) -> Indicator:
    """Hace cuánto que no entra nada, y cuánto hay esperando.

    Con `gaps` (K4) el nivel y el aviso salen de la regla por canal, que es la
    que sabe cuándo un silencio es raro para ese canal: `down` si no entra nada
    por ningún canal activo, `warn` si calla alguno. Es el mismo hecho que el
    umbral global de 2 días, así que no hay una segunda tarjeta que pueda
    contradecirlo; el umbral global queda solo como respaldo cuando la regla
    no tiene nada que decir (p. ej. entra solo captura manual).
    """
    base = _plain_intake_indicator(last_capture, now=now)
    if gaps is None or not gaps.silent:
        return base
    return Indicator(
        key=base.key,
        label=base.label,
        value=base.value,
        level="down" if gaps.all_silent else "warn",
        hint=_gap_hint(gaps),
        anchor=base.anchor,
    )


def _plain_intake_indicator(last_capture: datetime | None, *, now: datetime) -> Indicator:
    dias = _days_since(last_capture, now=now)

    if dias is None:
        return Indicator(
            key="intake",
            label="Última captura",
            value="nunca",
            level="warn",
            hint="Todavía no entró ningún anuncio.",
            anchor="#ingesta",
        )

    if dias == 0:
        valor = "hoy"
    elif dias == 1:
        valor = "ayer"
    else:
        valor = f"hace {dias} días"

    return Indicator(
        key="intake",
        label="Última captura",
        value=valor,
        level="warn" if dias >= _STALE_INTAKE_DAYS else "ok",
        hint=(
            "Hace días que no entra nada. Si esperabas que entrara, revisá la "
            "fuente."
            if dias >= _STALE_INTAKE_DAYS
            else ""
        ),
        anchor="#ingesta",
    )


def queue_indicator(health: IntakeHealth) -> Indicator:
    """Cuánto hay por curar. Sin umbral: es una medición, no un veredicto."""
    return Indicator(
        key="queue",
        label="Por curar",
        value=str(health.pending),
        level="ok",
        anchor="#ingesta",
    )


def rate_indicator(statuses: list[RateStatus], *, now: datetime) -> Indicator:
    """La tasa más vieja de las que hay, que es la que manda."""
    con_fecha = [s for s in statuses if s.age_days is not None]
    if not con_fecha:
        return Indicator(
            key="rate",
            label="Tasa de cambio",
            value="sin datos",
            level="warn",
            hint="No hay ninguna tasa cacheada todavía.",
            anchor="#tasa",
        )

    peor = max(con_fecha, key=lambda s: s.age_days or 0)
    edad = peor.age_days or 0
    return Indicator(
        key="rate",
        label="Tasa de cambio",
        value="al día" if edad == 0 else f"hace {edad} d",
        level="warn" if edad >= _STALE_RATE_DAYS else "ok",
        hint=(
            f"La tasa de {peor.currency} tiene {edad} días. Se sigue usando la "
            "última conocida."
            if edad >= _STALE_RATE_DAYS
            else ""
        ),
        anchor="#tasa",
    )


def assist_indicator(assist: ConsumptionSummary, *, configured: bool) -> Indicator:
    """Estado del asistente por LLM: apagado, andando, o sin saldo."""
    if not configured:
        return Indicator(
            key="assist",
            label="Asistente",
            value="apagado",
            level="off",
            hint="Sin clave configurada. La curaduría funciona igual, a mano.",
        )
    if assist.out_of_credit:
        return Indicator(
            key="assist",
            label="Asistente",
            value="sin saldo",
            level="down",
            hint="La última llamada falló por saldo. Cargá créditos.",
            anchor="#asistente",
        )
    return Indicator(
        key="assist",
        label="Asistente",
        value=f"${assist.estimated_usd:.2f}",
        level="ok",
        anchor="#asistente",
    )


def prefill_indicator(run: PrefillRun | None, *, now: datetime) -> Indicator:
    """El temporizador del pre-cálculo: si falló o si dejó de correr.

    Es el aviso de que algo falló sin que nadie mire (slice C1). Falla manda
    sobre antigüedad: una corrida que falló sigue en rojo hasta que otra salga
    bien. Sin registro es ámbar y no rojo: el temporizador puede no estar
    instalado todavía.
    """
    if run is None:
        return Indicator(
            key="prefill",
            label="Pre-cálculo",
            value="sin registro",
            level="warn",
            hint=(
                "Todavía no hay ninguna corrida registrada. Si el temporizador "
                "está instalado: systemctl status saimc-prefill.timer."
            ),
        )

    terminó = run.finished_at
    if terminó.tzinfo is None:
        terminó = terminó.replace(tzinfo=UTC)

    if run.exit_code != 0:
        causa = {
            "credit": "Se frenó por falta de saldo en la API: cargá créditos.",
            # Qué variable es depende del proveedor: lo dice el comando.
            "auth": "La API rechazó la clave: python -m saimc.llm.provider dice de qué variable sale.",
        }.get(run.aborted_failure, "")
        return Indicator(
            key="prefill",
            label="Pre-cálculo",
            value=f"falló (salida {run.exit_code}) el {terminó:%d/%m %H:%M} UTC",
            level="down",
            hint=(
                f"{causa} Detalle: journalctl -u saimc-prefill.service."
            ).strip(),
        )

    horas = int((now - terminó) / timedelta(hours=1))
    if now - terminó > timedelta(hours=PREFILL_INTERVAL_HOURS * _PREFILL_STALE_FACTOR):
        return Indicator(
            key="prefill",
            label="Pre-cálculo",
            value=f"sin correr hace {horas} h",
            level="warn",
            hint=(
                f"Debería correr cada {PREFILL_INTERVAL_HOURS} h. Revisá "
                "systemctl list-timers saimc-prefill.timer y "
                "journalctl -u saimc-prefill.service."
            ),
        )

    return Indicator(
        key="prefill",
        label="Pre-cálculo",
        value=f"ok, hace {horas} h",
        level="ok",
    )


# Cortes de la pasada de Jev que el operador tiene que arreglar a mano, con qué
# hacer en cada uno. Los valores son los de `validation.jev_signal.STOP_*`. El
# tope de gasto y los fallos de red no están: se resuelven solos en la corrida
# siguiente.
_JEV_STOP_HINTS = {
    "auth": "El gateway rechazó la clave: revisá SAIMC_VERCEL_API_KEY.",
    "forbidden": (
        "La clave autentica pero el gateway denegó el acceso (plan o modelo). "
        "El motivo está en journalctl -u saimc-prefill.service."
    ),
    "rate_limit": "Se agotó el cupo de pedidos del gateway. La próxima corrida reintenta.",
}
_JEV_STOP_VALUES = {
    "auth": "clave rechazada",
    "forbidden": "acceso denegado",
    "rate_limit": "sin cupo",
}


def jev_indicator(run: PrefillRun | None, *, active: bool) -> Indicator | None:
    """La señal en sombra de Jev (A6b), solo cuando hay algo que arreglar.

    Devuelve `None` —la tarjeta no existe— salvo que Jev esté activo y la
    última corrida haya cortado por la clave, el acceso o el cupo. Es ámbar y
    nunca rojo: Jev solo anota, así que nada dejó de funcionar por esto.
    """
    if not active or run is None or run.jev_stopped not in _JEV_STOP_HINTS:
        return None
    return Indicator(
        key="jev",
        label="Jev (sombra)",
        value=_JEV_STOP_VALUES[run.jev_stopped],
        level="warn",
        hint=_JEV_STOP_HINTS[run.jev_stopped],
        anchor="#jev",
    )


def build_strip(
    *,
    waha: SessionHealth,
    last_capture: datetime | None,
    health: IntakeHealth,
    rate_statuses: list[RateStatus],
    assist: ConsumptionSummary,
    assist_configured: bool,
    prefill_run: PrefillRun | None,
    now: datetime | None = None,
    channel_gaps: GapReport | None = None,
    jev_active: bool = False,
) -> list[Indicator]:
    """La franja completa, con lo más urgente primero.

    El orden no es fijo: se ordena por gravedad, así que lo roto sube solo. En
    un día sano la franja se lee de un vistazo y no exige nada; en un día roto,
    lo que hay que arreglar está arriba a la izquierda sin buscarlo.
    """
    ahora = now or datetime.now(UTC)
    indicadores = [
        waha_indicator(waha),
        intake_indicator(last_capture, health, now=ahora, gaps=channel_gaps),
        queue_indicator(health),
        rate_indicator(rate_statuses, now=ahora),
        assist_indicator(assist, configured=assist_configured),
        prefill_indicator(prefill_run, now=ahora),
    ]
    jev = jev_indicator(prefill_run, active=jev_active)
    if jev is not None:
        indicadores.append(jev)
    prioridad = {"down": 0, "warn": 1, "ok": 2, "off": 3}
    return sorted(indicadores, key=lambda i: prioridad.get(i.level, 9))


def worst_level(indicators: list[Indicator]) -> str:
    """El peor nivel de la franja: `down`, `warn` u `ok`.

    Sirve para que la vista pueda decir «algo está roto» de una sola vez, sin
    que el operador tenga que escanear las tarjetas. `off` no cuenta como
    problema — es una capacidad sin usar, no una rota.
    """
    for nivel in ("down", "warn"):
        if any(i.level == nivel for i in indicators):
            return nivel
    return "ok"
