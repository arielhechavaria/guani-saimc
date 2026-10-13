"""Lo de las últimas 24 h: lo que el operador necesita procesado para publicar.

Casos definidos a mano antes del código (2026-10-13).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from saimc.models import ContentType, ProcessingStatus, RawIngestion, SourceType
from saimc.progress import last_24h
from saimc.web.health_strip import last_24h_indicator

AHORA = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)


def _anuncio(session: Session, *, horas: float, estado: str) -> RawIngestion:
    clave = uuid.uuid4().hex
    ingesta = RawIngestion(
        source_type=SourceType.INBOX_TEXT,
        file_path=f"raw/{clave}.txt",
        content_hash=clave,
        content_type=ContentType.TEXT,
        original_text="Arroz a 320 la libra",
        processing_status=estado,
        captured_at=AHORA - timedelta(hours=horas),
        created_at=AHORA - timedelta(hours=horas),
    )
    session.add(ingesta)
    session.commit()
    return ingesta


def test_cuenta_solo_lo_que_entro_en_las_ultimas_24_horas(session: Session) -> None:
    _anuncio(session, horas=2, estado=ProcessingStatus.PENDING)
    _anuncio(session, horas=5, estado=ProcessingStatus.CURATED)
    _anuncio(session, horas=20, estado=ProcessingStatus.DISCARDED)
    _anuncio(session, horas=30, estado=ProcessingStatus.PENDING)  # fuera de la ventana

    resumen = last_24h(session, now=AHORA)

    assert resumen.entered == 3
    assert (resumen.curated, resumen.discarded, resumen.pending) == (1, 1, 1)


def test_dice_hace_cuanto_espera_el_pendiente_mas_viejo_de_la_ventana(session: Session) -> None:
    _anuncio(session, horas=2, estado=ProcessingStatus.PENDING)
    _anuncio(session, horas=15, estado=ProcessingStatus.PENDING)
    _anuncio(session, horas=40, estado=ProcessingStatus.PENDING)  # histórico: no cuenta

    resumen = last_24h(session, now=AHORA)

    assert resumen.pending == 2
    assert resumen.oldest_pending_hours == 15


def test_sin_pendientes_en_la_ventana_no_hay_espera(session: Session) -> None:
    _anuncio(session, horas=3, estado=ProcessingStatus.CURATED)

    resumen = last_24h(session, now=AHORA)

    assert resumen.pending == 0
    assert resumen.oldest_pending_hours is None


def test_el_historico_viejo_no_afecta_el_resumen(session: Session) -> None:
    for _ in range(5):
        _anuncio(session, horas=24 * 20, estado=ProcessingStatus.PENDING)

    resumen = last_24h(session, now=AHORA)

    assert resumen.entered == 0
    assert resumen.pending == 0


# --- el indicador -------------------------------------------------------------------


def test_sin_pendientes_el_indicador_esta_ok() -> None:
    ind = last_24h_indicator(entered=40, pending=0, oldest_pending_hours=None)

    assert ind.level == "ok"


def test_pendientes_recientes_siguen_ok() -> None:
    # Recién entraron: todavía hay margen para resolverlos antes de publicar.
    assert last_24h_indicator(entered=40, pending=5, oldest_pending_hours=3).level == "ok"


def test_un_pendiente_con_mas_de_12_horas_pasa_a_ambar_y_dice_cuantos() -> None:
    ind = last_24h_indicator(entered=40, pending=7, oldest_pending_hours=14)

    assert ind.level == "warn"
    assert "7" in ind.value
    assert ind.hint
