"""Carril 1 encendido: aceptación automática (slice B2b-2).

Contrato: `docs/spec-slice-B2b.md`. Como `lane0`, este módulo SÍ lee y escribe
la base, por eso no se importa desde `saimc.validation`.

Corre `python -m saimc.validation.lane1` (por defecto, en seco).

- **Aplicar** (`apply_lane_1`): el pre-cálculo, después de guardar la propuesta
  y de preguntar por el carril 0, le pregunta al motor (`validate_ad`) con los
  parámetros de `CONFIG`. Si da carril 1, guarda las filas en `prices_history`
  con `provenance = auto` y deja el anuncio curado, en la misma transacción.
  Solo con `SAIMC_LANE1_ENABLED` encendido. «Completar con IA» no pasa por acá:
  el operador ya está mirando el anuncio.
- **El comando** (`run`): aplica el mismo camino a los textos que ya están
  pendientes con una propuesta guardada. No gasta API. En seco lista lo que
  entraría; con `--execute` lo acepta, y solo si el interruptor está encendido.

Una fila automática se arma con las mismas piezas que una curada
(`saimc.curation_rows`): mismo formato, misma fecha por defecto y misma tasa.
Lo que no tiene una persona detrás:

- si la fecha por defecto no sirve (no hay, o es posterior a la captura), el
  anuncio queda pendiente: el formulario le habría pedido corregirla;
- si una fila en divisa no se puede convertir, el anuncio queda pendiente: no
  se publica una fila sin `price_cup`.

**Lo que falta, y por qué el interruptor nace apagado.** La auditoría y el
freno son del slice siguiente (B2b-3). Hasta que estén en producción, encender
`SAIMC_LANE1_ENABLED` publica filas que nadie revisa ni frena.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from saimc import curation_rows
from saimc.config import Settings, get_settings
from saimc.curation_events import record_curation_event
from saimc.db import session_scope
from saimc.exchange_rate import Fetcher, anchor_date
from saimc.exchange_rate.eltoque import fetch_rate
from saimc.extraction import llm_store
from saimc.extraction.engine import ExtractionProposal
from saimc.extraction.llm_checks import match_rule_rows
from saimc.extraction.llm_propose_ad import AdProposal
from saimc.models import (
    ContentType,
    CurationAction,
    CurationMode,
    LlmProposal,
    PriceHistory,
    ProcessingStatus,
    Provenance,
    RawIngestion,
    ReviewOutcome,
)
from saimc.models.base import utc_now
from saimc.units import canonical_unit
from saimc.validation import context, persist
from saimc.validation.engine import validate_ad
from saimc.validation.types import LANE_ACCEPT, AdContext, AdValidation, ValidationConfig

#: Los parámetros del carril 1. Los fijó el operador en G1b
#: (`docs/decisions.md`, 2026-10-09, «G1b se abre adelantada» y «B2b
#: aprobada»): son decisiones suyas, no configuración del despliegue.
#:
#: - celda de 5 referencias, la misma con que se persiste la validación (B1);
#: - un canal puede salir «sucio» desde 3 decisiones individuales, y lo es si
#:   el operador tocó más de la mitad de las filas propuestas;
#: - tope de 1 fila por anuncio;
#: - las señales externas no entran (Jev no separa): sin umbral.
CONFIG = ValidationConfig(
    min_cell_n=persist.MIN_CELL_N,
    channel_min_history=3,
    channel_max_edit_rate=0.5,
    signal_high_probability=None,
    lane1_max_rows=1,
)

TEXT = ContentType.TEXT.value


def enabled(settings: Settings) -> bool:
    """¿Está encendido el carril 1? (`SAIMC_LANE1_ENABLED`.)"""
    return settings.lane1_enabled


def rate_fetcher(settings: Settings) -> Fetcher:
    """El mismo `Fetcher` de la cola (`web.deps.get_rate_fetcher`), fuera de
    FastAPI: `eltoque.fetch_rate` con la configuración del proceso."""

    def _fetch(key: str, day: date) -> Decimal | None:
        return fetch_rate(key, day, api_key=settings.el_toque_api, timeout=settings.el_toque_timeout)

    return _fetch


# --- decidir ------------------------------------------------------------------


def decide(
    session: Session,
    ingestion: RawIngestion,
    result: AdProposal,
    *,
    text: str | None,
    catalog: context.Catalog,
) -> tuple[AdContext, AdValidation]:
    """Lo que dice el motor del anuncio, con los parámetros del carril 1."""
    ctx = context.build_ad_context(session, ingestion, model=result, catalog=catalog, text=text)
    return ctx, validate_ad(ctx, CONFIG)


def _published_date(ingestion: RawIngestion, text: str) -> date | None:
    """La fecha que el formulario habría prellenado, si el formulario la habría
    aceptado: no posterior al día de la captura en La Habana. Si no, `None`."""
    guessed = curation_rows.default_published_date(ingestion, text)
    if guessed is None or guessed > anchor_date(None, ingestion.captured_at):
        return None
    return guessed


# --- aplicar ------------------------------------------------------------------


def _entries(
    session: Session,
    ingestion: RawIngestion,
    ctx: AdContext,
    published_date: date,
) -> list[PriceHistory]:
    """Las filas de la propuesta, tal como las habría dejado el formulario sin
    que el operador tocara nada.

    El formulario parte de la fila de las reglas que le corresponde para lo que
    el modelo no propone (la compra mínima) y pone encima los campos del
    modelo (`curation._rows_from_model`). La provincia ya viene con la del
    canal aplicada (`build_ad_context`). La variante no la propone nadie.
    """
    model = ctx.model
    assert model is not None  # noqa: S101 — el carril 1 exige propuesta
    rules: list[ExtractionProposal | None] = list(match_rule_rows(model.rows, list(ctx.rule_rows)))
    entries: list[PriceHistory] = []
    for row, rule in zip(model.rows, rules, strict=True):
        # El carril 1 exige producto, precio y provincia (regla 1): no son None.
        assert row.product is not None and row.price is not None  # noqa: S101
        entries.append(
            PriceHistory(
                product_id=row.product.id,
                province_id=None if row.province is None else row.province.id,
                price=row.price,
                currency=row.currency,
                quantity=row.quantity,
                unit=canonical_unit(row.unit),
                sale_format=row.sale_format,
                format_id=curation_rows.resolve_format_id(
                    session,
                    row.product.id,
                    packaging=row.packaging,
                    quantity=row.quantity,
                    unit=row.unit,
                ),
                variant_id=None,
                min_purchase_quantity=None if rule is None else rule.min_purchase_quantity,
                price_basis=row.price_basis,
                published_date=published_date,
                source_id=ingestion.id,
                # Nadie la miró: la decidió el motor (B1, B2b).
                provenance=Provenance.AUTO.value,
            )
        )
    return entries


def apply_lane_1(
    session: Session,
    ingestion: RawIngestion,
    proposal: LlmProposal,
    result: AdProposal,
    *,
    text: str | None,
    catalog: context.Catalog,
    fetcher: Fetcher,
) -> bool:
    """Acepta el anuncio si el motor lo manda al carril 1. `True` si lo hizo.

    No hace commit: las filas van en la transacción de quien llama, dentro de
    un SAVEPOINT. Si una fila en divisa queda sin convertir se deshace el
    SAVEPOINT entero y el anuncio sigue pendiente, sin filas a medias.

    Lo que escribe es lo mismo que `submit_curation`, con `provenance = auto`
    y `mode = auto`: las filas con su tasa y su validación, el estado del
    anuncio, la revisión en `llm_reviews` (queda fuera de H0 como todo lo
    asistido, y no cuenta como decisión individual) y el evento de bitácora.
    """
    if ingestion.content_type != ContentType.TEXT:
        return False
    ctx, validation = decide(session, ingestion, result, text=text, catalog=catalog)
    if validation.lane != LANE_ACCEPT:
        return False
    published_date = _published_date(ingestion, ctx.text)
    if published_date is None:
        return False

    savepoint = session.begin_nested()
    entries = _entries(session, ingestion, ctx, published_date)
    if curation_rows.resolve_rates(session, entries, ingestion, fetcher):
        savepoint.rollback()
        return False
    session.add_all(entries)
    session.flush()
    llm_store.save_review(
        session,
        proposal=proposal,
        outcome=ReviewOutcome.CURATED,
        rows=llm_store.review_rows(
            proposal, [(str(index), entry) for index, entry in enumerate(entries)]
        ),
    )
    persist.validate_and_store(session, ingestion, entries)
    ingestion.processing_status = ProcessingStatus.CURATED
    record_curation_event(
        session,
        ingestion_id=ingestion.id,
        action=CurationAction.CURATED,
        mode=CurationMode.AUTO,
        proposal_id=proposal.id,
        shown_at=None,
        decided_at=utc_now(),
        detail={"row_count": len(entries)},
    )
    savepoint.commit()
    return True


# --- el comando: lo que ya está pendiente --------------------------------------


def pending_with_proposal(
    session: Session, limit: int | None = None
) -> list[tuple[RawIngestion, LlmProposal]]:
    """Los textos pendientes que ya tienen una propuesta `ok`, del más viejo
    al más nuevo."""
    stmt = (
        select(RawIngestion)
        .where(
            RawIngestion.processing_status == ProcessingStatus.PENDING,
            RawIngestion.content_type == TEXT,
        )
        .order_by(RawIngestion.captured_at, RawIngestion.id)
    )
    pairs: list[tuple[RawIngestion, LlmProposal]] = []
    for ingestion in session.execute(stmt).scalars():
        proposal = llm_store.latest_ok_proposal(session, ingestion.id)
        if proposal is None:
            continue
        pairs.append((ingestion, proposal))
        if limit is not None and len(pairs) >= limit:
            break
    return pairs


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m saimc.validation.lane1",
        description=(
            "Aplica el carril 1 a los textos pendientes que ya tienen propuesta. "
            "No gasta API. Por defecto, en seco."
        ),
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Acepta de verdad. Exige SAIMC_LANE1_ENABLED encendido.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Cuántos anuncios mirar.")
    return parser.parse_args(argv)


def run(
    args: argparse.Namespace,
    session: Session,
    settings: Settings,
    *,
    rate_fetcher: Fetcher,
    out: Callable[[str], None] = print,
) -> int:
    """Corre el modo pedido. Devuelve el código de salida."""
    if args.execute and not enabled(settings):
        out(
            "El carril 1 está apagado: no se acepta nada. Para encenderlo, "
            "SAIMC_LANE1_ENABLED=true en el entorno (ver docs/spec-slice-B2b.md)."
        )
        return 2

    catalog = context.load_catalog(session)
    pairs = pending_with_proposal(session, args.limit)
    if not args.execute:
        out("EN SECO: no se escribe nada (con --execute se aceptan).")
    accepted: list[RawIngestion] = []
    for ingestion, proposal in pairs:
        result = context.stored_proposal(session, proposal, catalog)
        if args.execute:
            ok = apply_lane_1(
                session,
                ingestion,
                proposal,
                result,
                text=None,
                catalog=catalog,
                fetcher=rate_fetcher,
            )
            # Un anuncio por transacción, como el pre-cálculo.
            session.commit()
        else:
            ctx, validation = decide(session, ingestion, result, text=None, catalog=catalog)
            ok = validation.lane == LANE_ACCEPT and _published_date(ingestion, ctx.text) is not None
        if ok:
            accepted.append(ingestion)
            out(f"  {str(ingestion.id)[:8]}  {ingestion.source_reference or '(sin canal)'}")
    label = "Aceptados solos (carril 1)" if args.execute else "Entrarían al carril 1"
    out(f"{label}: {len(accepted)} de {len(pairs)} textos pendientes con propuesta.")
    if not args.execute:
        out("En seco no se resuelve la tasa de cambio: una fila en divisa sin tasa no entra.")
    return 0


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    settings = get_settings()
    with session_scope() as session:
        code = run(args, session, settings, rate_fetcher=rate_fetcher(settings))
    sys.exit(code)


if __name__ == "__main__":
    main()
