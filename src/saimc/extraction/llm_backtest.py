"""Backtest del asistente LLM contra lo que el operador curó a mano.

Corre `python -m saimc.extraction.llm_backtest` (por defecto, en seco).

Mide qué tan bien la propuesta del modelo (`llm_propose_ad.propose_ad`, UNA
llamada por anuncio) coincide con lo que el operador terminó confirmando, por
separado para texto e imagen, y la compara contra el motor de reglas sobre los
MISMOS anuncios. Es la línea base para decidir si se confía en el modelo para
toda la cola (`docs/plan-asistente-llm-cola-completa.md`, slice 1).

**Qué es «correcto».** Las mismas definiciones de H0: se reusan
`measure._match_rows_to_candidates` y `measure.row_correctness`, así que los
números son comparables con `python -m saimc.extraction.measure`. Dos
consecuencias que hay que leer bien:

- Sobre las filas del modelo se aplica la provincia fija del canal, igual que
  la cola real («el canal siempre manda», `decisions.md` 2026-08-17) y que ya
  hacen las reglas en H0. La precisión de provincia no distingue al modelo del
  motor en los anuncios de canal.
- Un anuncio sin fila propuesta no cuenta como «no medido»: sus filas
  confirmadas puntúan como fallo (salvo la provincia del canal), igual que en
  H0. La abstención se muestra aparte.

**Conjunto de referencia («oro»).** Anuncios curados que NUNCA pasaron por el
asistente del operador (`assisted_ingestion_ids`): su verdad es solo humana. El
veredicto de descarte se mide además sobre una muestra estratificada y
determinista de descartados; esa muestra no entra en la precisión por campo.

**No decide nada.** El reporte no fija umbrales ni dice si conviene confiar en
el modelo: el umbral lo pone el operador al ver los números (`spec.md` §12.6).
Imprime siempre el `n` junto a cada porcentaje porque las muestras son chicas.

La base se lee y NUNCA se escribe. Lo que se escribe vive bajo `data/`
(gitignored): resultados por anuncio, registro de llamadas y el reporte.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import random
import sys
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from saimc.config import Settings, get_settings
from saimc.db import session_scope
from saimc.extraction import llm_assist, llm_checks, llm_propose_ad, measure, ocr
from saimc.extraction.engine import ExtractionProposal, propose_all
from saimc.ingestion import origin
from saimc.models import (
    ContentType,
    Currency,
    PriceHistory,
    ProcessingStatus,
    Product,
    Province,
    RawIngestion,
)
from saimc.units import canonical_unit

DEFAULT_RESULTS = Path("data/llm-backtest-results.jsonl")
DEFAULT_CALLS_LOG = Path("data/llm-backtest-calls.jsonl")
DEFAULT_REPORT = Path("data/llm-backtest-report.md")

# Heurística de tokens del dry-run: ~3.5 caracteres por token en español.
# Es una ESTIMACIÓN; el consumo real lo reporta la API y es lo que el reporte
# final usa.
CHARS_PER_TOKEN = 3.5
# Salida estimada: 30 tokens de envoltorio + 45 por fila esperada; 60 para un
# anuncio que se espera descartado. Supuestos, no medidos.
_OUT_BASE, _OUT_PER_ROW, _OUT_DISCARD = 30, 45, 60
# Una imagen sin transcripción a mano se supone de 1000 caracteres.
_IMAGE_FALLBACK_CHARS = 1000

# Fallas que no tiene sentido reintentar en la misma corrida: seguir solo
# gasta tiempo (y con «credit», un pedido más que va a fallar igual).
_ABORT_FAILURES = frozenset({"credit", "auth"})

_GROUPS = ("texto", "imagen", "total")

# Señales de calidad (v3): cada clave guarda `[aciertos, n]`. Se crean todas
# aunque no haya datos, para que un resultado viejo (sin `row_meta`) se imprima
# como `0/0 (n/d)` y no como una clave ausente.
_SIGNAL_KEYS = (
    "found",  # filas con la evidencia hallada en el texto
    "price_in",  # filas con el precio dentro de la evidencia
    "complete|found=True",  # extracción completa correcta, según evidencia
    "complete|found=False",
    "currency|explicit=True",  # moneda correcta, según moneda explícita o asumida
    "currency|explicit=False",
    "complete|agreement=coincide",  # extracción completa correcta, según acuerdo
    "complete|agreement=difiere",
    "complete|agreement=sin_regla",
)


@dataclass(frozen=True)
class WorkItem:
    """Un anuncio a evaluar y qué hizo el operador con él."""

    ingestion_id: str
    content_type: str
    expected: str  # "kept" | "discarded"


# --- selección del corpus ---------------------------------------------------


def assisted_ids(path: Path) -> set[str]:
    return llm_assist.assisted_ingestion_ids(path)


def select_gold(session: Session, assisted: set[str]) -> list[RawIngestion]:
    """Curados con fila confirmada y sin paso por el asistente, en orden estable."""
    history = measure._price_history_by_source(session)
    gold = [
        i
        for i in measure._curated_ingestions(session)
        if history.get(i.id) and str(i.id) not in assisted
    ]
    return sorted(gold, key=lambda i: str(i.id))


def select_discard_sample(
    session: Session, per_type: dict[str, int], *, seed: int, excluded: set[str]
) -> list[RawIngestion]:
    """Descartados, estratificado por tipo de contenido y determinista.

    Cada tipo se muestrea con su propio generador (`seed` + tipo): pedir más
    imágenes no cambia qué textos salen. Si hay menos de los pedidos, toma todos.
    """
    stmt = select(RawIngestion).where(
        RawIngestion.processing_status == ProcessingStatus.DISCARDED
    )
    rows = [i for i in session.execute(stmt).scalars() if str(i.id) not in excluded]
    sample: list[RawIngestion] = []
    for content_type, wanted in sorted(per_type.items()):
        pool = sorted(
            (i for i in rows if i.content_type == content_type), key=lambda i: str(i.id)
        )
        rng = random.Random(f"{seed}:{content_type}")  # noqa: S311 — muestreo, no criptografía
        sample.extend(rng.sample(pool, min(wanted, len(pool))))
    return sample


def build_workset(
    session: Session,
    assisted: set[str],
    *,
    sample_n: int | None,
    seed: int,
    limit: int | None = None,
) -> list[WorkItem]:
    """Oro + muestra de descartes, mezclados de forma determinista.

    Por defecto la muestra iguala al oro en cada tipo. Mezclar antes de
    recortar con `limit` hace que un piloto de pocos anuncios traiga de todo.
    """
    gold = select_gold(session, assisted)
    per_type = {
        t.value: (
            sample_n
            if sample_n is not None
            else sum(1 for i in gold if i.content_type == t.value)
        )
        for t in ContentType
    }
    sample = select_discard_sample(session, per_type, seed=seed, excluded=assisted)
    items = [WorkItem(str(i.id), i.content_type, "kept") for i in gold] + [
        WorkItem(str(i.id), i.content_type, "discarded") for i in sample
    ]
    items.sort(key=lambda w: (w.expected, w.ingestion_id))
    random.Random(seed).shuffle(items)  # noqa: S311 — muestreo, no criptografía
    return items[:limit] if limit is not None else items


# --- (de)serialización de propuestas ---------------------------------------


def rows_to_dicts(rows: Sequence[ExtractionProposal]) -> list[dict[str, Any]]:
    return [
        {
            "product_id": str(r.product.id) if r.product is not None else None,
            "price": str(r.price) if r.price is not None else None,
            "currency": r.currency,
            "quantity": str(r.quantity) if r.quantity is not None else None,
            "unit": r.unit,
            "province_id": str(r.province.id) if r.province is not None else None,
        }
        for r in rows
    ]


def _dec(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def rows_from_dicts(
    rows: Sequence[dict[str, Any]],
    products: dict[str, Product],
    provinces: dict[str, Province],
) -> list[ExtractionProposal]:
    return [
        ExtractionProposal(
            product=products.get(str(r.get("product_id"))),
            price=_dec(r.get("price")),
            currency=r.get("currency") or Currency.CUP.value,
            quantity=_dec(r.get("quantity")),
            unit=r.get("unit"),
            province=provinces.get(str(r.get("province_id"))),
        )
        for r in rows
    ]


def expected_rows_to_dicts(
    confirmed: Sequence[PriceHistory], names: dict[Any, str]
) -> list[dict[str, Any]]:
    """Las filas que el operador dejó para un anuncio, legibles sin la base.

    `names` mapea `product_id -> nombre`. Es un registro para el análisis: las
    métricas siguen leyendo la verdad de la base (los resultados viejos no la
    traen).
    """
    return [
        {
            "product": names.get(r.product_id),
            "price": str(r.price) if r.price is not None else None,
            "currency": r.currency,
            "quantity": str(r.quantity) if r.quantity is not None else None,
            "unit": r.unit,
        }
        for r in confirmed
    ]


# --- archivo de resultados --------------------------------------------------


def read_records(path: Path) -> list[dict[str, Any]]:
    """Registros en orden, saltando líneas ilegibles."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    records: list[dict[str, Any]] = []
    for line in raw.splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict) and "ingestion_id" in record:
            records.append(record)
    return records


def latest_records(records: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """El último registro por anuncio: un reintento reemplaza al degradado."""
    return {str(r["ingestion_id"]): r for r in records}


def _append_record(record: dict[str, Any], path: Path) -> None:
    # A diferencia del registro de llamadas, acá un fallo de disco NO se traga:
    # sin el resultado guardado la corrida no es reanudable y se pierde plata.
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


# --- estimación (dry-run) ---------------------------------------------------


@dataclass
class Estimate:
    ads: dict[str, int]
    prompt_tokens: int
    input_tokens: int
    output_tokens: int
    cost_usd: dict[str, float]


def _tokens(chars: int) -> int:
    return math.ceil(chars / CHARS_PER_TOKEN)


def _cost(input_tokens: int, output_tokens: int, model: str) -> float:
    rate_in, rate_out = llm_assist._RATES_USD_PER_MTOK[model]
    return input_tokens / 1e6 * rate_in + output_tokens / 1e6 * rate_out


def estimate(
    session: Session, items: Sequence[WorkItem], ocr_cache: dict[str, str]
) -> Estimate:
    """Tokens y costo de correr `items`, sin red.

    El prompt fijo (sistema + catálogos + esquema) se mide del pedido real. El
    texto del anuncio se estima por caracteres; para una imagen sin OCR cacheado
    se usa el largo de la transcripción del operador como proxy (o 1000
    caracteres si no hay).
    """
    products = measure._focus_products(session)
    provinces = measure._all_provinces(session)
    fixed = (
        len(llm_propose_ad._SYSTEM_PROMPT)
        + len(llm_propose_ad.build_prompt("", products, provinces))
        + len(json.dumps(llm_propose_ad.response_schema()))
    )
    prompt_tokens = _tokens(fixed)
    history = measure._price_history_by_source(session)

    ads: dict[str, int] = {}
    total_in = total_out = 0
    for item in items:
        ads[item.content_type] = ads.get(item.content_type, 0) + 1
        ingestion = session.get(RawIngestion, uuid.UUID(item.ingestion_id))
        if ingestion is None:
            continue
        cached = ocr_cache.get(item.ingestion_id)
        if cached is not None:
            chars = len(cached)
        elif ingestion.content_type == ContentType.IMAGE and not ingestion.original_text:
            chars = _IMAGE_FALLBACK_CHARS
        else:
            chars = len(ingestion.original_text or "")
        total_in += prompt_tokens + _tokens(chars)
        if item.expected == "kept":
            total_out += _OUT_BASE + _OUT_PER_ROW * len(history.get(ingestion.id, []))
        else:
            total_out += _OUT_DISCARD
    return Estimate(
        ads=ads,
        prompt_tokens=prompt_tokens,
        input_tokens=total_in,
        output_tokens=total_out,
        cost_usd={m: _cost(total_in, total_out, m) for m in llm_assist._RATES_USD_PER_MTOK},
    )


def print_estimate(est: Estimate, out: Callable[[str], None] = print) -> None:
    out("Anuncios a evaluar (oro curado a mano + muestra de descartados):")
    for tipo in ("text", "image"):
        out(f"  {tipo}: {est.ads.get(tipo, 0)}")
    out(f"  total: {sum(est.ads.values())}")
    out(
        f"Tokens estimados: ~{est.input_tokens:,} de entrada y ~{est.output_tokens:,} de "
        f"salida (heurística: caracteres / {CHARS_PER_TOKEN} por anuncio, más "
        f"{est.prompt_tokens:,} tokens de prompt fijo por llamada medido del pedido "
        f"real; salida supuesta: {_OUT_BASE}+{_OUT_PER_ROW} por fila esperada, "
        f"{_OUT_DISCARD} por descartado)."
    )
    out("Costo estimado en USD (tarifas de llm_assist.py):")
    for model, usd in est.cost_usd.items():
        out(f"  {model}: ${usd:.2f}")


# --- la corrida -------------------------------------------------------------


def run_backtest(
    session: Session,
    items: Sequence[WorkItem],
    *,
    results_path: Path,
    calls_log_path: Path,
    api_key: str | None,
    client: Any | None,
    model: str,
    timeout: float,
    transcribe: measure.Transcriber = ocr.transcribe,
    out: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Una llamada por anuncio, en serie, anexando un resultado por línea.

    Reanudable: se saltea lo que ya tiene un resultado no degradado. Un
    resultado degradado se reintenta y, si ya tenía el OCR, no se lo repite.
    Se detiene si la falla es de saldo o de clave.
    """
    from saimc.privacy import redact_personal_data

    products = measure._focus_products(session)
    provinces = measure._all_provinces(session)
    names = {p.id: p.name for p in session.execute(select(Product)).scalars()}
    history = measure._price_history_by_source(session)
    previous = latest_records(read_records(results_path))
    pending = [
        i for i in items if i.ingestion_id not in previous
        or previous[i.ingestion_id].get("degraded")
    ]
    out(f"A procesar: {len(pending)} de {len(items)} (el resto ya tiene resultado).")

    running_cost = 0.0
    done = degraded = 0
    aborted = False
    for n, item in enumerate(pending, start=1):
        ingestion = session.get(RawIngestion, uuid.UUID(item.ingestion_id))
        if ingestion is None:
            continue
        prev = previous.get(item.ingestion_id)
        record: dict[str, Any] = {}
        if ingestion.content_type == ContentType.IMAGE:
            if prev is not None and "ocr_text" in prev:
                text = str(prev["ocr_text"])
            else:
                # Lo que se guarda es lo que vio el modelo: ya sin teléfonos.
                text = redact_personal_data(measure._evaluation_text(ingestion, transcribe)) or ""
            record["ocr_text"] = text
        else:
            text = ingestion.original_text or ""

        channel = origin.channel_province(session, ingestion)
        rules = propose_all(text, products, provinces, channel_province=channel)
        result = llm_propose_ad.propose_ad(
            text,
            products,
            provinces,
            api_key=api_key,
            client=client,
            model=model,
            timeout=timeout,
            ingestion_id=item.ingestion_id,
            log_path=calls_log_path,
        )
        # El canal manda también sobre lo que propuso el modelo (ver módulo).
        rows = [
            dataclasses.replace(r, province=channel) if channel is not None else r
            for r in result.rows
        ]
        agreements = llm_checks.compare_with_rules(rows, rules)
        row_meta = [
            {
                **dataclasses.asdict(meta),
                "agreement": a.status,
                "agreement_fields": a.fields(),
            }
            for meta, a in zip(result.row_meta, agreements, strict=True)
        ]
        record.update(
            ingestion_id=item.ingestion_id,
            content_type=item.content_type,
            expected=item.expected,
            model=model,
            prompt_version=llm_propose_ad.PROMPT_VERSION,
            expected_rows=expected_rows_to_dicts(
                history.get(ingestion.id, []) if item.expected == "kept" else [], names
            ),
            proposal={
                "discard": result.discard,
                "discard_reason": result.discard_reason,
                "rows": rows_to_dicts(rows),
                # Paralela a `rows`. Los resultados de la v2 no la traen.
                "row_meta": row_meta,
                "dropped": result.dropped,
            },
            rules_proposal=rows_to_dicts(rules),
            channel_province_id=str(channel.id) if channel is not None else None,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            degraded=result.degraded,
            failure=result.failure,
            timestamp=datetime.now(UTC).isoformat(),
        )
        _append_record(record, results_path)

        done += 1
        degraded += 1 if result.degraded else 0
        if model in llm_assist._RATES_USD_PER_MTOK:
            running_cost += _cost(result.input_tokens, result.output_tokens, model)
        estado = f"DEGRADADO ({result.failure})" if result.degraded else "ok"
        out(
            f"[{n}/{len(pending)}] {item.ingestion_id[:8]} {item.content_type} "
            f"{item.expected}: {estado} — costo acumulado ${running_cost:.4f}"
        )
        if result.failure in _ABORT_FAILURES:
            out(
                f"Corrida detenida: falla «{result.failure}». Los resultados hechos "
                "quedaron guardados; al resolverlo se reanuda sin repetir nada."
            )
            aborted = True
            break
    return {"done": done, "degraded": degraded, "cost_usd": running_cost, "aborted": aborted}


# --- métricas ---------------------------------------------------------------


@dataclass
class Score:
    """Aciertos por campo sobre las filas confirmadas del oro."""

    rows: int = 0
    product: int = 0
    price: int = 0
    province: int = 0
    quantity_unit: int = 0
    complete: int = 0

    def add(self, ok: measure.RowCorrectness) -> None:
        self.rows += 1
        self.product += ok.product
        self.price += ok.price
        self.province += ok.province
        self.quantity_unit += ok.quantity_unit
        self.complete += ok.complete


@dataclass
class GroupMetrics:
    name: str
    gold_ads: int = 0
    gold_rows: int = 0
    degraded: int = 0
    llm: Score = field(default_factory=Score)
    rules: Score = field(default_factory=Score)
    # Desacuerdo, por fila, en «extracción completa correcta».
    both: int = 0
    only_llm: int = 0
    only_rules: int = 0
    neither: int = 0
    # Veredicto de descarte: positivo = «descartar».
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0
    # Abstención del modelo en el oro.
    zero_row_ads: int = 0
    null_price_rows: int = 0
    discard_sample_ads: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    untariffed_calls: int = 0
    dropped: int = 0
    # Filas del modelo que no corresponden a ninguna fila del operador.
    extra_rows: int = 0
    extra_dup_currency: int = 0  # misma oferta en otra moneda
    extra_other: int = 0
    ads_with_extra: int = 0
    # Llamadas que consumieron tokens (para el promedio de salida por llamada).
    calls: int = 0
    signals: dict[str, list[int]] = field(
        default_factory=lambda: {k: [0, 0] for k in _SIGNAL_KEYS}
    )

    def tally(self, key: str, ok: bool) -> None:
        self.signals[key][0] += bool(ok)
        self.signals[key][1] += 1

    @property
    def discard_precision(self) -> tuple[int, int]:
        return (self.tp, self.tp + self.fp)

    @property
    def discard_recall(self) -> tuple[int, int]:
        return (self.tp, self.tp + self.fn)


def extra_rows(
    confirmed: Sequence[PriceHistory],
    candidates: Sequence[ExtractionProposal],
    pairs: Sequence[tuple[PriceHistory, ExtractionProposal]],
) -> tuple[int, int]:
    """Filas del modelo sin fila del operador: `(moneda duplicada, otras)`.

    «Sin fila del operador» es lo que `_match_rows_to_candidates` no consumió,
    el mismo emparejado con el que se puntúa H0. «Moneda duplicada»: mismo
    producto, misma cantidad y misma unidad que alguna fila del operador pero
    otra moneda (la oferta en dos monedas que el operador carga una vez).
    """
    used = {id(p) for _, p in pairs}
    duplicated = other = 0
    for candidate in candidates:
        if id(candidate) in used:
            continue
        is_dup = candidate.product is not None and any(
            row.product_id == candidate.product.id
            and row.currency != candidate.currency
            and candidate.quantity == row.quantity
            and canonical_unit(candidate.unit) == canonical_unit(row.unit)
            for row in confirmed
        )
        duplicated += is_dup
        other += not is_dup
    return duplicated, other


def compute_metrics(
    session: Session, records: Sequence[dict[str, Any]]
) -> dict[str, GroupMetrics]:
    """Métricas por grupo (`texto`, `imagen`, `total`) desde los resultados.

    Los anuncios degradados quedan fuera de toda métrica de acierto y de la
    matriz de confusión —para que modelo y reglas se comparen sobre los mismos
    anuncios— y se cuentan aparte.
    """
    products = {str(p.id): p for p in session.execute(select(Product)).scalars()}
    provinces = {str(p.id): p for p in session.execute(select(Province)).scalars()}
    history = measure._price_history_by_source(session)
    groups = {name: GroupMetrics(name) for name in _GROUPS}

    for record in latest_records(records).values():
        own = groups["texto" if record.get("content_type") == "text" else "imagen"]
        targets = (own, groups["total"])
        proposal = record.get("proposal") or {}

        model = str(record.get("model", ""))
        tokens_in = _count(record.get("input_tokens"))
        tokens_out = _count(record.get("output_tokens"))
        for g in targets:
            g.input_tokens += tokens_in
            g.output_tokens += tokens_out
            g.calls += bool(tokens_in or tokens_out)
            g.dropped += _count(proposal.get("dropped"))
            if model in llm_assist._RATES_USD_PER_MTOK:
                g.cost_usd += _cost(tokens_in, tokens_out, model)
            elif tokens_in or tokens_out:
                g.untariffed_calls += 1

        if record.get("degraded"):
            for g in targets:
                g.degraded += 1
            continue

        predicted_discard = proposal.get("discard") is True
        kept = record.get("expected") == "kept"
        for g in targets:
            if kept:
                g.fp += predicted_discard
                g.tn += not predicted_discard
            else:
                g.discard_sample_ads += 1
                g.tp += predicted_discard
                g.fn += not predicted_discard
        if not kept:
            continue

        confirmed = history.get(uuid.UUID(str(record["ingestion_id"])), [])
        if not confirmed:
            continue
        channel = provinces.get(str(record.get("channel_province_id")))
        llm_candidates = rows_from_dicts(proposal.get("rows") or [], products, provinces)
        rule_candidates = rows_from_dicts(
            record.get("rules_proposal") or [], products, provinces
        )
        llm_pairs = measure._match_rows_to_candidates(confirmed, llm_candidates, channel)
        rule_pairs = measure._match_rows_to_candidates(confirmed, rule_candidates, channel)

        duplicated, other = extra_rows(confirmed, llm_candidates, llm_pairs)
        for g in targets:
            g.gold_ads += 1
            g.gold_rows += len(confirmed)
            g.zero_row_ads += not llm_candidates
            g.extra_dup_currency += duplicated
            g.extra_other += other
            g.extra_rows += duplicated + other
            g.ads_with_extra += (duplicated + other) > 0
        # `row_meta` va en paralelo a las filas; los resultados viejos no lo traen.
        raw_meta = proposal.get("row_meta")
        meta_by_row = (
            {id(c): m for c, m in zip(llm_candidates, raw_meta, strict=True)}
            if isinstance(raw_meta, list) and len(raw_meta) == len(llm_candidates)
            else {}
        )
        for meta in meta_by_row.values():
            for g in targets:
                _tally_shares(g, meta)
        for (row, llm_row), (_, rule_row) in zip(llm_pairs, rule_pairs, strict=True):
            llm_ok = measure.row_correctness(row, llm_row)
            rule_ok = measure.row_correctness(row, rule_row)
            meta = meta_by_row.get(id(llm_row))
            if isinstance(meta, dict):
                for g in targets:
                    _tally_splits(g, meta, row, llm_row, llm_ok.complete)
            for g in targets:
                g.llm.add(llm_ok)
                g.rules.add(rule_ok)
                g.null_price_rows += llm_row.price is None
                if llm_ok.complete and rule_ok.complete:
                    g.both += 1
                elif llm_ok.complete:
                    g.only_llm += 1
                elif rule_ok.complete:
                    g.only_rules += 1
                else:
                    g.neither += 1
    return groups


def _tally_shares(g: GroupMetrics, meta: object) -> None:
    """Cuántas filas del modelo tienen la evidencia hallada / el precio en ella."""
    if not isinstance(meta, dict):
        return
    found = meta.get("evidence_found")
    if isinstance(found, bool):
        g.signals["found"][0] += found
        g.signals["found"][1] += 1
    price_in = meta.get("price_in_evidence")
    if isinstance(price_in, bool):
        g.signals["price_in"][0] += price_in
        g.signals["price_in"][1] += 1


def _tally_splits(
    g: GroupMetrics,
    meta: dict[str, Any],
    confirmed: PriceHistory,
    proposed: ExtractionProposal,
    complete: bool,
) -> None:
    """Acierto de una fila emparejada, cortado por cada señal.

    Es lo que responde la pregunta de fondo: ¿una evidencia que no aparece, una
    moneda asumida o un desacuerdo con las reglas anticipan una fila mal?
    """
    found = meta.get("evidence_found")
    if isinstance(found, bool):
        g.tally(f"complete|found={found}", complete)
    explicit = meta.get("currency_explicit")
    if isinstance(explicit, bool):
        g.tally(f"currency|explicit={explicit}", proposed.currency == confirmed.currency)
    agreement = meta.get("agreement")
    if agreement in (llm_checks.COINCIDE, llm_checks.DIFIERE, llm_checks.SIN_REGLA):
        g.tally(f"complete|agreement={agreement}", complete)


def _count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


# --- reporte ----------------------------------------------------------------


def _frac(k: int, n: int) -> str:
    """`k/n (p%)`: el n siempre a la vista; `n/d` si no hay base."""
    return f"{k}/{n} ({100 * k / n:.1f}%)" if n else f"{k}/{n} (n/d)"


def render_report(
    metrics: dict[str, GroupMetrics],
    models: Sequence[str] = (),
    prompt_versions: Sequence[str] = (),
) -> str:
    lines = [
        "# Backtest del asistente LLM",
        "",
        f"Generado: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
    ]
    if models:
        lines.append(f"Modelo(s) en los resultados: {', '.join(sorted(set(models)))}")
    if prompt_versions:
        lines.append(f"Versión(es) de prompt en los resultados: {', '.join(sorted(set(prompt_versions)))}")
    lines += [
        "",
        "Cómo leerlo:",
        "",
        "- Oro: anuncios curados a mano que nunca pasaron por el asistente. "
        "«Correcto» es la definición de H0 (`measure.py`), así que es comparable "
        "con `python -m saimc.extraction.measure`.",
        "- Sobre las filas del modelo se aplica la provincia fija del canal, "
        "como en la cola real y en H0: en anuncios de canal la provincia no "
        "distingue al modelo de las reglas.",
        "- Los descartados son una muestra estratificada; sirven solo para el "
        "veredicto de descarte.",
        "- Las muestras son chicas: cada porcentaje trae su n. Este reporte no "
        "fija umbrales ni concluye; el umbral lo pone el operador (`spec.md` §12.6).",
    ]
    titles = {"texto": "Texto", "imagen": "Imagen", "total": "Total"}
    for name in _GROUPS:
        g = metrics[name]
        p_k, p_n = g.discard_precision
        r_k, r_n = g.discard_recall
        lines += [
            "",
            f"## {titles[name]}",
            "",
            f"Oro evaluado: {g.gold_ads} anuncios, {g.gold_rows} filas confirmadas. "
            f"Descartados de la muestra evaluados: {g.discard_sample_ads}.",
            "",
            "### (a) Veredicto de descarte contra el operador",
            "",
            "| | Modelo: descartar | Modelo: conservar |",
            "|---|---|---|",
            f"| Operador: descartó | {g.tp} | {g.fn} |",
            f"| Operador: conservó | {g.fp} | {g.tn} |",
            "",
            f"- Precisión de «descartar»: {_frac(p_k, p_n)}",
            f"- Recall de «descartar»: {_frac(r_k, r_n)}",
            "",
            f"### (b) Acierto por campo en el oro ({g.gold_rows} filas)",
            "",
            "| Campo | Modelo | Reglas |",
            "|---|---|---|",
        ]
        for label, attr in (
            ("Producto", "product"),
            ("Precio y moneda", "price"),
            ("Cantidad y unidad", "quantity_unit"),
            ("Provincia", "province"),
            ("Extracción completa", "complete"),
        ):
            lines.append(
                f"| {label} | {_frac(getattr(g.llm, attr), g.llm.rows)} "
                f"| {_frac(getattr(g.rules, attr), g.rules.rows)} |"
            )
        lines += [
            "",
            "### (c) Abstención y degradados",
            "",
            f"- Anuncios del oro sin ninguna fila propuesta por el modelo: "
            f"{_frac(g.zero_row_ads, g.gold_ads)}",
            f"- Filas del oro con precio en null del modelo: "
            f"{_frac(g.null_price_rows, g.gold_rows)}",
            f"- Respuestas descartadas por no resolver contra el catálogo "
            f"(filas o campos): {g.dropped}",
            f"- Llamadas degradadas (fuera de las métricas): {g.degraded}",
            "",
            "### (d) Desacuerdos en extracción completa (por fila)",
            "",
            f"- Aciertan los dos: {g.both}",
            f"- Solo acierta el modelo: {g.only_llm}",
            f"- Solo aciertan las reglas: {g.only_rules}",
            f"- Ninguno acierta: {g.neither}",
            "",
            "### (e) Filas de más",
            "",
            "Filas que el modelo propuso y no corresponden a ninguna fila que el "
            "operador dejó (mismo emparejado que H0). Estas filas **no son errores** "
            "en la definición de H0 —la precisión por fila no las cuenta—, pero le "
            "cuestan al operador un borrado cada una. Ojo: el oro puede estar "
            "incompleto (el operador no siempre carga todas las filas de una "
            "lista diaria repetida), así que «otras» no es necesariamente error "
            "del modelo.",
            "",
            f"- Filas de más: {g.extra_rows} en {g.gold_ads} anuncios "
            f"(promedio {g.extra_rows / g.gold_ads if g.gold_ads else 0:.2f} "
            "por anuncio)",
            f"- Anuncios con al menos una fila de más: "
            f"{_frac(g.ads_with_extra, g.gold_ads)}",
            f"- Moneda duplicada (misma oferta en otra moneda): {g.extra_dup_currency}",
            f"- Otras: {g.extra_other}",
            "",
            "### (f) Consumo",
            "",
            f"- Tokens reales: {g.input_tokens:,} de entrada, {g.output_tokens:,} de salida",
            f"- Costo real: ${g.cost_usd:.4f}"
            + (
                f" ({g.untariffed_calls} llamadas de un modelo sin tarifa no se cuentan)"
                if g.untariffed_calls
                else ""
            ),
            "",
            "### (g) Calidad de las señales",
            "",
            "Señales que acompañan a cada fila del modelo, medidas contra lo que "
            "el operador confirmó (mismas definiciones de H0). No fijan umbral ni "
            "dicen si conviene fiarse de ellas: solo muestran si anticipan un "
            "error. Con resultados de un prompt anterior a la v3 salen como n/d.",
            "",
        ]
        def line(label: str, key: str, g: GroupMetrics = g) -> str:
            return f"- {label}: {_frac(*g.signals[key])}"

        avg = f"{g.output_tokens / g.calls:.1f}" if g.calls else "n/d"
        lines += [
            line("Filas con evidencia hallada en el anuncio", "found"),
            line("Filas con el precio dentro de la evidencia", "price_in"),
            line(
                "Extracción completa correcta con evidencia hallada",
                "complete|found=True",
            ),
            line(
                "Extracción completa correcta con evidencia NO hallada",
                "complete|found=False",
            ),
            line("Moneda correcta con moneda explícita", "currency|explicit=True"),
            line("Moneda correcta con moneda asumida", "currency|explicit=False"),
            line(
                "Extracción completa correcta si coincide con las reglas",
                "complete|agreement=coincide",
            ),
            line(
                "Extracción completa correcta si difiere de las reglas",
                "complete|agreement=difiere",
            ),
            line(
                "Extracción completa correcta si las reglas no dicen",
                "complete|agreement=sin_regla",
            ),
            f"- Tokens de salida por llamada: {avg} ({g.calls} llamadas)",
        ]
    return "\n".join(lines) + "\n"


# --- CLI --------------------------------------------------------------------


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m saimc.extraction.llm_backtest",
        description=(
            "Backtest del asistente LLM contra lo curado a mano. Por defecto "
            "estima el costo sin llamar a nada; --execute hace la corrida real."
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Solo estimar (por defecto).")
    mode.add_argument(
        "--execute",
        action="store_true",
        help="Corrida real: llama a la API y GASTA plata. Exige la clave.",
    )
    mode.add_argument(
        "--report-only",
        action="store_true",
        help="Regenera el reporte desde el archivo de resultados, sin llamar a nada.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Tope de anuncios (piloto).")
    parser.add_argument(
        "--sample",
        type=int,
        default=None,
        help="Descartados por tipo (por defecto, tantos como anuncios de oro del tipo).",
    )
    parser.add_argument("--seed", type=int, default=42, help="Semilla del muestreo.")
    parser.add_argument("--model", default=None, help="Por defecto, SAIMC_LLM_ASSIST_MODEL.")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--calls-log", type=Path, default=DEFAULT_CALLS_LOG)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument(
        "--assist-log",
        type=Path,
        default=None,
        help="Registro del asistente del operador (por defecto, el de la configuración).",
    )
    return parser.parse_args(argv)


def _write_report(
    session: Session, args: argparse.Namespace, out: Callable[[str], None]
) -> None:
    records = read_records(args.results)
    models = [str(r.get("model", "")) for r in latest_records(records).values()]
    # Los resultados de la v1 no registraron la versión del prompt.
    versions = [
        str(r.get("prompt_version", "v1 (sin registro)"))
        for r in latest_records(records).values()
    ]
    text = render_report(compute_metrics(session, records), models, versions)
    out(text)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(text, encoding="utf-8")
    out(f"Reporte escrito en {args.report}")


def execute(
    args: argparse.Namespace,
    session: Session,
    settings: Settings,
    *,
    client: Any | None = None,
    out: Callable[[str], None] = print,
) -> int:
    """Corre el modo pedido. Devuelve el código de salida."""
    if args.report_only:
        _write_report(session, args, out)
        return 0

    assist_log = args.assist_log or settings.llm_assist_log_path
    items = build_workset(
        session,
        assisted_ids(assist_log),
        sample_n=args.sample,
        seed=args.seed,
        limit=args.limit,
    )
    ocr_cache = {
        i: str(r["ocr_text"])
        for i, r in latest_records(read_records(args.results)).items()
        if "ocr_text" in r
    }
    est = estimate(session, items, ocr_cache)

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
    model = args.model or settings.llm_assist_model
    print_estimate(est, out)
    if model not in llm_assist._RATES_USD_PER_MTOK:
        out(f"AVISO: {model} no tiene tarifa en llm_assist.py; el costo acumulado no se estima.")
    run_backtest(
        session,
        items,
        results_path=args.results,
        calls_log_path=args.calls_log,
        api_key=api_key,
        client=client,
        model=model,
        timeout=args.timeout,
        out=out,
    )
    _write_report(session, args, out)
    return 0


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    with session_scope() as session:
        code = execute(args, session, get_settings())
    sys.exit(code)


if __name__ == "__main__":
    main()
