"""Parseo del webhook de WAHA y verificación de su firma.

Automatización de canales de WhatsApp (`docs/decisions.md`, 2026-08-15):
apartamiento consciente de las decisiones cerradas 2 (nada de scraping/bots en
Fase 0) y 4 (nada de Docker en Fase 0) — el operador corre WAHA en Docker en su
propio VPS, y este módulo es el lado receptor.

Las funciones acá son puras y no tocan la base ni el filesystem: se testean
contra la forma de payload que documenta WAHA
(waha.devlike.pro/docs/how-to/events/, verificado 2026-08-15), no contra una
instancia real, que todavía no existe. Es una calibración razonada, no una
certeza — el primer webhook real puede exigir un ajuste acá, y eso es
esperable, no un error de diseño.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from saimc.ingestion.sender import sender_hash


def verify_hmac(secret: bytes, raw_body: bytes, provided_hex: str) -> bool:
    """Verifica la firma `X-Webhook-Hmac` que manda WAHA sobre el body crudo.

    WAHA firma con SHA-512 (`config.hmac.key` del lado de WAHA). Comparación
    de tiempo constante (`hmac.compare_digest`): un webhook público es un
    blanco de timing attack si se compara con `==`.
    """
    if not provided_hex:
        return False
    expected = hmac.new(secret, raw_body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, provided_hex)


# Qué adjunto vale la pena bajar, y con qué extensión se guarda
# (`docs/decisions.md`, 2026-08-21). La extensión importa porque
# `inbox.classify` decide el `content_type` por ella.
#
# Solo imágenes fijas: un video o un audio no tiene OCR que aplicarle y
# ocuparía la cola sin aportar un precio.
#
# `image/webp` queda AFUERA a propósito. WhatsApp reencoda las fotos a JPEG,
# así que lo que llega como webp es casi siempre un sticker, y dejarlos entrar
# inundaría la cola de caritas. Se pierde la foto webp rara: costo de recall
# aceptado a cambio de la precisión.
_MEDIA_SUFFIX_BY_MIMETYPE = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
}


@dataclass(frozen=True)
class ParsedMessage:
    chat_id: str
    text: str
    timestamp: datetime
    has_media: bool
    media_url: str | None = None
    media_suffix: str | None = None
    # Hash con clave del emisor de un mensaje de grupo (slice B5,
    # `docs/spec-slice-B5.md`). Es lo ÚNICO del emisor que sale del parser: el
    # JID en claro suele ser el teléfono y no llega a este objeto, así que no
    # puede terminar en un log, en la bandeja ni en la base (spec.md §10.6).
    # `repr=False` para que tampoco el hash aparezca si alguien registra el
    # mensaje entero.
    sender_hash: str | None = field(default=None, repr=False)


# Sufijo del JID de un grupo de WhatsApp. Solo ahí `from` es el grupo y el
# emisor viaja aparte, en `participant`. Un canal termina en "@newsletter" y un
# chat directo en "@c.us".
GROUP_JID_SUFFIX = "@g.us"


def _parse_media(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    """URL y extensión del adjunto, o `(None, None)` si no hay nada que bajar.

    `hasMedia: true` sin `media.url` significa que WAHA detectó el archivo y
    NO lo descargó — su `WHATSAPP_DOWNLOAD_MEDIA` o su
    `WHATSAPP_FILES_MIMETYPES` lo filtraron (documentado por WAHA, verificado
    2026-08-21). No es un error: es configuración del lado del operador, y acá
    simplemente no hay nada que pedir.
    """
    media = payload.get("media")
    if not isinstance(media, dict):
        return None, None

    url = media.get("url")
    if not url or not isinstance(url, str):
        return None, None

    mimetype = media.get("mimetype")
    if not isinstance(mimetype, str):
        return None, None

    suffix = _MEDIA_SUFFIX_BY_MIMETYPE.get(mimetype.split(";")[0].strip().lower())
    if suffix is None:
        return None, None
    return url, suffix


def parse_message(body: Any, sender_key: str | None = None) -> ParsedMessage | None:
    """Extrae un mensaje utilizable del payload de WAHA, o `None` si no aplica.

    Devuelve `None` —en vez de levantar una excepción— para los casos que el
    endpoint debe simplemente ignorar: mensaje propio (`fromMe`), sin
    identificador de chat, un payload sin forma reconocible, o un mensaje que
    no trae NI texto NI una imagen descargable. El endpoint decide qué código
    HTTP corresponde a cada caso; acá solo se decide si hay o no un mensaje
    que ingerir.

    Desde el 2026-08-21 una foto sin caption SÍ es un mensaje utilizable
    (`text` vacío, `media_url` presente): es el 91% de lo que publica un grupo
    de solo fotos, medido el 2026-08-18 en `parse_group_history`. Antes se
    descartaba acá mismo porque no había forma de traer el archivo.

    Cuando hay caption Y foto, gana el texto y el archivo no se baja. Preservar
    también la imagen como evidencia exige que la cola sepa proponer desde el
    `original_text` de una fila `image` —hoy no lo hace, ver `curation_queue`—
    y va en su propio slice.

    Acepta dos formas de payload, porque no hay certeza de cuál manda la
    instancia real hasta probarla: el objeto de mensaje directo (como lo
    muestra la documentación) o envuelto en `{"event", "session", "payload"}`
    (la convención general de WAHA para otros webhooks).

    `sender_key` es `SAIMC_SENDER_HASH_KEY` (slice B5). El emisor se lee de
    `participant`, solo cuando `from` es un grupo, y se convierte en hash acá
    mismo: el JID no sale de esta función. Sin clave, o sin emisor,
    `sender_hash` queda nulo y el mensaje se parsea igual. El nombre del campo
    está verificado para el histórico (2026-08-18); para el webhook lo confirma
    la verificación del operador de `docs/spec-slice-B5.md`.
    """
    if not isinstance(body, dict):
        return None

    wrapped = body.get("payload")
    payload: dict[str, Any] = wrapped if isinstance(wrapped, dict) else body

    if payload.get("fromMe"):
        return None

    chat_id = payload.get("from")
    if not chat_id or not isinstance(chat_id, str):
        return None

    raw_text = payload.get("body")
    text = raw_text.strip() if isinstance(raw_text, str) else ""

    media_url, media_suffix = (None, None) if text else _parse_media(payload)
    if not text and media_url is None:
        return None

    raw_timestamp = payload.get("timestamp")
    timestamp = (
        datetime.fromtimestamp(raw_timestamp, tz=UTC)
        if isinstance(raw_timestamp, int | float)
        else datetime.now(UTC)
    )

    return ParsedMessage(
        chat_id=chat_id,
        text=raw_text if isinstance(raw_text, str) and text else "",
        timestamp=timestamp,
        has_media=bool(payload.get("hasMedia", False)),
        media_url=media_url,
        media_suffix=media_suffix,
        sender_hash=(
            sender_hash(payload.get("participant"), sender_key)
            if chat_id.endswith(GROUP_JID_SUFFIX)
            else None
        ),
    )
