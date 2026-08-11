"""Vista de control del operador.

Responde una sola pregunta: **¿lo que estoy cargando sirve?**

Hasta ahora el circuito estaba abierto —capturar, curar, y nada— y los errores
de clasificación solo se descubrían consultando la base a mano. Esta vista lo
cierra: cada sesión de curaduría se puede verificar de un vistazo.

No es el dashboard público de la Fase 1 (mapa de calor, semáforos, tarjetas de
recomendación). Es el espejo del operador, y por eso muestra la matriz cruda
sin emitir juicios: ni umbral de muestra mínima ni marca de dispersión alta
(whitepaper §12.6).
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from saimc.cells import compute_cells
from saimc.models import PriceHistory, ProcessingStatus, RawIngestion
from saimc.web.deps import get_db

router = APIRouter()


@router.get("/control", response_class=HTMLResponse)
def control_panel(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    cells = compute_cells(db)

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
