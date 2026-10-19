#!/usr/bin/env python
"""¿Cuánto acierta un modelo de visión leyendo la imagen cruda? (medición, no decide nada)

Compara dos caminos sobre las MISMAS imágenes del conjunto de referencia
congelado vigente (`reference_set_members`), contra lo que el operador curó:

- **Visión directa** (este script): la imagen va tal cual al modelo, con el mismo
  prompt de sistema, los mismos catálogos y el mismo esquema de respuesta que el
  asistente (`llm_propose_ad`). Solo cambia la entrada: imagen en vez de texto.
- **OCR + asistente** (el camino actual): el resultado que ya dejó
  `python -m saimc.extraction.llm_backtest` (por defecto
  `data/llm-backtest-results-v7.jsonl`). Aquí no se vuelve a llamar a nada.

«Correcto» es la definición de H0 (`measure.row_correctness`), igual que
`llm_backtest`: el «precio» exige valor y moneda; sobre las filas del modelo se
aplica la provincia fija del canal («el canal siempre manda»); un anuncio sin
fila propuesta puntúa como fallo. Los dos caminos se re-puntúan contra las filas
congeladas con ese mismo código, así que la comparación es limpia aunque el
resultado OCR se haya corrido contra el oro vivo de otro día.

Seco por defecto: cuenta imágenes, bytes, tokens y costo estimado, y NO llama a
nada. `--execute` llama al proveedor (GASTA plata), con tope `--max-cost` por
corrida, y retoma sin repetir lo ya hecho. La base se lee y NUNCA se escribe.

Privacidad: los resultados guardan ids y filas extraídas, nunca bytes de imagen.
La imagen sale al proveedor tal cual (es el experimento) y solo con `--execute`;
el único texto libre que se guarda (la respuesta de una llamada degradada) pasa
por `privacy.redact_personal_data`.

Uso (desde la raíz del repo):
    python scripts/vision_backtest.py                       # seco
    python scripts/vision_backtest.py --execute --limit 5   # piloto
    python scripts/vision_backtest.py --execute             # corrida completa
    python scripts/vision_backtest.py --report-only         # regenera el informe
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from saimc.config import get_settings
from saimc.db import session_scope
from saimc.extraction import llm_assist, llm_backtest, llm_propose_ad, measure, reference_set
from saimc.extraction.engine import ExtractionProposal
from saimc.glossary import active_glossary_entries, effective_prompt_version
from saimc.ingestion import origin
from saimc.llm import provider
from saimc.models import ContentType, GlossaryEntry, Product, Province, RawIngestion
from saimc.privacy import redact_personal_data

DEFAULT_RESULTS = Path("data/vision_backtest/results.jsonl")
DEFAULT_REPORT = Path("data/vision_backtest/report.md")
DEFAULT_OCR_RESULTS = Path("data/llm-backtest-results-v7.jsonl")
DEFAULT_MAX_COST = 1.50

# La API rechaza imágenes de más de 5 MB: no se mandan (y no se cobran).
MAX_IMAGE_BYTES = 5 * 1024 * 1024
_MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}
# Una captura de celular se reduce a ~1568 px de lado mayor: ~1500-1600 tokens.
# Es un supuesto de la estimación; el consumo real lo informa la API.
DEFAULT_TOKENS_PER_IMAGE = 1600
# Salida supuesta por imagen: envoltorio + ~3 filas.
DEFAULT_OUT_TOKENS_PER_IMAGE = 250
# Como en llm_backtest: una falla de saldo o de clave no se reintenta.
_ABORT_FAILURES = llm_backtest._ABORT_FAILURES

Rates = tuple[float, float]  # USD por millón de tokens: (entrada, salida)


@dataclass(frozen=True)
class GoldItem:
    """Una imagen del oro con sus filas curadas (congeladas)."""

    ingestion_id: str
    file_path: str
    rows: list[measure.FrozenRow]


# --- costo ----------------------------------------------------------------------


def usage_cost(input_tokens: int, output_tokens: int, rates: Rates) -> float:
    return input_tokens / 1e6 * rates[0] + output_tokens / 1e6 * rates[1]


def resolve_rates(model: str, rate_in: float | None, rate_out: float | None) -> Rates | None:
    """Tarifa explícita si vienen las dos; si no, la del repo; si no, `None`."""
    if rate_in is not None and rate_out is not None:
        return (rate_in, rate_out)
    if rate_in is not None or rate_out is not None:
        return None  # media tarifa no es tarifa
    return llm_assist._RATES_USD_PER_MTOK.get(model)


@dataclass(frozen=True)
class Estimate:
    n_images: int
    total_bytes: int
    input_tokens: int
    output_tokens: int
    cost_usd: float | None


def estimate(
    *,
    n_images: int,
    total_bytes: int,
    fixed_prompt_tokens: int,
    tokens_per_image: int,
    out_tokens_per_image: int,
    rates: Rates | None,
) -> Estimate:
    tokens_in = n_images * (fixed_prompt_tokens + tokens_per_image)
    tokens_out = n_images * out_tokens_per_image
    return Estimate(
        n_images=n_images,
        total_bytes=total_bytes,
        input_tokens=tokens_in,
        output_tokens=tokens_out,
        cost_usd=usage_cost(tokens_in, tokens_out, rates) if rates else None,
    )


# --- el oro ---------------------------------------------------------------------


def load_gold(session: Session) -> list[GoldItem]:
    """Imágenes de la versión vigente del conjunto congelado, con sus filas."""
    version = reference_set.current_version(session)
    if version is None:
        raise measure.NoReferenceSetError("No hay ningún conjunto de referencia congelado.")
    members = reference_set.members_of(session, version)
    ingestions = {
        i.id: i
        for i in session.execute(
            select(RawIngestion).where(
                RawIngestion.id.in_([m.ingestion_id for m in members])
            )
        ).scalars()
    }
    gold = []
    for member in members:
        ingestion = ingestions[member.ingestion_id]
        rows = measure.frozen_rows(member)
        if ingestion.content_type == ContentType.IMAGE and rows:
            gold.append(GoldItem(str(ingestion.id), ingestion.file_path, rows))
    return sorted(gold, key=lambda g: g.ingestion_id)


# --- archivo de resultados (una línea por imagen) --------------------------------

read_records = llm_backtest.read_records
latest_records = llm_backtest.latest_records
append_record = llm_backtest._append_record


def pending_items(
    items: Sequence[GoldItem], previous: Mapping[str, Mapping[str, Any]]
) -> list[GoldItem]:
    """Lo que falta: sin resultado, o con uno degradado (se reintenta)."""
    return [
        i
        for i in items
        if i.ingestion_id not in previous or previous[i.ingestion_id].get("degraded")
    ]


# --- la llamada al proveedor -----------------------------------------------------

VisionCall = Callable[[GoldItem, bytes, str], llm_propose_ad.AdProposal]


def media_type_of(path: Path) -> str | None:
    return _MEDIA_TYPES.get(path.suffix.lower())


def make_vision_call(
    client: Any,
    *,
    request_model: str,
    products: Sequence[Product],
    provinces: Sequence[Province],
    glossary: Sequence[GlossaryEntry] = (),
) -> VisionCall:
    """Misma petición que `llm_propose_ad.propose_ad`, con la imagen como entrada.

    Mismo prompt de sistema, mismo bloque de catálogos (el cacheado), mismo
    esquema, mismo `thinking` apagado. Lo único distinto: el bloque final del
    anuncio es la imagen en base64 en vez del texto. No hay texto del que
    verificar evidencia. Nunca levanta: un error se vuelve un `AdProposal`
    degradado.
    """
    version = effective_prompt_version(llm_propose_ad.PROMPT_VERSION, glossary)
    catalog_block = llm_propose_ad.build_prompt_blocks("", products, provinces, glossary)[0]

    def call(item: GoldItem, image: bytes, media_type: str) -> llm_propose_ad.AdProposal:
        content = [
            catalog_block,
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": base64.b64encode(image).decode("ascii"),
                },
            },
            {"type": "text", "text": "Anuncio: es la imagen adjunta."},
        ]
        try:
            response = client.messages.create(
                model=request_model,
                max_tokens=llm_propose_ad.MAX_TOKENS,
                system=llm_propose_ad._SYSTEM_PROMPT,
                messages=[{"role": "user", "content": content}],
                output_config={
                    "format": {"type": "json_schema", "schema": llm_propose_ad.response_schema()}
                },
                thinking={"type": "disabled"},
            )
        except Exception as error:
            return llm_propose_ad.AdProposal(
                degraded=True, failure=llm_assist.classify_failure(error), prompt_version=version
            )
        tokens_in, tokens_out, cache_read, cache_write = llm_propose_ad._usage_with_cache(response)
        raw = llm_assist._response_text(response)
        body: object = None
        if raw:
            try:
                body = json.loads(raw)
            except ValueError:
                body = None
        parsed = llm_propose_ad.parse_response(body, products, provinces)
        common: dict[str, Any] = {
            "input_tokens": tokens_in,
            "output_tokens": tokens_out,
            "cache_read_tokens": cache_read,
            "cache_write_tokens": cache_write,
            "raw_response": raw or None,
            "prompt_version": version,
        }
        if parsed is None:
            return llm_propose_ad.AdProposal(degraded=True, failure="other", **common)
        return replace(parsed, **common)

    return call


# --- la corrida ------------------------------------------------------------------


def run_vision(
    items: Sequence[GoldItem],
    *,
    results_path: Path,
    raw_dir: Path,
    call: VisionCall,
    rates: Rates | None,
    model: str,
    max_cost: float,
    channel_of: Callable[[GoldItem], Province | None],
    limit: int | None = None,
    out: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Una llamada por imagen, en serie, anexando un resultado por línea.

    Retoma: salta lo que ya tiene resultado no degradado. Corta cuando el costo
    de ESTA corrida alcanza `max_cost`, o ante una falla de saldo o de clave.
    `limit` recorta lo pendiente (piloto).
    """
    if rates is None:
        # Sin tarifa el tope no se puede hacer cumplir: no se gasta a ciegas.
        raise ValueError("run_vision necesita una tarifa para hacer cumplir --max-cost")
    pending = pending_items(items, latest_records(read_records(results_path)))
    if limit is not None:
        pending = pending[:limit]
    out(f"A procesar: {len(pending)} de {len(items)} (el resto ya tiene resultado).")

    cost = 0.0
    done = degraded = 0
    aborted = stopped_by_cost = False
    for n, item in enumerate(pending, start=1):
        if cost >= max_cost:
            stopped_by_cost = True
            out(f"Tope de costo alcanzado (${cost:.4f} >= ${max_cost:.2f}): corrida detenida.")
            break
        path = raw_dir / item.file_path
        media_type = media_type_of(path)
        image: bytes | None = None
        failure = ""
        if not path.is_file():
            failure = "missing_file"
        elif media_type is None:
            failure = "unsupported_type"
        elif path.stat().st_size > MAX_IMAGE_BYTES:
            failure = "too_large"
        else:
            image = path.read_bytes()

        record: dict[str, Any] = {
            "ingestion_id": item.ingestion_id,
            "model": model,
            "prompt_version": llm_propose_ad.PROMPT_VERSION,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        if image is None or media_type is None:
            record.update(
                degraded=True, failure=failure, discard=None, rows=[],
                channel_province_id=None, input_tokens=0, output_tokens=0, cost_usd=0.0,
            )
        else:
            result = call(item, image, media_type)
            channel = channel_of(item)
            rows: list[ExtractionProposal] = [
                replace(r, province=channel) if channel is not None else r for r in result.rows
            ]
            call_cost = usage_cost(result.input_tokens, result.output_tokens, rates)
            cost += call_cost
            record.update(
                prompt_version=result.prompt_version,
                degraded=result.degraded,
                failure=result.failure,
                discard=result.discard,
                discard_reason=result.discard_reason,
                rows=llm_backtest.rows_to_dicts(rows),
                dropped=result.dropped,
                channel_province_id=str(channel.id) if channel is not None else None,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                cost_usd=call_cost,
            )
            if result.degraded and result.raw_response:
                # Único texto libre que se guarda, y solo para diagnosticar.
                record["raw_response"] = (redact_personal_data(result.raw_response) or "")[:500]
            failure = result.failure
        append_record(record, results_path)

        done += 1
        degraded += bool(record["degraded"])
        out(
            f"[{n}/{len(pending)}] {item.ingestion_id[:8]} "
            f"{'DEGRADADO (' + failure + ')' if record['degraded'] else 'ok'} "
            f"- costo acumulado ${cost:.4f}"
        )
        if failure in _ABORT_FAILURES:
            out(f"Corrida detenida: falla «{failure}». Lo hecho quedó guardado; se retoma.")
            aborted = True
            break
    return {
        "done": done,
        "degraded": degraded,
        "cost_usd": cost,
        "aborted": aborted,
        "stopped_by_cost": stopped_by_cost,
    }


# --- puntuación ------------------------------------------------------------------


@dataclass
class Score:
    """Aciertos por campo sobre las filas curadas (definición de H0)."""

    rows: int = 0
    product: int = 0
    price: int = 0  # valor Y moneda, como H0
    currency: int = 0  # moneda sola (informativo; exige que haya precio propuesto)
    quantity_unit: int = 0
    province: int = 0
    complete: int = 0


def score_rows(
    score: Score,
    gold: Sequence[measure.FrozenRow],
    candidates: list[ExtractionProposal],
    channel: Province | None,
) -> None:
    for row, proposal in measure._match_rows_to_candidates(list(gold), candidates, channel):
        ok = measure.row_correctness(row, proposal)
        score.rows += 1
        score.product += ok.product
        score.price += ok.price
        score.currency += proposal.price is not None and proposal.currency == row.currency
        score.quantity_unit += ok.quantity_unit
        score.province += ok.province
        score.complete += ok.complete


def frac(k: int, n: int) -> str:
    """`k/n (p%)`: el n siempre a la vista; `n/d` si no hay base."""
    return f"{k}/{n} ({100 * k / n:.1f}%)" if n else f"{k}/{n} (n/d)"


RowsOf = Callable[[Mapping[str, Any]], Any]


def _vision_rows(record: Mapping[str, Any]) -> Any:
    return record.get("rows")


def _ocr_rows(record: Mapping[str, Any]) -> Any:
    return (record.get("proposal") or {}).get("rows")


def _score_ids(
    ids: Sequence[str],
    records: Mapping[str, Mapping[str, Any]],
    rows_of: RowsOf,
    gold: Mapping[str, Sequence[measure.FrozenRow]],
    products: dict[str, Product],
    provinces: dict[str, Province],
) -> tuple[Score, int]:
    """Puntaje de los anuncios `ids` y cuántos de ellos no propusieron ninguna fila."""
    score = Score()
    empty = 0
    for ing in ids:
        record = records[ing]
        candidates = llm_backtest.rows_from_dicts(rows_of(record) or [], products, provinces)
        channel = provinces.get(str(record.get("channel_province_id")))
        empty += not candidates
        score_rows(score, gold[ing], candidates, channel)
    return score, empty


@dataclass
class Aggregate:
    ads: int
    degraded: int
    score: Score
    zero_row_ads: int
    cost_usd: float
    input_tokens: int
    output_tokens: int


def aggregate(
    records: Mapping[str, Mapping[str, Any]],
    gold: Mapping[str, Sequence[measure.FrozenRow]],
    products: dict[str, Product],
    provinces: dict[str, Province],
) -> Aggregate:
    """Visión sobre el oro. Los degradados quedan fuera del acierto y se cuentan aparte."""
    in_gold = [i for i in records if i in gold]
    ok_ids = [i for i in in_gold if not records[i].get("degraded")]
    score, empty = _score_ids(ok_ids, records, _vision_rows, gold, products, provinces)
    return Aggregate(
        ads=len(ok_ids),
        degraded=len(in_gold) - len(ok_ids),
        score=score,
        zero_row_ads=empty,
        cost_usd=sum(float(records[i].get("cost_usd") or 0.0) for i in in_gold),
        input_tokens=sum(int(records[i].get("input_tokens") or 0) for i in in_gold),
        output_tokens=sum(int(records[i].get("output_tokens") or 0) for i in in_gold),
    )


@dataclass
class Comparison:
    common_ads: int
    vision: Score = field(default_factory=Score)
    ocr: Score = field(default_factory=Score)
    vision_zero_rows: int = 0
    ocr_zero_rows: int = 0
    ocr_prompt_versions: set[str] = field(default_factory=set)
    ocr_models: set[str] = field(default_factory=set)


def compare(
    vision: Mapping[str, Mapping[str, Any]],
    ocr: Mapping[str, Mapping[str, Any]],
    gold: Mapping[str, Sequence[measure.FrozenRow]],
    products: dict[str, Product],
    provinces: dict[str, Province],
) -> Comparison:
    """Las dos rutas re-puntuadas con el mismo código sobre las imágenes que ambas cubren."""
    common = sorted(
        i
        for i in vision
        if i in gold
        and i in ocr
        and not vision[i].get("degraded")
        and not ocr[i].get("degraded")
    )
    result = Comparison(common_ads=len(common))
    result.vision, result.vision_zero_rows = _score_ids(
        common, vision, _vision_rows, gold, products, provinces
    )
    result.ocr, result.ocr_zero_rows = _score_ids(common, ocr, _ocr_rows, gold, products, provinces)
    for i in common:
        result.ocr_prompt_versions.add(str(ocr[i].get("prompt_version", "v1 (sin registro)")))
        result.ocr_models.add(str(ocr[i].get("model", "?")))
    return result


# --- informe ---------------------------------------------------------------------

_FIELDS = (
    ("Producto", "product"),
    ("Precio y moneda (H0)", "price"),
    ("Moneda sola", "currency"),
    ("Cantidad y unidad", "quantity_unit"),
    ("Provincia", "province"),
    ("Extracción completa correcta", "complete"),
)


def render_report(
    vision: Mapping[str, Mapping[str, Any]],
    ocr: Mapping[str, Mapping[str, Any]] | None,
    gold: Mapping[str, Sequence[measure.FrozenRow]],
    products: dict[str, Product],
    provinces: dict[str, Province],
    *,
    ocr_path: Path,
) -> str:
    agg = aggregate(vision, gold, products, provinces)
    s = agg.score
    models = sorted({str(r.get("model", "?")) for r in vision.values()})
    versions = sorted({str(r.get("prompt_version", "?")) for r in vision.values()})
    lines = [
        "# Backtest de visión directa",
        "",
        f"Generado: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
        f"Modelo(s): {', '.join(models) or 'n/d'}. Prompt: {', '.join(versions) or 'n/d'}.",
        "",
        "Cómo leerlo: oro = imágenes del conjunto de referencia congelado vigente, con sus "
        "filas curadas. «Correcto» es la definición de H0 (`measure.py`); el «precio» exige "
        "valor y moneda, y la provincia fija del canal se aplica a las filas del modelo "
        "(en anuncios de canal la provincia no distingue a un camino del otro). «Moneda "
        "sola» es informativa. Las muestras son chicas: cada porcentaje trae su n. No fija "
        "umbrales ni concluye.",
        "",
        "## Visión directa (imagen cruda al modelo)",
        "",
        f"- Imágenes evaluadas: {agg.ads} de {len(gold)} del oro; filas curadas: {s.rows}.",
        f"- Llamadas degradadas (fuera del acierto): {agg.degraded}.",
        f"- Imágenes sin ninguna fila propuesta: {frac(agg.zero_row_ads, agg.ads)}.",
        f"- Consumo real: {agg.input_tokens:,} tokens de entrada, {agg.output_tokens:,} de salida; "
        f"costo real: ${agg.cost_usd:.4f}.",
        "",
        "| Campo | Visión |",
        "|---|---|",
    ]
    lines += [f"| {label} | {frac(getattr(s, attr), s.rows)} |" for label, attr in _FIELDS]

    lines += ["", "## Lado a lado con OCR + asistente", ""]
    cmp_ = compare(vision, ocr, gold, products, provinces) if ocr else None
    if not ocr:
        lines.append(
            f"No se puede comparar: no hay resultados del camino OCR + asistente en `{ocr_path}` "
            "(archivo ausente o vacío)."
        )
    elif cmp_ is None or cmp_.common_ads == 0:
        lines.append(
            f"No se puede comparar: ninguna imagen del oro tiene resultado no degradado en los "
            f"dos caminos (`{ocr_path}` cubre otros anuncios o ninguno de estos)."
        )
    else:
        lines += [
            f"Imágenes que ambos caminos cubren (sin degradados): {cmp_.common_ads}; "
            f"filas curadas: {cmp_.vision.rows}. Los dos se puntúan contra las mismas filas "
            "congeladas con el mismo código.",
            "",
            "| Campo | Visión | OCR + asistente |",
            "|---|---|---|",
        ]
        lines += [
            f"| {label} | {frac(getattr(cmp_.vision, attr), cmp_.vision.rows)} "
            f"| {frac(getattr(cmp_.ocr, attr), cmp_.ocr.rows)} |"
            for label, attr in _FIELDS
        ]
        lines += [
            "",
            f"- Sin ninguna fila propuesta: visión {frac(cmp_.vision_zero_rows, cmp_.common_ads)}; "
            f"OCR {frac(cmp_.ocr_zero_rows, cmp_.common_ads)}.",
            f"- OCR + asistente: modelo(s) {', '.join(sorted(cmp_.ocr_models))}, prompt(s) "
            f"{', '.join(sorted(cmp_.ocr_prompt_versions))}.",
        ]
        if cmp_.ocr_prompt_versions != set(versions):
            lines.append(
                "- Ojo: la versión de prompt del camino OCR no es la de la visión; la "
                "comparación mezcla versiones de prompt."
            )
        if cmp_.ocr_models != set(models):
            lines.append("- Ojo: los dos caminos usaron modelos distintos.")
    return "\n".join(lines) + "\n"


# --- CLI -------------------------------------------------------------------------


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Visión directa vs OCR + asistente contra lo curado. Por defecto estima "
            "sin llamar a nada; --execute llama al proveedor y GASTA plata."
        )
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true", help="Corrida real (gasta).")
    mode.add_argument("--report-only", action="store_true", help="Solo regenera el informe.")
    parser.add_argument("--limit", type=int, default=None, help="Tope de imágenes (piloto).")
    parser.add_argument("--model", default=None, help="Por defecto, el modelo de la configuración.")
    parser.add_argument("--max-cost", type=float, default=DEFAULT_MAX_COST,
                        help="Tope de USD de ESTA corrida (default %(default)s).")
    parser.add_argument("--usd-per-mtok-in", type=float, default=None,
                        help="Tarifa de entrada si el modelo no la tiene en el repo.")
    parser.add_argument("--usd-per-mtok-out", type=float, default=None)
    parser.add_argument("--tokens-per-image", type=int, default=DEFAULT_TOKENS_PER_IMAGE)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--ocr-results", type=Path, default=DEFAULT_OCR_RESULTS,
                        help="Resultados de llm_backtest (camino OCR + asistente).")
    return parser.parse_args(argv)


def _write_report(
    session: Session,
    args: argparse.Namespace,
    gold: list[GoldItem],
    out: Callable[[str], None],
) -> None:
    products = {str(p.id): p for p in session.execute(select(Product)).scalars()}
    provinces = {str(p.id): p for p in session.execute(select(Province)).scalars()}
    ocr_records = latest_records(read_records(args.ocr_results))
    text = render_report(
        latest_records(read_records(args.results)),
        ocr_records or None,
        {g.ingestion_id: g.rows for g in gold},
        products,
        provinces,
        ocr_path=args.ocr_results,
    )
    out(text)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(text, encoding="utf-8")
    out(f"Informe escrito en {args.report}")


def main(argv: Sequence[str] | None = None, *, out: Callable[[str], None] = print) -> int:
    args = parse_args(argv)
    settings = get_settings()
    raw_dir = settings.raw_store_dir
    with session_scope() as session:
        gold = load_gold(session)
        if args.report_only:
            _write_report(session, args, gold, out)
            return 0

        model = provider.canonical_model(args.model) if args.model else provider.effective_model(settings)
        rates = resolve_rates(model, args.usd_per_mtok_in, args.usd_per_mtok_out)
        previous = latest_records(read_records(args.results))
        pending = pending_items(gold, previous)
        if args.limit is not None:
            pending = pending[: args.limit]
        present = [g for g in pending if (raw_dir / g.file_path).is_file()]
        products = measure._focus_products(session)
        provinces = measure._all_provinces(session)
        glossary = active_glossary_entries(session)
        fixed = (
            len(llm_propose_ad._SYSTEM_PROMPT)
            + len(llm_propose_ad.build_prompt("", products, provinces, glossary))
            + len(json.dumps(llm_propose_ad.response_schema()))
        )
        est = estimate(
            n_images=len(present),
            total_bytes=sum((raw_dir / g.file_path).stat().st_size for g in present),
            fixed_prompt_tokens=round(fixed / llm_backtest.CHARS_PER_TOKEN),
            tokens_per_image=args.tokens_per_image,
            out_tokens_per_image=DEFAULT_OUT_TOKENS_PER_IMAGE,
            rates=rates,
        )
        out(f"Oro: {len(gold)} imágenes del conjunto de referencia vigente; "
            f"{len(gold) - len(pending_items(gold, previous))} ya tienen resultado.")
        out(f"A procesar: {len(pending)}; con archivo en {raw_dir}: {len(present)} "
            f"({len(pending) - len(present)} sin archivo).")
        out(f"Tamaño total: {est.total_bytes / 1e6:.1f} MB. Modelo: {model}.")
        out(f"Tokens estimados: ~{est.input_tokens:,} de entrada y ~{est.output_tokens:,} de "
            f"salida (supuestos: {args.tokens_per_image} por imagen, "
            f"{DEFAULT_OUT_TOKENS_PER_IMAGE} de salida; el consumo real lo informa la API).")
        if est.cost_usd is None:
            out(f"Sin tarifa para {model}: pasá --usd-per-mtok-in y --usd-per-mtok-out.")
        else:
            out(f"Costo estimado: ${est.cost_usd:.2f} (tope por corrida: ${args.max_cost:.2f}).")

        if not args.execute:
            out("MODO SECO: no se llamó a nada. Para la corrida real, agregá --execute.")
            return 0
        if rates is None:
            out("No se ejecuta sin tarifa: el tope de costo no se podría hacer cumplir.")
            return 2
        try:
            llm = provider.resolve_config(settings).with_overrides(
                model=model, timeout=args.timeout
            )
        except ValueError as error:
            out(f"No hay clave de API para la corrida real. {error}. No se llama a nada.")
            return 2
        call = make_vision_call(
            provider.build_client(llm),
            request_model=llm.request_model,
            products=products,
            provinces=provinces,
            glossary=glossary,
        )

        def channel_of(item: GoldItem) -> Province | None:
            ingestion = session.get(RawIngestion, uuid.UUID(item.ingestion_id))
            return origin.channel_province(session, ingestion) if ingestion else None

        run_vision(
            gold,
            results_path=args.results,
            raw_dir=raw_dir,
            call=call,
            rates=rates,
            model=llm.model,
            max_cost=args.max_cost,
            channel_of=channel_of,
            limit=args.limit,
            out=out,
        )
        _write_report(session, args, gold, out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
