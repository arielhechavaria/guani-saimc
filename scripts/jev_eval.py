#!/usr/bin/env python
"""Prueba de Jev fuera de línea: ¿su probabilidad está calibrada? (slice A6)

Jev (TypeSafe AI, vía Vercel AI Gateway) contesta preguntas cerradas con una
probabilidad. Antes de fijarle un umbral hay que saber si esa probabilidad
dice la verdad sobre anuncios cubanos: si de los anuncios a los que les pone
0,9 acierta nueve de cada diez. Este script lo mide, por pregunta, contra
decisiones que el operador ya tomó en la cola.

- **Triage** (sí/no), a todo anuncio de texto con decisión: «¿ofrece un
  producto piloto con precio?». Verdad: curado → sí, descartado → no.
- **Campos cerrados** (elección), solo a los curados y en la misma llamada:
  producto, moneda, provincia y formato de venta. La verdad es lo que quedó
  guardado en `prices_history`, nunca la propuesta del extractor.

Está fuera del camino crítico: nada de `src/saimc` lo importa, y no escribe en
la base. Solo lee, llama a la API y deja dos archivos bajo `data/jev_eval/`
(`data/` no va a git): el caché de respuestas y el reporte.

Desde A6b el cliente (el pedido, la lectura de la respuesta y la red) vive en
`saimc.validation.jev_client`, que es el que usa el sistema: este script lo
importa. Acá queda lo que es de A6: la muestra, su juego de cinco preguntas
—armado con las piezas compartidas, así la huella del caché no cambia—, el
caché, la corrida, las métricas y el reporte.

El reporte **no decide**: muestra los números con su n y su cota. Qué se hace
con Jev (A6b o descartarlo) lo decide el operador y va a `docs/decisions.md`.

Privacidad: el texto sale redactado (`saimc.privacy`), y el caché no guarda ni
el texto del anuncio ni la clave.

Uso (desde la raíz del repo):
    python scripts/jev_eval.py --check                 # una llamada sintética: ¿anda la clave?
    python scripts/jev_eval.py                         # en seco: n por pregunta y costo estimado
    python scripts/jev_eval.py --execute --limit 50    # corrida corta
    python scripts/jev_eval.py --execute               # corrida completa (tope: --max-cost)

Sin `--execute` no llama a la red ni escribe nada, como el resto de los scripts.

Cada respuesta de elección trae dos señales, que se miden por separado: la
probabilidad de la opción elegida (`probabilities[choice]`) y `confidence`, que
no es lo mismo y cuyo cálculo el proveedor no documenta.

Contrato: `docs/plan-factor-humano.md`, «A6 — Prueba de Jev fuera de línea».
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx

# `python-dotenv` no está en `pyproject.toml` por nombre: lo trae
# `pydantic-settings`, que es el que lee el `.env` para el resto del sistema.
from dotenv import dotenv_values
from sqlalchemy import select
from sqlalchemy.orm import Session

from saimc.db import session_scope
from saimc.models import (
    Channel,
    ContentType,
    Currency,
    LlmProposal,
    PriceHistory,
    ProcessingStatus,
    Product,
    ProposalStatus,
    Province,
    RawIngestion,
    SaleFormat,
)
from saimc.validation import jev_client

# El cliente compartido con el sistema. Se importan los nombres uno por uno
# —y no solo el módulo— para que el resto del script los siga usando como
# siempre (`ask(...)`, `JevError`, `parse_answer(...)`).
#
# Los que llevan `as` con su mismo nombre no se usan acá abajo: es la forma de
# decir «esto se reexporta a propósito» (los tests los leen como atributos del
# script), y evita que el linter los marque como imports sin usar.
from saimc.validation.jev_client import (
    API_URL as API_URL,
)
from saimc.validation.jev_client import (
    CHARS_PER_TOKEN,
    INPUT_PRICE_PER_MTOK,
    MODEL,
    OTHER_KEY,
    REQUEST_OVERHEAD_FACTOR,
    TRIAGE,
    JevError,
    ask,
    parse_answer,
    questions_hash,
    request_tokens_from_text,
    slugify,
    tokens_cost,
)
from saimc.validation.jev_client import (
    MAX_RETRY_AFTER_SECONDS as MAX_RETRY_AFTER_SECONDS,
)
from saimc.validation.jev_client import (
    RETRY_BASE_SECONDS as RETRY_BASE_SECONDS,
)
from saimc.validation.jev_client import (
    estimate_tokens as estimate_tokens,
)
from saimc.validation.jev_client import (
    parse_response as parse_response,
)
from saimc.validation.stats import Rate, pct

#: Variable de la clave de Vercel AI Gateway. El script la lee por su cuenta,
#: del entorno o del `.env`, sin pasar por `saimc.config.Settings`: corre en
#: desarrollo y no depende del interruptor del sistema.
ENV_KEY = "SAIMC_VERCEL_API_KEY"
#: El nombre anterior de la misma clave, que sigue valiendo. Mismo orden que
#: `Settings.gateway_api_key`: la nueva gana.
LEGACY_ENV_KEY = "SAIMC_LLM_JEV_API_KEY"

DEFAULT_MAX_COST = 0.20

TIMEOUT_SECONDS = 30.0
FIELDS: tuple[str, ...] = ("product", "currency", "province", "sale_format")
#: «No lo dice»: la opción de los campos que la fila curada admite en nulo.
NONE_KEY = "none"
#: Tramos del diagrama de confiabilidad: diez de 0,1.
BINS = 10

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = REPO_ROOT / "data" / "jev_eval"
CACHE_PATH = OUTPUT_DIR / "responses.jsonl"
REPORT_PATH = OUTPUT_DIR / "report.md"

_SALE_FORMAT_LABELS: dict[str, str] = {
    SaleFormat.RETAIL.value: (
        "Minorista: vende por unidad, libra, paquete, saco o caja, aunque diga «por mayor»"
    ),
    SaleFormat.WHOLESALE.value: "Mayorista: escala de contenedor",
}


# --- Catálogo y muestra -------------------------------------------------------


# `frozen=True` hace al objeto inmutable (asignar un atributo levanta), como un
# `readonly class` de PHP 8.2. Los diccionarios de adentro siguen siendo
# mutables: es una convención de uso, no un candado.
@dataclass(frozen=True)
class Catalog:
    """Las opciones cerradas que se le ofrecen a Jev: clave estable → nombre."""

    products: dict[str, str]
    provinces: dict[str, str]
    #: id de la base (como texto) → clave. No entran al pedido ni al hash:
    #: solo traducen las filas curadas y las propuestas a claves.
    product_keys: dict[str, str] = field(default_factory=dict)
    province_keys: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Ad:
    """Un anuncio de la muestra, con su verdad y lo que propuso Claude."""

    ingestion_id: str
    text: str
    curated: bool
    discard_reason: str | None = None
    #: Por campo, el CONJUNTO de valores de sus filas curadas (nulo → `none`).
    #: Vacío en los descartados.
    truth: dict[str, frozenset[str]] = field(default_factory=dict)
    channel_has_province: bool = False
    #: `discard` de la última propuesta exitosa. `None` = sin propuesta.
    claude_discard: bool | None = None
    #: Igual que `truth`, pero con las filas propuestas. Vacío si no hay filas.
    claude_fields: dict[str, frozenset[str]] = field(default_factory=dict)


def truth_values(values: Iterable[str | None]) -> frozenset[str]:
    """El conjunto de valores de las filas de un anuncio; nulo cuenta como `none`."""
    return frozenset(NONE_KEY if value is None else value for value in values)


def is_correct(choice: str, truth: frozenset[str]) -> bool:
    """La elección acierta si es uno de los valores que el operador guardó."""
    return choice in truth


# --- Pedido -------------------------------------------------------------------


def build_questions(catalog: Catalog, *, curated: bool) -> dict[str, dict[str, Any]]:
    """Las preguntas de un anuncio: el triage siempre; los campos, si es curado.

    El triage pregunta lo mismo que el `discard` del asistente
    (`llm_propose_ad._SYSTEM_PROMPT`), al revés: sí = el operador lo curaría.
    """
    # El triage, el producto y la moneda son las piezas que comparte con el
    # sistema; provincia y formato de venta solo se preguntaron en A6.
    questions: dict[str, dict[str, Any]] = {
        TRIAGE: jev_client.triage_question(catalog.products.values())
    }
    if not curated:
        return questions

    questions["product"] = jev_client.product_question(catalog.products)
    questions["currency"] = jev_client.currency_question()
    questions["province"] = {
        "type": "choice",
        "instructions": "¿En qué provincia de Cuba se vende, según el texto del anuncio?",
        "criteria": {**catalog.provinces, NONE_KEY: "El anuncio no dice la provincia"},
    }
    questions["sale_format"] = {
        "type": "choice",
        "instructions": (
            "¿La oferta es minorista o mayorista? Decidí por la unidad que se vende, no por "
            "el título del anuncio."
        ),
        "criteria": {**_SALE_FORMAT_LABELS, NONE_KEY: "El anuncio no deja determinarlo"},
    }
    return questions


def build_request(ad: Ad, catalog: Catalog) -> dict[str, Any]:
    """El cuerpo del pedido de un anuncio de la muestra. La redacción de
    teléfonos la hace el cliente, en el borde."""
    return jev_client.build_request(ad.text, build_questions(catalog, curated=ad.curated))


def _request_tokens(payload: Mapping[str, Any]) -> int:
    return request_tokens_from_text(json.dumps(payload, ensure_ascii=False))


# --- Clave --------------------------------------------------------------------


def load_api_key(env: Mapping[str, str], env_file: Path) -> str | None:
    """La clave: primero el entorno, después el `.env`. `None` si no está.

    Se busca con el nombre nuevo y, si no aparece, con el anterior.
    """
    from_file = dotenv_values(env_file) if env_file.is_file() else {}
    for name in (ENV_KEY, LEGACY_ENV_KEY):
        value = env.get(name) or from_file.get(name)
        if value:
            return value
    return None


# --- Caché --------------------------------------------------------------------

#: Clave del caché: (id de ingesta, huella de las preguntas).
CacheKey = tuple[str, str]


def load_cache(path: Path) -> dict[CacheKey, dict[str, Any]]:
    """Lee el JSONL. Una línea rota (corrida cortada a mitad de escritura) se saltea."""
    cache: dict[CacheKey, dict[str, Any]] = {}
    if not path.is_file():
        return cache
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                entry = json.loads(line)
                cache[(entry["ingestion_id"], entry["questions_hash"])] = entry
            except (ValueError, KeyError, TypeError):
                continue
    return cache


def append_cache(path: Path, entry: Mapping[str, Any]) -> None:
    """Agrega una línea. Se escribe por anuncio para que cortar la corrida no
    pierda lo ya pagado."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _hashes(catalog: Catalog) -> dict[bool, str]:
    """La huella de cada juego de preguntas: el de curados y el de descartados."""
    return {curated: questions_hash(build_questions(catalog, curated=curated)) for curated in (True, False)}


def _cached_cost(cache: Mapping[CacheKey, Mapping[str, Any]], hashes: Mapping[bool, str]) -> float:
    current = set(hashes.values())
    return sum(float(entry.get("cost_usd") or 0.0) for (_, h), entry in cache.items() if h in current)


# --- Corrida ------------------------------------------------------------------


@dataclass
class RunSummary:
    asked: int = 0
    cached: int = 0
    #: Pendientes a los que no se llegó (tope de gasto, `--limit` o corte).
    unasked: int = 0
    #: (id de ingesta, motivo).
    errors: list[tuple[str, str]] = field(default_factory=list)
    cost_run: float = 0.0
    cost_cached: float = 0.0
    stopped_by_cap: bool = False
    #: Motivo si la corrida se cortó sola (la clave dejó de ser aceptada).
    aborted: str = ""


def run_eval(
    ads: Sequence[Ad],
    catalog: Catalog,
    *,
    cache_path: Path,
    client: httpx.Client,
    api_key: str,
    max_cost: float,
    limit: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = lambda _message: None,
) -> RunSummary:
    """Pregunta lo que falta, de a uno, sin pasarse del tope. Escribe el caché.

    El tope cuenta lo gastado en esta corrida más lo ya cacheado de esta misma
    versión de preguntas. Antes de cada llamada se le suma lo que se espera que
    cueste —la estimación por caracteres o la llamada más cara vista, lo que
    sea mayor— y si con eso se pasaría, corta.
    """
    hashes = _hashes(catalog)
    cache = load_cache(cache_path)
    summary = RunSummary(cost_cached=_cached_cost(cache, hashes))

    pending = [ad for ad in ads if (ad.ingestion_id, hashes[ad.curated]) not in cache]
    summary.cached = len(ads) - len(pending)
    # Los curados primero: son pocos y llevan las preguntas por campo, así una
    # corrida cortada ya deja esa parte entera. `sort` es estable: dentro de
    # cada grupo se conserva el orden de la muestra (`False` ordena antes).
    pending.sort(key=lambda ad: not ad.curated)

    highest_cost = 0.0
    for position, ad in enumerate(pending):
        left = len(pending) - position
        if limit is not None and position >= limit:
            summary.unasked = left
            break
        payload = build_request(ad, catalog)
        expected = max(tokens_cost(_request_tokens(payload)), highest_cost)
        if summary.cost_cached + summary.cost_run + expected > max_cost:
            summary.unasked = left
            summary.stopped_by_cap = True
            break
        try:
            result = ask(client, payload, api_key, sleep=sleep)
        except JevError as exc:
            summary.errors.append((ad.ingestion_id, str(exc)))
            if exc.status in (401, 403):
                # No es un problema de este anuncio: todos los demás fallarían igual.
                summary.unasked = left - 1
                # 401 es la credencial; 403 es que autentica pero no tiene
                # acceso (plan, modelo restringido): el motivo va en el error.
                summary.aborted = (
                    "la clave no fue aceptada"
                    if exc.status == 401
                    else "el gateway denegó el acceso (el motivo está en los errores)"
                )
                break
            continue
        summary.asked += 1
        summary.cost_run += result.cost
        highest_cost = max(highest_cost, result.cost)
        append_cache(
            cache_path,
            {
                "ingestion_id": ad.ingestion_id,
                "questions_hash": hashes[ad.curated],
                "model": result.model,
                "answers": result.answers,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "cost_usd": result.cost,
                "asked_at": datetime.now(UTC).isoformat(timespec="seconds"),
            },
        )
        if summary.asked % 100 == 0:
            log(f"  {summary.asked} respondidos, {_usd(summary.cost_run)} en esta corrida")
    return summary


@dataclass(frozen=True)
class DryRun:
    n_by_question: dict[str, int]
    pending: int
    cached: int
    #: Tokens y costo estimados de lo que falta preguntar.
    tokens: int
    cost: float
    cost_cached: float


def dry_run_estimate(
    ads: Sequence[Ad], catalog: Catalog, cache: Mapping[CacheKey, Mapping[str, Any]]
) -> DryRun:
    """Lo que costaría la corrida, sin llamar a nada."""
    hashes = _hashes(catalog)
    curated = sum(1 for ad in ads if ad.curated)
    pending = [ad for ad in ads if (ad.ingestion_id, hashes[ad.curated]) not in cache]
    tokens = sum(_request_tokens(build_request(ad, catalog)) for ad in pending)
    return DryRun(
        n_by_question={TRIAGE: len(ads), **dict.fromkeys(FIELDS, curated)},
        pending=len(pending),
        cached=len(ads) - len(pending),
        tokens=tokens,
        cost=tokens_cost(tokens),
        cost_cached=_cached_cost(cache, hashes),
    )


# --- Prueba de conexión -------------------------------------------------------

#: Anuncio inventado para `--check`: ningún dato real sale en esa llamada.
CHECK_TEXT = "Vendo arroz importado. Saco de 50 lb a 12000 CUP. Entrega en La Habana."
_CHECK_CATALOG = Catalog(
    products={"arroz": "Arroz", "pollo": "Pollo"},
    provinces={"hab": "La Habana", "scu": "Santiago de Cuba"},
)


def run_check(client: httpx.Client, api_key: str) -> tuple[bool, str]:
    """Una llamada sintética: confirma la clave y la forma de la respuesta.

    Devuelve `(anduvo, mensaje para el operador)`.
    """
    ad = Ad(ingestion_id="check", text=CHECK_TEXT, curated=True)
    payload = build_request(ad, _CHECK_CATALOG)
    try:
        result = ask(client, payload, api_key)
    except JevError as exc:
        if exc.status == 401:
            return False, (
                f"La clave no fue aceptada (HTTP {exc.status}). Revisá {ENV_KEY} en el `.env` "
                "y que la clave esté activa en Vercel AI Gateway."
            )
        if exc.status == 403:
            return False, f"La clave autentica, pero el gateway denegó el acceso: {exc}"
        return False, f"La llamada falló: {exc}"

    lines = [f"Modelo: {result.model or '(no informado)'}"]
    problems: list[str] = []
    for name, question in payload["questions"].items():
        raw = result.answers.get(name)
        answer = parse_answer(question, raw)
        if answer.invalid:
            # Solo los nombres de los campos que llegaron: sirven para ver en
            # qué difiere la forma real de la documentada.
            keys = sorted(raw) if isinstance(raw, dict) else []
            problems.append(f"  {name}: {answer.invalid} (campos recibidos: {keys})")
        elif answer.choice is None:
            lines.append(f"  {name}: P(sí) = {_num(answer.p, 2)}")
        else:
            lines.append(
                f"  {name}: {answer.choice} (probabilidad de la elegida {_num(answer.p, 4)} · "
                f"`confidence` {_num(answer.confidence, 4)})"
            )
    lines.append(f"Tokens: {result.input_tokens} de entrada, {result.output_tokens} de salida")
    lines.append(f"Costo de esta llamada: {_usd(result.cost)}")
    if problems:
        lines.append("La autenticación anduvo, pero la respuesta no trae la forma esperada:")
        lines.extend(problems)
        return False, "\n".join(lines)
    lines.append("La clave anda y la respuesta trae la forma esperada.")
    return True, "\n".join(lines)


# --- Métricas (código puro) ---------------------------------------------------

#: (probabilidad, resultado): p entre 0 y 1; y es 1 si ocurrió, 0 si no.
Pair = tuple[float, int]


@dataclass(frozen=True)
class Bin:
    """Un tramo del diagrama de confiabilidad: [index/10, (index+1)/10)."""

    index: int
    n: int = 0
    #: Cuántos de los n ocurrieron (y = 1).
    k: int = 0
    mean_p: float | None = None

    @property
    def observed(self) -> float | None:
        return self.k / self.n if self.n else None


def brier(pairs: Sequence[Pair]) -> float | None:
    """Media de (p − y)². 0 es perfecto; decir siempre 0,5 da 0,25."""
    if not pairs:
        return None
    return sum((p - y) ** 2 for p, y in pairs) / len(pairs)


def reliability_bins(pairs: Sequence[Pair]) -> list[Bin]:
    """Los diez tramos, siempre los diez. p = 1,0 cae en el último."""
    grouped: list[list[Pair]] = [[] for _ in range(BINS)]
    for p, y in pairs:
        grouped[min(int(p * BINS), BINS - 1)].append((p, y))
    return [
        Bin(
            index=index,
            n=len(items),
            k=sum(y for _, y in items),
            mean_p=sum(p for p, _ in items) / len(items) if items else None,
        )
        for index, items in enumerate(grouped)
    ]


def ece(pairs: Sequence[Pair]) -> float | None:
    """Error de calibración esperado: la distancia entre la probabilidad media
    y la frecuencia observada de cada tramo, ponderada por el tamaño del tramo."""
    if not pairs:
        return None
    return sum(
        b.n / len(pairs) * abs(b.observed - b.mean_p)
        for b in reliability_bins(pairs)
        if b.observed is not None and b.mean_p is not None
    )


def triage_hits(pairs: Sequence[Pair]) -> int:
    """Aciertos del sí/no con umbral 0,5: p ≥ 0,5 se lee como sí."""
    return sum(1 for p, y in pairs if (p >= 0.5) == (y == 1))


@dataclass(frozen=True)
class Calibration:
    n: int
    hits: int
    brier: float | None
    ece: float | None
    bins: list[Bin]


def _calibration(pairs: Sequence[Pair], hits: int) -> Calibration:
    return Calibration(
        n=len(pairs), hits=hits, brier=brier(pairs), ece=ece(pairs), bins=reliability_bins(pairs)
    )


# --- Evaluación ---------------------------------------------------------------


@dataclass(frozen=True)
class TriageGroup:
    label: str
    n: int
    hits: int
    mean_p: float | None


@dataclass(frozen=True)
class TriageResult:
    calibration: Calibration
    groups: list[TriageGroup]
    #: Anuncios de la muestra sin respuesta en el caché.
    unanswered: int
    invalid: int


@dataclass(frozen=True)
class FieldResult:
    """Una pregunta `choice`. Dos señales, medidas por separado:

    - `all` / `single`: p = `probabilities[choice]`.
    - `confidence_all` / `confidence_single`: p = `confidence`.

    «single» son los anuncios con un solo valor en este campo: el caso limpio.
    """

    #: Exactitud sobre toda respuesta con opción válida, traiga la señal que traiga.
    accuracy: Rate
    accuracy_single: Rate
    all: Calibration
    single: Calibration
    confidence_all: Calibration
    confidence_single: Calibration
    #: Respuestas válidas que no trajeron `probabilities`: cuentan para la
    #: exactitud y quedan fuera de la calibración por probabilidad.
    without_probability: int
    invalid: int


@dataclass(frozen=True)
class Agreement:
    #: (Claude descarta, Jev descarta) → n.
    triage: dict[tuple[bool, bool], int]
    triage_without_proposal: int
    fields: dict[str, Rate]
    #: Curados con respuesta de Jev pero sin filas propuestas que comparar.
    fields_without_proposal: int


@dataclass(frozen=True)
class Evaluation:
    sample: int
    curated: int
    answered: int
    models: tuple[str, ...]
    cost: float
    input_tokens: int
    triage: TriageResult
    fields: dict[str, FieldResult]
    province_by_channel: dict[str, Rate]
    agreement: Agreement


def _triage_group(ad: Ad) -> str:
    if ad.curated:
        return "curado"
    return ad.discard_reason or "descartado sin motivo"


def evaluate(ads: Sequence[Ad], catalog: Catalog, cache: Mapping[CacheKey, Mapping[str, Any]]) -> Evaluation:
    """Cruza las respuestas del caché con la verdad. Código puro.

    Las respuestas se leen acá, no al guardarlas: el caché tiene lo que llegó
    tal cual, así que corregir el lector no obliga a pagar de nuevo.
    """
    hashes = _hashes(catalog)
    questions = {curated: build_questions(catalog, curated=curated) for curated in (True, False)}

    triage_pairs: list[Pair] = []
    triage_invalid = 0
    by_group: dict[str, list[Pair]] = {
        label: [] for label in ("curado", "off_focus", "no_data", "descartado sin motivo")
    }
    table = {(c, j): 0 for c in (True, False) for j in (True, False)}
    triage_without_proposal = 0

    # Por campo y por alcance («all» / «single»): los pares de cada señal y
    # los aciertos [k, n] sobre toda respuesta válida.
    scopes = ("all", "single")
    prob_pairs: dict[str, dict[str, list[Pair]]] = {n: {sc: [] for sc in scopes} for n in FIELDS}
    conf_pairs: dict[str, dict[str, list[Pair]]] = {n: {sc: [] for sc in scopes} for n in FIELDS}
    accuracy = {n: {sc: [0, 0] for sc in scopes} for n in FIELDS}
    without_probability = dict.fromkeys(FIELDS, 0)
    field_invalid = dict.fromkeys(FIELDS, 0)
    field_agreement = {name: [0, 0] for name in FIELDS}  # [k, n]
    fields_without_proposal = 0
    by_channel = {"canal con provincia": [0, 0], "canal sin provincia": [0, 0]}

    answered = 0
    cost = 0.0
    input_tokens = 0
    models: set[str] = set()

    for ad in ads:
        entry = cache.get((ad.ingestion_id, hashes[ad.curated]))
        if entry is None:
            continue
        answered += 1
        cost += float(entry.get("cost_usd") or 0.0)
        tokens = entry.get("input_tokens")
        # `bool` queda afuera a propósito: en Python `True` es un `int`.
        input_tokens += tokens if isinstance(tokens, int) and not isinstance(tokens, bool) else 0
        if entry.get("model"):
            models.add(str(entry["model"]))
        raw = entry.get("answers")
        raw = raw if isinstance(raw, dict) else {}

        triage = parse_answer(questions[ad.curated][TRIAGE], raw.get(TRIAGE))
        if triage.p is None:
            triage_invalid += 1
        else:
            pair = (triage.p, 1 if ad.curated else 0)
            triage_pairs.append(pair)
            by_group.setdefault(_triage_group(ad), []).append(pair)
            if ad.claude_discard is None:
                triage_without_proposal += 1
            else:
                table[(ad.claude_discard, triage.p < 0.5)] += 1

        if not ad.curated:
            continue
        if not ad.claude_fields:
            fields_without_proposal += 1
        for name in FIELDS:
            truth = ad.truth.get(name)
            if not truth:
                continue
            answer = parse_answer(questions[True][name], raw.get(name))
            if answer.invalid or answer.choice is None:
                field_invalid[name] += 1
                continue
            hit = is_correct(answer.choice, truth)
            if answer.p is None:
                without_probability[name] += 1
            for scope in scopes if len(truth) == 1 else scopes[:1]:
                accuracy[name][scope][0] += int(hit)
                accuracy[name][scope][1] += 1
                if answer.p is not None:
                    prob_pairs[name][scope].append((answer.p, int(hit)))
                if answer.confidence is not None:
                    conf_pairs[name][scope].append((answer.confidence, int(hit)))
            if name == "province":
                label = "canal con provincia" if ad.channel_has_province else "canal sin provincia"
                by_channel[label][0] += int(hit)
                by_channel[label][1] += 1
            proposed = ad.claude_fields.get(name)
            if proposed:
                field_agreement[name][0] += int(answer.choice in proposed)
                field_agreement[name][1] += 1

    def hits(pairs: Sequence[Pair]) -> Calibration:
        return _calibration(pairs, sum(y for _, y in pairs))

    return Evaluation(
        sample=len(ads),
        curated=sum(1 for ad in ads if ad.curated),
        answered=answered,
        models=tuple(sorted(models)),
        cost=cost,
        input_tokens=input_tokens,
        triage=TriageResult(
            calibration=_calibration(triage_pairs, triage_hits(triage_pairs)),
            groups=[
                TriageGroup(
                    label=label,
                    n=len(pairs),
                    hits=triage_hits(pairs),
                    mean_p=sum(p for p, _ in pairs) / len(pairs) if pairs else None,
                )
                for label, pairs in by_group.items()
            ],
            unanswered=len(ads) - answered,
            invalid=triage_invalid,
        ),
        fields={
            name: FieldResult(
                accuracy=Rate(*accuracy[name]["all"]),
                accuracy_single=Rate(*accuracy[name]["single"]),
                all=hits(prob_pairs[name]["all"]),
                single=hits(prob_pairs[name]["single"]),
                confidence_all=hits(conf_pairs[name]["all"]),
                confidence_single=hits(conf_pairs[name]["single"]),
                without_probability=without_probability[name],
                invalid=field_invalid[name],
            )
            for name in FIELDS
        },
        province_by_channel={label: Rate(k, n) for label, (k, n) in by_channel.items()},
        agreement=Agreement(
            triage=table,
            triage_without_proposal=triage_without_proposal,
            fields={name: Rate(k, n) for name, (k, n) in field_agreement.items()},
            fields_without_proposal=fields_without_proposal,
        ),
    )


# --- Reporte ------------------------------------------------------------------


def _num(value: float | None, digits: int = 4) -> str:
    """Número con coma decimal, como el resto de los reportes. «—» sin dato."""
    return "—" if value is None else f"{value:.{digits}f}".replace(".", ",")


def _usd(value: float) -> str:
    return f"{_num(value, 8)} USD"


def _pct(value: float | None) -> str:
    return "—" if value is None else pct(value)


def _rate_cells(k: int, n: int) -> str:
    """`k/n | % | cota inferior | cota superior`, las cuatro celdas de una tasa."""
    rate = Rate(k, n)
    return f"{k}/{n} | {_pct(rate.rate)} | {_pct(rate.lower)} | {_pct(rate.upper)}"


def _calibration_lines(title: str, calibration: Calibration, *, accuracy: bool = True) -> list[str]:
    """Una calibración: n, Brier y ECE, la exactitud (salvo que quien llama ya
    la haya mostrado aparte) y los tramos."""
    lines = [
        f"**{title}** — n = {calibration.n} · Brier {_num(calibration.brier)} · ECE {_num(calibration.ece)}",
        "",
    ]
    if accuracy:
        lines += [
            "| Exactitud | % | Cota inf. 95 % | Cota sup. 95 % |",
            "|---|---|---|---|",
            f"| {_rate_cells(calibration.hits, calibration.n)} |",
            "",
        ]
    if calibration.n == 0:
        return lines
    lines += [
        "| Tramo | n | Prob. media | Frec. observada | Cota inf. 95 % | Cota sup. 95 % |",
        "|---|---|---|---|---|---|",
    ]
    for b in calibration.bins:
        tramo = f"{_num(b.index / BINS, 1)}–{_num((b.index + 1) / BINS, 1)}"
        rate = Rate(b.k, b.n)
        lines.append(
            f"| {tramo} | {b.n} | {_num(b.mean_p, 3)} | {_pct(b.observed)} | "
            f"{_pct(rate.lower)} | {_pct(rate.upper)} |"
        )
    lines.append("")
    return lines


def render_report(evaluation: Evaluation, *, today: str) -> str:
    """El reporte en Markdown. Muestra los números; no decide nada."""
    e = evaluation
    models = ", ".join(e.models) or "(sin respuestas todavía)"
    out = [
        f"# Prueba de Jev fuera de línea (A6) — modelo devuelto por la API: {models}",
        "",
        f"- Fecha: {today}",
        f"- Modelo devuelto por la API: {models}",
        f"- Muestra: {e.sample} anuncios de texto con decisión ({e.curated} curados, "
        f"{e.sample - e.curated} descartados)",
        f"- Con respuesta del modelo: {e.answered} · sin respuesta todavía: {e.triage.unanswered}",
        f"- Costo total de las respuestas usadas, según lo informó el gateway: {_usd(e.cost)} "
        f"({e.input_tokens} tokens de entrada)",
        "",
        "## Cómo leer",
        "",
        "- **Brier**: media de (p − y)². 0 es perfecto; contestar siempre 0,5 da 0,25.",
        "- **Tramos**: los anuncios agrupados por la probabilidad que dio el modelo, de a 0,1. "
        "Calibrado quiere decir que la frecuencia observada de cada tramo se parece a su "
        "probabilidad media.",
        "- **ECE**: la distancia media entre esas dos columnas, ponderada por el n del tramo.",
        "- **Cotas**: exactas (Clopper-Pearson), unilaterales al 95 % cada una, la convención "
        "de `saimc.validation.stats`. Un porcentaje sobre un n chico no dice nada: mirá la cota.",
        "",
        "## Triage — «¿ofrece un producto piloto con precio?»",
        "",
        "p = `noul`, la probabilidad de sí. Verdad: curado = sí, descartado = no. Exactitud con umbral 0,5.",
        "",
        *_calibration_lines("Todos", e.triage.calibration),
        "Por decisión del operador (acierto = sí en los curados, no en los descartados):",
        "",
        "| Grupo | Aciertos | % | Cota inf. 95 % | Cota sup. 95 % | P(sí) media |",
        "|---|---|---|---|---|---|",
    ]
    for group in e.triage.groups:
        out.append(f"| {group.label} | {_rate_cells(group.hits, group.n)} | {_num(group.mean_p, 3)} |")
    out += ["", f"Respuestas de triage ilegibles: {e.triage.invalid}.", ""]

    out += [
        "## Campos cerrados (solo curados)",
        "",
        "La verdad es el conjunto de valores de las filas curadas del anuncio (nulo = "
        "`none`): acierta si la opción elegida está en el conjunto. «Un solo valor» son los "
        "anuncios cuyas filas coinciden en ese campo, el caso limpio.",
        "",
        "Cada respuesta trae **dos señales distintas** y se miden por separado, con y = 1 si "
        "la opción elegida es correcta. En los dos casos es calibración de la opción elegida "
        "(top-1), no de la distribución completa.",
        "",
        "- **Probabilidad de la elegida**: p = `probabilities[choice]`, la probabilidad que el "
        "modelo le da a la opción que eligió.",
        "- **`confidence`**: p = el campo `confidence`. Es la señal que el proveedor ofrece "
        "para enrutar. No es la probabilidad de la elegida (en una respuesta real: 0,1974 "
        "contra 0,6558) y no se sabe cómo se calcula.",
        "",
        "Las dos tablas están para ver cuál de las dos sirve, si alguna. Una respuesta sin "
        "`probabilities` cuenta para la exactitud y para `confidence`, y queda fuera de la "
        "calibración por probabilidad; su n se informa en cada campo.",
        "",
    ]
    for name in FIELDS:
        result = e.fields[name]
        out += [
            f"### {name}",
            "",
            "| Exactitud | Aciertos | % | Cota inf. 95 % | Cota sup. 95 % |",
            "|---|---|---|---|---|",
            f"| Todos los curados | {_rate_cells(result.accuracy.k, result.accuracy.n)} |",
            f"| Un solo valor en el campo | "
            f"{_rate_cells(result.accuracy_single.k, result.accuracy_single.n)} |",
            "",
            f"Respuestas ilegibles: {result.invalid}. Válidas sin `probabilities` (fuera de la "
            f"calibración por probabilidad): {result.without_probability}.",
            "",
            "#### Señal 1: probabilidad de la elegida (`probabilities[choice]`)",
            "",
            *_calibration_lines("Todos los curados", result.all, accuracy=False),
            *_calibration_lines("Un solo valor en el campo", result.single, accuracy=False),
            "#### Señal 2: `confidence`",
            "",
            *_calibration_lines("Todos los curados", result.confidence_all, accuracy=False),
            *_calibration_lines("Un solo valor en el campo", result.confidence_single, accuracy=False),
        ]
        if name == "province":
            out += [
                "Provincia según el canal del anuncio:",
                "",
                "| Grupo | Aciertos | % | Cota inf. 95 % | Cota sup. 95 % |",
                "|---|---|---|---|---|",
                *(
                    f"| {label} | {_rate_cells(rate.k, rate.n)} |"
                    for label, rate in e.province_by_channel.items()
                ),
                "",
            ]

    table = e.agreement.triage
    with_proposal = sum(table.values())
    agree = table[(True, True)] + table[(False, False)]
    out += [
        "## Acuerdo con Claude (`llm_proposals`)",
        "",
        "Contra la propuesta exitosa más reciente de cada anuncio. Es acuerdo entre dos "
        "modelos, no acierto: ninguno de los dos es la verdad.",
        "",
        "Triage: `discard` de Claude contra `noul` < 0,5 del modelo.",
        "",
        "| | El modelo descarta | El modelo no descarta |",
        "|---|---|---|",
        f"| Claude descarta | {table[(True, True)]} | {table[(True, False)]} |",
        f"| Claude no descarta | {table[(False, True)]} | {table[(False, False)]} |",
        "",
        "| Acuerdo | % | Cota inf. 95 % | Cota sup. 95 % |",
        "|---|---|---|---|",
        f"| {_rate_cells(agree, with_proposal)} |",
        "",
        f"Anuncios con respuesta del modelo y sin propuesta: {e.agreement.triage_without_proposal} "
        "(fuera del acuerdo).",
        "",
        "Campos: la opción del modelo contra el conjunto de valores de las filas propuestas.",
        "",
        "| Campo | Acuerdo | % | Cota inf. 95 % | Cota sup. 95 % |",
        "|---|---|---|---|---|",
        *(f"| {name} | {_rate_cells(rate.k, rate.n)} |" for name, rate in e.agreement.fields.items()),
        "",
        f"Curados con respuesta del modelo y sin filas propuestas: "
        f"{e.agreement.fields_without_proposal} (fuera del acuerdo).",
        "",
        "## Límites conocidos",
        "",
        "- Los curados son pocos: las preguntas por campo tienen cotas anchas. Este reporte "
        "muestra cada número con su n y no fija umbrales.",
        "- **Provincia:** la curaduría completa la provincia con la del canal cuando el canal "
        "la tiene, aunque el texto no la diga. La verdad puede no estar en el texto que el modelo "
        "lee, así que un «none» suyo puede contar como error sin serlo. Por eso el desglose "
        "por canal.",
        "- **Producto:** las opciones son los productos piloto más `other`. Con varios "
        "productos en el anuncio, cualquiera de los curados cuenta como acierto.",
        "- **Moneda:** el sistema asume CUP cuando el anuncio no la dice; la pregunta le pide "
        "al modelo lo mismo.",
        "- **Contexto:** el script no trunca; un anuncio que no entra en el contexto del "
        "modelo da error y queda sin respuesta.",
        "- El triage compara contra la decisión del operador, que incluye descartes de "
        "anuncios con datos pero fuera del foco (`off_focus`).",
        "- Solo texto. Las imágenes necesitan la transcripción de K3.",
        "- No se pregunta coherencia de precio ni un puntaje global de confianza.",
        "",
    ]
    return "\n".join(out)


# --- Base ---------------------------------------------------------------------


def load_catalog(session: Session) -> Catalog:
    """Productos piloto y provincias, con su clave estable.

    Los productos son los del foco (`products.is_pilot`), la misma lista que el
    sistema le ofrece al extractor (`saimc.progress.pilot_products`): el resto
    del catálogo son altas sueltas de la curaduría, no opciones cerradas.
    """
    products: dict[str, str] = {}
    product_keys: dict[str, str] = {}
    stmt = select(Product.id, Product.name).where(Product.is_pilot.is_(True)).order_by(Product.name)
    for product_id, name in session.execute(stmt).all():
        key = slugify(name)
        if key in products or key in (OTHER_KEY, NONE_KEY):
            raise ValueError(f"clave de producto repetida o reservada: {key!r}")
        products[key] = name
        product_keys[str(product_id)] = key

    provinces: dict[str, str] = {}
    province_keys: dict[str, str] = {}
    for province_id, code, name in session.execute(
        select(Province.id, Province.code, Province.name).order_by(Province.name)
    ).all():
        provinces[code] = name
        province_keys[str(province_id)] = code

    return Catalog(
        products=products,
        provinces=provinces,
        product_keys=product_keys,
        province_keys=province_keys,
    )


def _field_sets(rows: Iterable[Mapping[str, Any]], catalog: Catalog) -> dict[str, frozenset[str]]:
    """El conjunto de valores por campo de unas filas (curadas o propuestas).

    Sirve para las dos porque ambas traen los mismos cuatro nombres:
    `product_id`, `currency`, `province_id`, `sale_format`.
    """
    rows = list(rows)
    if not rows:
        return {}

    # Un producto que no es piloto (o que no vino) cae en `other`.
    products = [catalog.product_keys.get(str(r.get("product_id")), OTHER_KEY) for r in rows]
    # Una provincia en nulo queda en `None`, que `truth_values` lee como `none`.
    provinces = [catalog.province_keys.get(str(r.get("province_id"))) for r in rows]
    return {
        "product": truth_values(products),
        "currency": truth_values(r.get("currency") or Currency.CUP.value for r in rows),
        "province": truth_values(provinces),
        "sale_format": truth_values(r.get("sale_format") for r in rows),
    }


def load_ads(session: Session, catalog: Catalog) -> list[Ad]:
    """La muestra: anuncios de texto curados o descartados, con texto.

    Tres consultas (anuncios, filas curadas, propuestas) en vez de una por
    anuncio.
    """
    decided = (ProcessingStatus.CURATED.value, ProcessingStatus.DISCARDED.value)

    curated_rows: dict[str, list[dict[str, Any]]] = {}
    price_stmt = (
        select(
            PriceHistory.source_id,
            PriceHistory.product_id,
            PriceHistory.currency,
            PriceHistory.province_id,
            PriceHistory.sale_format,
        )
        .join(RawIngestion, RawIngestion.id == PriceHistory.source_id)
        .where(RawIngestion.content_type == ContentType.TEXT.value)
    )
    for source_id, product_id, currency, province_id, sale_format in session.execute(price_stmt):
        curated_rows.setdefault(str(source_id), []).append(
            {
                "product_id": product_id,
                "currency": currency,
                "province_id": province_id,
                "sale_format": sale_format,
            }
        )

    # Ordenadas de vieja a nueva: la última que se asigna es la más reciente.
    proposals: dict[str, tuple[bool | None, list[dict[str, Any]]]] = {}
    proposal_stmt = (
        select(LlmProposal.ingestion_id, LlmProposal.discard, LlmProposal.rows)
        .where(LlmProposal.status == ProposalStatus.OK.value)
        .order_by(LlmProposal.created_at)
    )
    for ingestion_id, discard, rows in session.execute(proposal_stmt):
        usable = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
        proposals[str(ingestion_id)] = (discard, usable)

    ads: list[Ad] = []
    ad_stmt = (
        select(
            RawIngestion.id,
            RawIngestion.original_text,
            RawIngestion.processing_status,
            RawIngestion.discard_reason,
            Channel.province_id,
        )
        .outerjoin(Channel, Channel.id == RawIngestion.channel_id)
        .where(
            RawIngestion.content_type == ContentType.TEXT.value,
            RawIngestion.processing_status.in_(decided),
        )
        .order_by(RawIngestion.captured_at, RawIngestion.id)
    )
    for ingestion_id, text, status, discard_reason, channel_province in session.execute(ad_stmt):
        if not text or not text.strip():
            continue
        key = str(ingestion_id)
        curated = status == ProcessingStatus.CURATED.value
        claude_discard, claude_rows = proposals.get(key, (None, []))
        ads.append(
            Ad(
                ingestion_id=key,
                text=text,
                curated=curated,
                discard_reason=None if curated else discard_reason,
                truth=_field_sets(curated_rows.get(key, []), catalog) if curated else {},
                channel_has_province=channel_province is not None,
                claude_discard=claude_discard,
                claude_fields=_field_sets(claude_rows, catalog),
            )
        )
    return ads


# --- Entrada ------------------------------------------------------------------


def _require_key() -> str:
    api_key = load_api_key(os.environ, REPO_ROOT / ".env")
    if api_key is None:
        sys.exit(f"Falta {ENV_KEY}: no está en el entorno ni en {REPO_ROOT / '.env'}.")
    return api_key


def _load_sample() -> tuple[Catalog, list[Ad]]:
    with session_scope() as session:
        catalog = load_catalog(session)
        return catalog, load_ads(session, catalog)


def _new_client() -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT_SECONDS)


def main(argv: Sequence[str] | None = None) -> None:
    """`argv` en `None` lee la línea de comandos; los tests le pasan una lista."""
    parser = argparse.ArgumentParser(
        description="Prueba de Jev fuera de línea: calibración por pregunta (slice A6)."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check",
        action="store_true",
        help="una sola llamada con un anuncio inventado: confirma la clave y la respuesta",
    )
    mode.add_argument(
        "--execute",
        action="store_true",
        help="hace la corrida real: llama a la API (gasta) y escribe el caché y el reporte",
    )
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="el modo por defecto: no llama a la red, informa n por pregunta y costo estimado",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="máximo de llamadas nuevas en esta corrida (solo con --execute)",
    )
    parser.add_argument(
        "--max-cost",
        type=float,
        default=DEFAULT_MAX_COST,
        help=(
            "tope de gasto en USD, contando lo ya cacheado "
            f"(por defecto {DEFAULT_MAX_COST}; solo con --execute)"
        ),
    )
    args = parser.parse_args(argv)

    if args.check:
        api_key = _require_key()
        # `with` cierra el cliente (y sus conexiones) al salir del bloque,
        # también si hay una excepción: el `try/finally` de PHP, sin escribirlo.
        with _new_client() as client:
            ok, message = run_check(client, api_key)
        print(message)
        sys.exit(0 if ok else 1)

    catalog, ads = _load_sample()

    # En seco salvo que se pida `--execute`: `--dry-run` solo lo dice en voz alta.
    if not args.execute:
        estimate = dry_run_estimate(ads, catalog, load_cache(CACHE_PATH))
        print(f"Modelo: {MODEL}")
        print(f"Muestra: {len(ads)} anuncios de texto con decisión.")
        print("n por pregunta:")
        for name, n in estimate.n_by_question.items():
            print(f"  {name:<12} {n}")
        print(f"Ya respondidos (en caché): {estimate.cached} · gastado: {_usd(estimate.cost_cached)}")
        print(f"Por preguntar:             {estimate.pending}")
        print(
            f"Tokens de entrada estimados: {estimate.tokens} "
            f"(estimación: caracteres / {_num(CHARS_PER_TOKEN, 1)} por "
            f"{_num(REQUEST_OVERHEAD_FACTOR, 1)}, el factor medido el 2026-10-04)"
        )
        print(
            f"Costo estimado: {_usd(estimate.cost)} "
            f"(a {_num(INPUT_PRICE_PER_MTOK, 3)} USD por millón de tokens de entrada)"
        )
        print(f"Tope de gasto: {_num(args.max_cost, 2)} USD.")
        print("\nDry-run: no se llamó a la red ni se escribió nada. Volvé a correr con --execute.")
        return

    api_key = _require_key()
    with _new_client() as client:
        summary = run_eval(
            ads,
            catalog,
            cache_path=CACHE_PATH,
            client=client,
            api_key=api_key,
            max_cost=args.max_cost,
            limit=args.limit,
            log=print,
        )

    report = render_report(evaluate(ads, catalog, load_cache(CACHE_PATH)), today=date.today().isoformat())
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(report)

    print("---")
    print(f"Preguntados en esta corrida: {summary.asked} · ya estaban en caché: {summary.cached}")
    print(
        f"Gasto de esta corrida: {_usd(summary.cost_run)} · "
        "acumulado de esta versión de preguntas: "
        f"{_usd(summary.cost_run + summary.cost_cached)}"
    )
    if summary.errors:
        print(f"Con error (se reintentan en la próxima corrida): {len(summary.errors)}")
        for ingestion_id, reason in summary.errors[:5]:
            print(f"  {ingestion_id}: {reason}")
    if summary.aborted:
        print(f"La corrida se cortó: {summary.aborted}.")
    if summary.stopped_by_cap:
        print(f"Se llegó al tope de {_num(args.max_cost, 2)} USD.")
    if summary.unasked:
        print(f"Quedaron sin preguntar: {summary.unasked}.")
    print(f"Reporte: {REPORT_PATH}")


if __name__ == "__main__":
    main()
