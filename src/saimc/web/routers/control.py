"""Vista de control del operador.

Responde una sola pregunta: **¿lo que estoy cargando sirve?**

Hasta ahora el circuito estaba abierto —capturar, curar, y nada— y los errores
de clasificación solo se descubrían consultando la base a mano. Esta vista lo
cierra: cada sesión de curaduría se puede verificar de un vistazo.

No es el dashboard público de la Fase 1 (mapa de calor, semáforos, tarjetas de
recomendación). Es el espejo del operador, y por eso muestra la matriz cruda
sin emitir juicios: ni umbral de muestra mínima ni marca de dispersión alta
(spec.md §12.6).
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from saimc.cells import compute_cells, compute_retail_ranges
from saimc.models import PriceHistory, ProcessingStatus, RawIngestion
from saimc.web.deps import get_db

router = APIRouter()

# Ventana de la vista de rango minorista. 7 días —una semana— como default
# razonable para "cómo se movió el precio": ni tan corta que un día flojo de
# curaduría la vacíe, ni tan larga que mezcle tendencias distintas. Ajustable
# por query string (`?days=N`) sin exigir un valor fijo en código.
_DEFAULT_RETAIL_RANGE_DAYS = 7

# Filtro de pantalla, no de datos (2026-08-12): la curaduría se enfoca en
# estos 4 desde el 2026-08-11 (docs/decisions.md), pero el catálogo real tiene
# 51 productos por altas hechas al vuelo durante la curaduría. Mostrarlos
# todos con el mismo peso visual hacía ilegible la vista. El resto sigue
# entero en la base — nada se borra acá, ver `compute_cells`/`prices_history`
# directo si hace falta. Cuando cambien los pilotos, esta lista se actualiza
# a mano; no hay tabla de "producto piloto" en el esquema todavía porque no
# la pidió ninguna decisión.
_PILOT_PRODUCTS = frozenset({"Arroz", "Pollo", "Azúcar blanca", "Aceite"})


@router.get("/control", response_class=HTMLResponse)
def control_panel(
    request: Request, db: Session = Depends(get_db), days: int = _DEFAULT_RETAIL_RANGE_DAYS
) -> HTMLResponse:
    cells = [c for c in compute_cells(db) if c.product_name in _PILOT_PRODUCTS]
    retail_ranges = [
        r for r in compute_retail_ranges(db, days=days) if r.product_name in _PILOT_PRODUCTS
    ]

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
            "retail_ranges": retail_ranges,
            "retail_range_days": days,
            "curated": curated,
            "rows": rows,
            # Se cuentan sobre las celdas y no con un DISTINCT aparte para que
            # los números de la cabecera describan exactamente lo que la tabla
            # de abajo muestra.
            "products": len({cell.product_name for cell in cells}),
            "provinces": len(
                {cell.province_name for cell in cells if cell.province_name}
            ),
            "single_observation": sum(1 for cell in cells if cell.count == 1),
        },
    )
