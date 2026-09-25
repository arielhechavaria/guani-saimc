"""Pre-cálculo por lotes de la propuesta del modelo (slice 3b).

Corre `python -m saimc.extraction.prefill` (por defecto, en seco).

Recorre los pendientes que todavía no tienen una propuesta `ok` guardada, más
viejo primero, y para cada uno hace lo mismo que el botón «Completar con IA»:
arma el texto que vería el modelo (`llm_propose_ad.ad_text`, OCR incluido),
llama a `propose_ad` y guarda el resultado con `llm_store.save_proposal`. Al
abrir el anuncio en `/curation` la propuesta ya está y no se paga otra llamada
(`docs/plan-asistente-llm-cola-completa.md`, slice 3b).

- **Uno a la vez.** El VPS tiene 2 vCPU y el OCR es lo caro: nada de
  paralelismo. Tampoco cron ni cola de trabajos (decisión cerrada 3): el
  operador lo corre a mano antes de una sesión de curaduría.
- **Reanudable.** Commit después de cada anuncio; volver a correrlo saltea lo
  que ya tiene propuesta `ok` (de cualquier versión de prompt). Una propuesta
  degradada no cuenta como hecha: se reintenta.
- **Falla de saldo o de clave detiene la corrida.** Seguir solo gastaría
  pedidos que van a fallar igual. Las demás fallas se guardan como
  `degraded` y la corrida sigue.
- **Consumo sí, H0 no.** Cada llamada se anota en el registro del operador con
  `source = "prefill"`: suma en `/control`, pero no saca al anuncio de H0. De
  eso se encarga `llm_reviews`, cuando el operador cura con la propuesta a la
  vista (`docs/decisions.md`, slice 3a y 3b).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from saimc.config import Settings, get_settings
from saimc.db import session_scope
from saimc.extraction import llm_assist, llm_propose_ad, llm_store
from saimc.models import (
    ContentType,
    LlmProposal,
    ProcessingStatus,
    ProposalStatus,
    Province,
    RawIngestion,
)
from saimc.progress import pilot_products

# Consumo medido por anuncio con Sonnet 5 y el prompt `2026-09-25.3` sobre 100
# anuncios reales (`docs/costos.md`): ~2407 tokens de entrada y ~245 de salida,
# $0,0073 por anuncio. Se usa solo mientras no haya propuestas guardadas.
_DOC_INPUT_TOKENS = 2407
_DOC_OUTPUT_TOKENS = 245

# Fallas que no tiene sentido reintentar en la misma corrida.
_ABORT_FAILURES = frozenset({"credit", "auth"})

_TYPE_LABELS = {ContentType.TEXT.value: "texto", ContentType.IMAGE.value: "imagen"}


# --- selección --------------------------------------------------------------


def select_candidates(session: Session, limit: int | None = None) -> list[RawIngestion]:
    """Pendientes sin propuesta `ok` guardada, más viejo primero.

    Mismo orden que la cola (`captured_at` ascendente, `_pending_all` en
    `curation.py`).
    """
    has_ok = exists().where(
        LlmProposal.ingestion_id == RawIngestion.id,
        LlmProposal.status == ProposalStatus.OK,
    )
    stmt = (
        select(RawIngestion)
        .where(RawIngestion.processing_status == ProcessingStatus.PENDING, ~has_ok)
        .order_by(RawIngestion.captured_at.asc())
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    return list(session.execute(stmt).scalars().all())


# --- estimación (dry-run) ---------------------------------------------------


@dataclass(frozen=True)
class Estimate:
    ads: dict[str, int]
    model: str
    # `None` si el modelo no tiene tarifa en `llm_assist._RATES_USD_PER_MTOK`.
    per_ad_usd: float | None
    # De dónde sale el promedio, en palabras del operador.
    basis: str

    @property
    def total_ads(self) -> int:
        return sum(self.ads.values())

    @property
    def total_usd(self) -> float | None:
        return None if self.per_ad_usd is None else self.per_ad_usd * self.total_ads


def _cost(input_tokens: int, output_tokens: int, model: str) -> float | None:
    rates = llm_assist._RATES_USD_PER_MTOK.get(model)
    if rates is None:
        return None
    return input_tokens / 1e6 * rates[0] + output_tokens / 1e6 * rates[1]


def estimate(session: Session, candidates: Sequence[RawIngestion], model: str) -> Estimate:
    """Costo esperado de procesar `candidates`, sin red.

    Si ya hay propuestas guardadas con consumo, el promedio de tokens por
    llamada sale de ellas (medido en esta base); si no, del promedio
    documentado en `docs/costos.md`. En los dos casos se tarifa con `model`.
    """
    ads: dict[str, int] = {}
    for ingestion in candidates:
        ads[ingestion.content_type] = ads.get(ingestion.content_type, 0) + 1

    tokens = session.execute(
        select(LlmProposal.input_tokens, LlmProposal.output_tokens).where(
            (LlmProposal.input_tokens > 0) | (LlmProposal.output_tokens > 0)
        )
    ).all()
    if tokens:
        avg_in = sum(t[0] for t in tokens) / len(tokens)
        avg_out = sum(t[1] for t in tokens) / len(tokens)
        basis = f"medido: promedio de {len(tokens)} propuestas guardadas en la base"
    else:
        avg_in, avg_out = _DOC_INPUT_TOKENS, _DOC_OUTPUT_TOKENS
        basis = "documentado en docs/costos.md (todavía no hay propuestas guardadas)"
    basis += f"; ~{avg_in:,.0f} tokens de entrada y ~{avg_out:,.0f} de salida por anuncio"
    rates = llm_assist._RATES_USD_PER_MTOK.get(model)
    per_ad = (
        None if rates is None else avg_in / 1e6 * rates[0] + avg_out / 1e6 * rates[1]
    )
    return Estimate(ads=ads, model=model, per_ad_usd=per_ad, basis=basis)


def print_estimate(est: Estimate, out: Callable[[str], None] = print) -> None:
    out("Pendientes sin propuesta guardada:")
    for tipo, etiqueta in _TYPE_LABELS.items():
        out(f"  {etiqueta}: {est.ads.get(tipo, 0)}")
    out(f"  total: {est.total_ads}")
    if est.per_ad_usd is None or est.total_usd is None:
        out(f"Costo estimado: sin tarifa para {est.model} en llm_assist.py.")
        return
    out(
        f"Costo estimado con {est.model}: ${est.per_ad_usd:.4f} por anuncio, "
        f"${est.total_usd:.4f} en total."
    )
    out(f"  Base del promedio: {est.basis}.")


# --- la corrida -------------------------------------------------------------


@dataclass
class RunResult:
    done: int = 0
    degraded: int = 0
    skipped_no_text: int = 0
    cost_usd: float = 0.0
    aborted_failure: str = ""
    stopped_by_max_cost: bool = False


def run_prefill(
    session: Session,
    candidates: Sequence[RawIngestion],
    *,
    api_key: str,
    model: str,
    timeout: float,
    raw_store_dir: Path,
    log_path: Path,
    client: Any | None = None,
    transcribe: Callable[[Path], str] | None = None,
    max_cost: float | None = None,
    expected_per_ad: float | None = None,
    out: Callable[[str], None] = print,
) -> RunResult:
    """Un anuncio a la vez: texto, llamada, alta de la propuesta y commit.

    Con `max_cost`, antes de cada llamada se frena si el acumulado más lo que
    se espera que cueste la siguiente lo pasaría. «Lo que se espera» es el
    promedio real de esta corrida, o `expected_per_ad` antes de la primera.
    """
    products = pilot_products(session)
    provinces = list(session.execute(select(Province).order_by(Province.name)).scalars())
    result = RunResult()
    total = len(candidates)
    for n, ingestion in enumerate(candidates, start=1):
        if max_cost is not None:
            calls = result.done
            next_cost = result.cost_usd / calls if calls else (expected_per_ad or 0.0)
            if result.cost_usd + next_cost > max_cost:
                out(
                    f"Corrida detenida por el tope: ${result.cost_usd:.4f} gastados, "
                    f"el siguiente anuncio (~${next_cost:.4f}) pasaría ${max_cost:.4f}."
                )
                result.stopped_by_max_cost = True
                break

        short_id = str(ingestion.id)[:8]
        text = llm_propose_ad.ad_text(ingestion, raw_store_dir, transcribe)
        if not text.strip():
            result.skipped_no_text += 1
            out(f"[{n}/{total}] {short_id} {ingestion.content_type}: sin texto, no se llama")
            continue

        proposal = llm_propose_ad.propose_ad(
            text,
            products,
            provinces,
            api_key=api_key,
            client=client,
            model=model,
            timeout=timeout,
            ingestion_id=str(ingestion.id),
            log_path=None,
        )
        llm_store.save_proposal(
            session, ingestion_id=ingestion.id, result=proposal, model=model
        )
        session.commit()
        llm_propose_ad.record_operator_run(
            proposal,
            ingestion_id=str(ingestion.id),
            model=model,
            log_path=log_path,
            source=llm_assist.PREFILL_SOURCE,
        )

        result.done += 1
        result.degraded += proposal.degraded
        result.cost_usd += _cost(proposal.input_tokens, proposal.output_tokens, model) or 0.0
        estado = (
            f"DEGRADADO ({proposal.failure})"
            if proposal.degraded
            else f"ok, {len(proposal.rows)} filas"
            + (" (sugiere descartar)" if proposal.discard else "")
        )
        out(
            f"[{n}/{total}] {short_id} {ingestion.content_type}: {estado} — "
            f"{proposal.input_tokens}+{proposal.output_tokens} tokens — "
            f"acumulado ${result.cost_usd:.4f}"
        )
        if proposal.failure in _ABORT_FAILURES:
            out(
                f"Corrida detenida: falla «{proposal.failure}» "
                + ("(sin saldo en la cuenta de la API)." if proposal.failure == "credit"
                   else "(la clave de la API fue rechazada).")
                + " Lo hecho quedó guardado; al resolverlo se reanuda sin repetir nada."
            )
            result.aborted_failure = proposal.failure
            break
    return result


# --- CLI --------------------------------------------------------------------


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m saimc.extraction.prefill",
        description=(
            "Pre-calcula la propuesta del modelo para los pendientes de la cola. "
            "Por defecto estima el costo sin llamar a nada; --execute hace la corrida real."
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Solo estimar (por defecto).")
    mode.add_argument(
        "--execute",
        action="store_true",
        help="Corrida real: llama a la API y GASTA plata. Exige la clave.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Tope de anuncios.")
    parser.add_argument(
        "--max-cost",
        type=float,
        default=None,
        help="Tope en USD: frena antes de pasarlo (según el gasto real de la corrida).",
    )
    parser.add_argument("--model", default=None, help="Por defecto, SAIMC_LLM_ASSIST_MODEL.")
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Segundos por llamada (sin nadie esperando, más holgado que el botón).",
    )
    return parser.parse_args(argv)


def execute(
    args: argparse.Namespace,
    session: Session,
    settings: Settings,
    *,
    client: Any | None = None,
    transcribe: Callable[[Path], str] | None = None,
    out: Callable[[str], None] = print,
) -> int:
    """Corre el modo pedido. Devuelve el código de salida."""
    model = args.model or settings.llm_assist_model
    candidates = select_candidates(session, args.limit)
    est = estimate(session, candidates, model)

    if not args.execute:
        out("MODO DRY-RUN: no se llama a ninguna API ni se necesita clave.")
        print_estimate(est, out)
        out("Para la corrida real: agregá --execute (usa la clave de la configuración).")
        return 0

    api_key = settings.anthropic_api_key
    if not api_key:
        out(
            "No hay clave de API: la corrida real necesita SAIMC_ANTHROPIC_API_KEY. "
            "Sin clave no se llama a nada."
        )
        return 2
    print_estimate(est, out)
    if est.per_ad_usd is None:
        out(f"AVISO: {model} no tiene tarifa en llm_assist.py; el costo acumulado no se estima.")
    result = run_prefill(
        session,
        candidates,
        api_key=api_key,
        model=model,
        timeout=args.timeout,
        raw_store_dir=settings.raw_store_dir,
        log_path=settings.llm_assist_log_path,
        client=client,
        transcribe=transcribe,
        max_cost=args.max_cost,
        expected_per_ad=est.per_ad_usd,
        out=out,
    )
    out(
        f"Hechos: {result.done} ({result.degraded} degradados), "
        f"sin texto: {result.skipped_no_text}, costo real ${result.cost_usd:.4f}."
    )
    return 1 if result.aborted_failure else 0


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    with session_scope() as session:
        code = execute(args, session, get_settings())
    sys.exit(code)


if __name__ == "__main__":
    main()
