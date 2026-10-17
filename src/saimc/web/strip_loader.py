"""Arma la franja de estado desde la base y el entorno.

Lo usan `/control` (cada subpágina) y los avisos al celular (`saimc.alerting`):
una sola forma de leer el estado, para que lo que el operador ve en pantalla y
lo que le llega por Telegram nunca discrepen.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from saimc.config import Settings
from saimc.exchange_rate.status import RateStatus, compute_rate_status
from saimc.extraction.llm_assist import ConsumptionSummary, consumption_summary
from saimc.extraction.prefill_status import PrefillRun, read_run_status
from saimc.ingestion.channel_gaps import load_channel_gap_report
from saimc.ingestion.whatsapp_health import session_health
from saimc.llm.provider import try_resolve_config
from saimc.models import RawIngestion
from saimc.progress import IntakeHealth, compute_intake_health, last_24h
from saimc.validation import jev_signal, lane1
from saimc.web.health_strip import Indicator, build_strip


@dataclass(frozen=True)
class StripSnapshot:
    """La franja y los insumos que `/control` reusa para otras secciones."""

    strip: list[Indicator]
    intake: IntakeHealth
    rate_statuses: list[RateStatus]
    prefill_run: PrefillRun | None
    assist: ConsumptionSummary


def load_strip(db: Session, settings: Settings) -> StripSnapshot:
    """La franja ordenada por gravedad, con lo que la alimentó.

    El estado de WAHA es la única señal que sale de la red: corre en el mismo
    VPS y el timeout es corto. Si no contesta, eso ES el dato (hueco N-02).
    """
    waha = session_health(
        base_url=settings.waha_base_url,
        api_key=settings.waha_api_key,
        session=settings.waha_session,
    )
    ultima_captura = db.execute(select(func.max(RawIngestion.captured_at))).scalar_one()
    intake = compute_intake_health(db)
    rate_statuses = compute_rate_status(db)
    prefill_run = read_run_status(settings.prefill_status_path)
    assist = consumption_summary(settings.llm_assist_log_path)
    # Solo si el carril 1 está encendido o ya dejó filas: apagado y vacío no hay
    # nada que auditar y la tarjeta sobra.
    lane1_active = lane1.enabled(settings) or any(
        count for provenance, count in lane1.provenance_counts(db).items() if provenance != "human"
    )
    strip = build_strip(
        waha=waha,
        last_capture=ultima_captura,
        health=intake,
        rate_statuses=rate_statuses,
        assist=assist,
        # Con cualquiera de los proveedores, no solo con la clave de Anthropic.
        assist_configured=try_resolve_config(settings) is not None,
        prefill_run=prefill_run,
        channel_gaps=load_channel_gap_report(db),
        jev_active=jev_signal.is_active(settings),
        last_24h_summary=last_24h(db),
        lane1_audit=lane1.audit_pending(db, settings) if lane1_active else None,
    )
    return StripSnapshot(strip, intake, rate_statuses, prefill_run, assist)
