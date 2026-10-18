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

**Privado.** Todo el router va detrás de `require_operator`, la misma
contraseña de `/curation`, `/catalog` y `/docs` (`docs/decisions.md`,
2026-10-05). Estuvo abierto hasta ese día, cuando la premisa de «solo
conteos» ya no era cierta: los desgloses por canal nombran los canales y
grupos de origen, y el `POST /control/lint-drift` corre `ruff --fix` sobre el
código del servidor.
"""

import json
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from typing import cast

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from saimc.automation import automation_summary
from saimc.cells import (
    compute_availability,
    compute_cells,
    compute_retail_ranges,
    detect_basis_suspects,
)
from saimc.config import Settings, get_settings
from saimc.exchange_rate.status import compute_applied_rates
from saimc.extraction.measure import read_h0_runs
from saimc.extraction.prefill_status import PrefillRun
from saimc.models import PriceHistory, ProcessingStatus, RawIngestion
from saimc.progress import (
    PHASE0_PRODUCTS_MAX,
    PHASE0_PRODUCTS_MIN,
    PHASE0_PROVINCES,
    compute_channel_intake,
    compute_coverage,
    compute_daily_activity,
    compute_open_gaps,
    compute_pace,
    compute_phase0_progress,
    compute_weekly_automation,
    oldest_pending_age_days,
    pilot_product_names,
)
from saimc.requisitos_status import read_module_status
from saimc.validation import ValidationStatus, jev_signal, lane0, lane1, persist
from saimc.validation.control_cells import compute_cell_controls
from saimc.web import control_flow, control_nav
from saimc.web.auth import require_operator
from saimc.web.deps import get_db
from saimc.web.health_strip import Indicator, worst_level
from saimc.web.matrix import build_matrix, resolve_column, select_province
from saimc.web.product_nav import DEFAULT_WINDOW_DAYS
from saimc.web.rubber_stamp_metrics import rubber_stamp_alert
from saimc.web.strip_loader import load_strip

router = APIRouter(dependencies=[Depends(require_operator)])

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


# Sección «Validación» (B1): cada estado del motor con su nombre en pantalla y
# una línea que dice qué significa, en el orden de precedencia de
# `docs/spec-motor-validacion.md` («Estados y puntaje») y con «sin verificar»
# al final. Es texto de pantalla, por eso vive acá y no en el motor.
_VALIDATION_LABELS: tuple[tuple[ValidationStatus, str, str], ...] = (
    (
        ValidationStatus.APPROVED,
        "Aprobada",
        "Pasó las seis reglas y la revisó una persona.",
    ),
    (
        ValidationStatus.INCOMPLETE,
        "Incompleta",
        "Le falta producto piloto, precio, moneda o provincia.",
    ),
    (
        ValidationStatus.ANOMALY,
        "Anomalía",
        "El precio cae fuera de la banda de los precios ya cargados de su celda.",
    ),
    (
        ValidationStatus.INCONSISTENT,
        "Inconsistente",
        "El precio encaja mejor leído con la otra base: por unidad en vez de por el lote, o al revés.",
    ),
    (
        ValidationStatus.POSSIBLE_DUPLICATE,
        "Posible duplicado",
        "El mismo canal ya tenía una fila igual en las 24 horas previas.",
    ),
    (
        ValidationStatus.UNTRUSTED_SOURCE,
        "Fuente no confiable",
        "La provincia no es del catálogo, o el canal tiene un historial sucio.",
    ),
    (
        ValidationStatus.UNVERIFIED,
        "Sin verificar",
        "Ninguna regla falló, pero al menos una no tuvo con qué opinar.",
    ),
)


def _validation_summary(db: Session) -> dict[str, object]:
    """Los conteos de la sección «Validación». Solo conteos: `/control` es
    público y acá no va ningún canal, emisor ni detalle por fila."""
    counts = persist.status_counts(db)
    return {
        "states": [
            {
                "status": status.value,
                "label": label,
                "hint": hint,
                "count": counts.by_status.get(status.value, 0),
            }
            for status, label, hint in _VALIDATION_LABELS
        ],
        "unvalidated": counts.unvalidated,
        "total": counts.total,
    }


def _jev_summary(db: Session, settings: Settings, run: PrefillRun | None) -> dict[str, object]:
    """La sección «Jev (señal en sombra)» (A6b). Solo el estado, conteos y
    gasto: esta sección nació cuando `/control` era público y sigue igual de
    acotada, así que acá no va ningún texto, canal ni respuesta
    (tampoco el motivo que mandó el gateway en un corte: ese queda en el
    registro del pre-cálculo)."""
    counts = jev_signal.control_summary(db, settings)
    # La línea se arma acá y no en Jinja, para poder testearla.
    if run is None:
        last_run = "Todavía no hay ninguna corrida registrada del pre-cálculo."
    else:
        last_run = (
            f"Última corrida del pre-cálculo: {run.jev_asked} "
            f"{'preguntado' if run.jev_asked == 1 else 'preguntados'}, {run.jev_failed} "
            f"{'fallo' if run.jev_failed == 1 else 'fallos'}"
        )
        if run.jev_stopped:
            last_run += f"; cortó porque {jev_signal.STOP_LABELS.get(run.jev_stopped, run.jev_stopped)}"
        last_run += "."
    rows_hint = (
        "«Textos con señal» cuenta los que tienen respuesta a las preguntas vigentes; "
        f"«Filas anotadas», las filas de precio con esa respuesta en su detalle. Hay "
        f"{counts.total_rows} {'fila' if counts.total_rows == 1 else 'filas'} en la tabla en total."
    )
    state_hint = {
        jev_signal.STATE_ON: (
            "El pre-cálculo le hace tres preguntas a cada texto pendiente y la respuesta se anota al curar."
        ),
        jev_signal.STATE_NO_KEY: (
            "El interruptor está encendido pero falta la clave: no se pregunta ni se anota nada."
        ),
        jev_signal.STATE_OFF: "No se pregunta ni se anota nada.",
    }[counts.state]
    return {
        "counts": counts,
        "state_hint": state_hint,
        "rows_hint": rows_hint,
        "last_run": last_run,
    }


def _manipulation_controls(db: Session, *, burst_window_minutes: int) -> dict[str, object]:
    """La sección «Controles de manipulación (§10.7)» (slice C3).

    Solo mediciones por celda: conteos, medianas y porcentajes. `/control` es
    público, así que de acá no sale ningún canal, emisor ni hash.
    """
    cells = compute_cell_controls(db, burst_window=timedelta(minutes=burst_window_minutes))
    diverging = sum(1 for cell in cells if cell.status == "diverges")
    unanchored = sum(1 for cell in cells if cell.status == "unanchored")
    # La línea se arma acá y no en Jinja: una cadena construida en la plantilla
    # no se puede testear (mismo criterio que `Cell.sample_label`).
    headline = (
        f"{diverging} {'celda diverge' if diverging == 1 else 'celdas divergen'}"
        f" · {unanchored} sin ancla"
        f" · de {len(cells)} {'celda' if len(cells) == 1 else 'celdas'} de los pilotos"
        f" en los últimos {DEFAULT_WINDOW_DAYS} días"
    )
    return {
        "cells": cells,
        "headline": headline,
        "min_control_n": persist.MIN_CELL_N,
        "burst_window_minutes": burst_window_minutes,
    }


def _shell_context(request: Request, db: Session, page_key: str) -> dict[str, object]:
    """Lo que toda subpágina necesita: encabezado, menú con insignias y la franja.

    La franja se calcula en todas las vistas porque de ella salen las
    insignias del menú; es la misma que antes abría `/control`, sin consultas
    nuevas. El estado de WAHA es la única señal que sale de la red: corre en el
    mismo VPS y el timeout es corto. Si no contesta, eso ES el dato (hueco N-02).
    """
    settings = get_settings()
    snapshot = load_strip(db, settings)
    intake, strip = snapshot.intake, snapshot.strip
    rate_statuses, prefill_run = snapshot.rate_statuses, snapshot.prefill_run
    assist = snapshot.assist
    # Vigilancia del rubber-stamping (slice 5): semana actual vs. anterior de
    # `llm_reviews`. Puramente aditivo sobre la base — no golpea la API.
    rubber_stamp = rubber_stamp_alert(db)

    curated = db.execute(
        select(func.count())
        .select_from(RawIngestion)
        .where(RawIngestion.processing_status == ProcessingStatus.CURATED)
    ).scalar_one()
    rows = db.execute(select(func.count()).select_from(PriceHistory)).scalar_one()

    return {
        "page": control_nav.PAGE_BY_KEY[page_key],
        "control_pages": control_nav.PAGES,
        "badges": control_nav.compute_badges(strip, rubber_stamp_alert=bool(rubber_stamp["alert"])),
        "anchor_urls": control_nav.anchor_urls(),
        "curated": curated,
        "rows": rows,
        "health": intake,
        # La franja de estado: lo que exige acción hoy, ordenado por gravedad
        # (`web/health_strip.py`).
        "strip": strip,
        "strip_level": worst_level(strip),
        "assist": assist,
        "rubber_stamp": rubber_stamp,
        "rate_status": rate_statuses,
        "prefill_run": prefill_run,
        "settings": settings,
    }


def _render(request: Request, template: str, context: dict[str, object]) -> HTMLResponse:
    return cast(HTMLResponse, request.app.state.templates.TemplateResponse(request, template, context))


@router.get("/control", response_class=HTMLResponse, response_model=None)
def control_panel(
    request: Request,
    db: Session = Depends(get_db),
    province: str | None = None,
    inactivos: bool = False,
    days: int | None = None,
) -> HTMLResponse | RedirectResponse:
    """«Hoy»: la franja de estado, lo que toca hacer y las tarjetas a las demás vistas."""
    # Los enlaces viejos cargaban todo en `/control?...`. Con las secciones en
    # subpáginas, cada parámetro tiene ahora un solo dueño; se conserva la
    # query entera. (El `#ancla` no llega al servidor: lo resuelve el script de
    # la plantilla.)
    destino = (
        "/control/calidad"
        if province is not None
        else "/control/ingesta"
        if inactivos
        else "/control/mercado"
        if days is not None
        else None
    )
    if destino is not None:
        consulta = request.url.query
        return RedirectResponse(f"{destino}?{consulta}" if consulta else destino)

    context = _shell_context(request, db, "hoy")
    progress = compute_phase0_progress(db)
    activity = compute_daily_activity(db, days=_ACTIVITY_DAYS)
    context.update(
        {
            # Estado de los 16 módulos (docs/requisitos.md), para la sección
            # «Hoy»: qué atacar en diseño/implementación además de la hora de
            # curaduría. Fail-soft: tabla movida o archivo ausente degradan a
            # lista vacía, no a error.
            "module_status": read_module_status(),
            "activity": activity,
            "activity_days": _ACTIVITY_DAYS,
            # El máximo de la ventana escala las barras del gráfico. Va acá y
            # no en la plantilla porque Jinja no tiene `max` sobre atributos
            # sin un filtro extra, y el 1 evita dividir por cero.
            "activity_peak": max([day.rows for day in activity] or [0]) or 1,
            "pace": compute_pace(activity, rows_remaining=progress.rows_remaining),
            "gaps": compute_open_gaps(db),
        }
    )
    return _render(request, "control_hoy.html", context)


@router.get("/control/flujo", response_class=HTMLResponse)
def control_flujo(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """Flujo: el lienzo de siete nodos con el caudal de 24 h y la salud de cada etapa."""
    context = _shell_context(request, db, "flujo")
    context["flow"] = control_flow.build_view(
        control_flow.load_inputs(
            db,
            cast(Settings, context["settings"]),
            strip=cast("list[Indicator]", context["strip"]),
            prefill_run=cast(PrefillRun | None, context["prefill_run"]),
        )
    )
    return _render(request, "control_flujo.html", context)


@router.get("/control/ingesta", response_class=HTMLResponse)
def control_ingesta(
    request: Request,
    db: Session = Depends(get_db),
    days: int = _DEFAULT_RETAIL_RANGE_DAYS,
    inactivos: bool = False,
) -> HTMLResponse:
    """Ingesta: salud, descarte por canal, horas y automatización, disponibilidad."""
    pilot_filter = frozenset(pilot_product_names(db))
    channel_intake = compute_channel_intake(db)
    context = _shell_context(request, db, "ingesta")
    context.update(
        {
            "retail_range_days": days,
            "availability": [
                a for a in compute_availability(db, days=days) if a.product_name in pilot_filter
            ],
            # «Descarte por canal» lista solo los canales que siguen ingiriendo
            # (y la captura manual, `is_active is None`). Los inactivos se
            # piden con `?inactivos=1`. Es solo vista: las tarjetas de «Salud
            # de la ingesta» siguen sumando todos los canales.
            "channel_intake": [c for c in channel_intake if inactivos or c.is_active is not False],
            "inactive_channels": sum(1 for c in channel_intake if c.is_active is False),
            "show_inactive": inactivos,
            "inactive_toggle_url": str(
                (
                    request.url.remove_query_params("inactivos")
                    if inactivos
                    else request.url.include_query_params(inactivos=1)
                ).replace(fragment="canales")
            ),
            # Horas y automatización (A2d): la serie semanal sale de la
            # bitácora, de `prices_history` y de `llm_proposals`; el pendiente
            # más viejo es el estado de la cola hoy, fuera de la serie.
            "weekly_automation": compute_weekly_automation(db),
            "oldest_pending_days": oldest_pending_age_days(db),
            # Carril 0 (B2a): descartes automáticos y rescatados de la semana,
            # por tipo. La cota de falsos descartes NO va acá: la cota queda en
            # `/curation/auditoria`, que pide operador.
            "lane0_week": lane0.week_counts(db),
        }
    )
    return _render(request, "control_ingesta.html", context)


@router.get("/control/calidad", response_class=HTMLResponse)
def control_calidad(
    request: Request,
    db: Session = Depends(get_db),
    days: int = _DEFAULT_RETAIL_RANGE_DAYS,
    province: str | None = None,
) -> HTMLResponse:
    """Calidad: validación, carril 1, aceptación sin revisar, Jev, controles y matriz."""
    pilot_products = pilot_product_names(db)
    pilot_filter = frozenset(pilot_products)
    cells = [c for c in compute_cells(db) if c.product_name in pilot_filter]

    # La provincia de la pestaña activa. `resolve_column` no rompe con un
    # parámetro desconocido: cae en la primera provincia del catálogo.
    matrix = build_matrix(cells)
    selected = resolve_column(matrix, province)

    context = _shell_context(request, db, "calidad")
    settings = cast(Settings, context["settings"])
    prefill_run = cast(PrefillRun | None, context["prefill_run"])
    context.update(
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
            "retail_range_days": days,
            "pilot_products": pilot_products,
            # Se cuenta sobre las celdas ya filtradas, para que el número
            # describa exactamente la tabla de abajo.
            "single_observation": sum(1 for cell in cells if cell.count == 1),
            # Carril 1 (B2b-3b): filas por procedencia, error auditado con su
            # cota y el freno. Pide operador, así que la cota sí va acá; lo
            # público está en `/metodologia`.
            "lane1": lane1.summary(db, settings),
            "lane1_target": lane1.ERROR_TARGET,
            "lane1_n0": lane1.AUDIT_N0,
            # Validación (B1): cuántas filas hay en cada estado del motor.
            "validation": _validation_summary(db),
            # Jev en sombra (A6b): estado, conteos y gasto de la señal.
            "jev": _jev_summary(db, settings, prefill_run),
            # Controles de §10.7 (C3): índice de control y señales de
            # coordinación por celda pública. Se calcula al pedirlo, no se guarda.
            "controls": _manipulation_controls(db, burst_window_minutes=settings.burst_window_minutes),
        }
    )
    return _render(request, "control_calidad.html", context)


@router.get("/control/fase-0", response_class=HTMLResponse)
def control_fase_0(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """Fase 0: meta, métrica, historial de H0, cobertura y consumo del asistente."""
    pilot_products = pilot_product_names(db)
    progress = compute_phase0_progress(db)
    activity = compute_daily_activity(db, days=_ACTIVITY_DAYS)
    context = _shell_context(request, db, "fase0")
    settings = cast(Settings, context["settings"])
    context.update(
        {
            # Progreso de Fase 0 (spec.md §9) y su contexto.
            "progress": progress,
            "pace": compute_pace(activity, rows_remaining=progress.rows_remaining),
            # Historial de H0: cada corrida registrada de `measure.py`, en
            # orden cronológico. Lee `docs/h0-runs.jsonl` en cada carga (es un
            # archivo chico); degrada a lista vacía si todavía no existe.
            "h0_runs": read_h0_runs(),
            "coverage": compute_coverage(db),
            # Métrica de la Fase 0 (B3-3): la partición por cohorte de ingreso,
            # con el error auditado de lo que cuenta cada número.
            "phase0": automation_summary(db, settings),
            "lane1_target": lane1.ERROR_TARGET,
            "phase0_products_min": PHASE0_PRODUCTS_MIN,
            "phase0_products_max": PHASE0_PRODUCTS_MAX,
            "phase0_provinces": PHASE0_PROVINCES,
            "pilot_products": pilot_products,
        }
    )
    return _render(request, "control_fase0.html", context)


@router.get("/control/mercado", response_class=HTMLResponse)
def control_mercado(
    request: Request,
    db: Session = Depends(get_db),
    days: int = _DEFAULT_RETAIL_RANGE_DAYS,
) -> HTMLResponse:
    """Mercado: tasa de cambio y rango minorista por producto."""
    pilot_filter = frozenset(pilot_product_names(db))
    retail_report = compute_retail_ranges(db, days=days)
    context = _shell_context(request, db, "mercado")
    context.update(
        {
            "retail_ranges": [r for r in retail_report.ranges if r.product_name in pilot_filter],
            "retail_range_days": days,
            # Sin filtrar por piloto, a diferencia de `retail_ranges`: el
            # filtro de compra mínima corre dentro de `compute_retail_ranges`,
            # antes de que acá se sepa de qué producto era cada fila. Preferí
            # un número que abarca de más y es honesto sobre eso, a rehacer la
            # consulta para recortarlo.
            "excluded_by_minimum": retail_report.excluded_by_minimum,
            # Mismo criterio de honestidad que el de arriba: cuenta sobre
            # todos los productos, no solo los pilotos (2026-08-21).
            "excluded_by_basis": retail_report.excluded_by_basis,
            # Las filas que la propia celda desmiente. Van en la vista del
            # operador y no en la pública porque son trabajo de curaduría: cada
            # una es una corrección concreta con su evidencia al lado, no una
            # estadística. Ventana propia y más larga que la del rango
            # minorista: una fila mal marcada envenena su celda mientras exista,
            # no solo mientras esté fresca.
            "basis_suspects": detect_basis_suspects(db, days=30),
            # Ambas salen de la base: cargar `/control/mercado` no golpea la
            # API de elTOQUE (ver status.py).
            "applied_rates": compute_applied_rates(db),
        }
    )
    return _render(request, "control_mercado.html", context)


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
    return request.app.state.templates.TemplateResponse(request, "control_lint_drift.html", contexto)
