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

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from saimc.cells import RetailObservation, compute_retail_observations, compute_retail_ranges
from saimc.instagram.panel import load_panel
from saimc.marketing.cards import (
    build_como_se_movio,
    build_dato_del_dia,
    build_de_donde_sale,
)
from saimc.marketing.fonts import font_face_css
from saimc.marketing.render import RendererUnavailable, render_png
from saimc.progress import PHASE0_PROVINCES, pilot_product_names
from saimc.web.auth import require_operator
from saimc.web.card_view import build_cards
from saimc.web.deps import get_db
from saimc.web.price_charts import build_month_vs_month

router = APIRouter(dependencies=[Depends(require_operator)])

#: Placeholder mientras el dominio final no está decidido (pendiente en el
#: doc de campaña, 2026-09-26: «¿cuál será la dirección del sitio para poner
#: en las piezas?»). Cambiarlo es editar esta constante, no tocar cada plantilla.
SITE_URL_PLACEHOLDER = "guani.cuba · próximamente"

#: Una semana, igual que el pilar «Dato del día»/«Cómo se movió» del calendario.
DEFAULT_WINDOW_DAYS = 7

#: «De dónde sale este precio» mira más atrás a propósito (es la tarjeta que
#: explica el método, no la del ritmo semanal): con 7 días muchas veces no
#: hay ni 4 anuncios que listar.
LOOKBACK_WINDOW_DAYS = 30


@dataclass(frozen=True)
class CardType:
    slug: str
    label: str
    template: str
    width: int
    height: int
    needs_product: bool = False
    needs_province: bool = False


CARD_TYPES: tuple[CardType, ...] = (
    CardType(
        "dato-del-dia", "Dato del día", "marketing_dato_del_dia.html",
        1080, 1350, needs_product=True,
    ),
    CardType(
        "como-se-movio", "Cómo se movió", "marketing_como_se_movio.html",
        1080, 1350, needs_product=True,
    ),
    CardType(
        "de-donde-sale", "De dónde sale este precio", "marketing_de_donde_sale.html",
        1080, 1350, needs_product=True, needs_province=True,
    ),
    CardType(
        "carrusel-portada", "Carrusel · portada", "marketing_carrusel_portada.html", 1080, 1350
    ),
    CardType(
        "carrusel-consejo", "Carrusel · cómo leer los precios",
        "marketing_carrusel_consejo.html", 1080, 1350,
    ),
    CardType(
        "story-precalentamiento", "Story de precalentamiento", "marketing_story.html", 1080, 1920
    ),
    CardType(
        "portada-facebook", "Portada de Facebook", "marketing_portada_facebook.html", 1640, 624
    ),
    CardType(
        "como-funciona", "De los grupos a la cifra", "marketing_como_funciona.html", 1080, 1350
    ),
    CardType(
        "carrusel-cierre", "Carrusel · cierre", "marketing_carrusel_cierre.html", 1080, 1350
    ),
    # No es para publicar: es la imagen que ven WhatsApp, Telegram y X al
    # pegar un enlace del sitio (`og:image`). Se rasteriza una vez con
    # `tools/build_seo_assets.py` y se sirve desde `/static/seo/og-guani.png`.
    CardType("og-compartir", "Vista previa al compartir", "marketing_og_compartir.html", 1200, 630),
)

_BY_SLUG: dict[str, CardType] = {t.slug: t for t in CARD_TYPES}


def _build_context(
    kind: CardType, db: Session, *, product: str | None, province: str | None
) -> dict | None:
    """El contexto para la plantilla de `kind`, o `None` si el pedido no se
    puede armar (producto fuera del foco, o sin ese dato todavía).

    Cada rama reusa el mismo cómputo que ya usa la vista pública — nunca un
    promedio propio (`docs/decisions.md`, slice de marketing)."""
    products = pilot_product_names(db)
    if kind.needs_product and product not in products:
        return None

    if kind.slug == "dato-del-dia":
        assert product is not None
        hoy = date.today()
        desde = hoy - timedelta(days=DEFAULT_WINDOW_DAYS - 1)
        report = compute_retail_ranges(db, days=DEFAULT_WINDOW_DAYS, hoy=hoy)
        ranges = [
            r for r in report.ranges
            if r.province_name in PHASE0_PROVINCES and not r.unconverted
        ]
        cards = build_cards(ranges)
        dato = build_dato_del_dia(
            cards, product_name=product, province_names=PHASE0_PROVINCES,
            week_from=desde, week_to=hoy,
        )
        return {"card": dato}

    if kind.slug == "como-se-movio":
        assert product is not None
        hoy = date.today()
        ventana_desde = hoy - timedelta(days=DEFAULT_WINDOW_DAYS)
        actual = compute_retail_observations(db, days=DEFAULT_WINDOW_DAYS, hoy=hoy).observations
        previa_completa = compute_retail_observations(
            db, days=2 * DEFAULT_WINDOW_DAYS, hoy=hoy
        ).observations
        previa = [o for o in previa_completa if o.day < ventana_desde]

        def _en_foco(o: RetailObservation) -> bool:
            return o.product_name == product and o.province_name in PHASE0_PROVINCES

        slopes = build_month_vs_month(
            [o for o in actual if _en_foco(o)], [o for o in previa if _en_foco(o)],
            window_days=DEFAULT_WINDOW_DAYS, hoy=hoy,
        )
        movio = build_como_se_movio(slopes, product_name=product, province_names=PHASE0_PROVINCES)
        if movio is None:
            return None
        return {"card": movio}

    if kind.slug == "de-donde-sale":
        assert product is not None
        if province not in PHASE0_PROVINCES:
            return None
        assert province is not None
        observaciones = compute_retail_observations(db, days=LOOKBACK_WINDOW_DAYS).observations
        donde = build_de_donde_sale(observaciones, product_name=product, province_name=province)
        if donde is None:
            return None
        return {"card": donde}

    if kind.slug in ("story-precalentamiento", "portada-facebook"):
        return {"products": products}

    # Las de puro branding (portada, consejo, cómo funciona, cierre) no
    # necesitan nada más que el sitio, que ya se agrega en `_render_html`.
    return {}


def _render_html(request: Request, kind: CardType, context: dict) -> str:
    template = request.app.state.templates.get_template(kind.template)
    # Las fuentes viajan dentro del HTML: Playwright rasteriza sin acceso a /static.
    return template.render(site_url=SITE_URL_PLACEHOLDER, font_css=font_face_css(), **context)


@router.get("/marketing", response_class=HTMLResponse)
def marketing(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """Elegir tarjeta (y producto/provincia si hace falta) antes de bajarla."""
    products = pilot_product_names(db)
    kind_slug = request.query_params.get("kind") or CARD_TYPES[0].slug
    kind = _BY_SLUG.get(kind_slug, CARD_TYPES[0])
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
    kind = _BY_SLUG.get(kind_slug)
    if kind is None:
        return HTMLResponse("Tarjeta desconocida.", status_code=404)
    context = _build_context(kind, db, product=product, province=province)
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
    kind = _BY_SLUG.get(kind_slug)
    if kind is None:
        return HTMLResponse("Tarjeta desconocida.", status_code=404)
    context = _build_context(kind, db, product=product, province=province)
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
