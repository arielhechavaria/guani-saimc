"""La semana de la fábrica y el historial de publicadas (F4 de
`docs/plan-fabrica-marketing.md`).

Solo lectura: no escribe nada ni cambia el esquema. Lo que pide cada día es lo
mismo que pide el lote de las 06:00 y la pantalla «Hoy» (`cards_for_day`), así
las tres no se contradicen. Las stories de la guía
(`docs/guia-crecimiento.md`, «La semana tipo») no las arma el sistema: van como
nota a mano, sin estado.

El historial lee solo lo congelado al aprobar (`snapshot`): producto y
provincia de `params` —que el listener de `models/marketing_post.py` ya no
deja cambiar— y la fecha de corte del snapshot. Nunca recalcula una cifra.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from saimc.marketing.context import BY_SLUG
from saimc.marketing.daily import CAROUSEL_SLOT, cards_for_day, slot_label
from saimc.models.marketing_post import MarketingPost, PostFormat, PostStatus

DAY_NAMES: tuple[str, ...] = (
    "Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo",
)

#: Stories de la guía por día (`date.weekday()`, 0 = lunes). A mano: el sistema
#: no las crea ni las sigue.
STORY_NOTES: dict[int, str] = {
    0: "Recordá la pieza del día",
    2: "Encuesta: «¿Qué producto miramos el lunes?»",
    5: "Respuestas a las preguntas de la semana",
    6: "Story de precalentamiento del lunes",
}

#: Cuántas publicadas muestra el historial de una vez.
HISTORY_LIMIT = 30


def week_monday(day: date) -> date:
    """El lunes de la semana de `day`."""
    return day - timedelta(days=day.weekday())


def post_slot(post: MarketingPost) -> str:
    """El turno del calendario que cubre la publicación: el del lote si lo
    tiene; si la armó el operador, el carrusel o la tarjeta de la primera."""
    if post.batch_slot:
        return post.batch_slot
    return CAROUSEL_SLOT if post.format == PostFormat.CAROUSEL else post.slides[0].card_slug


@dataclass(frozen=True)
class SlotView:
    slot: str
    label: str
    #: `draft`, `approved`, `published`; si no hay nada, `missing` (el día ya
    #: llegó) o `pending` (todavía no).
    state: str
    post: MarketingPost | None = None
    #: Existe pero el día ya pasó y no se publicó.
    overdue: bool = False


@dataclass(frozen=True)
class DayView:
    day: date
    name: str
    slots: tuple[SlotView, ...]
    #: Nota de stories de la guía, o `None` si ese día no pide.
    stories: str | None
    is_today: bool


@dataclass(frozen=True)
class WeekView:
    monday: date
    days: tuple[DayView, ...]
    previous: date
    next: date
    is_current: bool
    #: Turnos vencidos sin ninguna publicación; lo que ya existe, aunque esté
    #: sin publicar, no cuenta.
    missing: int


def _state(
    candidates: list[MarketingPost], day: date, today: date
) -> tuple[str, MarketingPost | None]:
    """El mejor estado entre las publicaciones vivas de un turno: publicada,
    después aprobada, después borrador."""
    for estado in (PostStatus.PUBLISHED, PostStatus.APPROVED, PostStatus.DRAFT):
        for post in candidates:
            if post.status == estado:
                return str(estado), post
    return ("missing" if day <= today else "pending"), None


def build_week(db: Session, monday: date, *, today: date) -> WeekView:
    """Los siete días desde `monday` con lo que pide el calendario y el estado
    de lo que existe."""
    domingo = monday + timedelta(days=6)
    posts = db.scalars(
        select(MarketingPost)
        .where(MarketingPost.planned_for.between(monday, domingo))
        .where(MarketingPost.status != PostStatus.DISCARDED)
        .options(selectinload(MarketingPost.slides))
        .order_by(MarketingPost.created_at)
    ).all()
    por_turno: dict[tuple[date, str], list[MarketingPost]] = {}
    for post in posts:
        if post.slides:
            por_turno.setdefault((post.planned_for, post_slot(post)), []).append(post)

    dias: list[DayView] = []
    faltan = 0
    for i in range(7):
        dia = monday + timedelta(days=i)
        turnos: list[SlotView] = []
        for slot in cards_for_day(dia):
            estado, existente = _state(por_turno.get((dia, slot), []), dia, today)
            if estado == "missing":
                faltan += 1
            turnos.append(
                SlotView(
                    slot, slot_label(slot), estado, existente,
                    overdue=existente is not None and dia < today
                    and estado != PostStatus.PUBLISHED,
                )
            )
        dias.append(
            DayView(dia, DAY_NAMES[dia.weekday()], tuple(turnos), STORY_NOTES.get(dia.weekday()),
                    is_today=dia == today)
        )
    return WeekView(
        monday=monday,
        days=tuple(dias),
        previous=monday - timedelta(days=7),
        next=monday + timedelta(days=7),
        is_current=monday == week_monday(today),
        missing=faltan,
    )


@dataclass(frozen=True)
class HistorySlide:
    position: int
    label: str
    product: str | None
    province: str | None
    #: Hasta qué día llegan las cifras que se congelaron.
    data_through: date


@dataclass(frozen=True)
class HistoryEntry:
    post: MarketingPost
    url: str
    published_at: datetime
    approved_at: datetime
    title: str
    slides: tuple[HistorySlide, ...]


def published_history(db: Session, limit: int = HISTORY_LIMIT) -> list[HistoryEntry]:
    """Las publicadas, de la más reciente a la más vieja, con su enlace y lo
    que se congeló al aprobar."""
    posts = db.scalars(
        select(MarketingPost)
        .where(MarketingPost.status == PostStatus.PUBLISHED)
        .options(selectinload(MarketingPost.slides))
        .order_by(MarketingPost.published_at.desc())
        .limit(limit)
    ).all()
    entradas: list[HistoryEntry] = []
    for post in posts:
        if not post.slides or post.published_url is None or post.published_at is None:
            continue
        diapositivas = tuple(
            HistorySlide(
                position=s.position,
                label=BY_SLUG[s.card_slug].label,
                product=s.params.get("product"),
                province=s.params.get("province"),
                data_through=date.fromisoformat(
                    (s.snapshot or {}).get("data_through", s.params["data_through"])
                ),
            )
            for s in post.slides
        )
        entradas.append(
            HistoryEntry(
                post=post,
                url=post.published_url,
                published_at=post.published_at,
                approved_at=post.approved_at or post.published_at,
                title=(
                    slot_label(CAROUSEL_SLOT)
                    if post.format == PostFormat.CAROUSEL
                    else diapositivas[0].label
                ),
                slides=diapositivas,
            )
        )
    return entradas
