"""Vigilancia del rubber-stamping en `/control` (slice 5 del plan del asistente).

Con el asistente proponiendo el anuncio entero, curar puede degradar en
aceptar sin revisar: apretar «Aceptar» cada vez más rápido, cada vez sin tocar
nada. Este módulo mide dos señales sobre `LlmReview` (inmutable, una fila por
anuncio curado o descartado) para que esa deriva sea visible en vez de
silenciosa:

- **Tasa de "no editado" por campo**: de los campos que el modelo propuso,
  cuántos quedaron exactamente como los propuso, recalculado de `proposed` y
  `final` (A2c) con cuatro categorías: sin cambios, completó un vacío,
  corrigió un valor, borró la fila.
- **Tiempo por decisión**: `created_at - shown_at` de cada revisión, en
  segundos. `shown_at` es nuevo (slice 5, columna nullable sin backfill) —
  una fila vieja simplemente no entra en la muestra.

`rubber_stamp_alert` compara la semana actual contra la anterior y avisa solo
cuando las DOS empeoran a la vez: sube el "no editado" Y baja el tiempo por
decisión. Es puramente direccional, sin umbral de magnitud — un umbral
inventado violaría el mismo criterio que `spec.md` §12.6 les exige a las
demás métricas de esta vista.
"""

from __future__ import annotations

import statistics
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from saimc.extraction.llm_store import REVIEW_FIELDS, same_value
from saimc.ingestion.origin import channel_province
from saimc.models import LlmReview, RawIngestion, ReviewOutcome

# Qué hizo el operador con un campo que el modelo propuso. Ver `_classify`.
CATEGORIES = ("unchanged", "filled", "corrected", "deleted")

# La ventana de comparación: una semana. Mismo criterio que el resto de
# `/control` (`_ACTIVITY_DAYS`, rango minorista): ancho de lectura, no umbral.
_WINDOW_DAYS = 7


def _aware(moment: datetime) -> datetime:
    """SQLite (solo en tests) no conserva el offset pese a `DateTime(timezone=True)`;
    la columna es UTC por contrato del modelo, así que asumirlo acá es fiel al
    dato, no una adivinanza. Mismo patrón que `curation._last_capture_notice`.
    """
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _classify(field: str, proposed: str | None, final: dict[str, str | None] | None) -> str:
    """En cuál de las cuatro categorías cae un campo que el modelo propuso.

    - `deleted`: el operador borró la fila entera (`final` es `None`).
    - `filled`: el modelo se abstuvo (`None` o vacío) y el operador lo llenó.
      Completar un vacío no es corregir al modelo.
    - `corrected`: el modelo propuso un valor y el final es otro.
    - `unchanged`: quedó como estaba (también una abstención que siguió vacía).
    """
    if final is None:
        return "deleted"
    final_value = final.get(field)
    if not proposed:
        return "filled" if final_value else "unchanged"
    return "unchanged" if same_value(field, proposed, final_value) else "corrected"


def _channel_fixes_province(db: Session, review: LlmReview) -> bool:
    """¿La provincia de esta revisión la fijó el canal y no el operador?

    "El canal siempre manda" (docs/decisions.md, 2026-08-17): el formulario
    pisa la provincia propuesta con la del canal, así que una diferencia ahí
    es un artefacto, no una edición. Se resuelve con el canal de HOY: si el
    operador cambió la provincia del canal después, una revisión vieja se
    clasifica con la regla nueva (el error es chico y no hay dato guardado
    para hacerlo mejor).
    """
    ingestion = db.get(RawIngestion, review.ingestion_id)
    return ingestion is not None and channel_province(db, ingestion) is not None


def outcome_breakdown(db: Session, since: datetime, until: datetime) -> dict[str, Any]:
    """Desglose en cuatro categorías de lo que el operador hizo con cada campo.

    Recorre las revisiones CURADAS de la ventana `[since, until)`. La unidad es
    la celda: una fila propuesta por el modelo × un campo que el modelo
    propuso. Una fila agregada a mano (`proposed` es `None`) no tiene con qué
    comparar y queda afuera. La provincia se excluye de las revisiones cuyo
    canal la fija.

    Se recalcula de `proposed` y `final`, que son lo guardado y no cambia: el
    `edited` guardado no se lee (trataba la fila borrada como «sin editar»).
    Corrige también el pasado, sin tocar filas inmutables.

    Devuelve `{"total": {categoría: n}, "by_field": {campo: {categoría: n}}}`.
    """
    by_field = {field: dict.fromkeys(CATEGORIES, 0) for field in REVIEW_FIELDS}

    stmt = select(LlmReview).where(
        LlmReview.outcome == ReviewOutcome.CURATED,
        LlmReview.created_at >= since,
        LlmReview.created_at < until,
    )
    for review in db.execute(stmt).scalars():
        province_is_artifact = _channel_fixes_province(db, review)
        for entry in review.rows:
            proposed = entry.get("proposed")
            if not proposed:
                continue
            final = entry.get("final")
            for field in REVIEW_FIELDS:
                if field not in proposed:
                    continue
                if field == "province_id" and province_is_artifact:
                    continue
                by_field[field][_classify(field, proposed[field], final)] += 1

    total = {c: sum(counts[c] for counts in by_field.values()) for c in CATEGORIES}
    return {"total": total, "by_field": by_field}


def no_edit_rate_by_field(
    db: Session, since: datetime, until: datetime
) -> dict[str, tuple[int, int]]:
    """Por campo: (celdas sin cambios, celdas con propuesta del modelo).

    El denominador incluye lo corregido, lo completado y lo borrado: una fila
    borrada NO es una fila aceptada.
    """
    by_field = outcome_breakdown(db, since, until)["by_field"]
    return {
        field: (counts["unchanged"], sum(counts.values()))
        for field, counts in by_field.items()
    }


def _aggregate_rate(by_field: dict[str, tuple[int, int]]) -> tuple[int, int]:
    """Suma de numeradores / suma de denominadores de todos los campos — NO el
    promedio de las tasas por campo (un campo con pocos datos pesaría igual
    que uno con muchos)."""
    numerator = sum(num for num, _ in by_field.values())
    denominator = sum(den for _, den in by_field.values())
    return numerator, denominator


def decision_time_stats(db: Session, since: datetime, until: datetime) -> dict[str, Any]:
    """Mediana y `n` de `created_at - shown_at`, en segundos.

    Recorre revisiones (curadas o descartadas) de la ventana con `shown_at`
    cargado. Descarta duraciones negativas — reloj raro o `shown_at` que no se
    pudo parsear en el guardado. `median` es `None` con `n == 0`.
    """
    stmt = select(LlmReview).where(
        LlmReview.created_at >= since,
        LlmReview.created_at < until,
        LlmReview.shown_at.is_not(None),
    )
    durations: list[float] = []
    for review in db.execute(stmt).scalars():
        shown_at = review.shown_at
        if shown_at is None:
            continue  # el WHERE ya lo filtra; guarda para que mypy lo sepa
        delta = (_aware(review.created_at) - _aware(shown_at)).total_seconds()
        if delta < 0:
            continue
        durations.append(delta)

    return {
        "median": statistics.median(durations) if durations else None,
        "n": len(durations),
    }


def _window_summary(db: Session, since: datetime, until: datetime) -> dict[str, Any]:
    breakdown = outcome_breakdown(db, since, until)
    by_field = {
        field: (counts["unchanged"], sum(counts.values()))
        for field, counts in breakdown["by_field"].items()
    }
    numerator, denominator = _aggregate_rate(by_field)
    return {
        "breakdown": breakdown,
        "no_edit_rate_by_field": by_field,
        "no_edit_rate": (numerator / denominator) if denominator else None,
        "no_edit_n": denominator,
        "decision_time": decision_time_stats(db, since, until),
    }


def rubber_stamp_alert(db: Session, now: datetime | None = None) -> dict[str, Any]:
    """Compara los últimos 7 días contra los 7 anteriores.

    `alert` es `True` únicamente cuando, entre las dos ventanas, la tasa
    agregada de "no editado" SUBIÓ y la mediana de tiempo por decisión BAJÓ al
    mismo tiempo — las dos condiciones juntas, sin umbral de magnitud. Si
    cualquiera de las dos ventanas no tiene datos suficientes (tasa agregada
    sin denominador o mediana `None`), no hay comparación posible y `alert`
    es `False` con `reason: "datos_insuficientes"`.
    """
    ahora = now or datetime.now(UTC)
    current_since = ahora - timedelta(days=_WINDOW_DAYS)
    previous_since = current_since - timedelta(days=_WINDOW_DAYS)

    current = _window_summary(db, current_since, ahora)
    previous = _window_summary(db, previous_since, current_since)

    result: dict[str, Any] = {"current": current, "previous": previous}

    datos_suficientes = (
        current["no_edit_rate"] is not None
        and previous["no_edit_rate"] is not None
        and current["decision_time"]["median"] is not None
        and previous["decision_time"]["median"] is not None
    )
    if not datos_suficientes:
        return {"alert": False, "reason": "datos_insuficientes", **result}

    sube_no_editado = current["no_edit_rate"] > previous["no_edit_rate"]
    baja_tiempo = current["decision_time"]["median"] < previous["decision_time"]["median"]

    return {"alert": sube_no_editado and baja_tiempo, "reason": None, **result}
