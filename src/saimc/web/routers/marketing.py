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

**Fábrica (F2, `docs/spec-slice-fabrica-publicaciones.md`).** `/marketing` es
«Hoy»: las publicaciones del día con su estado. `/marketing/post/<id>` es el
editor de F1, que ahora guarda parámetros, opciones y texto en la publicación;
las transiciones (aprobar, descartar, marcar publicada, duplicar) son POST que
llaman a `saimc.marketing.posts`. Lo que se comparte es siempre el JPEG
congelado al aprobar. Las rutas sin estado `/marketing/preview/{slug}` y
`/marketing/card/{slug}.{png,jpg}` quedan para las piezas fijas. La sección
«Instagram» se sacó de la página (`docs/decisions.md`, 2026-10-18).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from saimc.config import get_settings
from saimc.marketing.caption import CAPTION_MAX
from saimc.marketing.context import (
    BY_SLUG,
    CARD_TYPES,
    CardType,
    build_context,
)
from saimc.marketing.daily import cards_for_day, havana_today, to_havana
from saimc.marketing.html import card_html
from saimc.marketing.options import CTA_MAX, SUBTITLE_MAX, parse_options
from saimc.marketing.posts import (
    FIXED_PIECES,
    POSTABLE,
    InvalidParams,
    InvalidTransition,
    InvalidUrl,
    NoData,
    approve,
    create_draft,
    discard,
    duplicate,
    effective_caption,
    mark_published,
    save_draft,
    slide_html,
    slide_options,
)
from saimc.marketing.render import RendererUnavailable, render_jpeg, render_png
from saimc.models.marketing_post import MarketingPost, MarketingPostSlide, PostStatus
from saimc.progress import PHASE0_PROVINCES, pilot_product_names
from saimc.web.auth import require_operator
from saimc.web.deps import get_db

router = APIRouter(dependencies=[Depends(require_operator)])

STATUS_LABELS: dict[str, str] = {
    PostStatus.DRAFT: "Borrador",
    PostStatus.APPROVED: "Aprobada",
    PostStatus.PUBLISHED: "Publicada",
    PostStatus.DISCARDED: "Descartada",
}


# --- Dependencias que los tests reemplazan -----------------------------------


def get_today() -> date:
    """El día de «Hoy»: el de La Habana, no el del servidor (corre en UTC)."""
    return havana_today()


def get_marketing_dir() -> Path:
    return get_settings().marketing_dir


def get_jpeg_renderer() -> Callable[..., bytes]:
    return render_jpeg


def _images_dir(marketing_dir: Path) -> Path:
    return marketing_dir / "posts"


# --- Piezas fijas y vistas previas sin estado --------------------------------

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
    return kind, card_html(kind, context, parse_options(kind, request.query_params))


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


# --- Hoy ---------------------------------------------------------------------


def _post_card(post: MarketingPost) -> dict[str, Any]:
    slide = post.slides[0]
    kind = BY_SLUG[slide.card_slug]
    detalle = " · ".join(
        x for x in (slide.params.get("product"), slide.params.get("province")) if x
    )
    return {
        "post": post,
        "kind": kind,
        "label": STATUS_LABELS.get(post.status, post.status),
        "detail": detalle,
        "frozen": slide.image_path is not None,
    }


@router.get("/marketing", response_class=HTMLResponse)
def marketing_hoy(
    request: Request, db: Session = Depends(get_db), today: date = Depends(get_today)
) -> HTMLResponse:
    """Lo que toca hoy: las publicaciones del día, lo que pide el calendario y
    todavía no existe, y las piezas fijas plegadas abajo."""
    posts = [
        p
        for p in db.scalars(
            select(MarketingPost)
            .where(MarketingPost.planned_for == today)
            .order_by(MarketingPost.created_at)
        )
        if p.slides
    ]
    presentes = {p.slides[0].card_slug for p in posts if p.status != PostStatus.DISCARDED}
    faltan = [BY_SLUG[slug] for slug in cards_for_day(today) if slug not in presentes]
    respuesta: HTMLResponse = request.app.state.templates.TemplateResponse(
        request,
        "marketing.html",
        {
            "today": today,
            "cards": [_post_card(p) for p in posts],
            "missing": faltan,
            "postable": POSTABLE,
            "fixed": [t for t in CARD_TYPES if t.slug in FIXED_PIECES],
        },
    )
    return respuesta


@router.post("/marketing/post")
async def marketing_post_create(
    request: Request, db: Session = Depends(get_db), today: date = Depends(get_today)
) -> RedirectResponse:
    """Un borrador del operador para hoy, con el primer producto y la primera
    provincia del foco; se ajustan en el editor."""
    form = await request.form()
    kind = BY_SLUG.get(str(form.get("kind") or ""))
    if kind is None or kind.slug in FIXED_PIECES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tarjeta desconocida.")
    productos = pilot_product_names(db)
    post = create_draft(
        db,
        card_slug=kind.slug,
        planned_for=today,
        product=productos[0] if productos else None,
        province=PHASE0_PROVINCES[0] if PHASE0_PROVINCES else None,
        data_through=today,
    )
    db.commit()
    return RedirectResponse(f"/marketing/post/{post.id}", status_code=status.HTTP_303_SEE_OTHER)


# --- Editor ------------------------------------------------------------------


def _load(db: Session, post_id: str) -> MarketingPost:
    try:
        clave = uuid.UUID(post_id)
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Publicación desconocida.") from None
    post = db.get(MarketingPost, clave)
    if post is None or not post.slides:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Publicación desconocida.")
    return post


def _revision(slide: MarketingPostSlide) -> str:
    estado = [slide.params, slide.options, slide.snapshot is not None]
    return hashlib.blake2s(json.dumps(estado, sort_keys=True).encode(), digest_size=5).hexdigest()


def _editor_state(db: Session, post: MarketingPost, today: date) -> dict[str, Any]:
    slide = post.slides[0]
    kind = BY_SLUG[slide.card_slug]
    return {
        "post": post,
        "slide": slide,
        "kind": kind,
        "status_label": STATUS_LABELS.get(post.status, post.status),
        "editable": post.status == PostStatus.DRAFT,
        "ready": slide_html(db, slide) is not None,
        "products": pilot_product_names(db),
        "provinces": PHASE0_PROVINCES,
        "params": slide.params,
        "today": today,
        "options": slide_options(slide),
        "toggles": [b for b in kind.blocks if b.toggleable],
        "has_texts": bool(kind.blocks),
        "subtitle_max": SUBTITLE_MAX,
        "cta_max": CTA_MAX,
        "caption": effective_caption(db, post),
        "caption_max": CAPTION_MAX,
        # `v` cambia con lo guardado: el iframe que HTMX vuelve a insertar no
        # puede salir del caché con la versión anterior.
        "preview_url": f"/marketing/post/{post.id}/preview/{slide.position}?v={_revision(slide)}",
        "jpg_url": (
            f"/marketing/post/{post.id}/slide/{slide.position}.jpg" if slide.image_path else None
        ),
        "jpg_name": f"guani-{kind.slug}-{post.planned_for.isoformat()}.jpg",
        # La base guarda UTC; el operador lee la hora de Cuba.
        "approved_local": _local(post.approved_at),
        "published_local": _local(post.published_at),
    }


def _local(moment: datetime | None) -> str:
    return to_havana(moment).strftime("%d/%m/%Y %H:%M") if moment else "—"


def _editor(
    request: Request,
    db: Session,
    post: MarketingPost,
    today: date,
    *,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    respuesta: HTMLResponse = request.app.state.templates.TemplateResponse(
        request,
        "marketing_post.html",
        {**_editor_state(db, post, today), "error": error},
        status_code=status_code,
    )
    return respuesta


@router.get("/marketing/post/{post_id}", response_class=HTMLResponse)
def marketing_post_editor(
    post_id: str,
    request: Request,
    db: Session = Depends(get_db),
    today: date = Depends(get_today),
) -> HTMLResponse:
    return _editor(request, db, _load(db, post_id), today)


def _parse_through(raw: object, today: date) -> date:
    try:
        elegido = date.fromisoformat(str(raw))
    except ValueError:
        raise InvalidParams("La fecha de corte no es una fecha.") from None
    # Una fecha futura no agrega datos: se queda en hoy.
    return min(elegido, today)


def _save_from_form(db: Session, post: MarketingPost, form: Any, today: date) -> None:
    slide = post.slides[0]
    kind = BY_SLUG[slide.card_slug]
    save_draft(
        db,
        post,
        product=str(form.get("product") or "") or slide.params.get("product"),
        province=str(form.get("province") or "") or slide.params.get("province"),
        data_through=_parse_through(
            form.get("data_through") or slide.params["data_through"], today
        ),
        options=parse_options(kind, form),
        caption=form.get("caption"),
    )


@router.post("/marketing/post/{post_id}", response_model=None)
async def marketing_post_save(
    post_id: str,
    request: Request,
    db: Session = Depends(get_db),
    today: date = Depends(get_today),
) -> Response:
    """Guarda el borrador. Con HTMX devuelve solo la vista previa; sin HTMX
    vuelve al editor."""
    post = _load(db, post_id)
    form = await request.form()
    try:
        _save_from_form(db, post, form, today)
    except InvalidTransition as error:
        db.rollback()
        return HTMLResponse(str(error), status_code=status.HTTP_409_CONFLICT)
    except InvalidParams as error:
        db.rollback()
        return _editor(request, db, post, today, error=str(error), status_code=422)
    db.commit()
    if request.headers.get("HX-Request"):
        vista: HTMLResponse = request.app.state.templates.TemplateResponse(
            request, "_marketing_vista.html", _editor_state(db, post, today)
        )
        return vista
    return _back(post)


def _slide(post: MarketingPost, position: int) -> MarketingPostSlide | None:
    return next((s for s in post.slides if s.position == position), None)


@router.get("/marketing/post/{post_id}/preview/{position}", response_class=HTMLResponse)
def marketing_post_preview(
    post_id: str, position: int, db: Session = Depends(get_db)
) -> HTMLResponse:
    """La diapositiva sola (lo que carga el `<iframe>`): en vivo si es un
    borrador, desde el snapshot si ya se aprobó."""
    slide = _slide(_load(db, post_id), position)
    if slide is None:
        return HTMLResponse("Diapositiva desconocida.", status_code=404)
    html = slide_html(db, slide)
    if html is None:
        return HTMLResponse("Sin datos para armar esta tarjeta todavía.", status_code=404)
    return HTMLResponse(html)


@router.get("/marketing/post/{post_id}/slide/{position}.jpg", response_model=None)
def marketing_post_jpg(
    post_id: str,
    position: int,
    db: Session = Depends(get_db),
    marketing_dir: Path = Depends(get_marketing_dir),
) -> Response:
    """El JPEG congelado al aprobar. Un borrador no tiene: lo que se comparte
    es siempre lo aprobado."""
    slide = _slide(_load(db, post_id), position)
    if slide is None or slide.image_path is None:
        return HTMLResponse("Esta publicación todavía no tiene imagen aprobada.", status_code=404)
    archivo = _images_dir(marketing_dir) / slide.image_path
    if not archivo.is_file():
        return HTMLResponse("La imagen no está en el disco.", status_code=404)
    return FileResponse(archivo, media_type="image/jpeg")


# --- Transiciones ------------------------------------------------------------


def _back(post: MarketingPost) -> RedirectResponse:
    return RedirectResponse(f"/marketing/post/{post.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/marketing/post/{post_id}/aprobar", response_model=None)
async def marketing_post_approve(
    post_id: str,
    request: Request,
    db: Session = Depends(get_db),
    today: date = Depends(get_today),
    marketing_dir: Path = Depends(get_marketing_dir),
    render: Callable[..., bytes] = Depends(get_jpeg_renderer),
) -> Response:
    """Aprueba. Si llega el formulario del editor (el botón usa `formaction`),
    guarda primero lo que haya: así no se pierde un texto recién escrito."""
    post = _load(db, post_id)
    form = await request.form()
    try:
        if "data_through" in form:
            _save_from_form(db, post, form, today)
        approve(db, post, images_dir=_images_dir(marketing_dir), render=render)
    except InvalidTransition as error:
        db.rollback()
        return HTMLResponse(str(error), status_code=status.HTTP_409_CONFLICT)
    except (NoData, InvalidParams) as error:
        db.rollback()
        return _editor(request, db, post, today, error=str(error), status_code=422)
    except RendererUnavailable as error:
        db.rollback()
        return HTMLResponse(str(error), status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    db.commit()
    return _back(post)


@router.post("/marketing/post/{post_id}/descartar", response_model=None)
def marketing_post_discard(
    post_id: str,
    db: Session = Depends(get_db),
    marketing_dir: Path = Depends(get_marketing_dir),
) -> Response:
    post = _load(db, post_id)
    try:
        discard(post, images_dir=_images_dir(marketing_dir))
    except InvalidTransition as error:
        db.rollback()
        return HTMLResponse(str(error), status_code=status.HTTP_409_CONFLICT)
    db.commit()
    return RedirectResponse("/marketing", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/marketing/post/{post_id}/publicada", response_model=None)
async def marketing_post_published(
    post_id: str,
    request: Request,
    db: Session = Depends(get_db),
    today: date = Depends(get_today),
) -> Response:
    post = _load(db, post_id)
    form = await request.form()
    try:
        mark_published(post, str(form.get("url") or ""))
    except InvalidTransition as error:
        db.rollback()
        return HTMLResponse(str(error), status_code=status.HTTP_409_CONFLICT)
    except InvalidUrl as error:
        db.rollback()
        return _editor(request, db, post, today, error=str(error), status_code=422)
    db.commit()
    return _back(post)


@router.post("/marketing/post/{post_id}/duplicar", response_model=None)
def marketing_post_duplicate(
    post_id: str, db: Session = Depends(get_db), today: date = Depends(get_today)
) -> Response:
    post = _load(db, post_id)
    try:
        copia = duplicate(db, post, today=today)
    except InvalidTransition as error:
        db.rollback()
        return HTMLResponse(str(error), status_code=status.HTTP_409_CONFLICT)
    db.commit()
    return _back(copia)
