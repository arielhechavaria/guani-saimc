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
    LlmProposal,
    LlmReview,
    Product,
    Province,
    RawIngestion,
    ReviewOutcome,
)
from saimc.units import canonical_unit
from saimc.validation import context
from saimc.validation.context import Catalog, ProposalDiff, Transcriber
from saimc.validation.engine import validate_ad

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
            # También es un error si el operador descartó el anuncio entero.
            if not result.kept or diff.has_error:
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
    dataset: CaseSet, runs: Sequence[tuple[ValidationConfig, SetSummary]]
) -> list[str]:
    lines = ["", "=" * 78, dataset.title, "=" * 78]
    lines += [f"  {key}: {value}" for key, value in dataset.notes.items()]
    lines.append(f"  anuncios que entran al informe: n = {len(dataset.cases)}")
    lines += [f"  Nota: {caveat}" for caveat in dataset.caveats]
    if not dataset.cases:
        lines.append("  CONJUNTO VACÍO (n = 0): no hay nada que medir.")
        return lines
    if len(runs) == 1:
        lines += ["", f"  Parámetros: {_params_line(runs[0][0])}", *render_summary(runs[0][1])]
        return lines
    return lines + _render_grid(runs)


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
) -> dict[str, Any]:
    """La corrida entera, serializable a UNA línea de `shadow-runs.jsonl`."""
    return {
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


# --- CLI ---------------------------------------------------------------------


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m saimc.validation.shadow",
        description=(
            "Informe del modo sombra: corre el motor de validación sobre el oro del "
            "backtest y sobre las revisiones individuales. Por defecto en seco; "
            "--execute registra la corrida. No gasta API ni escribe en la base."
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
        help="Probabilidad «alta» de una señal externa. Opcional: hoy no hay señales.",
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
        "Motor: saimc.validation (A5b), sin señales externas. No fija metas ni decide: "
        "eso es de G1a y G1b."
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
        for line in render_set(dataset, runs):
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
