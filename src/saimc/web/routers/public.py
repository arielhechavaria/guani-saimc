"""Vista pública. La primera ruta del proyecto que no es del operador.

`spec.md` §9 pone «Visualización: Prototipo de mapa de calor simple y tarjetas
de producto» dentro del alcance de la **Fase 0**, y era el único ítem de ese
alcance sin construir (`decisions.md`, 2026-08-22). Esto es su primer slice: el
mapa con la cobertura real. Las tarjetas de producto, la banda mín–máx y el
coloreado por precio vienen después, en el orden de `docs/backlog.md`.

Sin `require_operator` a propósito: es la capa pública. Y sin identificador de
usuario ni dato de contacto en ninguna respuesta — `CLAUDE.md` fija que el
identificador de usuario origen nunca se expone acá.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from saimc.models import PriceHistory, Province
from saimc.web.deps import get_db
from saimc.web.map_view import build_map

router = APIRouter()


def _rows_by_province(session: Session) -> dict[str, int]:
    """Observaciones por provincia, sobre todo el histórico.

    Sin ventana temporal: con 225 filas en dos meses, recortar a los últimos N
    días dejaría el mapa casi vacío y no diría nada más honesto. La ventana se
    vuelve una pregunta real cuando exista volumen que la justifique, y ahí hay
    que elegir el N con una medición, no de memoria.
    """
    return {
        name: count
        for name, count in session.execute(
            select(Province.name, func.count())
            .join(PriceHistory, PriceHistory.province_id == Province.id)
            .group_by(Province.name)
        )
    }


@router.get("/", response_class=HTMLResponse)
def public_map(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse(
        request,
        "public_map.html",
        {"map": build_map(_rows_by_province(db))},
    )
