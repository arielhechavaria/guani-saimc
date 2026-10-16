"""Tope de edad de la cola: un pendiente no pasa de N días.

Un precio de hace tres semanas no sirve en un mercado que se mueve cada
semana, y una cola con antigüedad sin límite deja de ser un flujo. Pasado el
tope, el anuncio sale de la cola solo. Esto es lo que NO hace:

- No borra nada: `raw_ingestions` sigue conservando el crudo.
- No crea ninguna fila de precio.
- No cambia la tasa de descarte de `spec.md` §12.5: queda como `off_focus`
  («tiene dato económico, pero no es de interés ahora»), que sale del numerador
  y del denominador de esa tasa. Lo distingue el evento de bitácora, que lleva
  `detail = {"expired_after_days": N}` y `mode = auto`.

Apartamiento de la decisión cerrada 2 (el descarte automático era solo del
carril 0); registrado en `docs/decisions.md` (2026-10-13).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from saimc.config import get_settings
from saimc.curation_events import record_curation_event
from saimc.db import session_scope
from saimc.models import (
    CurationAction,
    CurationMode,
    DiscardReason,
    ProcessingStatus,
    RawIngestion,
)
from saimc.models.base import utc_now

EXPIRED_DETAIL_KEY = "expired_after_days"


def expire_stale(session: Session, *, now: datetime, max_age_days: int) -> int:
    """Descarta los pendientes que entraron hace MÁS de `max_age_days` días.

    Devuelve cuántos. Sin `commit`: va en la transacción de quien llama. Con
    `max_age_days <= 0` está apagado.
    """
    if max_age_days <= 0:
        return 0
    limit = now - timedelta(days=max_age_days)
    stale = (
        session.execute(
            select(RawIngestion).where(
                RawIngestion.processing_status == ProcessingStatus.PENDING,
                RawIngestion.created_at < limit,
            )
        )
        .scalars()
        .all()
    )
    for ingestion in stale:
        ingestion.processing_status = ProcessingStatus.DISCARDED
        ingestion.discard_reason = DiscardReason.OFF_FOCUS.value
        record_curation_event(
            session,
            ingestion_id=ingestion.id,
            action=CurationAction.DISCARDED,
            mode=CurationMode.AUTO,
            discard_reason=DiscardReason.OFF_FOCUS.value,
            detail={EXPIRED_DETAIL_KEY: max_age_days},
            decided_at=now,
        )
    return len(stale)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m saimc.queue_expiry",
        description="Vence los pendientes que pasan del tope de edad. En seco por defecto.",
    )
    parser.add_argument("--execute", action="store_true", help="Descarta de verdad.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    settings = get_settings()
    with session_scope() as session:
        n = expire_stale(session, now=utc_now(), max_age_days=settings.queue_max_age_days)
        if not args.execute:
            session.rollback()
    cuando = f"más de {settings.queue_max_age_days} días"
    if args.execute:
        print(f"Vencidos ({cuando}): {n}.")
    else:
        print(f"EN SECO: vencerían {n} pendientes de {cuando}. Con --execute se descartan.")
    sys.exit(0)


if __name__ == "__main__":
    main()
