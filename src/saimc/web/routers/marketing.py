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

**Editor (F1 de `docs/plan-fabrica-marketing.md`).** Qué bloques se ven y dos
textos viajan en la URL (`saimc.marketing.options`): sin tabla ni estado. Esas
opciones solo llegan a la plantilla; las cifras salen de `build_context`, que
no las recibe. La sección «Instagram» se sacó de la página
(`docs/decisions.md`, 2026-10-18): sin app de Meta no hay token.
"""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from saimc.marketing.caption import CAPTION_MAX, build_caption
from saimc.marketing.context import (
    BY_SLUG,
    CARD_TYPES,
    SITE_URL_PLACEHOLDER,
    CardType,
    build_context,
)
from saimc.marketing.fonts import font_face_css
from saimc.marketing.options import CTA_MAX, SUBTITLE_MAX, CardOptions, parse_options
from saimc.marketing.render import RendererUnavailable, render_jpeg, render_png
from saimc.progress import PHASE0_PROVINCES, pilot_product_names
from saimc.web.auth import require_operator
from saimc.web.deps import get_db

router = APIRouter(dependencies=[Depends(require_operator)])


def _render_html(
    request: Request, kind: CardType, context: dict, options: CardOptions | None = None
) -> str:
    template = request.app.state.templates.get_template(kind.template)
    opciones = options or parse_options(kind, {})
    # Las fuentes viajan dentro del HTML: Playwright rasteriza sin acceso a /static.
    return template.render(
        site_url=SITE_URL_PLACEHOLDER,
        font_css=font_face_css(),
        show=opciones.show,
        subtitle=opciones.subtitle,
        cta=opciones.cta,
        **context,
    )


def _query(
    kind: CardType, product: str | None, province: str | None, options: CardOptions
) -> str:
    """Los parámetros que identifican la tarjeta tal como está en el editor."""
    conmutables = {f"b_{b.slug}" for b in kind.blocks if b.toggleable}
    pares = [("kind", kind.slug)]
    if kind.needs_product and product:
        pares.append(("product", product))
    if kind.needs_province and province:
        pares.append(("province", province))
    pares += [(k, v) for k, v in options.query_items() if k in conmutables or not k.startswith("b_")]
    return urlencode(pares)


def _editor_state(request: Request, db: Session) -> dict:
    """Todo lo que la página y la vista parcial necesitan del pedido."""
    products = pilot_product_names(db)
    params = request.query_params
    kind = BY_SLUG.get(params.get("kind") or "", CARD_TYPES[0])
    product = params.get("product") or (products[0] if products else "")
    province = params.get("province") or (PHASE0_PROVINCES[0] if PHASE0_PROVINCES else "")
    options = parse_options(kind, params)
    return {
        "card_types": CARD_TYPES,
        "kind": kind,
        "products": products,
        "provinces": PHASE0_PROVINCES,
        "selected_product": product,
        "selected_province": province,
        "ready": not kind.needs_product or product in products,
        "options": options,
        "customized": bool(options.subtitle or options.cta)
        or any(options.show[b.slug] != b.default for b in kind.blocks),
        "toggles": [b for b in kind.blocks if b.toggleable],
        "has_texts": bool(kind.blocks),
        "subtitle_max": SUBTITLE_MAX,
        "cta_max": CTA_MAX,
        "qs": _query(kind, product, province, options),
    }


@router.get("/marketing", response_class=HTMLResponse)
def marketing(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """El editor: elegir tarjeta, producto y provincia; apagar bloques, escribir
    los dos textos y llevar la imagen y el texto del post al teléfono."""
    estado = _editor_state(request, db)
    kind = estado["kind"]
    caption = ""
    if estado["ready"]:
        context = build_context(
            kind, db, product=estado["selected_product"], province=estado["selected_province"]
        )
        caption = build_caption(kind, context or {})
    return request.app.state.templates.TemplateResponse(
        request, "marketing.html", {**estado, "caption": caption, "caption_max": CAPTION_MAX}
    )


@router.get("/marketing/vista", response_class=HTMLResponse)
def marketing_vista(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """Solo la vista previa y sus botones: lo que HTMX cambia al tocar un
    interruptor o escribir un texto. Deja la URL de la página al día para que
    recargar no pierda lo elegido."""
    estado = _editor_state(request, db)
    respuesta: HTMLResponse = request.app.state.templates.TemplateResponse(
        request, "_marketing_vista.html", estado
    )
    respuesta.headers["HX-Replace-Url"] = f"/marketing?{estado['qs']}"
    return respuesta


def _armar(
    request: Request, db: Session, kind_slug: str, product: str | None, province: str | None
) -> tuple[CardType, str] | HTMLResponse:
    """El HTML de la tarjeta con las opciones de la URL, o la respuesta de error."""
    kind = BY_SLUG.get(kind_slug)
    if kind is None:
        return HTMLResponse("Tarjeta desconocida.", status_code=404)
    context = build_context(kind, db, product=product, province=province)
    if context is None:
        return HTMLResponse("Sin datos para armar esta tarjeta todavía.", status_code=404)
    return kind, _render_html(request, kind, context, parse_options(kind, request.query_params))


@router.get("/marketing/preview/{kind_slug}", response_class=HTMLResponse)
def marketing_preview(
    kind_slug: str,
    request: Request,
    db: Session = Depends(get_db),
    product: str | None = None,
    province: str | None = None,
) -> HTMLResponse:
    """La tarjeta sola, sin el resto de la página — lo que carga el `<iframe>`
    de la vista previa y lo que las descargas terminan rasterizando."""
    armada = _armar(request, db, kind_slug, product, province)
    if isinstance(armada, HTMLResponse):
        return armada
    return HTMLResponse(armada[1])


def _imagen(
    request: Request,
    db: Session,
    kind_slug: str,
    product: str | None,
    province: str | None,
    *,
    extension: str,
) -> Response:
    armada = _armar(request, db, kind_slug, product, province)
    if isinstance(armada, HTMLResponse):
        return armada
    kind, html = armada
    render = render_jpeg if extension == "jpg" else render_png
    try:
        imagen = render(html, width=kind.width, height=kind.height)
    except RendererUnavailable as error:
        return HTMLResponse(str(error), status_code=503)

    sufijo = f"-{product.lower()}" if kind.needs_product and product else ""
    return Response(
        content=imagen,
        media_type="image/jpeg" if extension == "jpg" else "image/png",
        headers={
            "Content-Disposition": f'attachment; filename="guani-{kind.slug}{sufijo}.{extension}"'
        },
    )


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
    return _imagen(request, db, kind_slug, product, province, extension="png")


@router.get("/marketing/card/{kind_slug}.jpg")
def marketing_card_jpg(
    kind_slug: str,
    request: Request,
    db: Session = Depends(get_db),
    product: str | None = None,
    province: str | None = None,
) -> Response:
    """Lo mismo en JPEG sRGB: es lo que se lleva al teléfono y se comparte."""
    return _imagen(request, db, kind_slug, product, province, extension="jpg")
