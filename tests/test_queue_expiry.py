"""Tope de edad de la cola: un pendiente no pasa de N días.

Los casos se definieron a mano antes del código (2026-10-13).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from saimc.models import (
    ContentType,
    CurationEvent,
    DiscardReason,
    ProcessingStatus,
    RawIngestion,
    SourceType,
)
from saimc.queue_expiry import EXPIRED_DETAIL_KEY, expire_stale

AHORA = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)


def _anuncio(
    session: Session, *, dias: float, estado: str = ProcessingStatus.PENDING
) -> RawIngestion:
    clave = uuid.uuid4().hex
    ingesta = RawIngestion(
        source_type=SourceType.INBOX_TEXT,
        file_path=f"raw/{clave}.txt",
        content_hash=clave,
        content_type=ContentType.TEXT,
        original_text="Arroz a 320 la libra",
        processing_status=estado,
        captured_at=AHORA - timedelta(days=dias),
        created_at=AHORA - timedelta(days=dias),
    )
    session.add(ingesta)
    session.commit()
    return ingesta


def test_vence_el_pendiente_viejo_y_no_el_reciente(session: Session) -> None:
    viejo = _anuncio(session, dias=8)
    reciente = _anuncio(session, dias=2)

    vencidos = expire_stale(session, now=AHORA, max_age_days=7)
    session.commit()

    assert vencidos == 1
    session.refresh(viejo)
    session.refresh(reciente)
    assert viejo.processing_status == ProcessingStatus.DISCARDED
    assert reciente.processing_status == ProcessingStatus.PENDING


def test_el_vencido_queda_como_fuera_de_foco_para_no_inflar_la_tasa_de_descarte(
    session: Session,
) -> None:
    viejo = _anuncio(session, dias=9)

    expire_stale(session, now=AHORA, max_age_days=7)
    session.commit()

    session.refresh(viejo)
    assert viejo.discard_reason == DiscardReason.OFF_FOCUS


def test_el_vencimiento_deja_un_evento_automatico_distinguible(session: Session) -> None:
    viejo = _anuncio(session, dias=9)

    expire_stale(session, now=AHORA, max_age_days=7)
    session.commit()

    (evento,) = session.execute(select(CurationEvent)).scalars().all()
    assert evento.ingestion_id == viejo.id
    assert evento.action == "discarded"
    assert evento.mode == "auto"
    assert evento.detail == {EXPIRED_DETAIL_KEY: 7}


def test_no_toca_lo_ya_curado_ni_lo_ya_descartado(session: Session) -> None:
    curado = _anuncio(session, dias=30, estado=ProcessingStatus.CURATED)
    descartado = _anuncio(session, dias=30, estado=ProcessingStatus.DISCARDED)

    vencidos = expire_stale(session, now=AHORA, max_age_days=7)

    assert vencidos == 0
    assert curado.processing_status == ProcessingStatus.CURATED
    assert descartado.processing_status == ProcessingStatus.DISCARDED


def test_con_tope_cero_esta_apagado(session: Session) -> None:
    viejo = _anuncio(session, dias=30)

    assert expire_stale(session, now=AHORA, max_age_days=0) == 0
    assert viejo.processing_status == ProcessingStatus.PENDING


def test_justo_en_siete_dias_todavia_no_vence(session: Session) -> None:
    _anuncio(session, dias=7)

    assert expire_stale(session, now=AHORA, max_age_days=7) == 0
