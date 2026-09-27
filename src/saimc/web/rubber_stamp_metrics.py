"""Vigilancia del rubber-stamping en `/control` (slice 5 del plan del asistente).

Con el asistente proponiendo el anuncio entero, curar puede degradar en
aceptar sin revisar: apretar «Aceptar» cada vez más rápido, cada vez sin tocar
nada. Este módulo mide dos señales sobre `LlmReview` (inmutable, una fila por
anuncio curado o descartado) para que esa deriva sea visible en vez de
silenciosa:

- **Tasa de "no editado" por campo**: de las filas que el modelo propuso para
  un campo, cuántas quedaron exactamente como las propuso (`LlmReview.rows[].
  edited`, que ya calcula `llm_store._edited` al guardar).
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

from saimc.extraction.llm_store import REVIEW_FIELDS
from saimc.models import LlmReview, ReviewOutcome

# La ventana de comparación: una semana. Mismo criterio que el resto de
# `/control` (`_ACTIVITY_DAYS`, rango minorista): ancho de lectura, no umbral.
_WINDOW_DAYS = 7


def _aware(moment: datetime) -> datetime:
    """SQLite (solo en tests) no conserva el offset pese a `DateTime(timezone=True)`;
    la columna es UTC por contrato del modelo, así que asumirlo acá es fiel al
    dato, no una adivinanza. Mismo patrón que `curation._last_capture_notice`.
    """
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def no_edit_rate_by_field(
    db: Session, since: datetime, until: datetime
) -> dict[str, tuple[int, int]]:
    """Por campo: (filas no editadas, filas con propuesta del modelo).

    Recorre las revisiones CURADAS de la ventana `[since, until)` y, dentro de
    cada una, sus filas. Una fila cuenta para un campo solo si el modelo
    propuso algo para ese campo (`proposed` lo trae) — una fila agregada a
    mano por el operador (`proposed` es `None`) no tiene con qué comparar y
    queda afuera del denominador.
    """
    counters: dict[str, list[int]] = {field: [0, 0] for field in REVIEW_FIELDS}

    stmt = select(LlmReview).where(
        LlmReview.outcome == ReviewOutcome.CURATED,
        LlmReview.created_at >= since,
        LlmReview.created_at < until,
    )
    for review in db.execute(stmt).scalars():
        for entry in review.rows:
            proposed = entry.get("proposed")
            if not proposed:
                continue
            edited = set(entry.get("edited") or [])
            for field in REVIEW_FIELDS:
                if field not in proposed:
                    continue
                counters[field][1] += 1
                if field not in edited:
                    counters[field][0] += 1

    return {field: (num, den) for field, (num, den) in counters.items()}


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
    by_field = no_edit_rate_by_field(db, since, until)
    numerator, denominator = _aggregate_rate(by_field)
    return {
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
