"""Lo que muestra la sección «Instagram» de `/marketing`.

Solo lee lo que dejó el lote (`saimc.instagram.sync`); nunca llama a Meta
desde una request. Cada publicación con su ÚLTIMA lectura de métricas (la serie
completa queda en `instagram_media_metrics` para cuando haga falta) y los
seguidores por día: la última lectura de cada día, con el cambio contra el día
anterior que tenga lectura. Sin gráficos: una tabla alcanza para empezar
(`docs/spec-slice-instagram.md`, fuera de alcance).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from saimc.models import InstagramAccountSnapshot, InstagramMedia, InstagramMediaMetrics

#: Cuántas publicaciones lista la tabla: las más recientes.
POSTS_LIMIT = 30
#: Cuántos días de seguidores muestra.
FOLLOWER_DAYS = 30
_CAPTION_CHARS = 60

_TYPE_LABELS = {"FEED": "Post", "REELS": "Reel", "STORY": "Story"}


@dataclass(frozen=True)
class PostRow:
    ig_media_id: str
    type_label: str
    posted_at: datetime | None
    permalink: str | None
    caption_short: str
    # Nula si nunca dio métricas (un carrusel, o todavía sin datos de Meta).
    metrics: InstagramMediaMetrics | None

    @property
    def avg_watch_seconds(self) -> float | None:
        if self.metrics is None or self.metrics.avg_watch_time_ms is None:
            return None
        return self.metrics.avg_watch_time_ms / 1000


@dataclass(frozen=True)
class FollowerDay:
    day: date
    followers: int
    # Contra el día anterior con lectura; nulo en el primero.
    change: int | None


@dataclass(frozen=True)
class InstagramPanel:
    posts: list[PostRow]
    followers: list[FollowerDay]  # lo más nuevo primero
    current_followers: int | None
    last_sync: datetime | None


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _short(caption: str | None) -> str:
    text = " ".join((caption or "").split())
    return text if len(text) <= _CAPTION_CHARS else text[:_CAPTION_CHARS].rstrip() + "…"


def _posts(session: Session) -> list[PostRow]:
    latest = (
        select(
            InstagramMediaMetrics.media_id,
            func.max(InstagramMediaMetrics.captured_at).label("captured_at"),
        )
        .group_by(InstagramMediaMetrics.media_id)
        .subquery()
    )
    rows = session.execute(
        select(InstagramMedia, InstagramMediaMetrics)
        .outerjoin(latest, latest.c.media_id == InstagramMedia.id)
        .outerjoin(
            InstagramMediaMetrics,
            (InstagramMediaMetrics.media_id == latest.c.media_id)
            & (InstagramMediaMetrics.captured_at == latest.c.captured_at),
        )
        .order_by(InstagramMedia.posted_at.desc().nulls_last(), InstagramMedia.first_seen_at.desc())
        .limit(POSTS_LIMIT)
    ).all()
    return [
        PostRow(
            ig_media_id=media.ig_media_id,
            type_label=_TYPE_LABELS.get(media.media_type, media.media_type),
            posted_at=_as_utc(media.posted_at) if media.posted_at else None,
            permalink=media.permalink,
            caption_short=_short(media.caption),
            metrics=metrics,
        )
        for media, metrics in rows
    ]


def _followers(session: Session, *, now: datetime) -> list[FollowerDay]:
    snapshots = session.scalars(
        select(InstagramAccountSnapshot)
        .where(InstagramAccountSnapshot.captured_at >= now - timedelta(days=FOLLOWER_DAYS))
        .order_by(InstagramAccountSnapshot.captured_at)
    ).all()
    # Orden ascendente: la última lectura de cada día pisa a las anteriores.
    by_day: dict[date, int] = {}
    for snap in snapshots:
        by_day[_as_utc(snap.captured_at).date()] = snap.followers_count
    days: list[FollowerDay] = []
    previous: int | None = None
    for day, followers in by_day.items():
        days.append(
            FollowerDay(
                day=day, followers=followers, change=None if previous is None else followers - previous
            )
        )
        previous = followers
    return list(reversed(days))


def load_panel(session: Session, *, now: datetime) -> InstagramPanel:
    last = session.scalars(
        select(InstagramAccountSnapshot).order_by(InstagramAccountSnapshot.captured_at.desc()).limit(1)
    ).first()
    return InstagramPanel(
        posts=_posts(session),
        followers=_followers(session, now=now),
        current_followers=last.followers_count if last else None,
        last_sync=_as_utc(last.captured_at) if last else None,
    )
