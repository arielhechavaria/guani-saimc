"""Informe del modo sombra del motor de validación (slice A5c).

Corre `python -m saimc.validation.shadow` (por defecto, en seco).

Corre el motor de A5b (`validate_ad`) sobre anuncios que el operador ya decidió
y dice cuánto se podría automatizar y con qué error. No gasta API (usa las
propuestas ya guardadas), no escribe en la base y no enciende nada: el motor
sigue sin que nadie lo llame desde la aplicación.

**Dos conjuntos, que no se mezclan** (`docs/spec-motor-validacion.md`,
«Protocolo del modo sombra»):

- *Calibración*: el oro del backtest (`--gold`). La propuesta del modelo es la
  que trae cada línea, no la de la base.
- *Evaluación*: las revisiones de `llm_reviews` que fueron decisiones
  INDIVIDUALES del operador. Un descarte aceptado en lote no cuenta.

**Los parámetros no tienen valor por defecto** (`spec.md` §12.6: se miden, no se
inventan). O se pasan los tres, o se pide `--grid`, que corre una grilla chica
y declarada (`GRID_*`) para elegirlos con el dato a la vista.

**No decide nada.** No fija metas ni dice si conviene encender un carril: eso es
de G1a y G1b. Un porcentaje nunca se imprime sin su n y su cota superior de
Clopper-Pearson al 95%.

**El bloque «Jev» (slice A6b-2).** Por conjunto, y solo sobre los textos que
tienen una señal de Jev con la huella de preguntas vigente (`jev_signals`),
tres tablas: el carril 1 con Jev y sin Jev, el triage al lado del carril 0, y
el acierto de producto y moneda contra las filas curadas. Lee la tabla: no
llama a Jev, no depende de `SAIMC_LLM_JEV_ENABLED` y no escribe nada. Las
grillas son para mirar; ningún umbral se elige acá (es de G1). Contrato:
`docs/spec-slice-A6b.md`, punto 8.

Con `--execute` agrega una línea a `docs/shadow-runs.jsonl`. Sin él no escribe.
"""

from __future__ import annotations

import argparse
import dataclasses
import itertools
import json
import sys
import uuid
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from saimc.db import get_session_factory
from saimc.extraction import llm_backtest, llm_store, measure
from saimc.extraction.llm_checks import RowMeta
from saimc.extraction.llm_propose_ad import AdProposal
from saimc.ingestion import origin
from saimc.models import (
    ContentType,
    CurationEvent,
    CurationMode,
    JevSignal,
    LlmProposal,
    LlmReview,
    PriceHistory,
    Product,
    Province,
    RawIngestion,
    ReviewOutcome,
)
from saimc.units import canonical_unit
from saimc.validation import context, jev_signal
from saimc.validation.context import Catalog, ProposalDiff, Transcriber
from saimc.validation.engine import decide_lane_0, validate_ad

# `x as x`: reexportación explícita (las cotas se importaban de acá).
from saimc.validation.stats import CONFIDENCE, Rate, format_rate, pct
from saimc.validation.stats import clopper_pearson_lower as clopper_pearson_lower
from saimc.validation.stats import clopper_pearson_upper as clopper_pearson_upper
from saimc.validation.types import (
    LANE_ACCEPT,
    LANE_DISCARD,
    LANE_REVIEW,
    AdContext,
    ValidationConfig,
)

# `shadow.py` vive en `src/saimc/validation/`: la raíz del repo está tres
# niveles arriba. Se resuelve desde `__file__` y no del cwd, como `measure`.
_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_RUNS_FILE = _REPO_ROOT / "docs" / "shadow-runs.jsonl"

#: La grilla de `--grid`. No son valores recomendados ni por defecto: son los
#: puntos que se miden para que el operador elija (§12.6). Chica a propósito;
#: cambiarla cambia qué se compara entre corridas de `shadow-runs.jsonl`.
GRID_MIN_CELL_N = (3, 5, 10)
GRID_CHANNEL_MIN_HISTORY = (1, 3, 5, 10)
#: 1.00 es «sin tope»: no es un valor a elegir, es la referencia que muestra
#: cuánto volumen deja afuera exigir que el canal esté limpio.
GRID_CHANNEL_MAX_EDIT_RATE = (0.10, 0.25, 0.50, 1.00)

#: Campos que el oro del backtest permite comparar: `expected_rows` no trae
#: provincia, formato de venta, base del precio ni envase. El producto se usa
#: para emparejar las filas, así que una diferencia de producto aparece como
#: una fila de más y una que falta.
GOLD_FIELDS = ("price", "currency", "quantity", "unit")

_FIELD_LABELS = {
    "product_id": "producto",
    "price": "precio",
    "currency": "moneda",
    "quantity": "cantidad",
    "unit": "unidad",
    "province_id": "provincia",
    "sale_format": "formato de venta",
    "price_basis": "base del precio",
    "packaging": "envase",
}

_TYPE_LABELS = {ContentType.TEXT.value: "texto", ContentType.IMAGE.value: "imagen"}


# --- Clopper-Pearson ---------------------------------------------------------

# Viven en `validation.stats` desde B2a (la auditoría del carril 0 también las
# usa y no tiene por qué cargar este módulo); se reexportan con su nombre de
# siempre para quien ya las importaba de acá.
_pct = pct

# --- error del carril 1 contra el oro ----------------------------------------


def _number(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _gold_key(row: Mapping[str, Any]) -> dict[str, object]:
    """Una fila del oro o de la propuesta, comparable: «320» y «320.00» son el
    mismo precio y «libra» y «lb» la misma unidad."""
    unit = row.get("unit")
    return {
        "product": row.get("product"),
        "price": _number(row.get("price")),
        "currency": row.get("currency"),
        "quantity": _number(row.get("quantity")),
        "unit": canonical_unit(unit) or unit,
    }


def diff_expected(
    proposed: Sequence[Mapping[str, Any]], expected: Sequence[Mapping[str, Any]]
) -> ProposalDiff:
    """Diferencia entre las filas del modelo y las `expected_rows` del oro.

    Cada fila trae `product` (nombre), `price`, `currency`, `quantity`, `unit`.
    Primero se emparejan las idénticas; después, las del mismo producto, en
    orden. Una fila propuesta sin pareja es una que el operador borraría; una
    del oro sin pareja, una que agregaría.
    """
    left = [_gold_key(row) for row in proposed]
    right: list[dict[str, object] | None] = [_gold_key(row) for row in expected]
    pairs: dict[int, dict[str, object]] = {}
    # Dos pasadas: la idéntica gana, para que el orden no invente diferencias.
    for exact in (True, False):
        for i, row in enumerate(left):
            if i in pairs:
                continue
            for j, candidate in enumerate(right):
                if candidate is None:
                    continue
                if (row == candidate) if exact else (row["product"] == candidate["product"]):
                    pairs[i] = candidate
                    right[j] = None
                    break

    edited = 0
    field_errors: dict[str, int] = {}
    for i, match in pairs.items():
        changed = [name for name in GOLD_FIELDS if left[i][name] != match[name]]
        edited += bool(changed)
        for name in changed:
            field_errors[name] = field_errors.get(name, 0) + 1
    return ProposalDiff(
        proposed_rows=len(left),
        deleted_rows=len(left) - len(pairs),
        edited_rows=edited,
        added_rows=sum(1 for candidate in right if candidate is not None),
        field_errors=field_errors,
    )


# --- los conjuntos -----------------------------------------------------------


@dataclass(frozen=True)
class JevRow:
    """Lo único que Jev mira de una fila: su producto y su moneda. Sirve para
    las filas curadas (`prices_history`) y para las que propuso el modelo."""

    product_id: uuid.UUID | None
    currency: str | None


@dataclass(frozen=True)
class Case:
    """Un anuncio ya decidido: el contexto para el motor y la verdad."""

    ingestion_id: str
    content_type: str
    ctx: AdContext
    #: `True` si el operador (o el oro) lo conservó; `False` si lo descartó.
    kept: bool
    #: Qué habría tenido que cambiar el operador en la propuesta. `None` si el
    #: modelo no propuso filas que comparar (descartó o degradó).
    diff: ProposalDiff | None
    #: La señal de Jev de la huella vigente, ya leída. `None` = sin señal. La
    #: pone `attach_jev`; el resto del informe no la mira.
    jev: jev_signal.Reading | None = None
    #: Las filas que quedaron curadas en `prices_history` (bloque «Jev», tabla 3).
    curated_rows: tuple[JevRow, ...] = ()


@dataclass
class CaseSet:
    name: str
    title: str
    #: Campos sobre los que se informa el error del carril 1.
    fields: tuple[str, ...]
    cases: list[Case] = field(default_factory=list)
    #: Conteos de lo que quedó adentro y afuera, para el reporte.
    notes: dict[str, int] = field(default_factory=dict)
    #: Advertencias en texto: lo que el conjunto no permite medir.
    caveats: list[str] = field(default_factory=list)


def _gold_proposal(
    record: Mapping[str, Any],
    products: dict[str, Product],
    provinces: dict[str, Province],
) -> AdProposal | None:
    """La propuesta del modelo tal como quedó en la línea del oro. `None` si la
    línea no trae el metadato por fila (resultados anteriores a la v3)."""
    proposal = record.get("proposal") or {}
    raw_rows = proposal.get("rows") or []
    raw_meta = proposal.get("row_meta")
    if not isinstance(raw_meta, list) or len(raw_meta) != len(raw_rows):
        return None
    dropped = proposal.get("dropped")
    discard = proposal.get("discard")
    return AdProposal(
        rows=llm_backtest.rows_from_dicts(raw_rows, products, provinces),
        row_meta=[
            RowMeta(
                evidence=meta.get("evidence"),
                currency_explicit=meta.get("currency_explicit") is True,
                evidence_found=meta.get("evidence_found") is True,
                price_in_evidence=meta.get("price_in_evidence"),
            )
            for meta in raw_meta
        ],
        discard=discard if isinstance(discard, bool) else None,
        discard_reason=proposal.get("discard_reason"),
        degraded=bool(record.get("degraded")),
        failure=str(record.get("failure") or ""),
        dropped=dropped if isinstance(dropped, int) else 0,
    )


def _bump(notes: dict[str, int], key: str, amount: int = 1) -> None:
    notes[key] = notes.get(key, 0) + amount


def load_calibration(
    session: Session,
    catalog: Catalog,
    gold_path: Path,
    *,
    raw_store_dir: Path | None = None,
    transcribe: Transcriber | None = None,
) -> CaseSet:
    """El oro del backtest: una línea por anuncio, con `expected`,
    `expected_rows` y la propuesta del modelo de esa corrida."""
    dataset = CaseSet(
        name="calibration",
        title=f"Calibración — oro del backtest ({gold_path.name})",
        fields=GOLD_FIELDS,
        caveats=[
            "El oro solo trae producto, precio, moneda, cantidad y unidad de cada fila: "
            "el error por campo no cubre provincia, formato de venta, base del precio ni envase.",
            "El oro puede estar incompleto (el operador no siempre cargó todas las filas "
            "de una lista): una fila del modelo sin pareja cuenta como diferencia igual.",
            "Historial del canal (regla 4): el de hoy. Los anuncios del oro se curaron "
            "antes de que existieran las revisiones.",
        ],
    )
    products = {str(p.id): p for p in session.execute(select(Product)).scalars()}
    provinces = {str(p.id): p for p in catalog.provinces}
    records = llm_backtest.latest_records(llm_backtest.read_records(gold_path))
    dataset.notes["líneas del oro"] = len(records)
    for ingestion_id, record in records.items():
        try:
            ingestion = session.get(RawIngestion, uuid.UUID(ingestion_id))
        except ValueError:
            ingestion = None
        if ingestion is None:
            _bump(dataset.notes, "sin el anuncio en esta base (fuera)")
            continue
        model = _gold_proposal(record, products, provinces)
        if model is None:
            _bump(dataset.notes, "sin metadato por fila en la línea (fuera)")
            continue
        ocr_text = record.get("ocr_text")
        ctx = context.build_ad_context(
            session,
            ingestion,
            model=model,
            catalog=catalog,
            text=ocr_text if isinstance(ocr_text, str) else None,
            raw_store_dir=raw_store_dir,
            transcribe=transcribe,
        )
        if not ctx.text.strip():
            _bump(dataset.notes, "sin texto recuperable (fuera)")
            continue
        assert ctx.model is not None  # noqa: S101 — recién armado con `model`
        proposed = [
            {
                "product": None if row.product is None else row.product.name,
                "price": row.price,
                "currency": row.currency,
                "quantity": row.quantity,
                "unit": row.unit,
            }
            for row in ctx.model.rows
        ]
        dataset.cases.append(
            Case(
                ingestion_id=ingestion_id,
                content_type=str(ingestion.content_type),
                ctx=ctx,
                kept=record.get("expected") == "kept",
                diff=(
                    None
                    if model.discard is not False
                    else diff_expected(proposed, record.get("expected_rows") or [])
                ),
            )
        )
    return dataset


def load_evaluation(
    session: Session,
    catalog: Catalog,
    *,
    raw_store_dir: Path | None = None,
    transcribe: Transcriber | None = None,
) -> CaseSet:
    """Las revisiones de `llm_reviews` que fueron decisiones individuales."""
    dataset = CaseSet(
        name="evaluation",
        title="Evaluación — revisiones individuales del operador (llm_reviews)",
        fields=llm_store.REVIEW_FIELDS,
        caveats=[
            "Lo rescatado de un lote no se puede identificar: la casilla desmarcada no "
            "deja rastro (el anuncio queda pendiente, sin evento). Si después se curó o "
            "descartó de a uno, ya está contado acá como decisión individual.",
            "Lo que el carril 0 descartó solo no cuenta; si la auditoría lo rescató y "
            "después se decidió de a uno, esa decisión sí cuenta como individual.",
            "Historial del canal (regla 4): solo las decisiones anteriores a la de cada "
            "anuncio.",
        ],
    )
    events = {
        (ingestion_id, str(action), str(mode))
        for ingestion_id, action, mode in session.execute(
            select(CurationEvent.ingestion_id, CurationEvent.action, CurationEvent.mode)
        )
    }
    individual = CurationMode.INDIVIDUAL.value
    # Por tramo (`context.BatchDecisions`): lo rescatado y después decidido de
    # a uno cuenta como individual aunque el anuncio tenga un evento de lote.
    batch = context.batch_decisions(session)
    logged = {(i, action) for i, action, mode in events if mode == individual}

    reviews = list(
        session.execute(select(LlmReview).order_by(LlmReview.created_at)).scalars().all()
    )
    dataset.notes["revisiones en total"] = len(reviews)
    dataset.notes["decididas en lote (no cuentan)"] = 0
    dataset.notes["individuales sin evento en la bitácora"] = 0
    for review in reviews:
        if not context.is_individual(review, batch):
            _bump(dataset.notes, "decididas en lote (no cuentan)")
            continue
        if (review.ingestion_id, str(review.outcome)) not in logged:
            # Anteriores a la bitácora (A2b): ningún camino de lote las escribió.
            _bump(dataset.notes, "individuales sin evento en la bitácora")
        ingestion = session.get(RawIngestion, review.ingestion_id)
        proposal = session.get(LlmProposal, review.proposal_id)
        if ingestion is None or proposal is None:
            _bump(dataset.notes, "sin anuncio o sin propuesta (fuera)")
            continue
        model = context.stored_proposal(session, proposal, catalog)
        ctx = context.build_ad_context(
            session,
            ingestion,
            model=model,
            catalog=catalog,
            history_before=review.created_at,
            raw_store_dir=raw_store_dir,
            transcribe=transcribe,
        )
        if not ctx.text.strip():
            _bump(dataset.notes, "sin texto recuperable (fuera)")
            continue
        channel_province = origin.channel_province(session, ingestion)
        dataset.cases.append(
            Case(
                ingestion_id=str(ingestion.id),
                content_type=str(ingestion.content_type),
                ctx=ctx,
                kept=review.outcome == ReviewOutcome.CURATED,
                diff=(
                    None
                    if model.discard is not False
                    else context.diff_review(
                        review.rows,
                        channel_province_id=(
                            None if channel_province is None else str(channel_province.id)
                        ),
                    )
                ),
            )
        )
    return dataset


# --- correr el motor y contar ------------------------------------------------


@dataclass(frozen=True)
class CaseResult:
    """Lo que el motor dijo de un anuncio, al lado de lo que hizo el operador."""

    ingestion_id: str
    content_type: str
    lane: int
    kept: bool
    diff: ProposalDiff | None
    #: Los motivos del motor, tal cual.
    reasons: tuple[str, ...] = ()
    #: El modelo propuso filas y en alguna la moneda no está escrita.
    currency_assumed: bool = False
    #: Quedó fuera del carril 1 SOLO por la moneda no escrita: con
    #: `currency_explicit` en verdadero en todas sus filas habría entrado.
    only_currency: bool = False


def _lane1_error(kept: bool, diff: ProposalDiff | None) -> bool:
    """El error del carril 1, por anuncio: el operador editó o borró algo, o
    agregó una fila. También lo es si descartó el anuncio entero."""
    return not kept or (diff or ProposalDiff()).has_error


def evaluate_case(case: Case, config: ValidationConfig) -> CaseResult:
    validation = validate_ad(case.ctx, config)
    model = case.ctx.model
    assumed = (
        model is not None
        and model.discard is False
        and any(not meta.currency_explicit for meta in model.row_meta)
    )
    only_currency = False
    if assumed and validation.lane == LANE_REVIEW and model is not None:
        # Contrafáctico: el mismo anuncio con la moneda dada por escrita. Si
        # así entra al carril 1, lo único que lo dejaba afuera era esa regla.
        as_explicit = dataclasses.replace(
            case.ctx,
            model=dataclasses.replace(
                model,
                row_meta=[
                    dataclasses.replace(meta, currency_explicit=True) for meta in model.row_meta
                ],
            ),
        )
        only_currency = validate_ad(as_explicit, config).lane == LANE_ACCEPT
    return CaseResult(
        ingestion_id=case.ingestion_id,
        content_type=case.content_type,
        lane=validation.lane,
        kept=case.kept,
        diff=case.diff,
        reasons=validation.reasons,
        currency_assumed=assumed,
        only_currency=only_currency,
    )


# Motivos del motor que nombran varios campos en una lista: se cuentan por campo.
_LIST_REASONS = ("reglas y modelo no coinciden en ", "el modelo se abstuvo en ")


def split_reasons(reasons: Sequence[str]) -> set[str]:
    """Los motivos de UN anuncio, sin el número de fila y sin repetir.

    «fila 2: reglas y modelo no coinciden en precio, moneda» pasa a ser dos
    motivos, uno por campo. Devuelve un conjunto: cada motivo cuenta una vez
    por anuncio aunque lo tengan varias filas.
    """
    unique: set[str] = set()
    for reason in reasons:
        head, sep, tail = reason.partition(": ")
        text = tail if sep and head.startswith("fila ") else reason
        for prefix in _LIST_REASONS:
            if text.startswith(prefix):
                unique.update(prefix + name for name in text[len(prefix) :].split(", "))
                break
        else:  # `for ... else`: corre solo si el `for` no cortó con `break`
            unique.add(text)
    return unique


@dataclass
class SetSummary:
    """Los números de un conjunto con una combinación de parámetros."""

    total: int = 0
    by_type: dict[str, int] = field(default_factory=dict)
    lanes: dict[int, int] = field(
        default_factory=lambda: {LANE_DISCARD: 0, LANE_ACCEPT: 0, LANE_REVIEW: 0}
    )
    lane0_by_type: dict[str, int] = field(default_factory=dict)
    automation: Rate = field(default_factory=Rate)
    false_discards_text: Rate = field(default_factory=Rate)
    false_discards_image: Rate = field(default_factory=Rate)
    lane1_ad_errors: Rate = field(default_factory=Rate)
    #: Campo -> filas con ese campo distinto, sobre las filas propuestas del carril 1.
    lane1_field_errors: dict[str, Rate] = field(default_factory=dict)
    lane1_deleted_rows: Rate = field(default_factory=Rate)
    lane1_added_rows: int = 0
    #: Motivo -> anuncios del carril 2 que lo tienen.
    reasons: Counter[str] = field(default_factory=Counter)
    #: Anuncios con filas en que alguna moneda no está escrita.
    currency_assumed: int = 0
    #: De esos, los que quedan fuera del carril 1 SOLO por eso.
    only_currency: int = 0
    false_discard_ids: list[str] = field(default_factory=list)
    lane1_ids: list[str] = field(default_factory=list)
    lane1_error_ids: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "n": self.total,
            "by_type": self.by_type,
            "lanes": {str(lane): count for lane, count in self.lanes.items()},
            "lane0_by_type": self.lane0_by_type,
            "automation": self.automation.as_dict(),
            "false_discards_text": self.false_discards_text.as_dict(),
            "false_discards_image": self.false_discards_image.as_dict(),
            "lane1_ad_errors": self.lane1_ad_errors.as_dict(),
            "lane1_field_errors": {
                name: rate.as_dict() for name, rate in self.lane1_field_errors.items()
            },
            "lane1_deleted_rows": self.lane1_deleted_rows.as_dict(),
            "lane1_added_rows": self.lane1_added_rows,
            "lane2_reasons": dict(sorted(self.reasons.items())),
            "currency_assumed": self.currency_assumed,
            "only_currency": self.only_currency,
        }


def summarize(
    results: Sequence[CaseResult], fields: Sequence[str] = llm_store.REVIEW_FIELDS
) -> SetSummary:
    """Cuenta carriles, falsos descartes, errores del carril 1 y motivos."""
    summary = SetSummary(total=len(results))
    text, image = ContentType.TEXT.value, ContentType.IMAGE.value
    false_discards = {text: [0, 0], image: [0, 0]}  # tipo -> [conservados, n]
    ad_errors = lane1_rows = deleted = 0
    field_errors = dict.fromkeys(fields, 0)

    for result in results:
        summary.by_type[result.content_type] = summary.by_type.get(result.content_type, 0) + 1
        summary.lanes[result.lane] += 1
        summary.currency_assumed += result.currency_assumed
        summary.only_currency += result.only_currency

        if result.lane == LANE_DISCARD:
            kind = text if result.content_type == text else image
            summary.lane0_by_type[kind] = summary.lane0_by_type.get(kind, 0) + 1
            false_discards[kind][1] += 1
            if result.kept:
                # Carril 0 predicho y el operador (o el oro) lo conservó.
                false_discards[kind][0] += 1
                summary.false_discard_ids.append(result.ingestion_id)
        elif result.lane == LANE_ACCEPT:
            summary.lane1_ids.append(result.ingestion_id)
            diff = result.diff or ProposalDiff()
            if _lane1_error(result.kept, result.diff):
                ad_errors += 1
                summary.lane1_error_ids.append(result.ingestion_id)
            lane1_rows += diff.proposed_rows
            deleted += diff.deleted_rows
            summary.lane1_added_rows += diff.added_rows
            for name in fields:
                field_errors[name] += diff.field_errors.get(name, 0)
        else:
            summary.reasons.update(split_reasons(result.reasons))

    automated = summary.lanes[LANE_DISCARD] + summary.lanes[LANE_ACCEPT]
    summary.automation = Rate(automated, summary.total)
    summary.false_discards_text = Rate(*false_discards[text])
    summary.false_discards_image = Rate(*false_discards[image])
    summary.lane1_ad_errors = Rate(ad_errors, summary.lanes[LANE_ACCEPT])
    summary.lane1_field_errors = {name: Rate(field_errors[name], lane1_rows) for name in fields}
    summary.lane1_deleted_rows = Rate(deleted, lane1_rows)
    return summary


def run_set(dataset: CaseSet, config: ValidationConfig) -> SetSummary:
    return summarize([evaluate_case(case, config) for case in dataset.cases], dataset.fields)


# --- Jev en sombra (slice A6b-2) ---------------------------------------------

#: Las grillas del bloque «Jev». Son para MIRAR: el informe no elige ninguna, y
#: ningún umbral queda fijado por estar acá (eso es de G1). `None` es «cualquier
#: desacuerdo», o sea `signal_high_probability` sin fijar.
JEV_THRESHOLDS: tuple[float | None, ...] = (None, 0.5, 0.7, 0.9)
JEV_TRIAGE_THRESHOLDS = (0.1, 0.2, 0.3, 0.5)
#: Las dos preguntas de elección, con su nombre en el reporte y lo que midió A6
#: (con su juego de cinco preguntas). Es una referencia para comparar, no una meta.
JEV_FIELDS = {"product": ("producto", "96,8 %"), "currency": ("moneda", "94,3 %")}

_PROBABILITY_LABELS = {
    jev_signal.USE_PROBABILITY: "probabilities[choice]",
    jev_signal.USE_CONFIDENCE: "confidence",
}


def _canonical_id(ingestion_id: str) -> str:
    """El id como lo escribe `str(uuid)`: el oro podría traerlo en mayúsculas."""
    try:
        return str(uuid.UUID(ingestion_id))
    except ValueError:
        return ingestion_id


def attach_jev(session: Session, datasets: Sequence[CaseSet]) -> None:
    """Le pone a cada caso su señal vigente de Jev y sus filas curadas.

    Solo LEE `jev_signals` y `prices_history`: no llama a Jev y no mira el
    interruptor (una señal guardada se informa aunque Jev esté apagado hoy).
    La huella vigente sale del catálogo de pilotos de hoy; una señal de otra
    huella no entra. Si el catálogo no da una huella (dos pilotos con la misma
    clave), ningún caso tiene señal y el bloque informa n = 0.
    """
    try:
        current = jev_signal.load_options(session).questions_hash
    except ValueError:
        return
    readings = {
        str(signal.ingestion_id): jev_signal.read_signal(signal)
        for signal in session.execute(select(JevSignal).where(JevSignal.questions_hash == current)).scalars()
    }
    if not readings:
        return
    curated: dict[str, list[JevRow]] = {}
    rows = session.execute(
        select(PriceHistory.source_id, PriceHistory.product_id, PriceHistory.currency)
        .join(JevSignal, JevSignal.ingestion_id == PriceHistory.source_id)
        .where(JevSignal.questions_hash == current)
    )
    for source_id, product_id, currency in rows:
        curated.setdefault(str(source_id), []).append(
            JevRow(product_id=product_id, currency=None if currency is None else str(currency))
        )
    for dataset in datasets:
        dataset.cases = [
            dataclasses.replace(
                case,
                jev=readings.get(_canonical_id(case.ingestion_id)),
                curated_rows=tuple(curated.get(_canonical_id(case.ingestion_id), ())),
            )
            for case in dataset.cases
        ]


def _with_signal(cases: Sequence[Case]) -> list[tuple[Case, jev_signal.Reading]]:
    """Los casos que entran al bloque: textos con señal. Las imágenes no (C2)."""
    return [
        (case, case.jev)
        for case in cases
        if case.jev is not None and case.content_type == ContentType.TEXT.value
    ]


def _proposed_rows(model: AdProposal | None) -> list[JevRow]:
    """Las filas de la propuesta, con la forma que lee el adaptador y en el
    mismo orden: `ExternalSignal.row_index` es el índice en `model.rows`."""
    if model is None:
        return []
    return [
        JevRow(product_id=None if row.product is None else row.product.id, currency=row.currency)
        for row in model.rows
    ]


@dataclass(frozen=True)
class JevLane1Row:
    """El carril 1 CON Jev, para una probabilidad y un umbral."""

    #: Cuál de las dos probabilidades viaja en la señal (`jev_signal.USE_*`).
    probability: str
    #: `signal_high_probability`; `None` = cualquier desacuerdo saca.
    threshold: float | None
    #: Errores (`k`) sobre los anuncios que quedan en el carril 1 (`n`).
    lane1: Rate
    #: Anuncios que estaban en el carril 1 y con la señal pasan al 2.
    removed: int
    #: De esos, los que eran errores (el operador había cambiado algo)…
    removed_errors: int
    #: …y los que estaban bien: automatización perdida.
    removed_correct: int
    #: Anuncios que entran al carril 1 POR la señal. El motor no lo permite
    #: (caso 22): tiene que dar 0, y se cuenta para que quede a la vista.
    entered: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "probability": self.probability,
            "threshold": self.threshold,
            "lane1": self.lane1.as_dict(),
            "removed": self.removed,
            "removed_errors": self.removed_errors,
            "removed_correct": self.removed_correct,
            "entered": self.entered,
        }


@dataclass(frozen=True)
class JevLane1:
    """La tabla 1 con UNA combinación de parámetros."""

    config: ValidationConfig
    #: Sin Jev: errores (`k`) sobre los anuncios en el carril 1 (`n`).
    without: Rate
    rows: tuple[JevLane1Row, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "params": _params_dict(self.config),
            "without_jev": self.without.as_dict(),
            "rows": [row.as_dict() for row in self.rows],
        }


def jev_lane1(cases: Sequence[Case], config: ValidationConfig) -> JevLane1:
    """Tabla 1: el carril 1 sin Jev y con Jev, sobre los textos con señal.

    Corre `validate_ad` sin señales y con ellas. El umbral de cada fila pisa
    el `signal_high_probability` de `config`: acá se recorre la grilla.
    """
    signaled = _with_signal(cases)
    # `signals=()` a propósito: «sin Jev» es sin ninguna señal, traiga lo que
    # traiga el contexto.
    bare = [dataclasses.replace(case.ctx, signals=()) for case, _ in signaled]
    before = [validate_ad(ctx, config).lane == LANE_ACCEPT for ctx in bare]
    errors = [_lane1_error(case.kept, case.diff) for case, _ in signaled]

    rows: list[JevLane1Row] = []
    for source in jev_signal.PROBABILITY_SOURCES:
        signals = [
            jev_signal.to_external_signals(reading, _proposed_rows(case.ctx.model), probability=source)
            for case, reading in signaled
        ]
        for threshold in JEV_THRESHOLDS:
            with_jev = dataclasses.replace(config, signal_high_probability=threshold)
            lane1 = lane1_errors = removed = removed_errors = entered = 0
            for ctx, was_lane1, is_error, ad_signals in zip(bare, before, errors, signals, strict=True):
                # Sin señales el motor da lo mismo: no hace falta correrlo.
                now_lane1 = was_lane1
                if ad_signals:
                    signaled_ctx = dataclasses.replace(ctx, signals=ad_signals)
                    now_lane1 = validate_ad(signaled_ctx, with_jev).lane == LANE_ACCEPT
                lane1 += now_lane1
                lane1_errors += now_lane1 and is_error
                removed += was_lane1 and not now_lane1
                removed_errors += was_lane1 and not now_lane1 and is_error
                entered += now_lane1 and not was_lane1
            rows.append(
                JevLane1Row(
                    probability=source,
                    threshold=threshold,
                    lane1=Rate(lane1_errors, lane1),
                    removed=removed,
                    removed_errors=removed_errors,
                    removed_correct=removed - removed_errors,
                    entered=entered,
                )
            )
    in_lane1 = sum(before)
    in_lane1_errors = sum(
        1 for was_lane1, is_error in zip(before, errors, strict=True) if was_lane1 and is_error
    )
    return JevLane1(config=config, without=Rate(in_lane1_errors, in_lane1), rows=tuple(rows))


@dataclass(frozen=True)
class JevTriageRow:
    """Qué descartaría Jev con P(sí) menor que `threshold`.

    En los cuatro `Rate`, `n` son los anuncios del grupo y `k` los que el
    operador (o el oro) conservó: los falsos descartes.
    """

    threshold: float
    #: Todo lo que Jev descartaría.
    discards: Rate
    #: El cruce con el carril 0 del motor: lo que descartaría solo el motor,
    #: solo Jev, y los dos.
    engine_only: Rate
    jev_only: Rate
    both: Rate

    def as_dict(self) -> dict[str, Any]:
        return {
            "threshold": self.threshold,
            "discards": self.discards.as_dict(),
            "engine_only": self.engine_only.as_dict(),
            "jev_only": self.jev_only.as_dict(),
            "both": self.both.as_dict(),
        }


@dataclass(frozen=True)
class JevTriage:
    #: Textos con señal y triage legible: los que entran a la tabla.
    n: int
    #: Textos con señal cuyo triage no se pudo leer (`invalid`): afuera.
    invalid: int
    rows: tuple[JevTriageRow, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"n": self.n, "invalid": self.invalid, "rows": [row.as_dict() for row in self.rows]}


def jev_triage(cases: Sequence[Case]) -> JevTriage:
    """Tabla 2: el triage de Jev al lado del carril 0 del motor.

    El carril 0 es el de `decide_lane_0`, el mismo predicado que usa el
    descarte automático: no mira ningún parámetro, así que la tabla es una
    sola por conjunto.
    """
    invalid = 0
    # (P(sí), lo conservó el operador, el motor lo manda al carril 0)
    valid: list[tuple[float, bool, bool]] = []
    for case, reading in _with_signal(cases):
        p_yes = reading.triage.p
        if reading.triage.invalid or p_yes is None:
            invalid += 1
            continue
        valid.append((p_yes, case.kept, decide_lane_0(case.ctx).lane == LANE_DISCARD))

    def kept_of(group: Sequence[tuple[float, bool, bool]]) -> Rate:
        return Rate(sum(1 for _, kept, _ in group if kept), len(group))

    rows = [
        JevTriageRow(
            threshold=threshold,
            discards=kept_of([ad for ad in valid if ad[0] < threshold]),
            engine_only=kept_of([ad for ad in valid if ad[2] and not ad[0] < threshold]),
            jev_only=kept_of([ad for ad in valid if ad[0] < threshold and not ad[2]]),
            both=kept_of([ad for ad in valid if ad[0] < threshold and ad[2]]),
        )
        for threshold in JEV_TRIAGE_THRESHOLDS
    ]
    return JevTriage(n=len(valid), invalid=invalid, rows=tuple(rows))


@dataclass(frozen=True)
class JevField:
    #: Aciertos (`k`) sobre las respuestas legibles (`n`).
    accuracy: Rate
    #: Respuestas que no se pudieron leer: fuera del porcentaje.
    invalid: int


@dataclass(frozen=True)
class JevFields:
    #: Conservados con señal y con filas curadas: sobre los que se mide.
    n: int
    fields: dict[str, JevField]

    def as_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            **{
                name: {**entry.accuracy.as_dict(), "invalid": entry.invalid}
                for name, entry in self.fields.items()
            },
        }


def jev_fields(cases: Sequence[Case]) -> JevFields:
    """Tabla 3: acierto de producto y de moneda contra las filas curadas.

    La definición es la de A6, que es la de `jev_signal.annotate`: Jev acierta
    si su elección es el valor de ALGUNA fila curada del anuncio; un producto
    que no es piloto vale `other` y una fila sin moneda es en pesos.
    """
    counts = {name: [0, 0, 0] for name in JEV_FIELDS}  # campo -> [aciertos, legibles, ilegibles]
    n = 0
    for case, reading in _with_signal(cases):
        if not case.kept or not case.curated_rows:
            continue
        n += 1
        # `agrees` es por anuncio: da lo mismo en todas las filas.
        block = jev_signal.annotate(reading, case.curated_rows)[0]
        for name, count in counts.items():
            if block[name]["status"] != "ok":
                count[2] += 1
                continue
            count[0] += bool(block[name]["agrees"])
            count[1] += 1
    return JevFields(
        n=n,
        fields={
            name: JevField(accuracy=Rate(hits, valid), invalid=unreadable)
            for name, (hits, valid, unreadable) in counts.items()
        },
    )


@dataclass(frozen=True)
class JevSummary:
    """El bloque «Jev» de un conjunto."""

    #: Textos del conjunto.
    texts: int
    #: De esos, los que tienen señal de la huella vigente.
    n: int
    #: Tabla 1: una por combinación de parámetros, en el orden de la corrida.
    lane1: tuple[JevLane1, ...]
    triage: JevTriage
    fields: JevFields

    def as_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "texts": self.texts,
            "lane1": [table.as_dict() for table in self.lane1],
            "triage": self.triage.as_dict(),
            "fields": self.fields.as_dict(),
        }


def summarize_jev(dataset: CaseSet, configs: Sequence[ValidationConfig]) -> JevSummary:
    cases = dataset.cases
    return JevSummary(
        texts=sum(1 for case in cases if case.content_type == ContentType.TEXT.value),
        n=len(_with_signal(cases)),
        lane1=tuple(jev_lane1(cases, config) for config in configs),
        triage=jev_triage(cases),
        fields=jev_fields(cases),
    )


def _decimal(value: float) -> str:
    """Un umbral con coma decimal: `0,5`."""
    return f"{value:.1f}".replace(".", ",")


def _jev_lane1_lines(tables: Sequence[JevLane1]) -> list[str]:
    lines = [
        "  1. Carril 1, con Jev y sin Jev.",
        "     «Saca»: estaban en el carril 1 y con la señal pasan al 2. De esos, «eran errores» "
        "si el operador",
        "     había cambiado algo y «estaban bien» si no (automatización perdida). «Entran» tiene que dar 0:",
        "     una señal nunca mete un anuncio en un carril. Umbral «cualquiera»: todo desacuerdo saca.",
    ]
    # Muchas combinaciones dan exactamente la misma tabla: se imprime una vez.
    groups: dict[tuple[Rate, tuple[JevLane1Row, ...]], list[ValidationConfig]] = {}
    for table in tables:
        groups.setdefault((table.without, table.rows), []).append(table.config)
    for (without, rows), configs in groups.items():
        if len(tables) == 1:
            lines.append(f"     Parámetros: {_params_line(configs[0])}")
        else:
            combos = ", ".join(
                f"{c.min_cell_n}/{c.channel_min_history}/{c.channel_max_edit_rate:.2f}" for c in configs
            )
            lines += [
                f"     --- {len(configs)} de {len(tables)} combinaciones dan esta tabla "
                "(min_cell_n/min_history/max_edit_rate):",
                f"         {combos}",
            ]
        lines += [
            f"     Sin Jev: {without.n} en el carril 1; error por anuncio: "
            + format_rate(without.k, without.n),
            "     probabilidad          | umbral     | C1 | saca | eran errores | estaban bien "
            "| entran | error del carril 1 por anuncio",
        ]
        for row in rows:
            threshold = "cualquiera" if row.threshold is None else _decimal(row.threshold)
            lines.append(
                f"     {_PROBABILITY_LABELS[row.probability]:<21} | {threshold:<10} | "
                f"{row.lane1.n:>2} | {row.removed:>4} | {row.removed_errors:>12} | "
                f"{row.removed_correct:>12} | {row.entered:>6} | " + format_rate(row.lane1.k, row.lane1.n)
            )
    return lines


def _jev_triage_lines(triage: JevTriage) -> list[str]:
    lines = [
        f"  2. Triage al lado del carril 0: n = {triage.n} textos con triage legible "
        f"({triage.invalid} ilegibles, afuera).",
        "     Jev descartaría los de P(sí) menor que el umbral. «Falsos descartes»: los que el "
        "operador conservó.",
        "     El cruce es con el carril 0 del motor, que no mira ningún parámetro.",
    ]
    for row in triage.rows:
        lines += [
            f"     P(sí) < {_decimal(row.threshold)}: descartaría {row.discards.n}; falsos descartes: "
            + format_rate(row.discards.k, row.discards.n),
            f"       solo el motor: {row.engine_only.n} ({row.engine_only.k} conservados) | "
            f"solo Jev: {row.jev_only.n} ({row.jev_only.k} conservados) | "
            f"los dos: {row.both.n} ({row.both.k} conservados)",
        ]
    return lines


def _jev_field_lines(fields: JevFields) -> list[str]:
    lines = [
        f"  3. Campos: acierto contra las filas curadas, n = {fields.n} conservados con señal "
        "y con filas en prices_history.",
        "     Definición de A6: acierta si su elección es el valor de alguna fila curada del anuncio.",
    ]
    for name, (label, reference) in JEV_FIELDS.items():
        entry = fields.fields[name]
        lines.append(
            f"     {label}: "
            + format_rate(entry.accuracy.k, entry.accuracy.n, lower=True)
            + f"; ilegibles: {entry.invalid}. Medido en A6, con otras preguntas: {reference}."
        )
    return lines


def render_jev(summary: JevSummary, configs: Sequence[ValidationConfig]) -> list[str]:
    """El bloque «Jev» de un conjunto. `configs` va en paralelo a `summary.lane1`."""
    if len(configs) != len(summary.lane1):
        raise ValueError("configs y summary.lane1 tienen que ir en paralelo")
    lines = [
        "",
        f"  Jev (señal en sombra): n = {summary.n} de {summary.texts} textos del conjunto "
        "tienen señal de la huella vigente.",
    ]
    if not summary.n:
        lines.append(
            "    Sin señales no hay nada que medir. El relleno es "
            "`python -m saimc.validation.jev_signal --decided`."
        )
        return lines
    lines += [
        "    Solo esos textos entran a las tres tablas. Las grillas son para mirar: el informe "
        "no elige ningún valor.",
        *_jev_lane1_lines(summary.lane1),
        *_jev_triage_lines(summary.triage),
        *_jev_field_lines(summary.fields),
    ]
    return lines


# --- reporte -----------------------------------------------------------------


def _short(ids: Sequence[str]) -> str:
    return ", ".join(i[:8] for i in ids) if ids else "ninguno"


def _types(counts: Mapping[str, int]) -> str:
    return ", ".join(
        f"{label} {counts.get(kind, 0)}" for kind, label in _TYPE_LABELS.items()
    )


def _params_line(config: ValidationConfig) -> str:
    signal = (
        "sin fijar" if config.signal_high_probability is None else config.signal_high_probability
    )
    return (
        f"min_cell_n={config.min_cell_n}  channel_min_history={config.channel_min_history}  "
        f"channel_max_edit_rate={config.channel_max_edit_rate}  signal_high_probability={signal}"
    )


def _head_lines(s: SetSummary) -> list[str]:
    return [
        f"  Anuncios: {s.total} ({_types(s.by_type)})",
        f"  Carril 0 (descarte automático):   {s.lanes[LANE_DISCARD]} ({_types(s.lane0_by_type)})",
        f"  Carril 1 (aceptación automática): {s.lanes[LANE_ACCEPT]}",
        f"  Carril 2 (revisión humana):       {s.lanes[LANE_REVIEW]}",
        "  Tasa de automatización (carriles 0 y 1 sobre el total): "
        + format_rate(s.automation.k, s.automation.n, lower=True),
    ]


def _lane0_lines(s: SetSummary) -> list[str]:
    return [
        "  Falsos descartes del carril 0 (el motor descarta y el operador lo conservó):",
        f"    texto:  {format_rate(s.false_discards_text.k, s.false_discards_text.n)}",
        f"    imagen: {format_rate(s.false_discards_image.k, s.false_discards_image.n)}",
        f"    anuncios: {_short(s.false_discard_ids)}",
    ]


def _lane1_lines(s: SetSummary) -> list[str]:
    lines = [
        "  Error del carril 1, por anuncio (editó o borró algo, o agregó una fila): "
        + format_rate(s.lane1_ad_errors.k, s.lane1_ad_errors.n),
        f"    anuncios en el carril 1: {_short(s.lane1_ids)}",
        f"    con error: {_short(s.lane1_error_ids)}",
        f"  Error del carril 1, por campo (sobre {s.lane1_deleted_rows.n} filas propuestas):",
    ]
    lines += [
        f"    {_FIELD_LABELS[name]}: {format_rate(rate.k, rate.n)}"
        for name, rate in s.lane1_field_errors.items()
    ]
    lines += [
        "    filas borradas o de más: "
        + format_rate(s.lane1_deleted_rows.k, s.lane1_deleted_rows.n),
        f"    filas agregadas o que faltan: {s.lane1_added_rows}",
    ]
    return lines


_REASONS_TITLE = (
    "Por qué los anuncios quedan en el carril 2 (anuncios por motivo; uno puede tener varios)"
)
_ASSUMED_LABEL = "con alguna fila con la moneda no escrita (`currency_explicit` falso)"
_ONLY_CURRENCY_LABEL = "fuera del carril 1 SOLO por la moneda no escrita"


def _reason_lines(s: SetSummary) -> list[str]:
    lines = [f"  {_REASONS_TITLE}: {s.lanes[LANE_REVIEW]} anuncios."]
    lines += [
        f"    {count:>4}  {reason}"
        for reason, count in sorted(s.reasons.items(), key=lambda item: (-item[1], item[0]))
    ] or ["    (ninguno)"]
    lines += [
        "  Moneda:",
        f"    {s.currency_assumed:>4}  anuncios {_ASSUMED_LABEL}",
        f"    {s.only_currency:>4}  anuncios {_ONLY_CURRENCY_LABEL} "
        f"(de {s.total}; con la moneda dada por escrita habrían entrado)",
    ]
    return lines


def render_summary(summary: SetSummary) -> list[str]:
    """El detalle de un conjunto con UNA combinación de parámetros."""
    return [
        *_head_lines(summary),
        *_lane0_lines(summary),
        *_lane1_lines(summary),
        *_reason_lines(summary),
    ]


def _signature(summary: SetSummary) -> str:
    """Dos combinaciones con la misma firma dieron exactamente lo mismo."""
    return json.dumps(
        {**summary.as_dict(), "ids": [summary.lane1_ids, summary.false_discard_ids]},
        sort_keys=True,
    )


def _render_grid(runs: Sequence[tuple[ValidationConfig, SetSummary]]) -> list[str]:
    """La grilla: una fila por combinación y el detalle una vez por resultado
    distinto (muchas combinaciones dan exactamente los mismos números)."""
    groups: dict[str, SetSummary] = {}
    for _, summary in runs:
        groups.setdefault(_signature(summary), summary)
    labels = {signature: f"R{i}" for i, signature in enumerate(groups, start=1)}
    distinct = list(groups.values())
    first = distinct[0]

    # El carril 0 no mira ningún parámetro: da lo mismo en toda la grilla.
    lane0 = _lane0_lines(first)
    lane0_is_constant = all(_lane0_lines(s) == lane0 for s in distinct)
    lines = [
        "",
        f"  Anuncios: {first.total} ({_types(first.by_type)})",
    ]
    if lane0_is_constant:
        lines += [
            f"  Carril 0 (descarte automático), igual en toda la grilla: "
            f"{first.lanes[LANE_DISCARD]} ({_types(first.lane0_by_type)})",
            *lane0,
        ]
    lines += [
        "",
        f"  Una fila por combinación ({len(runs)}). «Res.» agrupa las que dan exactamente "
        f"el mismo resultado ({len(distinct)} distintos). «Solo moneda»: anuncios fuera "
        "del carril 1 solo por la moneda no escrita.",
        "  min_cell_n | min_history | max_edit_rate | C0 | C1 | C2 | Res. | solo moneda "
        "| automatización | error del carril 1 por anuncio",
    ]
    for config, s in runs:
        lines.append(
            f"  {config.min_cell_n:>10} | {config.channel_min_history:>11} | "
            f"{config.channel_max_edit_rate:>13.2f} | "
            f"{s.lanes[LANE_DISCARD]:>2} | {s.lanes[LANE_ACCEPT]:>2} | "
            f"{s.lanes[LANE_REVIEW]:>2} | {labels[_signature(s)]:>4} | {s.only_currency:>11} | "
            f"{format_rate(s.automation.k, s.automation.n, lower=True)} | "
            f"{format_rate(s.lane1_ad_errors.k, s.lane1_ad_errors.n)}"
        )

    with_lane1 = [(labels[sig], s) for sig, s in groups.items() if s.lanes[LANE_ACCEPT]]
    lines += ["", "  Detalle del carril 1, por resultado (solo los que tienen anuncios en él):"]
    if not with_lane1:
        lines.append("    Ninguna combinación deja anuncios en el carril 1: n = 0, no hay error que medir.")
    # Resultados que difieren solo en los motivos comparten el mismo carril 1.
    lane1_blocks: dict[tuple[str, ...], list[str]] = {}
    for label, s in with_lane1:
        lane1_blocks.setdefault(tuple(_lane1_lines(s)), []).append(label)
    for block, block_labels in lane1_blocks.items():
        lines += [f"  --- {', '.join(block_labels)}", *block]
    if not lane0_is_constant:
        for sig, s in groups.items():
            lines += [f"  --- {labels[sig]}", *_lane0_lines(s)]

    # Motivos: una fila por motivo, una columna por resultado distinto.
    reasons = sorted(
        {reason for s in distinct for reason in s.reasons},
        key=lambda reason: (-max(s.reasons[reason] for s in distinct), reason),
    )
    header = " ".join(f"{label:>4}" for label in labels.values())
    lines += ["", f"  {_REASONS_TITLE}:", f"    {header}  motivo"]
    lines.append(
        "    " + " ".join(f"{s.lanes[LANE_REVIEW]:>4}" for s in distinct)
        + "  (anuncios en el carril 2)"
    )
    for reason in reasons:
        lines.append(
            "    " + " ".join(f"{s.reasons[reason]:>4}" for s in distinct) + f"  {reason}"
        )
    lines += [
        "  Moneda:",
        "    " + " ".join(f"{s.currency_assumed:>4}" for s in distinct)
        + f"  anuncios {_ASSUMED_LABEL}",
        "    " + " ".join(f"{s.only_currency:>4}" for s in distinct)
        + f"  anuncios {_ONLY_CURRENCY_LABEL}",
    ]
    return lines


def render_set(
    dataset: CaseSet,
    runs: Sequence[tuple[ValidationConfig, SetSummary]],
    jev: JevSummary | None = None,
) -> list[str]:
    """Un conjunto entero. Con `jev`, cierra con su bloque «Jev» (A6b-2)."""
    lines = ["", "=" * 78, dataset.title, "=" * 78]
    lines += [f"  {key}: {value}" for key, value in dataset.notes.items()]
    lines.append(f"  anuncios que entran al informe: n = {len(dataset.cases)}")
    lines += [f"  Nota: {caveat}" for caveat in dataset.caveats]
    if not dataset.cases:
        lines.append("  CONJUNTO VACÍO (n = 0): no hay nada que medir.")
        return lines
    if len(runs) == 1:
        lines += ["", f"  Parámetros: {_params_line(runs[0][0])}", *render_summary(runs[0][1])]
    else:
        lines += _render_grid(runs)
    if jev is not None:
        lines += render_jev(jev, [config for config, _ in runs])
    return lines


# --- registro de corridas ----------------------------------------------------


def _params_dict(config: ValidationConfig) -> dict[str, Any]:
    return dataclasses.asdict(config)


def run_record(
    datasets: Sequence[CaseSet],
    results: Sequence[tuple[ValidationConfig, dict[str, SetSummary]]],
    *,
    now: datetime,
    gold: Path,
    grid: bool,
    commit: str,
    dirty: bool,
    branch: str,
    jev: Mapping[str, JevSummary] | None = None,
) -> dict[str, Any]:
    """La corrida entera, serializable a UNA línea de `shadow-runs.jsonl`.

    Con `jev` (el bloque de cada conjunto, por nombre) suma la clave `jev`.
    """
    record: dict[str, Any] = {
        "timestamp": now.astimezone(UTC).isoformat(),
        "git_commit": commit,
        "git_dirty": dirty,
        "git_branch": branch,
        "gold": gold.name,
        "grid": grid,
        "confidence": CONFIDENCE,
        "sets": {
            dataset.name: {"n": len(dataset.cases), "notes": dataset.notes}
            for dataset in datasets
        },
        "runs": [
            {
                "params": _params_dict(config),
                "sets": {name: summary.as_dict() for name, summary in summaries.items()},
            }
            for config, summaries in results
        ],
    }
    if jev is not None:
        record["jev"] = {name: summary.as_dict() for name, summary in jev.items()}
    return record


# --- CLI ---------------------------------------------------------------------


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m saimc.validation.shadow",
        description=(
            "Informe del modo sombra: corre el motor de validación sobre el oro del "
            "backtest y sobre las revisiones individuales. Por defecto en seco; "
            "--execute registra la corrida. No gasta API ni escribe en la base. "
            "Incluye el bloque «Jev» (con y sin la señal) si hay señales guardadas."
        ),
    )
    parser.add_argument(
        "--gold",
        type=Path,
        required=True,
        help="Resultados del backtest que hacen de oro (p. ej. data/llm-backtest-results-v6.jsonl).",
    )
    # Sin `default`: los parámetros se miden, no se inventan (§12.6).
    parser.add_argument("--min-cell-n", type=int, help="Referencias mínimas por celda.")
    parser.add_argument(
        "--channel-min-history", type=int, help="Decisiones individuales mínimas del canal."
    )
    parser.add_argument(
        "--channel-max-edit-rate",
        type=float,
        help="Tope de filas editadas o borradas para «canal limpio» (0..1).",
    )
    parser.add_argument(
        "--signal-high-probability",
        type=float,
        default=None,
        help=(
            "Probabilidad «alta» de una señal externa. Opcional y sin efecto en los "
            "números generales (corren sin señales); el bloque «Jev» recorre su propia grilla."
        ),
    )
    parser.add_argument(
        "--grid",
        action="store_true",
        help="Corre la grilla declarada en el módulo (GRID_*) en vez de una combinación.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Agrega la corrida a docs/shadow-runs.jsonl. Sin esto es en seco.",
    )
    parser.add_argument("--runs-file", type=Path, default=DEFAULT_RUNS_FILE)
    args = parser.parse_args(argv)

    given = [args.min_cell_n, args.channel_min_history, args.channel_max_edit_rate]
    if args.grid and any(value is not None for value in given):
        parser.error("--grid usa su propia grilla: no se combina con los tres parámetros.")
    if not args.grid and any(value is None for value in given):
        parser.error(
            "No hay valores por defecto: pasá --min-cell-n, --channel-min-history y "
            "--channel-max-edit-rate, o pedí --grid."
        )
    return args


def configs_from_args(args: argparse.Namespace) -> list[ValidationConfig]:
    if not args.grid:
        return [
            ValidationConfig(
                min_cell_n=args.min_cell_n,
                channel_min_history=args.channel_min_history,
                channel_max_edit_rate=args.channel_max_edit_rate,
                signal_high_probability=args.signal_high_probability,
            )
        ]
    return [
        ValidationConfig(
            min_cell_n=cell_n,
            channel_min_history=history,
            channel_max_edit_rate=edit_rate,
            signal_high_probability=args.signal_high_probability,
        )
        # `product` = los tres bucles anidados, en una línea.
        for cell_n, history, edit_rate in itertools.product(
            GRID_MIN_CELL_N, GRID_CHANNEL_MIN_HISTORY, GRID_CHANNEL_MAX_EDIT_RATE
        )
    ]


def run(
    args: argparse.Namespace,
    session: Session,
    *,
    out: Callable[[str], None] = print,
    raw_store_dir: Path | None = None,
    transcribe: Transcriber | None = None,
) -> int:
    """Corre el informe. Devuelve el código de salida."""
    if not args.gold.is_file():
        out(f"No existe el oro: {args.gold}")
        return 2
    now = datetime.now(UTC)
    catalog = context.load_catalog(session)
    datasets = [
        load_calibration(
            session, catalog, args.gold, raw_store_dir=raw_store_dir, transcribe=transcribe
        ),
        load_evaluation(session, catalog, raw_store_dir=raw_store_dir, transcribe=transcribe),
    ]
    configs = configs_from_args(args)
    # El bloque «Jev»: lee `jev_signals`, no llama a nadie y no escribe.
    attach_jev(session, datasets)
    jev = {dataset.name: summarize_jev(dataset, configs) for dataset in datasets}
    results = [
        (config, {dataset.name: run_set(dataset, config) for dataset in datasets})
        for config in configs
    ]

    out(f"INFORME DEL MODO SOMBRA — {now.strftime('%Y-%m-%d %H:%M UTC')}")
    out(
        "EN SECO: no se escribe nada (con --execute se registra la corrida)."
        if not args.execute
        else f"Se registra la corrida en {args.runs_file}."
    )
    out(
        "Motor: saimc.validation (A5b), sin señales externas salvo en el bloque «Jev», que "
        "lo corre con y sin ellas. No fija metas ni decide: eso es de G1a y G1b."
    )
    out(
        "Los dos conjuntos no se mezclan. Las cotas son de Clopper-Pearson, unilaterales "
        "al 95 %."
    )
    if args.grid:
        out(
            f"Grilla ({len(configs)} combinaciones): min_cell_n ∈ {list(GRID_MIN_CELL_N)}, "
            f"channel_min_history ∈ {list(GRID_CHANNEL_MIN_HISTORY)}, "
            f"channel_max_edit_rate ∈ {list(GRID_CHANNEL_MAX_EDIT_RATE)}."
        )
    for dataset in datasets:
        runs = [(config, summaries[dataset.name]) for config, summaries in results]
        for line in render_set(dataset, runs, jev[dataset.name]):
            out(line)

    if args.execute:
        commit, dirty, branch = measure._git_revision()
        record = run_record(
            datasets,
            results,
            now=now,
            gold=args.gold,
            grid=args.grid,
            commit=commit,
            dirty=dirty,
            branch=branch,
            jev=jev,
        )
        measure.append_h0_run(record, args.runs_file)  # un append de una línea JSON
        out("")
        out(f"Corrida registrada en {args.runs_file} (commit {commit}).")
    return 0


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    # Sin `session_scope`: ese hace commit al salir. Acá nunca se escribe, y un
    # rollback explícito lo deja a la vista.
    session = get_session_factory()()
    try:
        code = run(args, session)
    finally:
        session.rollback()
        session.close()
    sys.exit(code)


if __name__ == "__main__":
    main()
