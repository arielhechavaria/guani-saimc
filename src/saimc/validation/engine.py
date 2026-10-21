"""Motor de validación §6.2 y carriles. Código puro (slice A5b).

`validate_ad` recibe un anuncio con todo lo que hace falta ya leído
(`AdContext`) y los parámetros (`ValidationConfig`), y devuelve por fila el
resultado de las seis reglas, `validation_status` y `confidence_score`, y por
anuncio el carril con sus motivos. No consulta la base, no llama a ningún
proveedor y no escribe nada: eso es de quien lo llama (A5c, B1, B2).

`validate_stored_row` (B1) aplica las mismas seis reglas a UNA fila ya guardada
en `prices_history`, sin propuesta del modelo y sin decidir carril.

Contrato: `docs/spec-motor-validacion.md`. Las precisiones que obligó la
implementación están en `docs/decisions.md` (2026-10-02, «A5b»), y el filtro del
carril 1 (tope de filas, moneda no escrita, posible duplicado) en la entrada del
2026-10-05, «G1b». El canal sucio que deja pasar cuando reglas y modelo
coinciden: `docs/spec-slice-canal-sucio.md` (2026-10-17).

Dos cosas que conviene tener presentes al leerlo:

- **El carril se decide por predicados, no por puntaje.** `confidence_score`
  es descriptivo y nada lo compara contra un umbral.
- **Todo lo dudoso va al carril 2.** `unknown` nunca aprueba, sin configuración
  no se automatiza, y una señal externa solo puede sacar del carril 1.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Sequence
from datetime import datetime

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
    ChannelContext,
    ExternalSignal,
    Outcome,
    RecentRow,
    RowContext,
    RowValidation,
    ValidationConfig,
    ValidationStatus,
)

#: Versión del motor. Se guarda en cada fila validada
#: (`prices_history.validation_detail`) para saber con qué reglas se la miró.
#: Se sube A MANO cuando cambian las reglas, el orden de los estados o el cerco
#: (`fence.py`); después se corre `python -m saimc.validation.revalidate`.
ENGINE_VERSION = "2026-10-03.1"

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

#: Un repost no es un error de extracción. La fila igual queda en
#: `possible_duplicate`.
_DUPLICATE_REASON = _RULE_REASONS["duplicate"][Outcome.FAIL]

#: Los motivos de una fila que no sacan al anuncio del carril 1. Además del
#: duplicado (G1b), los tres «todavía no sé» que no hablan del anuncio (B2b,
#: `docs/spec-slice-B2b.md`): un canal que nadie curó y una celda sin
#: referencias con que comparar. La fila conserva el motivo y su estado
#: (`unverified`); lo que cambia es solo el carril. Un canal SUCIO y un precio
#: FUERA de su celda siguen frenando: esos sí son evidencia. El canal sucio
#: tiene una salida propia, `_dirty_channel_yields` (2026-10-17).
_NON_BLOCKING_REASONS = frozenset(
    {
        _DUPLICATE_REASON,
        _RULE_REASONS["trust"][Outcome.UNKNOWN],
        _RULE_REASONS["price"][Outcome.UNKNOWN],
        _RULE_REASONS["quantity_price"][Outcome.UNKNOWN],
    }
)


#: Prefijo del motivo de desacuerdo entre reglas y modelo; el resto del texto
#: lista los campos y varía, por eso no puede ir en `_NON_BLOCKING_REASONS`.
_DISAGREEMENT_PREFIX = "reglas y modelo no coinciden en "


#: El canal sucio: frena, salvo que sea lo único y reglas y modelo coincidan
#: (`_dirty_channel_yields`).
_DIRTY_CHANNEL_REASON = _RULE_REASONS["trust"][Outcome.FAIL]


def _blocks_lane1(reason: str, config: ValidationConfig | None) -> bool:
    """¿Este motivo de fila saca al anuncio del carril 1?"""
    if reason in _NON_BLOCKING_REASONS:
        return False
    if reason.startswith(_DISAGREEMENT_PREFIX):
        return config is None or not config.lane1_model_over_rules
    return True


def _dirty_channel_yields(
    other_reasons: Sequence[str],
    row_blocks: Sequence[tuple[int, str]],
    rows: Sequence[RowValidation],
    config: ValidationConfig | None,
) -> bool:
    """¿El canal sucio deja pasar al anuncio? Solo si es su ÚNICO freno y
    reglas y modelo coinciden en todas las filas (2026-10-17,
    `docs/spec-slice-canal-sucio.md`). Las dos lecturas que dicen lo mismo
    hacen de verificación; el canal sucio solo dice que ahí el modelo suele
    equivocarse."""
    if config is None or not config.lane1_dirty_channel_if_agree:
        return False
    if other_reasons or not row_blocks:
        return False
    if any(reason != _DIRTY_CHANNEL_REASON for _, reason in row_blocks):
        return False
    # El desacuerdo está en los motivos de la fila aunque no frene
    # (`lane1_model_over_rules`): por eso se mira acá y no en `row_blocks`.
    return not any(
        reason.startswith(_DISAGREEMENT_PREFIX) for row in rows for reason in row.reasons
    )


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
    # La moneda coincide si reglas y modelo dan la misma, la diga el texto o
    # no: `meta.currency_explicit` no se mira (G1b, caso 24). Sin moneda de las
    # reglas (`sin_regla`) no hay acuerdo: es el caso 6.
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


def _rule_outcomes(
    row: ExtractionProposal,
    row_ctx: RowContext,
    *,
    published_at: datetime,
    channel: ChannelContext | None,
    pilot_ids: frozenset[uuid.UUID],
    province_ids: frozenset[uuid.UUID],
    recent_channel_rows: Sequence[RecentRow],
    min_cell_n: int | None,
    channel_min_history: int | None,
    channel_max_edit_rate: float | None,
) -> dict[str, Outcome]:
    """Las seis reglas sobre una fila. Lo comparten la fila de una propuesta
    (`_validate_row`) y la fila ya guardada (`validate_stored_row`)."""
    quantity_price = rules.check_quantity_price(
        row, price_cup=row_ctx.price_cup, cell=row_ctx.cell, min_cell_n=min_cell_n
    )
    # Un dict conserva el orden de inserción: es el orden de las claves del JSON.
    return {
        "structure": rules.check_structure(row, pilot_ids=pilot_ids),
        "price": rules.check_price(
            row,
            price_cup=row_ctx.price_cup,
            cell=row_ctx.cell,
            min_cell_n=min_cell_n,
            quantity_price=quantity_price,
        ),
        "location": rules.check_location(row, province_ids=province_ids, channel=channel),
        "trust": rules.check_trust(
            channel, min_history=channel_min_history, max_edit_rate=channel_max_edit_rate
        ),
        "duplicate": rules.check_duplicate(
            row, published_at=published_at, recent_rows=recent_channel_rows
        ),
        "quantity_price": quantity_price,
    }


def _row_validation(
    outcomes: dict[str, Outcome], verification_reasons: list[str]
) -> RowValidation:
    """Arma el resultado de una fila: estado, puntaje y motivos. La fila está
    verificada si no trae ningún motivo de verificación."""
    reasons = verification_reasons + [
        _RULE_REASONS[name][outcome]
        for name, outcome in outcomes.items()
        if outcome is not Outcome.PASS
    ]
    passed = sum(1 for o in outcomes.values() if o is Outcome.PASS)
    return RowValidation(
        **outcomes,
        status=_status(outcomes, verified=not verification_reasons),
        confidence_score=round(100 * passed / len(outcomes)),
        reasons=tuple(reasons),
    )


def _validate_row(
    row: ExtractionProposal,
    meta: RowMeta,
    agreement: RowAgreement,
    row_ctx: RowContext,
    ctx: AdContext,
    config: ValidationConfig | None,
    pilot_ids: frozenset[uuid.UUID],
) -> RowValidation:
    outcomes = _rule_outcomes(
        row,
        row_ctx,
        published_at=ctx.published_at,
        channel=ctx.channel,
        pilot_ids=pilot_ids,
        province_ids=ctx.province_ids,
        recent_channel_rows=ctx.recent_channel_rows,
        min_cell_n=None if config is None else config.min_cell_n,
        channel_min_history=None if config is None else config.channel_min_history,
        channel_max_edit_rate=None if config is None else config.channel_max_edit_rate,
    )
    return _row_validation(outcomes, _verification_reasons(row, meta, agreement))


def validate_stored_row(
    row: ExtractionProposal,
    row_ctx: RowContext,
    *,
    published_at: datetime,
    channel: ChannelContext | None,
    pilot_ids: frozenset[uuid.UUID],
    province_ids: frozenset[uuid.UUID],
    recent_channel_rows: Sequence[RecentRow] = (),
    verified: bool,
    min_cell_n: int,
    channel_min_history: int | None = None,
    channel_max_edit_rate: float | None = None,
) -> RowValidation:
    """Valida UNA fila ya guardada en `prices_history` (slice B1).

    `validate_ad` valida la propuesta del modelo; esto valida lo que quedó
    después de que el operador corrigió, agregó o borró: no necesita propuesta.
    Aplica las mismas seis reglas y el mismo orden de estados, y **no decide
    carril**: el carril es del anuncio y de su propuesta.

    - `row`: la fila guardada, con la forma que leen las reglas (quien llama la
      arma desde `prices_history`; ver `validation.persist`).
    - `row_ctx`: su celda y su `price_cup` (el que quedó guardado en la fila).
    - `verified`: si alguien miró la fila. Una fila con `provenance = human`
      cuenta como verificada; sin eso nunca sale `approved`.
    - `min_cell_n`: gobierna cuándo opinan las reglas 2 y 6.
    - `channel_min_history` y `channel_max_edit_rate`: los dos de la regla 4.
      Si falta alguno la regla queda en `unknown`, y ninguna fila sale
      `approved`.
    """
    outcomes = _rule_outcomes(
        row,
        row_ctx,
        published_at=published_at,
        channel=channel,
        pilot_ids=pilot_ids,
        province_ids=province_ids,
        recent_channel_rows=recent_channel_rows,
        min_cell_n=min_cell_n,
        channel_min_history=channel_min_history,
        channel_max_edit_rate=channel_max_edit_rate,
    )
    return _row_validation(outcomes, [] if verified else ["la fila no está verificada"])


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


def validate_ad(
    ctx: AdContext, config: ValidationConfig | None, *, lane1_images: bool = False
) -> AdValidation:
    """Valida un anuncio y decide su carril. Es el único lugar que lo decide.

    `config` es obligatorio y no tiene valor por defecto: pasar `None` es decir
    «no hay configuración», y entonces todo anuncio va al carril 2 (las filas
    igual se evalúan, con las reglas que dependen de un parámetro en `unknown`).

    `lane1_images` admite una imagen en el carril 1, leída por el texto de su
    OCR (slice OCR como texto, `docs/spec-slice-ocr-como-texto.md`). Apagado,
    como siempre: toda imagen va a la cola. Lo prende solo el carril 1, que
    tiene para las imágenes su interruptor, su freno y su auditoría propios.
    No es un parámetro de `ValidationConfig` a propósito: no se calibra, y así
    el informe de sombra sigue registrando los mismos parámetros.
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
    if ctx.content_type != ContentType.TEXT.value and not lane1_images:
        reasons.append("el anuncio es una imagen: las imágenes no entran al carril 1")
    if model.dropped:
        reasons.append("la propuesta del modelo tiene filas o campos caídos")
    if not rows:
        reasons.append("la propuesta del modelo no trae ninguna fila")
    max_rows = None if config is None else config.lane1_max_rows
    if max_rows is not None and len(rows) > max_rows:
        # Sin la cantidad de filas: el informe de sombra cuenta anuncios por
        # motivo, y con ella cada largo sería un motivo distinto.
        reasons.append(f"la propuesta trae más filas que el tope del carril 1 ({max_rows})")
    row_blocks = [
        (index, reason)
        for index, row in enumerate(rows, start=1)
        for reason in row.reasons
        if _blocks_lane1(reason, config)
    ]
    signal_reasons = [] if config is None else _signal_reasons(ctx, config)
    if _dirty_channel_yields(reasons + signal_reasons, row_blocks, rows, config):
        return AdValidation(
            LANE_ACCEPT,
            (
                "anuncio de texto en un canal con historial sucio: es su único freno, "
                "y reglas y modelo coinciden en todas sus filas",
            ),
            rows,
        )
    reasons += [f"fila {index}: {reason}" for index, reason in row_blocks]
    reasons += signal_reasons

    if reasons:
        return AdValidation(LANE_REVIEW, tuple(reasons), rows)
    return AdValidation(
        LANE_ACCEPT,
        (
            "anuncio de texto con todas sus filas verificadas y ninguna regla fallada "
            "(canal sin historial, celda sin referencias y posible duplicado no frenan)",
        ),
        rows,
    )


def decide_lane_0(
    ctx: AdContext,
    *,
    external_discard_below: float | None = None,
    arbiter_below: float | None = None,
    arbiter_images: bool = False,
) -> AdValidation:
    """Decide SOLO el carril 0, sin configuración de parámetros (slice B2a).

    El carril 0 no usa ningún parámetro de `ValidationConfig`, pero
    `validate_ad` lo niega sin configuración. Esta función aplica los mismos
    predicados que `validate_ad` (propuesta presente, sin degradar, el modelo
    descarta por `off_focus` o `no_data`, las reglas no ven ningún piloto) y
    nada más: devuelve carril 0 o carril 2, nunca el 1. Quien la llama hace de
    configuración con su interruptor por tipo (`SAIMC_LANE0_*_ENABLED`).

    `external_discard_below` suma un segundo juez, el triage externo de
    `ctx.triage_probability` (slice jev-decisor, `docs/spec-slice-jev-decisor.md`
    §2.1). Con `None` todo es como antes. Con un umbral:

    - si el modelo descarta, hace falta además que P(sí) sea MENOR que el
      umbral; si no hay señal, decide como antes (un juez caído no endurece);
    - si el modelo no propuso (sin propuesta, degradada o con falla), el
      triage externo puede descartar solo, con la misma regla del piloto en
      el texto;
    - si el modelo propone filas, el triage no cambia nada.

    `arbiter_below` hace de Jev el árbitro del desacuerdo (slice
    jev-desacuerdo, `docs/spec-slice-jev-desacuerdo.md`): si el modelo
    descarta por `off_focus` o `no_data` y lo ÚNICO que lo frena es el piloto
    en el texto, P(sí) MENOR que el umbral lo descarta igual. Solo textos (Jev
    no ve imágenes), salvo con `arbiter_images`: Jev leyó la imagen por su OCR
    (`SAIMC_JEV_OCR_ENABLED`, slice OCR como texto). Sin señal válida, sigue a
    la cola. Con `None`, lo de antes.
    """
    model = ctx.model
    judged = external_discard_below is not None or arbiter_below is not None
    p = _triage(ctx.triage_probability) if judged else None
    if model is None or model.degraded or model.failure:
        if p is not None and external_discard_below is not None and p < external_discard_below:
            return _external_discard(ctx, p)
        if model is None:
            return AdValidation(LANE_REVIEW, ("el anuncio no tiene propuesta del modelo",))
        return AdValidation(LANE_REVIEW, ("la propuesta del modelo está degradada",))
    if model.discard is not True:
        return AdValidation(LANE_REVIEW, ("el modelo no descarta el anuncio",))
    reasons = _lane_0_reasons(ctx, model)
    if p is not None and external_discard_below is not None and p >= external_discard_below:
        reasons.append(f"el modelo descarta, pero Jev no (P(sí) = {p:.2f})")
    if (
        arbiter_below is not None
        and p is not None
        and p < arbiter_below
        and (arbiter_images or ctx.content_type == ContentType.TEXT.value)
        and reasons == [_PILOT_REASON]
    ):
        return AdValidation(
            LANE_DISCARD,
            (
                f"el modelo descarta ({model.discard_reason}) y las reglas ven un producto "
                f"piloto en el texto: Jev arbitra y descarta (P(sí) = {p:.2f})",
            ),
        )
    verdict = _lane_0_verdict(ctx, model, reasons)
    if verdict.lane == LANE_DISCARD and p is not None:
        return AdValidation(LANE_DISCARD, (*verdict.reasons, f"Jev también descarta (P(sí) = {p:.2f})"))
    return verdict


def _triage(value: object) -> float | None:
    """La P(sí) del triage externo, o `None` si no es un número finito entre 0
    y 1. `bool` queda afuera a propósito: en Python `True` es un `int`."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if not math.isfinite(value) or not 0 <= value <= 1:
        return None
    return float(value)


def _external_discard(ctx: AdContext, p: float) -> AdValidation:
    """Rol 2: el modelo no propuso y el triage externo descarta. La regla del
    piloto en el texto vale igual que para un descarte del modelo."""
    if pilot_in_text(ctx):
        return AdValidation(
            LANE_REVIEW,
            (
                f"el asistente no propuso y Jev descarta (P(sí) = {p:.2f}), "
                "pero las reglas ven un producto piloto en el texto",
            ),
        )
    return AdValidation(
        LANE_DISCARD,
        (
            f"el asistente no propuso y Jev descarta (P(sí) = {p:.2f}); "
            "las reglas no ven ningún producto piloto",
        ),
    )


def pilot_in_text(ctx: AdContext) -> bool:
    """¿Las reglas ven un producto piloto en un texto con algún dígito? Lo usa
    también el carril 0 para saber si un descarte lo arbitró Jev."""
    # Sin un solo dígito no puede haber precio: que el texto nombre un producto
    # («pollo agotado») no lo vuelve un anuncio con dato (2026-10-13). Se mira el
    # dígito y no el significado: un «2» suelto ya basta para que lo mire una
    # persona, que es la lectura conservadora.
    return has_digit(ctx.text) and mentions_any_product(ctx.text, ctx.pilot_products)


def has_digit(text: str) -> bool:
    """¿Hay algún dígito? Sin uno no puede haber precio. Lo usa también el
    carril 0 para el motivo de un descarte de Jev (slice jev-decisor)."""
    return any(char.isdigit() for char in text)


#: El freno del piloto en el texto: el único que Jev puede arbitrar.
_PILOT_REASON = "el modelo descarta, pero las reglas ven un producto piloto en el texto"


def _lane_0_reasons(ctx: AdContext, model: AdProposal) -> list[str]:
    """Por qué un descarte del modelo NO va al carril 0. Vacío si va."""
    reasons: list[str] = []
    if model.discard_reason not in _LANE_0_REASONS:
        reasons.append("el modelo descarta, pero no por fuera de foco ni por falta de datos")
    if pilot_in_text(ctx):
        reasons.append(_PILOT_REASON)
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
