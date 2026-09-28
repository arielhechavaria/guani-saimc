"""Tarjetas para redes sociales, con datos reales del sitio (`/marketing`).

Primer slice (2026-09-28): un solo formato, «Dato del día» — el más simple
y el más frecuente del calendario de la campaña (`docs/decisions.md`,
campaña de marketing 2026-09-26; fuentes de las 7 plantillas en
`marketing-design/project/`). No inventa ningún cómputo: reusa
`cells.compute_retail_ranges` y `card_view.build_cards`, el mismo cálculo
que ya pinta el tablero público — evita a propósito la trampa ya conocida
del promedio simple mezclando presentaciones («~1,3 millones» para arroz).

Sin `require_operator`: mismo criterio que `/control` (lectura del dato ya
público, sin mutar nada), a diferencia de `/catalog`/`/curation` que sí
cambian qué mide el sistema.
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from saimc.cells import compute_retail_ranges
from saimc.marketing.cards import DatoDelDiaCard, build_dato_del_dia
from saimc.marketing.render import RendererUnavailable, render_png
from saimc.progress import PHASE0_PROVINCES, pilot_product_names
from saimc.web.card_view import build_cards
from saimc.web.deps import get_db

router = APIRouter()

#: Placeholder mientras el dominio final no está decidido (pendiente en el
#: doc de campaña, 2026-09-26: «¿cuál será la dirección del sitio para poner
#: en las piezas?»). Cambiarlo es editar esta constante, no tocar la plantilla.
SITE_URL_PLACEHOLDER = "guani.cuba · próximamente"

#: 1080×1350 — tamaño de exportación de las 7 plantillas del canvas
#: (`marketing-design/project/canvas.json`), Instagram 4:5.
CARD_WIDTH = 1080
CARD_HEIGHT = 1350

#: Una semana, igual que el pilar «Dato del día» del calendario de campaña.
DEFAULT_WINDOW_DAYS = 7


def _build_card(db: Session, product_name: str, days: int) -> DatoDelDiaCard | None:
    """`None` si el producto no está en el foco — nunca inventa datos de uno
    que no se mide."""
    if product_name not in pilot_product_names(db):
        return None

    hoy = date.today()
    desde = hoy - timedelta(days=days - 1)
    report = compute_retail_ranges(db, days=days, hoy=hoy)
    ranges = [
        r for r in report.ranges
        if r.province_name in PHASE0_PROVINCES and not r.unconverted
    ]
    cards = build_cards(ranges)
    return build_dato_del_dia(
        cards,
        product_name=product_name,
        province_names=PHASE0_PROVINCES,
        week_from=desde,
        week_to=hoy,
    )


def _render_card_html(request: Request, card: DatoDelDiaCard) -> str:
    template = request.app.state.templates.get_template("marketing_dato_del_dia.html")
    return template.render(card=card, site_url=SITE_URL_PLACEHOLDER)


@router.get("/marketing", response_class=HTMLResponse)
def marketing(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """Elegir producto y ver la vista previa de «Dato del día» antes de bajarla."""
    products = pilot_product_names(db)
    selected = request.query_params.get("product") or (products[0] if products else "")
    days = int(request.query_params.get("days") or DEFAULT_WINDOW_DAYS)

    card = _build_card(db, selected, days) if selected else None
    return request.app.state.templates.TemplateResponse(
        request,
        "marketing.html",
        {
            "products": products,
            "selected": selected,
            "days": days,
            "card": card,
        },
    )


@router.get("/marketing/preview", response_class=HTMLResponse)
def marketing_preview(
    request: Request, product: str, db: Session = Depends(get_db), days: int = DEFAULT_WINDOW_DAYS
) -> HTMLResponse:
    """La tarjeta sola, sin el resto de la página — lo que carga el `<iframe>`
    de la vista previa y lo que `card.png` termina rasterizando."""
    card = _build_card(db, product, days)
    if card is None:
        return HTMLResponse("Producto fuera del foco.", status_code=404)
    return HTMLResponse(_render_card_html(request, card))


@router.get("/marketing/card.png")
def marketing_card_png(
    request: Request, product: str, db: Session = Depends(get_db), days: int = DEFAULT_WINDOW_DAYS
) -> Response:
    """La misma tarjeta de `/marketing/preview`, rasterizada.

    Playwright es opcional (`pyproject.toml`, extra `marketing`): sin el
    Chromium instalado, `RendererUnavailable` se traduce en un 503 con el
    mismo texto que explica cómo instalarlo — no una pila de Python cruda.
    """
    card = _build_card(db, product, days)
    if card is None:
        return HTMLResponse("Producto fuera del foco.", status_code=404)

    html = _render_card_html(request, card)
    try:
        png = render_png(html, width=CARD_WIDTH, height=CARD_HEIGHT)
    except RendererUnavailable as error:
        return HTMLResponse(str(error), status_code=503)

    filename = f"guani-dato-del-dia-{product.lower()}.png"
    return Response(
        content=png,
        media_type="image/png",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
