"""Las publicaciones de la fábrica y sus transiciones (F2 de
`docs/plan-fabrica-marketing.md`; reglas en
`docs/spec-slice-fabrica-publicaciones.md` §1.2).

    borrador ──aprobar──▶ aprobada ──marcar publicada (+ enlace)──▶ publicada
        │                    │
        └──── descartar ◀────┘   (descartar una aprobada borra su imagen)

Toda otra transición lanza `InvalidTransition`. Solo un borrador se edita.
Aprobar congela el contexto en `snapshot` y rasteriza el JPEG **desde el
snapshot**, nunca desde la base viva: si después se corrige un precio, lo
aprobado no cambia (regla 2 del plan).

No conoce FastAPI. El render y la carpeta de imágenes llegan como argumentos:
los tests no levantan Chromium ni escriben en el repo. Ninguna función hace
`commit`; eso lo decide quien llama.
"""

from __future__ import annotations

import shutil
import uuid
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from saimc.marketing.caption import CAPTION_MAX, build_caption
from saimc.marketing.context import BY_SLUG, CARD_TYPES, CardType, build_context
from saimc.marketing.html import card_html
from saimc.marketing.options import CardOptions, options_from_dict, options_to_dict, parse_options
from saimc.marketing.render import render_jpeg
from saimc.marketing.snapshot import freeze_context, thaw_context
from saimc.models.base import utc_now
from saimc.models.marketing_post import (
    MarketingPost,
    MarketingPostSlide,
    PostFormat,
    PostOrigin,
    PostStatus,
)
from saimc.progress import PHASE0_PROVINCES, pilot_product_names

SNAPSHOT_VERSION = 1

#: Días que se guardan borradores y descartadas. Una de exactamente este
#: tiempo todavía se queda; la de un día más, se va. Las publicadas, siempre.
RETENTION_DAYS = 30

#: Piezas de branding que se bajan tal cual desde «Hoy» y no son publicaciones.
FIXED_PIECES: frozenset[str] = frozenset(
    {"portada-facebook", "story-precalentamiento", "como-funciona", "og-compartir"}
)

#: Lo que el operador puede crear como publicación.
POSTABLE: tuple[CardType, ...] = tuple(t for t in CARD_TYPES if t.slug not in FIXED_PIECES)

_URL_MAX = 2000

Renderer = Callable[..., bytes]


class InvalidTransition(ValueError):
    """La publicación no está en un estado que permita lo pedido."""


class NoData(ValueError):
    """No hay con qué armar la tarjeta con esos parámetros."""


class InvalidUrl(ValueError):
    """El enlace del post no es http(s)."""


class InvalidParams(ValueError):
    """Producto o provincia fuera del foco."""


_STATUS_LABELS = {
    PostStatus.DRAFT: "borrador",
    PostStatus.APPROVED: "aprobada",
    PostStatus.PUBLISHED: "publicada",
    PostStatus.DISCARDED: "descartada",
}


def _require(post: MarketingPost, allowed: set[PostStatus], action: str) -> None:
    if post.status not in allowed:
        estado = _STATUS_LABELS.get(PostStatus(post.status), post.status)
        raise InvalidTransition(f"Una publicación {estado} no se puede {action}.")


def _kind(slide: MarketingPostSlide) -> CardType:
    return BY_SLUG[slide.card_slug]


def _data_through(slide: MarketingPostSlide) -> date:
    return date.fromisoformat(slide.params["data_through"])


def context_has_data(slug: str, context: dict | None) -> bool:
    """Si `context` alcanza para una pieza que diga algo. «Dato del día» arma
    siempre la tarjeta, con una fila «sin datos» por provincia; las otras ya
    devuelven `None` desde `build_context` cuando no hay con qué."""
    if context is None:
        return False
    if slug == "dato-del-dia":
        return any(row.has_data for row in context["card"].rows)
    return True


def _params(kind: CardType, product: str | None, province: str | None, through: date) -> dict:
    return {
        "product": product if kind.needs_product else None,
        "province": province if kind.needs_province else None,
        "data_through": through.isoformat(),
    }


# --- Lectura -----------------------------------------------------------------


def live_context(db: Session, slide: MarketingPostSlide) -> dict | None:
    """El contexto en vivo de la diapositiva, con tope en su fecha de corte."""
    kind = _kind(slide)
    contexto = build_context(
        kind, db, product=slide.params.get("product"), province=slide.params.get("province"),
        hoy=_data_through(slide),
    )
    return contexto if context_has_data(kind.slug, contexto) else None


def slide_context(db: Session, slide: MarketingPostSlide) -> dict | None:
    """Del snapshot si está aprobada; en vivo si es borrador."""
    if slide.snapshot is not None:
        return thaw_context(slide.snapshot["context"])
    return live_context(db, slide)


def slide_options(slide: MarketingPostSlide) -> CardOptions:
    fuente = slide.snapshot["options"] if slide.snapshot is not None else slide.options
    return options_from_dict(_kind(slide), fuente)


def slide_html(db: Session, slide: MarketingPostSlide) -> str | None:
    """La diapositiva pintada, o `None` si un borrador no tiene datos."""
    contexto = slide_context(db, slide)
    if contexto is None:
        return None
    return card_html(_kind(slide), contexto, slide_options(slide))


def _generated_caption(db: Session, post: MarketingPost) -> str:
    slide = post.slides[0]
    return build_caption(_kind(slide), slide_context(db, slide) or {})


def effective_caption(db: Session, post: MarketingPost) -> str:
    """El texto del post: el guardado, o el que sale de los datos."""
    if post.caption is not None:
        return post.caption
    return _generated_caption(db, post)


# --- Altas y edición ---------------------------------------------------------


def create_draft(
    db: Session,
    *,
    card_slug: str,
    planned_for: date,
    product: str | None,
    province: str | None,
    data_through: date,
    origin: PostOrigin = PostOrigin.OPERATOR,
    batch_slot: str | None = None,
    options: CardOptions | None = None,
    caption: str | None = None,
    now: datetime | None = None,
) -> MarketingPost:
    """Un borrador de una diapositiva. No hace `flush`: la restricción única
    del lote salta al guardar, donde quien llama la atiende."""
    kind = BY_SLUG.get(card_slug)
    if kind is None:
        raise ValueError(f"Tarjeta desconocida: {card_slug}")
    formato = PostFormat.STORY if kind.height > kind.width * 1.5 else PostFormat.SINGLE
    post = MarketingPost(
        format=formato,
        status=PostStatus.DRAFT,
        planned_for=planned_for,
        caption=caption,
        origin=origin,
        batch_slot=batch_slot,
        created_at=now or utc_now(),
    )
    post.slides.append(
        MarketingPostSlide(
            position=1,
            card_slug=kind.slug,
            params=_params(kind, product, province, data_through),
            options=options_to_dict(options or parse_options(kind, {})),
        )
    )
    db.add(post)
    return post


def _normalize_caption(raw: str | None) -> str | None:
    if raw is None:
        return None
    texto = raw.replace("\r\n", "\n").strip()
    return texto[:CAPTION_MAX] if texto else None


def save_draft(
    db: Session,
    post: MarketingPost,
    *,
    product: str | None,
    province: str | None,
    data_through: date,
    options: CardOptions,
    caption: str | None,
) -> None:
    """Guarda lo que se elige en el editor. Si el texto enviado es el que se
    habría generado con los parámetros **anteriores**, queda automático: así
    cambiar de producto no deja pegado el texto del producto viejo."""
    _require(post, {PostStatus.DRAFT}, "editar")
    slide = post.slides[0]
    kind = _kind(slide)
    if kind.needs_product and product not in pilot_product_names(db):
        raise InvalidParams(f"«{product}» no está en el foco.")
    if kind.needs_province and province not in PHASE0_PROVINCES:
        raise InvalidParams(f"«{province}» no es una provincia del foco.")

    generado = _normalize_caption(_generated_caption(db, post))
    texto = _normalize_caption(caption)
    post.caption = None if texto in (None, generado) else texto
    slide.params = _params(kind, product, province, data_through)
    slide.options = options_to_dict(options)


# --- Transiciones ------------------------------------------------------------


def _post_dir(images_dir: Path, post: MarketingPost) -> Path:
    return images_dir / str(post.id)


def approve(
    db: Session,
    post: MarketingPost,
    *,
    images_dir: Path,
    render: Renderer = render_jpeg,
    now: datetime | None = None,
) -> None:
    """Congela, rasteriza desde lo congelado y recién entonces cambia el
    estado. Si falta un dato (`NoData`) o Chromium (`RendererUnavailable`), no
    cambia nada: ni fila ni archivo."""
    _require(post, {PostStatus.DRAFT}, "aprobar")
    listas: list[tuple[MarketingPostSlide, dict[str, Any], bytes]] = []
    for slide in post.slides:
        kind = _kind(slide)
        contexto = live_context(db, slide)
        if contexto is None:
            raise NoData(
                f"Sin datos para «{kind.label}» hasta el {slide.params['data_through']}."
            )
        snapshot: dict[str, Any] = {
            "version": SNAPSHOT_VERSION,
            "card_slug": kind.slug,
            "data_through": slide.params["data_through"],
            "context": freeze_context(contexto),
            "options": options_to_dict(options_from_dict(kind, slide.options)),
        }
        # Lo que se rasteriza es el snapshot reconstruido, no `contexto`: si
        # algo no sobrevive a la ida y vuelta, se ve en la imagen aprobada.
        html = card_html(
            kind, thaw_context(snapshot["context"]), options_from_dict(kind, snapshot["options"])
        )
        listas.append((slide, snapshot, render(html, width=kind.width, height=kind.height)))

    primera, snapshot_primera, _ = listas[0]
    caption = post.caption
    if caption is None:
        caption = build_caption(_kind(primera), thaw_context(snapshot_primera["context"]))

    carpeta = _post_dir(images_dir, post)
    carpeta.mkdir(parents=True, exist_ok=True)
    for slide, snapshot, jpeg in listas:
        nombre = f"{slide.position}.jpg"
        (carpeta / nombre).write_bytes(jpeg)
        slide.snapshot = {**snapshot, "caption": caption}
        slide.image_path = f"{post.id}/{nombre}"
    post.caption = caption
    post.status = PostStatus.APPROVED
    post.approved_at = now or utc_now()


def _remove_images(images_dir: Path, post: MarketingPost) -> None:
    carpeta = _post_dir(images_dir, post)
    if carpeta.is_dir():
        shutil.rmtree(carpeta)


def discard(post: MarketingPost, *, images_dir: Path, now: datetime | None = None) -> None:
    """Descarta un borrador o una aprobada; la imagen se borra del disco."""
    _require(post, {PostStatus.DRAFT, PostStatus.APPROVED}, "descartar")
    _remove_images(images_dir, post)
    for slide in post.slides:
        slide.image_path = None
    post.status = PostStatus.DISCARDED
    post.discarded_at = now or utc_now()


def _valid_url(raw: str) -> str:
    url = (raw or "").strip()
    partes = urlparse(url)
    if partes.scheme not in ("http", "https") or not partes.netloc or len(url) > _URL_MAX:
        raise InvalidUrl("El enlace tiene que empezar con https:// (copialo desde el post).")
    return url


def mark_published(post: MarketingPost, url: str, *, now: datetime | None = None) -> None:
    _require(post, {PostStatus.APPROVED}, "marcar publicada")
    post.published_url = _valid_url(url)
    post.status = PostStatus.PUBLISHED
    post.published_at = now or utc_now()


def duplicate(
    db: Session, post: MarketingPost, *, today: date, now: datetime | None = None
) -> MarketingPost:
    """Un borrador nuevo para `today` con los mismos parámetros y opciones.
    El texto se copia solo si se había escrito a mano."""
    _require(
        post, {PostStatus.APPROVED, PostStatus.PUBLISHED, PostStatus.DISCARDED}, "duplicar"
    )
    slide = post.slides[0]
    kind = _kind(slide)
    caption = post.caption
    if caption is not None and slide.snapshot is not None:
        generado = build_caption(kind, thaw_context(slide.snapshot["context"]))
        if caption == generado:
            caption = None
    return create_draft(
        db,
        card_slug=kind.slug,
        planned_for=today,
        product=slide.params.get("product"),
        province=slide.params.get("province"),
        data_through=_data_through(slide),
        options=options_from_dict(kind, slide.options),
        caption=caption,
        now=now,
    )


# --- Retención ---------------------------------------------------------------


def purge_expired(
    db: Session, images_dir: Path, today: date, keep_days: int = RETENTION_DAYS
) -> list[uuid.UUID]:
    """Borra borradores (por `created_at`) y descartadas (por `discarded_at`)
    de más de `keep_days` días, con su carpeta de imágenes. Las publicadas y
    las aprobadas no se tocan."""
    candidatas = db.scalars(
        select(MarketingPost)
        .where(MarketingPost.status.in_([PostStatus.DRAFT, PostStatus.DISCARDED]))
        .order_by(MarketingPost.created_at)
    )
    borradas: list[uuid.UUID] = []
    for post in candidatas:
        referencia = post.discarded_at if post.status == PostStatus.DISCARDED else post.created_at
        if referencia is None or (today - referencia.date()).days <= keep_days:
            continue
        _remove_images(images_dir, post)
        borradas.append(post.id)
        db.delete(post)
    db.flush()
    return borradas
