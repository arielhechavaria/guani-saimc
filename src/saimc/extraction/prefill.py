"""Pre-cálculo por lotes de la propuesta del modelo (slice 3b).

Corre `python -m saimc.extraction.prefill` (por defecto, en seco).

Recorre los pendientes que todavía no tienen una propuesta `ok` guardada, más
viejo primero, y para cada uno hace lo mismo que el botón «Completar con IA»:
arma el texto que vería el modelo (`llm_propose_ad.ad_text`, OCR incluido),
llama a `propose_ad` y guarda el resultado con `llm_store.save_proposal`. Al
abrir el anuncio en `/curation` la propuesta ya está y no se paga otra llamada
(`docs/plan-asistente-llm-cola-completa.md`, slice 3b).

- **Uno a la vez.** El VPS tiene 2 vCPU y el OCR es lo caro: nada de
  paralelismo. Tampoco cola de trabajos (decisión cerrada 3): lo corre el
  temporizador de systemd cada 3 h (`systemd/saimc-prefill.timer`, slice C1) o
  el operador a mano antes de una sesión de curaduría.
- **Una corrida real a la vez.** `--execute` toma un candado
  (`prefill_lock`, `data/prefill.lock`); si otra corrida lo tiene, esta sale
  enseguida con código 0, sin llamar a nada y sin tocar el registro de
  `/control`. El modo en seco ni lo toma ni lo mira.
- **Reanudable.** Commit después de cada anuncio; volver a correrlo saltea lo
  que ya tiene propuesta `ok` (de cualquier versión de prompt). Una propuesta
  degradada no cuenta como hecha: se reintenta.
- **Falla de saldo o de clave detiene la corrida.** Seguir solo gastaría
  pedidos que van a fallar igual. Las demás fallas se guardan como
  `degraded` y la corrida sigue.
- **Carril 0 (B2a).** Con `SAIMC_LANE0_TEXT_ENABLED` o
  `SAIMC_LANE0_IMAGE_ENABLED`, después de guardar la propuesta se le pregunta
  al motor por el carril 0 y, si lo da, el anuncio queda descartado solo, en la
  misma transacción (`validation.lane0.apply_lane_0`). Con el de texto
  encendido también entran los textos sin foco: son los únicos que el carril 0
  puede descartar (`docs/spec-slice-B2a.md`).
- **Consumo sí, H0 no.** Cada llamada se anota en el registro del operador con
  `source = "prefill"`: suma en `/control`, pero no saca al anuncio de H0. De
  eso se encarga `llm_reviews`, cuando el operador cura con la propuesta a la
  vista (`docs/decisions.md`, slice 3a y 3b).
- **Jev en sombra (A6b).** Con `SAIMC_LLM_JEV_ENABLED` y su clave, antes del
  bucle de Claude va una pasada que le hace tres preguntas cerradas a cada
  texto pendiente y guarda la respuesta (`validation.jev_signal`). Va antes
  porque el carril 0 descarta en este mismo bucle, y después el anuncio ya no
  estaría pendiente. No depende de la clave de Anthropic ni mira el foco, tiene
  su propio tope (`SAIMC_LLM_JEV_MAX_COST`) y un fallo suyo no cambia el código
  de salida (`docs/spec-slice-A6b.md`).
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from saimc import queue_expiry
from saimc.config import Settings, get_settings
from saimc.db import session_scope
from saimc.exchange_rate import Fetcher
from saimc.extraction import (
    llm_assist,
    llm_propose_ad,
    llm_store,
    ocr_store,
    prefill_lock,
    prefill_status,
)
from saimc.extraction.engine import mentions_any_product
from saimc.glossary import active_glossary_entries, effective_prompt_version
from saimc.llm.provider import LLMConfig, canonical_model, effective_model, resolve_config
from saimc.models import (
    ContentType,
    LlmProposal,
    ProcessingStatus,
    Product,
    ProposalStatus,
    Province,
    RawIngestion,
)
from saimc.models.base import utc_now
from saimc.privacy import redact_personal_data
from saimc.progress import pilot_products
from saimc.validation import jev_signal, lane0, lane1
from saimc.validation.context import Catalog

logger = logging.getLogger(__name__)

# Consumo medido por anuncio con Sonnet 5 y el prompt `2026-09-25.3` sobre 100
# anuncios reales (`docs/costos.md`): ~2407 tokens de entrada y ~245 de salida,
# $0,0073 por anuncio. Se usa solo mientras no haya propuestas guardadas.
_DOC_INPUT_TOKENS = 2407
_DOC_OUTPUT_TOKENS = 245

# Fallas que no tiene sentido reintentar en la misma corrida.
_ABORT_FAILURES = frozenset({"credit", "auth"})

_TYPE_LABELS = {ContentType.TEXT.value: "texto", ContentType.IMAGE.value: "imagen"}


# --- selección --------------------------------------------------------------


def _focus_text(session: Session, ingestion: RawIngestion) -> str | None:
    """El texto con que se decide el foco, o `None` si no se sabe sin pagar.

    Un texto, su original. Una imagen, la transcripción del OCR ya guardada
    (slice OCR como texto): es el mismo texto que verán el modelo y los
    carriles. Una imagen sin transcripción guardada no tiene foco conocido.
    """
    if ingestion.content_type == ContentType.TEXT:
        return ingestion.original_text
    return ocr_store.saved_transcription(session, ingestion)


def _in_focus(
    session: Session,
    ingestion: RawIngestion,
    products: Sequence[Product],
    off_focus_types: frozenset[str],
) -> bool:
    if str(ingestion.content_type) in off_focus_types:
        return True
    text = _focus_text(session, ingestion)
    return text is None or mentions_any_product(text, products)


def _off_focus_types(*, texts: bool, images: bool) -> frozenset[str]:
    return frozenset(
        tipo
        for tipo, on in ((ContentType.TEXT.value, texts), (ContentType.IMAGE.value, images))
        if on
    )


def select_candidates(
    session: Session,
    products: Sequence[Product],
    limit: int | None = None,
    *,
    include_off_focus: bool = False,
    include_off_focus_images: bool = False,
) -> list[RawIngestion]:
    """Pendientes sin propuesta `ok` guardada, más nuevo primero, EN FOCO.

    Mismo orden que la cola (`captured_at` descendente, `_pending_all` en
    `curation.py`): el operador publica con lo de las últimas 24 h, así que con
    el tope de gasto por corrida lo de hoy no puede esperar detrás del
    histórico (2026-10-13). Y el mismo criterio de foco que `_pending_in_focus`: un
    texto que no menciona ningún producto del foco se descarta en lote
    (`discard_out_of_focus`) sin pasar nunca por curaduría, así que pagarle
    una propuesta acá es tirar la plata (docs/plan-asistente-llm-cola-
    completa.md, pendiente suelto). Una imagen con el OCR ya guardado decide
    su foco con ese texto (slice OCR como texto); una sin transcribir sigue
    entrando — su foco no se sabe sin pagar el OCR, que esta misma corrida ya
    paga al pedir el texto.

    El filtro corre en Python, después de traer todos los pendientes: el
    `limit` tiene que contar candidatos YA filtrados, no filas de la tabla —
    aplicarlo en SQL antes de filtrar podría devolver menos de `limit` aunque
    sobren pendientes en foco más nuevos.

    Con `include_off_focus` (el carril 0 de texto encendido, B2a) entran
    también los textos sin foco: el carril 0 exige que las reglas no vean
    ningún piloto, así que sin pagarles propuesta no se descartaría ninguno.
    Apagado, no se gasta API en textos que nada va a descartar solo.
    `include_off_focus_images` es lo mismo para las imágenes (el carril 0 de
    imágenes encendido).
    """
    has_ok = exists().where(
        LlmProposal.ingestion_id == RawIngestion.id,
        LlmProposal.status == ProposalStatus.OK,
    )
    stmt = (
        select(RawIngestion)
        .where(RawIngestion.processing_status == ProcessingStatus.PENDING, ~has_ok)
        .order_by(RawIngestion.captured_at.desc())
    )
    pending = list(session.execute(stmt).scalars().all())
    off_focus = _off_focus_types(texts=include_off_focus, images=include_off_focus_images)
    en_foco = [
        ingestion for ingestion in pending if _in_focus(session, ingestion, products, off_focus)
    ]
    return en_foco if limit is None else en_foco[:limit]


def select_stale_candidates(
    session: Session,
    products: Sequence[Product],
    current_version: str,
    limit: int | None = None,
    *,
    include_off_focus: bool = False,
    include_off_focus_images: bool = False,
) -> list[RawIngestion]:
    """Pendientes cuya última propuesta buena se hizo con otro prompt, EN FOCO.

    `current_version` es la versión efectiva de hoy (`PROMPT_VERSION` más el hash
    del glosario activo). Los que no tienen propuesta no entran: son de
    `select_candidates`. Mismo orden (más nuevo primero) y mismo criterio de
    foco. Sirve para rehacer lo que se propuso antes de que el glosario o el
    vocabulario existieran: ahí nacen las abstenciones que frenan el carril 1
    (`docs/analisis-carril1-frenos.md`, §3).

    Una imagen entra solo con el OCR ya guardado (slice OCR como texto):
    rehacerla no suma OCR. Sin transcripción guardada sigue afuera.
    """
    stmt = (
        select(RawIngestion)
        .where(RawIngestion.processing_status == ProcessingStatus.PENDING)
        .order_by(RawIngestion.captured_at.desc())
    )
    off_focus = _off_focus_types(texts=include_off_focus, images=include_off_focus_images)
    stale: list[RawIngestion] = []
    for ingestion in session.execute(stmt).scalars().all():
        latest = llm_store.latest_ok_proposal(session, ingestion.id)
        if latest is None or latest.prompt_version == current_version:
            continue
        if (
            ingestion.content_type != ContentType.TEXT
            and ocr_store.saved_transcription(session, ingestion) is None
        ):
            continue
        if not _in_focus(session, ingestion, products, off_focus):
            continue
        stale.append(ingestion)
    return stale if limit is None else stale[:limit]


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
    return llm_assist.usage_cost_usd(input_tokens, output_tokens, model)


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


def print_estimate(
    est: Estimate,
    out: Callable[[str], None] = print,
    *,
    title: str = "Pendientes sin propuesta guardada:",
) -> None:
    out(title)
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
    #: Tipo -> anuncios que el carril 0 descartó solos en esta corrida.
    auto_discarded: dict[str, int] = field(
        default_factory=lambda: dict.fromkeys(_TYPE_LABELS, 0)
    )
    #: Anuncios que el carril 1 aceptó solos en esta corrida (B2b-2).
    auto_accepted: int = 0
    #: De ellos, imágenes (por su OCR, slice OCR como texto).
    auto_accepted_images: int = 0


def run_prefill(
    session: Session,
    candidates: Sequence[RawIngestion],
    *,
    api_key: str | None = None,
    model: str = "claude-sonnet-5",
    timeout: float = 120.0,
    raw_store_dir: Path,
    log_path: Path,
    client: Any | None = None,
    transcribe: ocr_store.Transcriber | None = None,
    max_cost: float | None = None,
    expected_per_ad: float | None = None,
    lane0_types: frozenset[str] = frozenset(),
    lane0_jev_hash: str | None = None,
    lane0_jev_arbiter: bool = False,
    lane0_jev_images: bool = False,
    lane1_fetcher: Fetcher | None = None,
    lane1_gate: lane1.Gate | None = None,
    lane1_images: bool = False,
    out: Callable[[str], None] = print,
    llm: LLMConfig | None = None,
) -> RunResult:
    """Un anuncio a la vez: texto, llamada, alta de la propuesta y commit.

    `llm` es la configuración del proveedor (`saimc.llm.provider`); es lo que
    pasa `execute`. Si viene, manda sobre `api_key`, `model` y `timeout`, igual
    que en `propose_ad`, y lo que se guarda y se tarifa es `llm.model`, el
    nombre canónico. Sin `llm`, los tres sueltos contra Anthropic directo.

    `lane0_types` son los tipos con el carril 0 encendido
    (`validation.lane0.enabled_types`); vacío, ningún anuncio se descarta solo.
    `lane0_jev_hash` es la huella vigente de Jev si Jev decide en el carril 0
    (`validation.lane0.jev_questions_hash`); `None`, Jev queda en sombra.
    `lane0_jev_arbiter` suma a Jev como árbitro del desacuerdo (slice
    jev-desacuerdo); solo cuenta con `lane0_jev_hash`. `lane0_jev_images` le da
    a Jev también las imágenes (`jev_signal.ocr_enabled`, slice OCR como texto).

    `lane1_fetcher` enciende el carril 1 (`validation.lane1`): es de dónde sale
    la tasa de cambio de una fila en divisa. `None`, ningún anuncio se acepta
    solo. Va después del carril 0 y solo si este no descartó. `lane1_gate` es
    su freno (`validation.lane1.Gate`), uno por tipo: con el de un tipo
    puesto, nada de ese tipo se acepta. `lane1_images` deja entrar también
    las imágenes (`validation.lane1.image_enabled`, slice OCR como texto).

    Un solo texto por anuncio (slice OCR como texto): el carril 0 y el carril 1
    reciben el mismo, ya redactado. De un texto, el original (redactado en la
    ingesta); de una imagen, su OCR guardado, redactado otra vez por si acaso
    (`redact_personal_data` es idempotente). Ninguno lee `original_text` de una
    imagen.

    Con `max_cost`, antes de cada llamada se frena si el acumulado más lo que
    se espera que cueste la siguiente lo pasaría. «Lo que se espera» es el
    promedio real de esta corrida, o `expected_per_ad` antes de la primera.
    """
    if llm is None and not api_key:
        # Sin config ni clave `propose_ad` degradaría cada anuncio a `no_key`
        # sin avisar; mejor fallar acá, antes de gastar nada.
        raise ValueError("run_prefill necesita `llm` o `api_key`")
    if llm is not None:
        model = llm.model
    products = pilot_products(session)
    provinces = list(session.execute(select(Province).order_by(Province.name)).scalars())
    glossary = active_glossary_entries(session)
    catalog = Catalog(pilot_products=products, provinces=provinces)
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
        text = llm_propose_ad.ad_text(session, ingestion, raw_store_dir, transcribe)
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
            glossary=glossary,
            llm=llm,
        )
        stored = llm_store.save_proposal(
            session, ingestion_id=ingestion.id, result=proposal, model=model
        )
        # El texto que vio el modelo: el de un texto ya viene redactado de la
        # ingesta; el OCR de una imagen se redacta acá, igual que en el borde
        # de `propose_ad`. Es el mismo para los dos carriles.
        lane_text = (
            text
            if ingestion.content_type == ContentType.TEXT
            else redact_personal_data(text) or ""
        )
        auto_discarded = (
            stored is not None
            and str(ingestion.content_type) in lane0_types
            and lane0.apply_lane_0(
                session,
                ingestion,
                stored,
                proposal,
                text=lane_text,
                catalog=catalog,
                jev_hash=lane0_jev_hash,
                arbiter=lane0_jev_arbiter,
                jev_images=lane0_jev_images,
            )
        )
        auto_accepted = (
            stored is not None
            and not auto_discarded
            and lane1_fetcher is not None
            and lane1.apply_lane_1(
                session,
                ingestion,
                stored,
                proposal,
                text=lane_text,
                catalog=catalog,
                fetcher=lane1_fetcher,
                gate=lane1_gate,
                images=lane1_images,
            )
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
        if auto_discarded:
            tipo = str(ingestion.content_type)
            result.auto_discarded[tipo] = result.auto_discarded.get(tipo, 0) + 1
        result.auto_accepted += auto_accepted
        if auto_accepted and ingestion.content_type != ContentType.TEXT:
            result.auto_accepted_images += 1
        result.cost_usd += _cost(proposal.input_tokens, proposal.output_tokens, model) or 0.0
        estado = (
            f"DEGRADADO ({proposal.failure})"
            if proposal.degraded
            else f"ok, {len(proposal.rows)} filas"
            + (" (sugiere descartar)" if proposal.discard else "")
            + (" — DESCARTADO SOLO (carril 0)" if auto_discarded else "")
            + (" — ACEPTADO SOLO (carril 1)" if auto_accepted else "")
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
        "--stale",
        action="store_true",
        help=(
            "En vez de los pendientes sin propuesta, rehace los que ya tienen una hecha con "
            "otro prompt (antes del glosario o del vocabulario actual). Con --execute gasta."
        ),
    )
    parser.add_argument(
        "--max-cost",
        type=float,
        default=None,
        help="Tope en USD: frena antes de pasarlo (según el gasto real de la corrida).",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Sin prefijo de proveedor. Por defecto, SAIMC_LLM_MODEL (o SAIMC_LLM_ASSIST_MODEL).",
    )
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
    transcribe: ocr_store.Transcriber | None = None,
    jev_http: httpx.Client | None = None,
    jev_sleep: Callable[[float], None] = time.sleep,
    rate_fetcher: Fetcher | None = None,
    out: Callable[[str], None] = print,
) -> int:
    """Corre el modo pedido. Devuelve el código de salida.

    `jev_http` y `jev_sleep` son para los tests de la pasada de Jev: en la
    corrida real quedan en su valor por defecto. `rate_fetcher` es la tasa de
    cambio para el carril 1; por defecto, elTOQUE con la configuración.

    La corrida real va entera bajo el candado, pasada de Jev incluida: también
    gasta. Si otra corrida lo tiene, se sale con 0 —no es una falla, y con otro
    código systemd dejaría la unidad en «failed»— y sin `_record_run`, para que
    `/control` siga mostrando la última corrida que sí corrió.
    """
    if not args.execute:
        return _run_mode(
            args, session, settings,
            client=client, transcribe=transcribe, jev_http=jev_http, jev_sleep=jev_sleep,
            rate_fetcher=rate_fetcher, out=out,
        )
    with prefill_lock.run_lock(settings.prefill_lock_path) as tomado:
        if not tomado:
            out(
                "Ya hay otra corrida del pre-cálculo en marcha "
                f"(candado {settings.prefill_lock_path}): esta no arranca y no gasta nada. "
                "No es un error: lo que quede pendiente lo toma la próxima corrida."
            )
            return 0
        return _run_mode(
            args, session, settings,
            client=client, transcribe=transcribe, jev_http=jev_http, jev_sleep=jev_sleep,
            rate_fetcher=rate_fetcher, out=out,
        )


def _run_mode(
    args: argparse.Namespace,
    session: Session,
    settings: Settings,
    *,
    client: Any | None,
    transcribe: ocr_store.Transcriber | None,
    jev_http: httpx.Client | None,
    jev_sleep: Callable[[float], None],
    rate_fetcher: Fetcher | None,
    out: Callable[[str], None],
) -> int:
    """El modo pedido, ya con el candado tomado si es la corrida real."""
    # La pasada de Jev va primero y aparte: antes del bucle de Claude (que con
    # el carril 0 encendido descarta), sin depender de su clave y sin tocar el
    # código de salida.
    jev = _jev_pass(args, session, settings, client=jev_http, sleep=jev_sleep, out=out)
    # Canónico (sin prefijo de proveedor): con él se estima y se tarifa. No
    # necesita clave, así que el dry-run sigue andando sin ninguna.
    model = canonical_model(args.model) if args.model else effective_model(settings)
    # El tope de edad de la cola corre primero en cada pasada real, antes de
    # elegir candidatos: no se gasta API en un anuncio que ya vence, y un
    # pendiente viejo no espera a que haya trabajo ni clave.
    if args.execute:
        vencidos = queue_expiry.expire_stale(
            session, now=utc_now(), max_age_days=settings.queue_max_age_days
        )
        session.commit()
        if settings.queue_max_age_days > 0:
            out(f"Vencidos por edad (más de {settings.queue_max_age_days} días): {vencidos}")
    products = pilot_products(session)
    lane0_types = lane0.enabled_types(settings)
    off_focus = {
        "include_off_focus": ContentType.TEXT.value in lane0_types,
        "include_off_focus_images": ContentType.IMAGE.value in lane0_types,
    }
    if args.stale:
        version = effective_prompt_version(
            llm_propose_ad.PROMPT_VERSION, active_glossary_entries(session)
        )
        candidates = select_stale_candidates(session, products, version, args.limit, **off_focus)
        title = f"Pendientes con propuesta de prompt viejo (la de hoy es {version}):"
    else:
        candidates = select_candidates(session, products, args.limit, **off_focus)
        title = "Pendientes sin propuesta guardada:"
    est = estimate(session, candidates, model)

    if not args.execute:
        out("MODO DRY-RUN: no se llama a ninguna API ni se necesita clave.")
        print_estimate(est, out, title=title)
        out("Para la corrida real: agregá --execute (usa la clave de la configuración).")
        return 0

    try:
        llm = resolve_config(settings)
    except ValueError as error:
        # El mensaje nombra la variable que falta según el proveedor elegido.
        out(f"No hay clave de API para la corrida real. {error}. Sin clave no se llama a nada.")
        _record_run(settings, 2, RunResult(), jev)
        return 2
    # `--model` y `--timeout` pisan lo de la configuración; el modelo que se
    # manda (con prefijo en Vercel) se recalcula ahí.
    llm = llm.with_overrides(model=args.model, timeout=args.timeout)
    print_estimate(est, out, title=title)
    # El freno del carril 1 se lee una vez, antes de gastar: si está puesto,
    # la corrida igual guarda propuestas, pero no acepta nada.
    lane1_gate = lane1.Gate(session, settings) if lane1.enabled(settings) else None
    if est.per_ad_usd is None:
        out(f"AVISO: {model} no tiene tarifa en llm_assist.py; el costo acumulado no se estima.")
    result = run_prefill(
        session,
        candidates,
        llm=llm,
        raw_store_dir=settings.raw_store_dir,
        log_path=settings.llm_assist_log_path,
        client=client,
        transcribe=transcribe,
        max_cost=args.max_cost,
        expected_per_ad=est.per_ad_usd,
        lane0_types=lane0_types,
        lane0_jev_hash=lane0.jev_questions_hash(session, settings),
        lane0_jev_arbiter=settings.lane0_jev_arbiter_enabled,
        lane0_jev_images=jev_signal.ocr_enabled(settings),
        lane1_fetcher=(
            (rate_fetcher or lane1.rate_fetcher(settings)) if lane1.enabled(settings) else None
        ),
        lane1_gate=lane1_gate,
        lane1_images=lane1.image_enabled(settings),
        out=out,
    )
    out(
        f"Hechos: {result.done} ({result.degraded} degradados), "
        f"sin texto: {result.skipped_no_text}, costo real ${result.cost_usd:.4f}."
    )
    out(
        "Descartados solos (carril 0): "
        + ", ".join(
            f"{etiqueta} {result.auto_discarded.get(tipo, 0)}"
            for tipo, etiqueta in _TYPE_LABELS.items()
        )
        + ("" if lane0_types else " (interruptores apagados)")
    )
    images = lane1.image_enabled(settings)
    out(
        f"Aceptados solos (carril 1): {result.auto_accepted}"
        + ("" if lane1.enabled(settings) else " (interruptor apagado)")
        + (
            f" — texto {result.auto_accepted - result.auto_accepted_images}, "
            f"imagen {result.auto_accepted_images}"
            if images
            else ""
        )
        + (
            f" — freno puesto: {lane1_gate.brake.reason}"
            if lane1_gate is not None and lane1_gate.brake.on
            else ""
        )
        + (
            f" — freno de imágenes puesto: {lane1_gate.brake_of(lane1.IMAGE).reason}"
            if images and lane1_gate is not None and lane1_gate.brake_of(lane1.IMAGE).on
            else ""
        )
    )
    code = 1 if result.aborted_failure else 0
    _record_run(settings, code, result, jev)
    return code


def _jev_pass(
    args: argparse.Namespace,
    session: Session,
    settings: Settings,
    *,
    client: httpx.Client | None,
    sleep: Callable[[float], None],
    out: Callable[[str], None],
) -> jev_signal.PassResult | None:
    """La pasada de Jev, sin que nada de ella pueda frenar el pre-cálculo.

    `--limit` también la acota: quien prueba con `--limit 1` no espera decenas
    de llamadas a otro proveedor.
    """
    try:
        return jev_signal.prefill_pass(
            session,
            settings,
            execute=args.execute,
            limit=args.limit,
            client=client,
            sleep=sleep,
            out=out,
        )
    except Exception:
        # Amplio a propósito: Jev es una señal en sombra y ningún fallo suyo
        # (ni uno de programación) puede costarle la corrida a Claude. El
        # `rollback` deja la sesión usable para el bucle que sigue.
        logger.exception("jev.pasada_fallo")
        session.rollback()
        out("Jev: la pasada falló y se salteó (el detalle está en el registro).")
        return None


def _record_run(
    settings: Settings,
    code: int,
    result: RunResult,
    jev: jev_signal.PassResult | None = None,
) -> None:
    """Deja anotado cómo terminó la corrida real, para la franja de `/control`."""
    jev = jev or jev_signal.PassResult()
    prefill_status.write_run_status(
        settings.prefill_status_path,
        prefill_status.PrefillRun(
            finished_at=datetime.now(UTC),
            exit_code=code,
            done=result.done,
            degraded=result.degraded,
            skipped_no_text=result.skipped_no_text,
            cost_usd=result.cost_usd,
            stopped_by_max_cost=result.stopped_by_max_cost,
            aborted_failure=result.aborted_failure,
            jev_asked=jev.asked,
            jev_failed=jev.failed,
            jev_cost_usd=jev.cost_usd,
            jev_stopped=jev.stopped,
        ),
    )


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    with session_scope() as session:
        code = execute(args, session, get_settings())
    sys.exit(code)


if __name__ == "__main__":
    main()
