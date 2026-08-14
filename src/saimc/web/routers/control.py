"""Vista de control del operador.

Responde dos preguntas, en este orden:

1. **¿Cuánto falta, y qué falta exactamente?** (`saimc.progress`) — progreso
   contra la meta de `spec.md` §9, ritmo de carga, huecos por responder y
   cobertura producto × provincia. Es lo que decide dónde poner la hora diaria
   de curaduría.
2. **¿Lo que estoy cargando sirve?** (`saimc.cells`) — la matriz cruda y el
   rango minorista, que era el alcance original de esta vista.

No es el dashboard público de la Fase 1 (mapa de calor, semáforos, tarjetas de
recomendación). Es el espejo del operador, y por eso muestra medición sin
emitir juicios: los únicos dos objetivos que aparecen —500 registros de 3–5
productos en 2 provincias (§9), tasa de descarte <30% (§12.5)— están escritos
en el documento rector. Ningún otro umbral se inventa acá (§12.6).
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from saimc.cells import compute_cells, compute_retail_ranges
from saimc.exchange_rate.status import compute_applied_rates, compute_rate_status
from saimc.models import PriceHistory, ProcessingStatus, RawIngestion
from saimc.progress import (
    PHASE0_PRODUCTS_MAX,
    PHASE0_PRODUCTS_MIN,
    PHASE0_PROVINCES,
    PILOT_PRODUCTS,
    compute_coverage,
    compute_daily_activity,
    compute_intake_health,
    compute_open_gaps,
    compute_pace,
    compute_phase0_progress,
)
from saimc.web.deps import get_db
from saimc.web.matrix import build_matrix

router = APIRouter()

# Ventana de la vista de rango minorista. 7 días —una semana— como default
# razonable para "cómo se movió el precio": ni tan corta que un día flojo de
# curaduría la vacíe, ni tan larga que mezcle tendencias distintas. Ajustable
# por query string (`?days=N`) sin exigir un valor fijo en código.
_DEFAULT_RETAIL_RANGE_DAYS = 7

# Ventana del ritmo de carga. Dos semanas: suficiente para que un día sin
# curar no hunda el promedio, corta para que siga describiendo el ritmo de
# ahora y no el del mes pasado. No es un umbral —nada se decide con este
# número—, es el ancho de una ventana de lectura.
_ACTIVITY_DAYS = 14

# Filtro de pantalla, no de datos (2026-08-12): la curaduría se enfoca en
# estos 4 desde el 2026-08-11 (docs/decisions.md), pero el catálogo real tiene
# 50+ productos por altas hechas al vuelo durante la curaduría. Mostrarlos
# todos con el mismo peso visual hacía ilegible la vista. El resto sigue
# entero en la base — nada se borra acá, ver `compute_cells`/`prices_history`
# directo si hace falta. La lista canónica vive en `saimc.progress` porque el
# progreso de Fase 0 se mide contra ella; acá solo se usa para filtrar.
_PILOT_FILTER = frozenset(PILOT_PRODUCTS)


@router.get("/control", response_class=HTMLResponse)
def control_panel(
    request: Request, db: Session = Depends(get_db), days: int = _DEFAULT_RETAIL_RANGE_DAYS
) -> HTMLResponse:
    cells = [c for c in compute_cells(db) if c.product_name in _PILOT_FILTER]
    retail_ranges = [
        r for r in compute_retail_ranges(db, days=days) if r.product_name in _PILOT_FILTER
    ]

    progress = compute_phase0_progress(db)
    activity = compute_daily_activity(db, days=_ACTIVITY_DAYS)
    pace = compute_pace(activity, rows_remaining=progress.rows_remaining)

    curated = db.execute(
        select(func.count())
        .select_from(RawIngestion)
        .where(RawIngestion.processing_status == ProcessingStatus.CURATED)
    ).scalar_one()
    rows = db.execute(select(func.count()).select_from(PriceHistory)).scalar_one()

    return request.app.state.templates.TemplateResponse(
        request,
        "control.html",
        {
            "cells": cells,
            # Grilla producto × provincia, con las barras ya calculadas (ver
            # `saimc.web.matrix`). Apilado por grupo, «Arroz · Santiago» y
            # «Arroz · La Habana» caían separados por otros productos —el orden
            # es por muestra— y comparar provincias, que es el objeto medido de
            # spec.md §5, había que hacerlo de memoria.
            "matrix": build_matrix(cells),
            "retail_ranges": retail_ranges,
            "retail_range_days": days,
            "curated": curated,
            "rows": rows,
            # Se cuenta sobre las celdas ya filtradas, no con un agregado
            # aparte, para que el número describa exactamente la tabla de
            # abajo. Los contadores de productos y provincias que vivían acá
            # los reemplazaron las tarjetas de «Meta de Fase 0», que además
            # nombran cuáles faltan.
            "single_observation": sum(1 for cell in cells if cell.count == 1),
            # Progreso de Fase 0 (spec.md §9) y su contexto.
            "progress": progress,
            "activity": activity,
            "activity_days": _ACTIVITY_DAYS,
            # El máximo de la ventana escala las barras del gráfico. Va acá y
            # no en la plantilla porque Jinja no tiene `max` sobre atributos
            # sin un filtro extra, y el 1 evita dividir por cero.
            "activity_peak": max([day.rows for day in activity] or [0]) or 1,
            "pace": pace,
            "gaps": compute_open_gaps(db),
            "coverage": compute_coverage(db),
            "health": compute_intake_health(db),
            # Estado de la tasa de cambio. Ambas salen de la base: cargar
            # `/control` no golpea la API de elTOQUE (ver status.py).
            "rate_status": compute_rate_status(db),
            "applied_rates": compute_applied_rates(db),
            "phase0_products_min": PHASE0_PRODUCTS_MIN,
            "phase0_products_max": PHASE0_PRODUCTS_MAX,
            "phase0_provinces": PHASE0_PROVINCES,
            "pilot_products": PILOT_PRODUCTS,
        },
    )
