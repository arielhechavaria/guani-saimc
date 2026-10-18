"""Tarjetas para redes sociales, con datos reales del sitio (`/marketing`).

Las 7 plantillas del canvas de campaña (`docs/decisions.md`, campaña de
marketing 2026-09-26; fuentes en `marketing-design/project/`) cuentan 9
tarjetas postables (el tablero «Identidad» es referencia de diseño, no una
pieza). No inventa ningún cómputo: reusa `cells.py`/`card_view.py`/
`price_charts.py`, el mismo cálculo que ya pinta el tablero público — evita
a propósito la trampa ya conocida del promedio simple mezclando
presentaciones («~1,3 millones» para arroz).

**Privado.** Todo el router —la página, las vistas previas y los PNG— va
detrás de `require_operator`, la misma contraseña de `/curation`
(`docs/decisions.md`, 2026-10-05). Hasta ese día estuvo abierto «con el mismo
criterio que `/control`», y ese criterio cayó junto con el de `/control`. Lo
que muestra es dato ya público, pero es una herramienta del operador y cada
PNG levanta un Chromium en el servidor.

Debajo de las tarjetas, la sección «Instagram» muestra cómo rinden las
publicaciones de la cuenta propia (`docs/spec-slice-instagram.md`), con lo que
guarda el lote `saimc.instagram.sync`.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from saimc.instagram.panel import load_panel
from saimc.marketing.context import (
    BY_SLUG,
    CARD_TYPES,
    SITE_URL_PLACEHOLDER,
    CardType,
    build_context,
)
from saimc.marketing.fonts import font_face_css
from saimc.marketing.render import RendererUnavailable, render_png
from saimc.progress import PHASE0_PROVINCES, pilot_product_names
from saimc.web.auth import require_operator
from saimc.web.deps import get_db

router = APIRouter(dependencies=[Depends(require_operator)])


def _render_html(request: Request, kind: CardType, context: dict) -> str:
    template = request.app.state.templates.get_template(kind.template)
    # Las fuentes viajan dentro del HTML: Playwright rasteriza sin acceso a /static.
    return template.render(site_url=SITE_URL_PLACEHOLDER, font_css=font_face_css(), **context)


@router.get("/marketing", response_class=HTMLResponse)
def marketing(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """Elegir tarjeta (y producto/provincia si hace falta) antes de bajarla."""
    products = pilot_product_names(db)
    kind_slug = request.query_params.get("kind") or CARD_TYPES[0].slug
    kind = BY_SLUG.get(kind_slug, CARD_TYPES[0])
    product = request.query_params.get("product") or (products[0] if products else "")
    province = request.query_params.get("province") or (
        PHASE0_PROVINCES[0] if PHASE0_PROVINCES else ""
    )

    ready = not kind.needs_product or product in products
    return request.app.state.templates.TemplateResponse(
        request,
        "marketing.html",
        {
            "card_types": CARD_TYPES,
            "kind": kind,
            "products": products,
            "provinces": PHASE0_PROVINCES,
            "selected_product": product,
            "selected_province": province,
            "ready": ready,
            # Lo que dejó el lote de Instagram; la página nunca llama a Meta.
            "instagram": load_panel(db, now=datetime.now(UTC)),
        },
    )


@router.get("/marketing/preview/{kind_slug}", response_class=HTMLResponse)
def marketing_preview(
    kind_slug: str,
    request: Request,
    db: Session = Depends(get_db),
    product: str | None = None,
    province: str | None = None,
) -> HTMLResponse:
    """La tarjeta sola, sin el resto de la página — lo que carga el `<iframe>`
    de la vista previa y lo que `card.png` termina rasterizando."""
    kind = BY_SLUG.get(kind_slug)
    if kind is None:
        return HTMLResponse("Tarjeta desconocida.", status_code=404)
    context = build_context(kind, db, product=product, province=province)
    if context is None:
        return HTMLResponse("Sin datos para armar esta tarjeta todavía.", status_code=404)
    return HTMLResponse(_render_html(request, kind, context))


@router.get("/marketing/card/{kind_slug}.png")
def marketing_card_png(
    kind_slug: str,
    request: Request,
    db: Session = Depends(get_db),
    product: str | None = None,
    province: str | None = None,
) -> Response:
    """La misma tarjeta de `/marketing/preview/{kind_slug}`, rasterizada.

    Playwright es opcional (`pyproject.toml`, extra `marketing`): sin el
    Chromium instalado, `RendererUnavailable` se traduce en un 503 con el
    mismo texto que explica cómo instalarlo — no una pila de Python cruda.
    """
    kind = BY_SLUG.get(kind_slug)
    if kind is None:
        return HTMLResponse("Tarjeta desconocida.", status_code=404)
    context = build_context(kind, db, product=product, province=province)
    if context is None:
        return HTMLResponse("Sin datos para armar esta tarjeta todavía.", status_code=404)

    html = _render_html(request, kind, context)
    try:
        png = render_png(html, width=kind.width, height=kind.height)
    except RendererUnavailable as error:
        return HTMLResponse(str(error), status_code=503)

    sufijo = f"-{product.lower()}" if kind.needs_product and product else ""
    filename = f"guani-{kind.slug}{sufijo}.png"
    return Response(
        content=png,
        media_type="image/png",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
