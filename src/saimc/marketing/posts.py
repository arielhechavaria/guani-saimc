"""Las publicaciones de la fábrica y sus transiciones (F2 de
`docs/plan-fabrica-marketing.md`; reglas en
`docs/spec-slice-fabrica-publicaciones.md` §1.2).

    borrador ──aprobar──▶ aprobada ──marcar publicada (+ enlace)──▶ publicada
        │                    │
        └──── descartar ◀────┘   (descartar una aprobada borra su imagen)

Toda otra transición lanza `InvalidTransition`. Solo un borrador se edita.
Un carrusel (F3) es la misma publicación con varias diapositivas: se agregan,
se quitan y se reordenan con botones mientras es borrador, hasta
`MAX_SLIDES` y todas del mismo tamaño.
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
from collections.abc import Callable, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from saimc.marketing.caption import CAPTION_MAX, build_caption, datos_caption, with_hashtags
from saimc.marketing.context import BY_SLUG, CARD_TYPES, CardType, build_context
from saimc.marketing.html import card_html
from saimc.marketing.lessons import LESSON_CARD, Lesson
from saimc.marketing.options import (
    CardOptions,
    options_from_dict,
    options_to_dict,
    parse_options,
)
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

#: Lo que admite Instagram en un carrusel.
MAX_SLIDES = 10

Renderer = Callable[..., bytes]


class InvalidTransition(ValueError):
    """La publicación no está en un estado que permita lo pedido."""


class NoData(ValueError):
    """No hay con qué armar la tarjeta con esos parámetros."""


class InvalidUrl(ValueError):
    """El enlace del post no es http(s)."""


class InvalidParams(ValueError):
    """Producto o provincia fuera del foco."""


class InvalidSlide(ValueError):
    """La diapositiva pedida no entra: tope, tamaño, plantilla o posición."""


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


def slide_counter(slide: MarketingPostSlide) -> str | None:
    """«2 / 5» si la diapositiva es parte de un carrusel; `None` si va sola.
    No se congela en el snapshot: aprobada, ni la posición ni el total cambian."""
    total = len(slide.post.slides)
    return f"{slide.position} / {total}" if total > 1 else None


def slide_html(db: Session, slide: MarketingPostSlide) -> str | None:
    """La diapositiva pintada, o `None` si un borrador no tiene datos."""
    contexto = slide_context(db, slide)
    if contexto is None:
        return None
    return card_html(_kind(slide), contexto, slide_options(slide), slide_counter(slide))


def caption_slide(post: MarketingPost) -> MarketingPostSlide:
    """De qué diapositiva sale el texto automático: la primera con datos. En
    un carrusel la portada no dice nada; la de «Dato del día», sí."""
    return next((s for s in post.slides if _kind(s).needs_product), post.slides[0])


def _datos_caption(post: MarketingPost) -> str | None:
    """El texto del carrusel de datos (abre con `datos-portada`): sale de los
    nombres de producto de sus diapositivas, que `params` ya fija. `None` si
    la publicación es otra cosa."""
    if post.slides[0].card_slug != "datos-portada":
        return None
    return datos_caption([s.params["product"] for s in post.slides if s.params.get("product")])


def _generated_caption(db: Session, post: MarketingPost) -> str:
    datos = _datos_caption(post)
    if datos is not None:
        return datos
    slide = caption_slide(post)
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


def _postable_kind(card_slug: str) -> CardType:
    kind = BY_SLUG.get(card_slug)
    if kind is None or kind.slug in FIXED_PIECES:
        raise InvalidSlide(f"«{card_slug}» no es una tarjeta que se pueda publicar.")
    return kind


def create_carousel_draft(
    db: Session,
    *,
    card_slugs: Sequence[str],
    planned_for: date,
    product: str | None,
    province: str | None,
    data_through: date,
    products: Sequence[str | None] | None = None,
    origin: PostOrigin = PostOrigin.OPERATOR,
    batch_slot: str | None = None,
    now: datetime | None = None,
) -> MarketingPost:
    """Un borrador de carrusel con `card_slugs` en ese orden. Producto y
    provincia van a las diapositivas que los usan; las de branding no llevan.
    `products`, si se pasa, da un producto por diapositiva (mismo largo que
    `card_slugs`) en lugar de `product` para todas. Como `create_draft`, no
    hace `flush`."""
    if not 1 <= len(card_slugs) <= MAX_SLIDES:
        raise InvalidSlide(f"Un carrusel lleva de 1 a {MAX_SLIDES} diapositivas.")
    if products is not None and len(products) != len(card_slugs):
        raise InvalidSlide("Hace falta un producto por diapositiva.")
    kinds = [_postable_kind(slug) for slug in card_slugs]
    if len({(k.width, k.height) for k in kinds}) > 1:
        raise InvalidSlide("Todas las diapositivas de un carrusel tienen el mismo tamaño.")
    post = MarketingPost(
        format=PostFormat.CAROUSEL,
        status=PostStatus.DRAFT,
        planned_for=planned_for,
        origin=origin,
        batch_slot=batch_slot,
        created_at=now or utc_now(),
    )
    for posicion, kind in enumerate(kinds, start=1):
        producto = product if products is None else products[posicion - 1]
        post.slides.append(
            MarketingPostSlide(
                position=posicion,
                card_slug=kind.slug,
                params=_params(kind, producto, province, data_through),
                options=options_to_dict(parse_options(kind, {})),
            )
        )
    db.add(post)
    return post


def create_lesson_draft(
    db: Session,
    *,
    lesson: Lesson,
    planned_for: date,
    origin: PostOrigin = PostOrigin.OPERATOR,
    batch_slot: str | None = None,
    now: datetime | None = None,
) -> MarketingPost:
    """Un borrador de carrusel educativo: una diapositiva `leccion` por cada
    una de la lección, con sus bloques libres ya cargados. No lleva producto ni
    provincia (texto fijo, sin cifras), así que no depende de ningún dato. El
    texto del post queda guardado: la lección lo trae hecho. Sin `flush`."""
    if not 1 <= len(lesson.slides) <= MAX_SLIDES:
        raise InvalidSlide(f"Un carrusel lleva de 1 a {MAX_SLIDES} diapositivas.")
    kind = BY_SLUG[LESSON_CARD]
    post = MarketingPost(
        format=PostFormat.CAROUSEL if len(lesson.slides) > 1 else PostFormat.SINGLE,
        status=PostStatus.DRAFT,
        planned_for=planned_for,
        caption=_normalize_caption(with_hashtags(lesson.summary)),
        origin=origin,
        batch_slot=batch_slot,
        created_at=now or utc_now(),
    )
    for posicion, bloques in enumerate(lesson.slides, start=1):
        post.slides.append(
            MarketingPostSlide(
                position=posicion,
                card_slug=kind.slug,
                params=_params(kind, None, None, planned_for),
                options=options_to_dict(CardOptions(show={}, free=bloques)),
            )
        )
    db.add(post)
    return post


# --- Diapositivas de un carrusel ---------------------------------------------


def find_slide(post: MarketingPost, position: int) -> MarketingPostSlide:
    slide = next((s for s in post.slides if s.position == position), None)
    if slide is None:
        raise InvalidSlide(f"La publicación no tiene diapositiva {position}.")
    return slide


def _renumber(db: Session, post: MarketingPost, orden: list[MarketingPostSlide]) -> None:
    """Deja `orden` en las posiciones 1..n. `UNIQUE (post_id, position)` no es
    diferible: primero se corren todas fuera del rango y después se asientan,
    con un `flush` entre medio, para que ningún UPDATE choque con otra fila."""
    for i, slide in enumerate(orden, start=1):
        slide.position = 1000 + i
    db.flush()
    for i, slide in enumerate(orden, start=1):
        slide.position = i
    db.flush()
    post.slides.sort(key=lambda s: s.position)


def _sync_format(post: MarketingPost) -> None:
    if post.format != PostFormat.STORY:
        post.format = PostFormat.CAROUSEL if len(post.slides) > 1 else PostFormat.SINGLE


def _inherited(post: MarketingPost, campo: str) -> str | None:
    valor: str | None = next((s.params[campo] for s in post.slides if s.params.get(campo)), None)
    return valor


def add_slide(db: Session, post: MarketingPost, card_slug: str) -> MarketingPostSlide:
    """Agrega al final una diapositiva de la plantilla `card_slug`. Hereda
    producto, provincia y fecha de corte de las que ya hay: se ajustan en el
    editor como cualquier otra."""
    _require(post, {PostStatus.DRAFT}, "cambiar de diapositivas")
    if post.format == PostFormat.STORY:
        raise InvalidSlide("Una story no se convierte en carrusel.")
    if len(post.slides) >= MAX_SLIDES:
        raise InvalidSlide(f"Un carrusel lleva hasta {MAX_SLIDES} diapositivas.")
    kind = _postable_kind(card_slug)
    primera = _kind(post.slides[0])
    if (kind.width, kind.height) != (primera.width, primera.height):
        raise InvalidSlide(
            f"«{kind.label}» es de {kind.width}×{kind.height}: todas las diapositivas tienen "
            f"el tamaño de la primera ({primera.width}×{primera.height})."
        )

    producto = _inherited(post, "product")
    if producto is None and kind.needs_product:
        productos = pilot_product_names(db)
        producto = productos[0] if productos else None
    provincia = _inherited(post, "province")
    if provincia is None and kind.needs_province:
        provincia = PHASE0_PROVINCES[0] if PHASE0_PROVINCES else None

    slide = MarketingPostSlide(
        position=max(s.position for s in post.slides) + 1,
        card_slug=kind.slug,
        params=_params(kind, producto, provincia, _data_through(post.slides[0])),
        options=options_to_dict(parse_options(kind, {})),
    )
    post.slides.append(slide)
    _sync_format(post)
    db.flush()
    return slide


def move_slide(db: Session, post: MarketingPost, position: int, delta: int) -> int:
    """Sube (`delta=-1`) o baja (`+1`) una diapositiva un lugar. Devuelve su
    posición nueva; en el borde se queda donde está."""
    _require(post, {PostStatus.DRAFT}, "cambiar de diapositivas")
    slide = find_slide(post, position)
    orden = list(post.slides)
    i = orden.index(slide)
    j = i + (1 if delta > 0 else -1)
    if not 0 <= j < len(orden):
        return position
    orden[i], orden[j] = orden[j], orden[i]
    _renumber(db, post, orden)
    return slide.position


def remove_slide(db: Session, post: MarketingPost, position: int) -> None:
    """Quita una diapositiva y corre las de atrás. La última no se quita: una
    publicación sin diapositivas se descarta."""
    _require(post, {PostStatus.DRAFT}, "cambiar de diapositivas")
    slide = find_slide(post, position)
    if len(post.slides) == 1:
        raise InvalidSlide("Es la única diapositiva: para sacarla, descartá la publicación.")
    post.slides.remove(slide)
    db.delete(slide)
    db.flush()
    _renumber(db, post, list(post.slides))
    _sync_format(post)


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
    position: int = 1,
) -> None:
    """Guarda lo que se elige en el editor para la diapositiva `position`. Si
    el texto enviado es el que se habría generado con los parámetros
    **anteriores**, queda automático: así cambiar de producto no deja pegado
    el texto del producto viejo."""
    _require(post, {PostStatus.DRAFT}, "editar")
    slide = find_slide(post, position)
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
            kind,
            thaw_context(snapshot["context"]),
            options_from_dict(kind, snapshot["options"]),
            slide_counter(slide),
        )
        listas.append((slide, snapshot, render(html, width=kind.width, height=kind.height)))

    origen = caption_slide(post)
    snapshot_origen = next(snap for slide, snap, _ in listas if slide is origen)
    caption = post.caption
    if caption is None:
        caption = _datos_caption(post) or build_caption(
            _kind(origen), thaw_context(snapshot_origen["context"])
        )

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
    """Un borrador nuevo para `today` con las mismas diapositivas, parámetros
    y opciones. El texto se copia solo si se había escrito a mano."""
    _require(
        post, {PostStatus.APPROVED, PostStatus.PUBLISHED, PostStatus.DISCARDED}, "duplicar"
    )
    origen = caption_slide(post)
    caption = post.caption
    if caption is not None and origen.snapshot is not None:
        generado = _datos_caption(post) or build_caption(
            _kind(origen), thaw_context(origen.snapshot["context"])
        )
        if caption == generado:
            caption = None
    copia = MarketingPost(
        format=post.format,
        status=PostStatus.DRAFT,
        planned_for=today,
        caption=caption,
        origin=PostOrigin.OPERATOR,
        created_at=now or utc_now(),
    )
    for slide in post.slides:
        kind = _kind(slide)
        copia.slides.append(
            MarketingPostSlide(
                position=slide.position,
                card_slug=kind.slug,
                params=dict(slide.params),
                options=options_to_dict(options_from_dict(kind, slide.options)),
            )
        )
    db.add(copia)
    return copia


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
