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

Una tercera cosa vive acá, aparte de las dos preguntas de arriba: el botón
de `POST /control/lint-drift`, que dispara `scripts/lint_drift.py` (ruff +
mypy, arreglo mecánico + reporte de drift contra `docs/lint-baseline.json`,
razonado en `docs/decisions.md`). No mide nada de curaduría — vive acá porque
es la vista que el operador abre para "¿cómo estamos hoy?", y esto es parte
de esa pregunta para quien programa, no para quien cura.
"""

import json
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from saimc.cells import (
    compute_availability,
    compute_cells,
    compute_retail_ranges,
    detect_basis_suspects,
)
from saimc.config import get_settings
from saimc.exchange_rate.status import compute_applied_rates, compute_rate_status
from saimc.extraction.llm_assist import consumption_summary
from saimc.extraction.measure import read_h0_runs
from saimc.ingestion.whatsapp_health import session_health
from saimc.models import PriceHistory, ProcessingStatus, RawIngestion
from saimc.progress import (
    PHASE0_PRODUCTS_MAX,
    PHASE0_PRODUCTS_MIN,
    PHASE0_PROVINCES,
    compute_channel_intake,
    compute_coverage,
    compute_daily_activity,
    compute_intake_health,
    compute_open_gaps,
    compute_pace,
    compute_phase0_progress,
    compute_weekly_automation,
    oldest_pending_age_days,
    pilot_product_names,
)
from saimc.requisitos_status import read_module_status
from saimc.web.deps import get_db
from saimc.web.health_strip import build_strip, worst_level
from saimc.web.matrix import build_matrix, resolve_column, select_province
from saimc.web.rubber_stamp_metrics import rubber_stamp_alert

router = APIRouter()

# `control.py` vive en `src/saimc/web/routers/`, cuatro niveles bajo la raíz
# del repo. Mismo criterio de resolver por `__file__` que `docs_catalog.py` y
# `measure.py`.
_REPO_ROOT = Path(__file__).resolve().parents[4]
_LINT_DRIFT_SCRIPT = _REPO_ROOT / "scripts" / "lint_drift.py"

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

# Filtro de pantalla, no de datos (2026-08-12): el catálogo real acumuló 50+
# productos por altas hechas al vuelo durante la curaduría, y mostrarlos todos
# con el mismo peso visual hacía ilegible la vista. El resto sigue entero en la
# base — nada se borra acá, ver `compute_cells`/`prices_history` directo si
# hace falta.
#
# Desde el 2026-08-15 el conjunto sale de `products.is_pilot` en vez de una
# tupla fija: promover un producto en /catalog tiene que cambiar esta vista
# también, y con dos listas separadas no lo hacía.


@router.get("/control", response_class=HTMLResponse)
def control_panel(
    request: Request,
    db: Session = Depends(get_db),
    days: int = _DEFAULT_RETAIL_RANGE_DAYS,
    province: str | None = None,
) -> HTMLResponse:
    pilot_products = pilot_product_names(db)
    pilot_filter = frozenset(pilot_products)

    cells = [c for c in compute_cells(db) if c.product_name in pilot_filter]
    retail_report = compute_retail_ranges(db, days=days)
    retail_ranges = [r for r in retail_report.ranges if r.product_name in pilot_filter]
    availability = [
        a for a in compute_availability(db, days=days) if a.product_name in pilot_filter
    ]

    progress = compute_phase0_progress(db)
    activity = compute_daily_activity(db, days=_ACTIVITY_DAYS)
    pace = compute_pace(activity, rows_remaining=progress.rows_remaining)

    # La provincia de la pestaña activa. `resolve_column` no rompe con un
    # parámetro desconocido: cae en la primera provincia del catálogo.
    matrix = build_matrix(cells)
    selected = resolve_column(matrix, province)

    settings = get_settings()
    # Estado de WAHA. Es la única señal de la franja que sale de la red: WAHA
    # corre en el mismo VPS y el timeout es corto, así que no demora el panel.
    # Si no contesta, eso ES el dato (hueco N-02).
    waha = session_health(
        base_url=settings.waha_base_url,
        api_key=settings.waha_api_key,
        session=settings.waha_session,
    )
    ultima_captura = db.execute(select(func.max(RawIngestion.captured_at))).scalar_one()

    intake = compute_intake_health(db)
    rate_statuses = compute_rate_status(db)
    assist = consumption_summary(settings.llm_assist_log_path)
    # Vigilancia del rubber-stamping (slice 5): semana actual vs. anterior de
    # `llm_reviews`. Puramente aditivo sobre la base — no golpea la API.
    rubber_stamp = rubber_stamp_alert(db)
    strip = build_strip(
        waha=waha,
        last_capture=ultima_captura,
        health=intake,
        rate_statuses=rate_statuses,
        assist=assist,
        assist_configured=bool(settings.anthropic_api_key),
    )

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
            # La matriz entera alimenta la barra de pestañas: los totales por
            # provincia que van en cada pestaña salen de acá, así que la
            # pestaña que no estás mirando igual te dice cuánto tiene.
            "matrix": matrix,
            # Y el panel muestra una sola provincia (`?province=`, mismo
            # lenguaje que los enlaces al historial). Recortar es presentación:
            # las barras no se reescalan, así que la misma serie mide igual en
            # todas las pestañas.
            "matrix_tab": select_province(matrix, selected) if selected else None,
            "selected_province": selected,
            "retail_ranges": retail_ranges,
            "retail_range_days": days,
            "availability": availability,
            # Sin filtrar por piloto, a diferencia de `retail_ranges`: el
            # filtro de compra mínima corre dentro de `compute_retail_ranges`,
            # antes de que acá se sepa de qué producto era cada fila. Preferí
            # un número que abarca de más y es honesto sobre eso, a rehacer la
            # consulta para recortarlo.
            "excluded_by_minimum": retail_report.excluded_by_minimum,
            # Mismo criterio de honestidad que el de arriba: cuenta sobre
            # todos los productos, no solo los pilotos, porque el filtro de
            # `compute_retail_ranges` corre antes de saber de qué producto
            # era cada fila (2026-08-21).
            "excluded_by_basis": retail_report.excluded_by_basis,
            # Las filas que la propia celda desmiente. Van en la vista del
            # operador y no en la pública porque son trabajo de curaduría: cada
            # una es una corrección concreta con su evidencia al lado, no una
            # estadística. Ventana propia y más larga que la del rango
            # minorista: una fila mal marcada envenena su celda mientras exista,
            # no solo mientras esté fresca.
            "basis_suspects": detect_basis_suspects(db, days=30),
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
            # Historial de H0: cada corrida registrada de `measure.py`, en
            # orden cronológico. Lee `docs/h0-runs.jsonl` en cada carga (es un
            # archivo chico); degrada a lista vacía si todavía no existe.
            "h0_runs": read_h0_runs(),
            # Estado de los 16 módulos (docs/requisitos.md), para la sección
            # «Hoy»: qué atacar en diseño/implementación además de la hora de
            # curaduría. Fail-soft igual que `h0_runs`: tabla movida o archivo
            # ausente degradan a lista vacía, no a error.
            "module_status": read_module_status(),
            # Consumo del asistente. Sale del registro de invocaciones, no de
            # una API de facturación: es el gasto de SAIMC medido sobre lo que
            # la propia API reportó consumir, no el total de la cuenta.
            "assist": assist,
            "rubber_stamp": rubber_stamp,
            "activity": activity,
            "activity_days": _ACTIVITY_DAYS,
            # El máximo de la ventana escala las barras del gráfico. Va acá y
            # no en la plantilla porque Jinja no tiene `max` sobre atributos
            # sin un filtro extra, y el 1 evita dividir por cero.
            "activity_peak": max([day.rows for day in activity] or [0]) or 1,
            "pace": pace,
            "gaps": compute_open_gaps(db),
            "coverage": compute_coverage(db),
            "health": intake,
            "waha": waha,
            # La franja de estado que abre la vista: lo que exige acción hoy,
            # ordenado por gravedad (`web/health_strip.py`).
            "strip": strip,
            "strip_level": worst_level(strip),
            "channel_intake": compute_channel_intake(db),
            # Horas y automatización (A2d): la serie semanal sale de la
            # bitácora, de `prices_history` y de `llm_proposals`; el pendiente
            # más viejo es el estado de la cola hoy, fuera de la serie.
            "weekly_automation": compute_weekly_automation(db),
            "oldest_pending_days": oldest_pending_age_days(db),
            # Estado de la tasa de cambio. Ambas salen de la base: cargar
            # `/control` no golpea la API de elTOQUE (ver status.py).
            "rate_status": rate_statuses,
            "applied_rates": compute_applied_rates(db),
            "phase0_products_min": PHASE0_PRODUCTS_MIN,
            "phase0_products_max": PHASE0_PRODUCTS_MAX,
            "phase0_provinces": PHASE0_PROVINCES,
            "pilot_products": pilot_products,
        },
    )


@router.post("/control/lint-drift", response_class=HTMLResponse)
def run_lint_drift(request: Request) -> HTMLResponse:
    """Dispara `scripts/lint_drift.py` y muestra su salida tal cual.

    Ruta absoluta fija, sin entrada del pedido: lo único que decide qué
    corre es este archivo, nunca el cliente HTTP. El script en sí ya decide
    solo (docs/decisions.md) si conviene aplicar el `--fix` seguro y
    commitear, o si el árbol sucio lo frena — acá no se repite ese criterio.

    `--json` para poder mostrar cada hallazgo como elemento visual (banner +
    tabla), no como texto plano — pedido explícito del operador después de
    ver la primera versión. Si el parseo falla por lo que sea, degrada al
    texto crudo (`lint_output`) en vez de romper la página: la plantilla
    decide qué mostrar según `lint_parsed`.
    """
    resultado = subprocess.run(  # noqa: S603 — ruta absoluta fija, sin shell, sin entrada del pedido
        [sys.executable, str(_LINT_DRIFT_SCRIPT), "--json"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    contexto: dict[str, object] = {
        "lint_output": (resultado.stdout + resultado.stderr).strip(),
        "lint_parsed": False,
    }
    try:
        reporte = json.loads(resultado.stdout)
    except (json.JSONDecodeError, ValueError):
        pass
    else:
        contexto.update(
            lint_parsed=True,
            lint_ok=not reporte["has_new"],
            lint_autofix=reporte["autofix"],
            lint_drift=reporte["drift"],
        )
    return request.app.state.templates.TemplateResponse(
        request, "control_lint_drift.html", contexto
    )
