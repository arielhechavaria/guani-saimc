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
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


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


@dataclass(frozen=True)
class ParsedMessage:
    chat_id: str
    text: str
    timestamp: datetime
    has_media: bool


def parse_message(body: Any) -> ParsedMessage | None:
    """Extrae un mensaje utilizable del payload de WAHA, o `None` si no aplica.

    Devuelve `None` —en vez de levantar una excepción— para los casos que el
    endpoint debe simplemente ignorar: mensaje propio (`fromMe`), sin texto
    (media sin caption, fuera de alcance de este slice: no hay OCR desde el
    webhook todavía), sin identificador de chat, o un payload que no tiene
    forma reconocible. El endpoint decide qué código HTTP corresponde a cada
    caso; acá solo se decide si hay o no un mensaje que ingerir.

    Acepta dos formas de payload, porque no hay certeza de cuál manda la
    instancia real hasta probarla: el objeto de mensaje directo (como lo
    muestra la documentación) o envuelto en `{"event", "session", "payload"}`
    (la convención general de WAHA para otros webhooks).
    """
    if not isinstance(body, dict):
        return None

    payload = body.get("payload") if isinstance(body.get("payload"), dict) else body

    if payload.get("fromMe"):
        return None

    chat_id = payload.get("from")
    if not chat_id or not isinstance(chat_id, str):
        return None

    text = payload.get("body")
    if not text or not isinstance(text, str) or not text.strip():
        return None

    raw_timestamp = payload.get("timestamp")
    timestamp = (
        datetime.fromtimestamp(raw_timestamp, tz=UTC)
        if isinstance(raw_timestamp, int | float)
        else datetime.now(UTC)
    )

    return ParsedMessage(
        chat_id=chat_id,
        text=text,
        timestamp=timestamp,
        has_media=bool(payload.get("hasMedia", False)),
    )
