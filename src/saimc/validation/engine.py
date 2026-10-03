"""Motor de validación §6.2 y carriles. Código puro (slice A5b).

`validate_ad` recibe un anuncio con todo lo que hace falta ya leído
(`AdContext`) y los parámetros (`ValidationConfig`), y devuelve por fila el
resultado de las seis reglas, `validation_status` y `confidence_score`, y por
anuncio el carril con sus motivos. No consulta la base, no llama a ningún
proveedor y no escribe nada: eso es de quien lo llama (A5c, B1, B2).

Contrato: `docs/spec-motor-validacion.md`. Las precisiones que obligó la
implementación están en `docs/decisions.md` (2026-10-02, «A5b»).

Dos cosas que conviene tener presentes al leerlo:

- **El carril se decide por predicados, no por puntaje.** `confidence_score`
  es descriptivo y nada lo compara contra un umbral.
- **Todo lo dudoso va al carril 2.** `unknown` nunca aprueba, sin configuración
  no se automatiza, y una señal externa solo puede sacar del carril 1.
"""

from __future__ import annotations

import math
import uuid

from saimc.extraction.engine import ExtractionProposal, mentions_any_product
from saimc.extraction.llm_checks import COINCIDE, RowAgreement, RowMeta, compare_with_rules
from saimc.extraction.llm_propose_ad import AdProposal
from saimc.models import ContentType, DiscardReason
from saimc.validation import rules
from saimc.validation.types import (
    LANE_ACCEPT,
    LANE_DISCARD,
    LANE_REVIEW,
    SIGNAL_FIELDS,
    AdContext,
    AdValidation,
    ExternalSignal,
    Outcome,
    RowContext,
    RowValidation,
    ValidationConfig,
    ValidationStatus,
)

#: Los únicos motivos de descarte del modelo que habilitan el carril 0.
_LANE_0_REASONS = frozenset({DiscardReason.OFF_FOCUS.value, DiscardReason.NO_DATA.value})


def _status(outcomes: dict[str, Outcome], *, verified: bool) -> ValidationStatus:
    """El estado de la fila. Si falla más de una regla gana la primera en el
    orden de la tabla de la especificación.

    `approved` exige, además de las seis reglas en `pass`, que la extracción
    esté verificada (cita, acuerdo con las reglas, sin abstenciones): sin eso la
    fila es `unverified`, como piden los casos 6, 8 y 22.
    """
    if outcomes["structure"] is Outcome.FAIL:
        return ValidationStatus.INCOMPLETE
    if outcomes["price"] is Outcome.FAIL:
        return ValidationStatus.ANOMALY
    if outcomes["quantity_price"] is Outcome.FAIL:
        return ValidationStatus.INCONSISTENT
    if outcomes["duplicate"] is Outcome.FAIL:
        return ValidationStatus.POSSIBLE_DUPLICATE
    if outcomes["location"] is Outcome.FAIL or outcomes["trust"] is Outcome.FAIL:
        return ValidationStatus.UNTRUSTED_SOURCE
    if verified and all(o is Outcome.PASS for o in outcomes.values()):
        return ValidationStatus.APPROVED
    return ValidationStatus.UNVERIFIED


#: Cómo se nombra cada regla en un motivo, según cómo salió.
_RULE_REASONS: dict[str, dict[Outcome, str]] = {
    "structure": {
        Outcome.FAIL: "falta producto piloto, precio, moneda o provincia",
    },
    "price": {
        Outcome.FAIL: "el precio cae fuera de la banda de su celda",
        Outcome.UNKNOWN: "no se pudo comparar el precio con su celda",
    },
    "location": {
        Outcome.FAIL: "la provincia no es del catálogo",
        Outcome.UNKNOWN: "la provincia no se pudo contrastar con la del canal",
    },
    "trust": {
        Outcome.FAIL: "el canal tiene un historial sucio",
        Outcome.UNKNOWN: "el canal no tiene historial suficiente",
    },
    "duplicate": {
        Outcome.FAIL: "ya hay una fila igual del mismo canal en las 24 h previas",
    },
    "quantity_price": {
        Outcome.FAIL: "el precio leído con la otra base queda más cerca de su celda",
        Outcome.UNKNOWN: "no se pudo contrastar la base del precio con su celda",
    },
}


def _verification_reasons(
    row: ExtractionProposal, meta: RowMeta, agreement: RowAgreement
) -> list[str]:
    """Condiciones 1 a 3 del carril 1 para una fila. Vacío si las cumple."""
    reasons: list[str] = []
    # `is not True` y no `not ...`: `price_in_evidence` puede ser `None`.
    if not meta.evidence_found or meta.price_in_evidence is not True:
        reasons.append("la cita del modelo no está verificada en el texto")
    differing = [
        label
        for label, state in (
            ("producto", agreement.product),
            ("precio", agreement.price),
            ("moneda", agreement.currency),
            ("provincia", agreement.province),
        )
        if state != COINCIDE
    ]
    # Las reglas siempre proponen CUP, así que una moneda asumida por los dos
    # «coincide» sin que nadie la haya leído. Solo cuenta como acuerdo si el
    # texto la dice (`currency_explicit`): es el caso 6 de la especificación.
    if "moneda" not in differing and not meta.currency_explicit:
        differing.append("moneda")
    if differing:
        reasons.append("reglas y modelo no coinciden en " + ", ".join(differing))
    abstained = [
        label
        for label, value in (
            ("cantidad", row.quantity),
            ("unidad", row.unit),
            ("base del precio", row.price_basis),
        )
        if value is None
    ]
    if abstained:
        reasons.append("el modelo se abstuvo en " + ", ".join(abstained))
    return reasons


def _validate_row(
    row: ExtractionProposal,
    meta: RowMeta,
    agreement: RowAgreement,
    row_ctx: RowContext,
    ctx: AdContext,
    config: ValidationConfig | None,
    pilot_ids: frozenset[uuid.UUID],
) -> RowValidation:
    min_cell_n = None if config is None else config.min_cell_n
    quantity_price = rules.check_quantity_price(
        row, price_cup=row_ctx.price_cup, cell=row_ctx.cell, min_cell_n=min_cell_n
    )
    # Un dict conserva el orden de inserción: es el orden de las claves del JSON.
    outcomes: dict[str, Outcome] = {
        "structure": rules.check_structure(row, pilot_ids=pilot_ids),
        "price": rules.check_price(
            row,
            price_cup=row_ctx.price_cup,
            cell=row_ctx.cell,
            min_cell_n=min_cell_n,
            quantity_price=quantity_price,
        ),
        "location": rules.check_location(
            row, province_ids=ctx.province_ids, channel=ctx.channel
        ),
        "trust": rules.check_trust(
            ctx.channel,
            min_history=None if config is None else config.channel_min_history,
            max_edit_rate=None if config is None else config.channel_max_edit_rate,
        ),
        "duplicate": rules.check_duplicate(
            row, published_at=ctx.published_at, recent_rows=ctx.recent_channel_rows
        ),
        "quantity_price": quantity_price,
    }
    reasons = _verification_reasons(row, meta, agreement)
    verified = not reasons
    reasons += [
        _RULE_REASONS[name][outcome]
        for name, outcome in outcomes.items()
        if outcome is not Outcome.PASS
    ]
    passed = sum(1 for o in outcomes.values() if o is Outcome.PASS)
    return RowValidation(
        **outcomes,
        status=_status(outcomes, verified=verified),
        confidence_score=round(100 * passed / len(outcomes)),
        reasons=tuple(reasons),
    )


def _extracted_value(row: ExtractionProposal, field: str) -> object:
    """El valor de la fila contra el que se compara una señal externa."""
    if field == "product":
        return None if row.product is None else row.product.id
    if field == "province":
        return None if row.province is None else row.province.id
    if field == "currency":
        return row.currency
    return row.sale_format


def _signal_reasons(ctx: AdContext, config: ValidationConfig) -> list[str]:
    """Señales externas que sacan el anuncio del carril 1.

    Solo cuenta un desacuerdo con probabilidad alta. Un acuerdo no suma nada:
    una señal nunca mete un anuncio en un carril automático. Lo mal formado
    (pregunta fuera del contrato, probabilidad que no es un número entre 0 y 1,
    respuesta ausente, fila inexistente) equivale a «sin señal».
    """
    rows = [] if ctx.model is None else ctx.model.rows
    reasons: list[str] = []
    for signal in ctx.signals:
        if not _well_formed(signal, len(rows)):
            continue
        if signal.value == _extracted_value(rows[signal.row_index], signal.field):
            continue
        high = config.signal_high_probability
        if high is None or signal.probability >= high:
            reasons.append(
                f"fila {signal.row_index + 1}: una señal externa no coincide en "
                f"{signal.field} con probabilidad {signal.probability:.2f}"
            )
    return reasons


def _well_formed(signal: ExternalSignal, n_rows: int) -> bool:
    probability = signal.probability
    return (
        signal.field in SIGNAL_FIELDS
        and signal.value is not None
        # `bool` es subclase de `int` en Python: se excluye a mano.
        and isinstance(signal.row_index, int)
        and not isinstance(signal.row_index, bool)
        and 0 <= signal.row_index < n_rows
        and isinstance(probability, int | float)
        and not isinstance(probability, bool)
        and math.isfinite(probability)
        and 0 <= probability <= 1
    )


def validate_ad(ctx: AdContext, config: ValidationConfig | None) -> AdValidation:
    """Valida un anuncio y decide su carril. Es el único lugar que lo decide.

    `config` es obligatorio y no tiene valor por defecto: pasar `None` es decir
    «no hay configuración», y entonces todo anuncio va al carril 2 (las filas
    igual se evalúan, con las reglas que dependen de un parámetro en `unknown`).
    """
    model = ctx.model
    if model is None:
        return AdValidation(LANE_REVIEW, ("el anuncio no tiene propuesta del modelo",))
    if model.degraded or model.failure:
        return AdValidation(LANE_REVIEW, ("la propuesta del modelo está degradada",))
    if model.discard is None:
        return AdValidation(LANE_REVIEW, ("el modelo no dio veredicto de descarte",))

    if model.discard:
        return _discard_lane(ctx, model, config)

    if len(ctx.row_contexts) != len(model.rows) or len(model.row_meta) != len(model.rows):
        raise ValueError(
            "row_contexts y row_meta tienen que ir en paralelo a las filas de la propuesta"
        )

    pilot_ids = frozenset(p.id for p in ctx.pilot_products)
    agreements = compare_with_rules(model.rows, ctx.rule_rows)
    rows = tuple(
        _validate_row(row, meta, agreement, row_ctx, ctx, config, pilot_ids)
        for row, meta, agreement, row_ctx in zip(
            model.rows, model.row_meta, agreements, ctx.row_contexts, strict=True
        )
    )

    reasons: list[str] = []
    if config is None:
        reasons.append("no hay configuración de parámetros: no se automatiza")
    if ctx.content_type != ContentType.TEXT.value:
        reasons.append("el anuncio es una imagen: las imágenes no entran al carril 1")
    if model.dropped:
        reasons.append("la propuesta del modelo tiene filas o campos caídos")
    if not rows:
        reasons.append("la propuesta del modelo no trae ninguna fila")
    for index, row in enumerate(rows, start=1):
        reasons += [f"fila {index}: {reason}" for reason in row.reasons]
    if config is not None:
        reasons += _signal_reasons(ctx, config)

    if reasons:
        return AdValidation(LANE_REVIEW, tuple(reasons), rows)
    return AdValidation(
        LANE_ACCEPT,
        ("anuncio de texto con todas sus filas verificadas y las seis reglas en pass",),
        rows,
    )


def decide_lane_0(ctx: AdContext) -> AdValidation:
    """Decide SOLO el carril 0, sin configuración de parámetros (slice B2a).

    El carril 0 no usa ningún parámetro de `ValidationConfig`, pero
    `validate_ad` lo niega sin configuración. Esta función aplica los mismos
    predicados que `validate_ad` (propuesta presente, sin degradar, el modelo
    descarta por `off_focus` o `no_data`, las reglas no ven ningún piloto) y
    nada más: devuelve carril 0 o carril 2, nunca el 1. Quien la llama hace de
    configuración con su interruptor por tipo (`SAIMC_LANE0_*_ENABLED`).
    """
    model = ctx.model
    if model is None:
        return AdValidation(LANE_REVIEW, ("el anuncio no tiene propuesta del modelo",))
    if model.degraded or model.failure:
        return AdValidation(LANE_REVIEW, ("la propuesta del modelo está degradada",))
    if model.discard is not True:
        return AdValidation(LANE_REVIEW, ("el modelo no descarta el anuncio",))
    return _lane_0_verdict(ctx, model, _lane_0_reasons(ctx, model))


def _lane_0_reasons(ctx: AdContext, model: AdProposal) -> list[str]:
    """Por qué un descarte del modelo NO va al carril 0. Vacío si va."""
    reasons: list[str] = []
    if model.discard_reason not in _LANE_0_REASONS:
        reasons.append("el modelo descarta, pero no por fuera de foco ni por falta de datos")
    if mentions_any_product(ctx.text, ctx.pilot_products):
        reasons.append("el modelo descarta, pero las reglas ven un producto piloto en el texto")
    return reasons


def _lane_0_verdict(ctx: AdContext, model: AdProposal, reasons: list[str]) -> AdValidation:
    if reasons:
        return AdValidation(LANE_REVIEW, tuple(reasons))
    return AdValidation(
        LANE_DISCARD,
        (
            f"el modelo descarta ({model.discard_reason}) y las reglas no ven "
            "ningún producto piloto",
        ),
    )


def _discard_lane(
    ctx: AdContext, model: AdProposal, config: ValidationConfig | None
) -> AdValidation:
    """Carril 0 si el modelo descarta por `off_focus` o `no_data` Y las reglas
    no encuentran ningún producto piloto en el texto. No distingue texto de
    imagen."""
    reasons: list[str] = []
    if config is None:
        reasons.append("no hay configuración de parámetros: no se automatiza")
    reasons += _lane_0_reasons(ctx, model)
    return _lane_0_verdict(ctx, model, reasons)
