#!/usr/bin/env python
"""¿Puede Jev arbitrar cuando el modelo descarta y las reglas ven un producto?

Hoy esos anuncios van siempre a la cola (`engine._lane_0_reasons`: «el modelo
descarta, pero las reglas ven un producto piloto en el texto»). La regla es
gruesa: basta un dígito y el nombre de un piloto (`engine._pilot_in_text`).
Este informe mide, SOLO sobre ese desacuerdo y contra lo que decidió el
operador, qué pasaría si Jev (su triage ya guardado en `jev_signals`) hiciera
de árbitro: descartar con P(sí) < umbral aunque las reglas vean un piloto.

No llama a Jev, no gasta y no escribe en la base ni en archivos. Lee los dos
conjuntos de `saimc.validation.shadow` (oro y revisiones individuales); no se
mezclan. Un porcentaje nunca va sin su n y su cota superior.

Uso (desde la raíz del repo):
    python scripts/jev_arbitro_eval.py --gold data/llm-backtest-results-v7.jsonl
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from saimc.db import session_scope
from saimc.models import (
    ContentType,
    CurationEvent,
    CurationMode,
    LlmProposal,
    ProcessingStatus,
    RawIngestion,
)
from saimc.validation import context, shadow
from saimc.validation.engine import _LANE_0_REASONS, _pilot_in_text
from saimc.validation.stats import format_rate

THRESHOLDS = (0.05, 0.1, 0.2, 0.3, 0.5)
#: Tramos de P(sí) para ver cómo se reparten los conservados y los descartados.
BINS = ((0.0, 0.1), (0.1, 0.2), (0.2, 0.5), (0.5, 0.8), (0.8, 1.0001))


def disagreement(case: shadow.Case) -> bool:
    """El modelo descarta por foco o falta de datos y las reglas ven un piloto."""
    model = case.ctx.model
    return (
        case.content_type == ContentType.TEXT.value
        and model is not None
        and not model.degraded
        and not model.failure
        and model.discard is True
        and model.discard_reason in _LANE_0_REASONS
        and _pilot_in_text(case.ctx)
    )


def load_all(
    session: Session, catalog: context.Catalog
) -> tuple[shadow.CaseSet, list[shadow.Case], int]:
    """Todos los textos cuya última propuesta descarta, decididos o no.

    El conjunto de evaluación casi no trae desacuerdos porque el operador los
    descarta en lote y eso no deja una revisión individual. Acá la verdad es el
    estado final: curado = conservado; descartado por una persona (individual
    o en lote) = descartado. Lo que descartó el carril 0 solo se deja afuera:
    lo decidió la máquina y contarlo sería circular. Los pendientes se
    devuelven aparte, sin verdad.
    """
    dataset = shadow.CaseSet(
        name="all",
        title="Todos los textos con propuesta que descarta (estado final)",
        fields=(),
    )
    latest: dict[str, LlmProposal] = {}
    for proposal in session.execute(
        select(LlmProposal).where(LlmProposal.status == "ok").order_by(LlmProposal.created_at)
    ).scalars():
        latest[str(proposal.ingestion_id)] = proposal
    last_mode: dict[str, str] = {}
    for ingestion_id, mode in session.execute(
        select(CurationEvent.ingestion_id, CurationEvent.mode)
        .where(CurationEvent.action.in_(["discarded", "rescued", "audited"]))
        .order_by(CurationEvent.decided_at)
    ):
        last_mode[str(ingestion_id)] = str(mode)
    pending: list[shadow.Case] = []
    auto = 0
    for ingestion in session.execute(
        select(RawIngestion).where(RawIngestion.content_type == ContentType.TEXT.value)
    ).scalars():
        proposal = latest.get(str(ingestion.id))
        if proposal is None or proposal.discard is not True:
            continue
        status = str(ingestion.processing_status)
        if status == ProcessingStatus.DISCARDED.value and last_mode.get(str(ingestion.id)) == (
            CurationMode.AUTO.value
        ):
            auto += 1
            continue
        model = context.stored_proposal(session, proposal, catalog)
        ctx = context.build_ad_context(session, ingestion, model=model, catalog=catalog)
        case = shadow.Case(
            ingestion_id=str(ingestion.id),
            content_type=str(ingestion.content_type),
            ctx=ctx,
            kept=status == ProcessingStatus.CURATED.value,
            diff=None,
        )
        if status == ProcessingStatus.PENDING.value:
            pending.append(case)
        elif status in (ProcessingStatus.CURATED.value, ProcessingStatus.DISCARDED.value):
            dataset.cases.append(case)
    return dataset, pending, auto


def render(dataset: shadow.CaseSet) -> list[str]:
    cases = [c for c in dataset.cases if disagreement(c)]
    signalled = [c for c in cases if c.jev is not None and c.jev.triage.p is not None]
    kept = [c for c in signalled if c.kept]
    dropped = [c for c in signalled if not c.kept]
    lines = [
        f"## {dataset.title}",
        "",
        f"Desacuerdo (modelo descarta + reglas ven piloto): {len(cases)} textos; "
        f"con triage legible de Jev: {len(signalled)}. De esos, el operador "
        f"conservó {len(kept)} y descartó {len(dropped)}.",
        "",
    ]
    if not signalled:
        return [*lines, "Sin muestra.", ""]
    lines += ["P(sí) de Jev por tramo (conservados / descartados):", ""]
    for low, high in BINS:
        k = sum(1 for c in kept if low <= c.jev.triage.p < high)  # type: ignore[union-attr]
        d = sum(1 for c in dropped if low <= c.jev.triage.p < high)  # type: ignore[union-attr]
        lines.append(f"- [{low:.2f}, {min(high, 1.0):.2f}): {k} / {d}")
    lines += ["", "Si Jev descartara con P(sí) < umbral aunque las reglas vean un piloto:", ""]
    lines.append("| umbral | descartaría | de ellos, falsos (el operador lo conservó) | "
                 "descartes verdaderos que ya no pasan por la cola |")
    lines.append("|---|---|---|---|")
    for t in THRESHOLDS:
        taken = [c for c in signalled if c.jev.triage.p < t]  # type: ignore[union-attr]
        false = sum(1 for c in taken if c.kept)
        lines.append(
            f"| {t} | {len(taken)} | {format_rate(false, len(taken))} | "
            f"{len(taken) - false} de {len(dropped)} |"
        )
    lines.append("")
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gold", type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.gold.is_file():
        print(f"No existe el oro: {args.gold}", file=sys.stderr)
        return 2
    with session_scope() as session:
        catalog = context.load_catalog(session)
        datasets = [
            shadow.load_calibration(session, catalog, args.gold),
            shadow.load_evaluation(session, catalog),
        ]
        everything, pending, auto = load_all(session, catalog)
        shadow.attach_jev(session, [*datasets, everything])
        shadow_pending = shadow.CaseSet(name="pending", title="", fields=(), cases=pending)
        shadow.attach_jev(session, [shadow_pending])
        for dataset in (*datasets, everything):
            print("\n".join(render(dataset)))
        print(f"(Afuera: {auto} descartes automáticos del carril 0, por circulares.)")
        waiting = [c for c in shadow_pending.cases if disagreement(c)]
        with_p = [c for c in waiting if c.jev is not None and c.jev.triage.p is not None]
        print(f"\nPendientes hoy en desacuerdo: {len(waiting)}; con triage legible: {len(with_p)}.")
        for t in THRESHOLDS:
            print(f"- P(sí) < {t}: {sum(1 for c in with_p if c.jev.triage.p < t)}")  # type: ignore[union-attr]
    print("Solo mide el desacuerdo; no fija umbrales (eso lo decide el operador).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
