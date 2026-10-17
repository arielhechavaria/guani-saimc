"""Vista de desacuerdo de la curaduría (`/curation/desacuerdo`).

Los anuncios que el modelo descarta pero las reglas ven con un producto piloto
(ver `saimc.web.disagreement_view`). Tres acciones, todas de a un anuncio:

- **Aceptar modelo**: se descarta con el motivo que dio el modelo.
- **Aceptar reglas**: se guardan las filas que leyeron las reglas, como las
  habría guardado el formulario (`provenance = human`).
- **Descartar**: se descarta con el motivo que elige el operador.

Cada POST revalida contra la misma selección que armó el GET
(`disagreement_of`) y no confía en el id que llega: un anuncio que ya no está en
desacuerdo (curado en otra pestaña, rescatado, con otra propuesta) se ignora
sin tocar nada. Las tres acciones escriben lo mismo que las rutas individuales
de la cola (`submit_curation`, `discard_curation`), con `detail.desacuerdo` en
el evento para poder contarlas aparte.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from saimc import curation_rows
from saimc.curation_events import parse_shown_at, record_curation_event
from saimc.exchange_rate import Fetcher
from saimc.extraction import llm_store
from saimc.models import (
    CurationAction,
    CurationMode,
    ProcessingStatus,
    ReviewOutcome,
)
from saimc.validation import persist
from saimc.web.auth import require_operator
from saimc.web.deps import get_db, get_rate_fetcher
from saimc.web.disagreement_view import (
    DISCARD_LABELS,
    DisagreementItem,
    disagreement_of,
    entries_from_rules,
    pending_disagreements,
)

router = APIRouter(dependencies=[Depends(require_operator)])

_LIST_URL = "/curation/desacuerdo"
def _back() -> RedirectResponse:
    return RedirectResponse(url=_LIST_URL, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/curation/desacuerdo", response_class=HTMLResponse)
def curation_disagreement(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    items = pending_disagreements(db)
    return request.app.state.templates.TemplateResponse(
        request,
        "curation_desacuerdo.html",
        {
            "items": items,
            "reason_labels": DISCARD_LABELS,
            # Cuándo se mostró la lista: vuelve en cada POST para medir el
            # tiempo por decisión.
            "shown_at": datetime.now(UTC).isoformat(),
        },
    )


def _discard(
    db: Session,
    item: DisagreementItem,
    *,
    reason: str,
    kind: str,
    shown_at: str,
) -> None:
    ingestion = item.ingestion
    ingestion.processing_status = ProcessingStatus.DISCARDED
    ingestion.discard_reason = reason
    llm_store.save_review(
        db,
        proposal=item.proposal,
        outcome=ReviewOutcome.DISCARDED,
        discard_reason=reason,
        rows=llm_store.review_rows(item.proposal, []),
    )
    record_curation_event(
        db,
        ingestion_id=ingestion.id,
        action=CurationAction.DISCARDED,
        mode=CurationMode.INDIVIDUAL,
        discard_reason=reason,
        shown_at=parse_shown_at(shown_at),
        proposal_id=item.proposal.id,
        detail={"desacuerdo": kind},
    )
    db.commit()


@router.post("/curation/desacuerdo/{ingestion_id}/modelo")
def accept_model(
    ingestion_id: uuid.UUID,
    shown_at: str = Form(""),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Da la razón al modelo: se descarta con el motivo que él dio."""
    item = disagreement_of(db, ingestion_id)
    if item is not None:
        _discard(db, item, reason=item.model_reason, kind="modelo", shown_at=shown_at)
    return _back()


@router.post("/curation/desacuerdo/{ingestion_id}/descartar")
def discard_disagreement(
    ingestion_id: uuid.UUID,
    reason: str = Form(...),
    shown_at: str = Form(""),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Descarta por criterio del operador, con el motivo que elige. Los dos
    únicos motivos que ofrece la vista son los del carril 0."""
    if reason not in DISCARD_LABELS:
        # Solo `off_focus` y `no_data`: es lo que ofrece la vista.
        raise HTTPException(status_code=422)
    item = disagreement_of(db, ingestion_id)
    if item is not None:
        _discard(db, item, reason=reason, kind="descartar", shown_at=shown_at)
    return _back()


@router.post("/curation/desacuerdo/{ingestion_id}/reglas")
def accept_rules(
    ingestion_id: uuid.UUID,
    shown_at: str = Form(""),
    db: Session = Depends(get_db),
    rate_fetcher: Fetcher = Depends(get_rate_fetcher),
) -> RedirectResponse:
    """Da la razón a las reglas: se guardan las filas que ellas leyeron.

    Solo si se pueden guardar tal cual (`DisagreementItem.can_accept_rules`):
    producto, precio, base del precio cuando hay cantidad, y una fecha de
    publicación confiable. Si no, no se hace nada y el anuncio sigue pendiente:
    la vista ofrece el formulario completo en ese caso.
    """
    item = disagreement_of(db, ingestion_id)
    if item is None or not item.can_accept_rules:
        return _back()
    ingestion = item.ingestion
    entries = entries_from_rules(db, item)
    # Misma ancla y tasa que `submit_curation` y el carril 1.
    unconverted = curation_rows.resolve_rates(db, entries, ingestion, rate_fetcher)
    db.add_all(entries)
    db.flush()
    llm_store.save_review(
        db,
        proposal=item.proposal,
        outcome=ReviewOutcome.CURATED,
        rows=llm_store.review_rows(
            item.proposal, [(str(index), entry) for index, entry in enumerate(entries)]
        ),
        shown_at=parse_shown_at(shown_at),
    )
    persist.validate_and_store(db, ingestion, entries)
    ingestion.processing_status = ProcessingStatus.CURATED
    record_curation_event(
        db,
        ingestion_id=ingestion.id,
        action=CurationAction.CURATED,
        mode=CurationMode.INDIVIDUAL,
        shown_at=parse_shown_at(shown_at),
        proposal_id=item.proposal.id,
        detail={"row_count": len(entries), "desacuerdo": "reglas"},
    )
    db.commit()
    if unconverted:
        return RedirectResponse(
            url=f"{_LIST_URL}?unconverted={unconverted}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return _back()
