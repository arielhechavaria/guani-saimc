"""Cliente de *Instagram API with Instagram Login* (graph.instagram.com).

Mismo corte que `exchange_rate/eltoque.py`: el transporte (`GraphClient`,
`refresh_token`) está separado del parseo puro (`parse_*`), que recibe el texto
crudo de la respuesta y nunca levanta excepción. Ninguna función de este módulo
levanta: sin token, sin red, con un status distinto de 200 o con un cuerpo
ilegible, devuelve un `GraphError` que el lote decide cómo informar.

Lo que dice la documentación de Meta (consultada el 2026-10-13) y su
consecuencia acá:

- Las métricas se piden por nombre y un nombre que no corresponde al tipo de
  publicación hace fallar el pedido entero. Por eso `metrics_for` arma la lista
  según el tipo: una story no tiene `likes`, `comments` ni `saved`, y
  `ig_reels_avg_watch_time` (en milisegundos) es solo de reels.
- `views` y no `impressions`, deprecada para el contenido posterior al
  2024-07-02. `impressions` no se pide ni se lee.
- Los carruseles no tienen insights: `metrics_for` devuelve `None` y el lote no
  los pide.
- Cada métrica trae su valor en `values[0].value` o, en las más nuevas, en
  `total_value.value`. Se leen las dos formas.
- Las stories no salen en `/me/media`: viven en `/me/stories`, solo 24 h.
- `media_product_type` (FEED / REELS / STORY) puede faltar según la versión: si
  falta, un video del feed es reel (Instagram publica todo video nuevo como
  reel) y lo demás es feed.
- Un token vencido o inválido responde con `error.code` 190. Un token vencido
  no se puede renovar: hay que repetir el alta a mano.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import httpx

from saimc.models import InstagramMediaType

_HOST = "https://graph.instagram.com"
# Versión fija de la API: una versión nueva puede cambiar métricas (ya pasó con
# `impressions`), y cambiarla es una decisión, no algo que pase solo.
API_VERSION = "v25.0"
_BASE = f"{_HOST}/{API_VERSION}"
# La renovación no lleva versión en la documentación de Meta.
_REFRESH_URL = f"{_HOST}/refresh_access_token"

#: Cuántas publicaciones del feed se leen por corrida: las más recientes. Una
#: más vieja deja de actualizar sus métricas, pero conserva su historial.
MEDIA_LIMIT = 50

_MEDIA_FIELDS = "id,media_type,media_product_type,permalink,caption,timestamp"
_TOKEN_INVALID_CODE = 190

_FEED_METRICS: tuple[str, ...] = (
    "views",
    "reach",
    "likes",
    "comments",
    "saved",
    "shares",
    "total_interactions",
)
_REELS_METRICS: tuple[str, ...] = (*_FEED_METRICS, "ig_reels_avg_watch_time")
_STORY_METRICS: tuple[str, ...] = ("views", "reach", "shares", "total_interactions")

#: Nombre de la métrica en Meta → campo de `Metrics`.
_METRIC_FIELDS: dict[str, str] = {
    "views": "views",
    "reach": "reach",
    "likes": "likes",
    "comments": "comments",
    "saved": "saved",
    "shares": "shares",
    "total_interactions": "total_interactions",
    "ig_reels_avg_watch_time": "avg_watch_time_ms",
}


@dataclass(frozen=True)
class GraphError:
    """Lo que salió mal, en una línea apta para el registro (nunca lleva el token)."""

    message: str
    code: int | None = None

    @property
    def token_invalid(self) -> bool:
        """El token venció o fue revocado. Reintentar no sirve."""
        return self.code == _TOKEN_INVALID_CODE


@dataclass(frozen=True)
class Account:
    followers_count: int
    media_count: int


@dataclass(frozen=True)
class MediaItem:
    ig_media_id: str
    media_type: InstagramMediaType
    is_carousel: bool
    permalink: str | None
    caption: str | None
    posted_at: datetime | None


@dataclass(frozen=True)
class Metrics:
    views: int | None = None
    reach: int | None = None
    likes: int | None = None
    comments: int | None = None
    saved: int | None = None
    shares: int | None = None
    total_interactions: int | None = None
    avg_watch_time_ms: int | None = None


@dataclass(frozen=True)
class RefreshedToken:
    access_token: str
    expires_at: datetime


# --- Parseo puro ----------------------------------------------------------------


def _load(body: str) -> Any:
    try:
        return json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return None


def _count(value: Any) -> int | None:
    """Un conteo válido: entero no negativo. `bool` es `int` en Python y no vale."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _timestamp(value: Any) -> datetime | None:
    """`2026-10-10T14:05:00+0000`, el formato de Meta. `fromisoformat` acepta el
    `+0000` sin dos puntos desde Python 3.11."""
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


def parse_error(body: str, *, status: int) -> GraphError:
    """El error de Meta (`{"error": {"message", "code"}}`) o uno genérico con el status."""
    payload = _load(body)
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        message = _text(error.get("message")) or f"error de Meta (HTTP {status})"
        code = error.get("code")
        return GraphError(message=message, code=code if isinstance(code, int) else None)
    return GraphError(message=f"respuesta inesperada de Meta (HTTP {status})")


def parse_account(body: str) -> Account | None:
    payload = _load(body)
    if not isinstance(payload, dict):
        return None
    followers = _count(payload.get("followers_count"))
    media = _count(payload.get("media_count"))
    if followers is None or media is None:
        return None
    return Account(followers_count=followers, media_count=media)


def _media_type(item: dict[str, Any], *, stories: bool) -> InstagramMediaType:
    if stories:
        return InstagramMediaType.STORY
    product = item.get("media_product_type")
    if product in (InstagramMediaType.REELS.value, InstagramMediaType.STORY.value):
        return InstagramMediaType(product)
    if product is None and item.get("media_type") == "VIDEO":
        return InstagramMediaType.REELS
    return InstagramMediaType.FEED


def parse_media_list(body: str, *, stories: bool = False) -> list[MediaItem]:
    """Las publicaciones de `/me/media` (o de `/me/stories` con `stories=True`).

    Un elemento sin `id` se saltea; una fecha ilegible queda nula. Cualquier
    otra forma del cuerpo da una lista vacía.
    """
    payload = _load(body)
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return []
    items: list[MediaItem] = []
    for raw in data:
        if not isinstance(raw, dict):
            continue
        media_id = _text(raw.get("id"))
        if media_id is None:
            continue
        items.append(
            MediaItem(
                ig_media_id=media_id,
                media_type=_media_type(raw, stories=stories),
                is_carousel=raw.get("media_type") == "CAROUSEL_ALBUM",
                permalink=_text(raw.get("permalink")),
                caption=_text(raw.get("caption")),
                posted_at=_timestamp(raw.get("timestamp")),
            )
        )
    return items


def _metric_value(entry: dict[str, Any]) -> int | None:
    total = entry.get("total_value")
    if isinstance(total, dict):
        return _count(total.get("value"))
    values = entry.get("values")
    if isinstance(values, list) and values and isinstance(values[0], dict):
        return _count(values[0].get("value"))
    return None


def parse_insights(body: str) -> Metrics | None:
    """Las métricas de `/{media-id}/insights`. `None` si no trae ninguna conocida."""
    payload = _load(body)
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return None
    found: dict[str, int | None] = {}
    for entry in data:
        if not isinstance(entry, dict):
            continue
        field = _METRIC_FIELDS.get(entry.get("name", ""))
        if field is not None:
            found[field] = _metric_value(entry)
    if not found:
        return None
    return Metrics(**found)


def parse_refresh(body: str, *, now: datetime) -> RefreshedToken | None:
    """El token renovado y cuándo vence (`now` + `expires_in` segundos)."""
    payload = _load(body)
    if not isinstance(payload, dict):
        return None
    token = _text(payload.get("access_token"))
    seconds = _count(payload.get("expires_in"))
    if token is None or not seconds:
        return None
    return RefreshedToken(access_token=token, expires_at=now + timedelta(seconds=seconds))


def metrics_for(item: MediaItem) -> tuple[str, ...] | None:
    """Qué métricas pedir para `item`; `None` si no tiene insights (carrusel)."""
    if item.is_carousel:
        return None
    if item.media_type == InstagramMediaType.STORY:
        return _STORY_METRICS
    if item.media_type == InstagramMediaType.REELS:
        return _REELS_METRICS
    return _FEED_METRICS


# --- Transporte -----------------------------------------------------------------

_NO_TOKEN = GraphError(message="no hay token de Instagram configurado")


def _get(
    url: str,
    params: dict[str, str],
    *,
    timeout: float,
    transport: httpx.BaseTransport | None,
) -> str | GraphError:
    """El cuerpo de un 200, o un `GraphError`. Nunca levanta.

    El mensaje de un fallo de red nombra solo el tipo de error: el de `httpx`
    puede traer la URL, y la URL lleva el token.
    """
    try:
        with httpx.Client(transport=transport, timeout=timeout) as client:
            response = client.get(url, params=params)
    except httpx.HTTPError as error:
        return GraphError(message=f"sin conexión con graph.instagram.com ({type(error).__name__})")
    if response.status_code != 200:
        return parse_error(response.text, status=response.status_code)
    return response.text


class GraphClient:
    """Lecturas de la cuenta propia. Cada método devuelve el dato o un `GraphError`.

    `transport` es el seam de test, igual que en `eltoque.fetch_rate`: en
    producción queda en `None` y `httpx` arma el transporte real.
    """

    def __init__(
        self, token: str, *, timeout: float = 15.0, transport: httpx.BaseTransport | None = None
    ) -> None:
        self._token = token
        self._timeout = timeout
        self._transport = transport

    def _fetch(self, path: str, params: dict[str, str]) -> str | GraphError:
        if not self._token:
            return _NO_TOKEN
        return _get(
            f"{_BASE}/{path}",
            {**params, "access_token": self._token},
            timeout=self._timeout,
            transport=self._transport,
        )

    def account(self) -> Account | GraphError:
        body = self._fetch("me", {"fields": "user_id,username,followers_count,media_count"})
        if isinstance(body, GraphError):
            return body
        return parse_account(body) or GraphError(message="Meta no devolvió los seguidores de la cuenta")

    def _list(self, path: str, *, stories: bool) -> list[MediaItem] | GraphError:
        params = {"fields": _MEDIA_FIELDS}
        if not stories:
            params["limit"] = str(MEDIA_LIMIT)
        body = self._fetch(path, params)
        if isinstance(body, GraphError):
            return body
        return parse_media_list(body, stories=stories)

    def media(self) -> list[MediaItem] | GraphError:
        """Las `MEDIA_LIMIT` publicaciones más recientes del feed (posts y reels)."""
        return self._list("me/media", stories=False)

    def stories(self) -> list[MediaItem] | GraphError:
        """Las stories vivas (las de las últimas 24 h)."""
        return self._list("me/stories", stories=True)

    def insights(self, ig_media_id: str, metrics: tuple[str, ...]) -> Metrics | GraphError:
        body = self._fetch(f"{ig_media_id}/insights", {"metric": ",".join(metrics)})
        if isinstance(body, GraphError):
            return body
        return parse_insights(body) or GraphError(message="Meta no devolvió métricas")


def refresh_token(
    token: str,
    *,
    now: datetime,
    timeout: float = 15.0,
    transport: httpx.BaseTransport | None = None,
) -> RefreshedToken | GraphError:
    """Renueva un token de larga duración (`grant_type=ig_refresh_token`).

    Meta solo lo acepta si el token tiene más de 24 h y todavía no venció; el
    token nuevo dura 60 días. Decidir cuándo renovar es del lote.
    """
    if not token:
        return _NO_TOKEN
    body = _get(
        _REFRESH_URL,
        {"grant_type": "ig_refresh_token", "access_token": token},
        timeout=timeout,
        transport=transport,
    )
    if isinstance(body, GraphError):
        return body
    return parse_refresh(body, now=now) or GraphError(message="Meta no devolvió el token renovado")
