"""Backfill puntual del histórico de un canal de WhatsApp vía WAHA.

Disparador manual desde `/catalog` (docs/decisions.md, 2026-08-17): a
diferencia del webhook (`saimc.web.routers.whatsapp_webhook`), que reacciona
a mensajes nuevos uno por uno a medida que llegan, esto trae de una sola vez
lo que WAHA ya tiene sincronizado de un canal — típicamente unos días a unas
semanas, según cuánto publique ese canal en particular.

Separa transporte (`fetch_channel_history`) de parseo puro (`parse_history`)
por el mismo motivo que `saimc.exchange_rate.eltoque`: probar la política de
error sin abrir un socket, y probar el parseo sin un cliente HTTP de por
medio.
"""

from __future__ import annotations

from typing import Any

import httpx

from saimc.ingestion.whatsapp_webhook import ParsedMessage, parse_message

# Techo real medido contra la instancia del operador (2026-08-17, 96 mensajes
# — WAHA ignora en silencio `startCursor`/`endCursor`/`offset`: no hay forma
# de paginar hacia atrás más allá de esto). No se expone como parámetro
# configurable a propósito: no es una preferencia del operador, es lo que la
# API da. Pedir más tampoco es gratis — `limit=500` tardó 172s y tumbó la
# sesión de WAHA en producción; 100 se midió estable (~1s).
_HISTORY_LIMIT = 100


def _parse_preview_entry(entry: Any) -> ParsedMessage | None:
    """Desenvuelve una entrada de `.../messages/preview` y reusa `parse_message`.

    El endpoint envuelve cada mensaje en `{"message": {...}, "reactions":
    {...}}` — una forma distinta a la del webhook (objeto directo, o
    `{"event", "session", "payload"}`), verificada contra la instancia real
    del operador el 2026-08-17. Una vez desenvuelto, el objeto interno trae
    los mismos campos (`from`, `body`, `timestamp`, `fromMe`, `hasMedia`) que
    `parse_message` ya sabe leer sin envoltorio adicional.
    """
    if not isinstance(entry, dict):
        return None
    return parse_message(entry.get("message"))


def parse_history(entries: list[Any]) -> list[ParsedMessage]:
    """Filtra y parsea entradas ya decodificadas de `.../messages/preview`.

    Puro, sin red — mismo criterio que `eltoque.parse_rate`: una entrada sin
    la forma esperada se descarta, nunca rompe el resto del lote.
    """
    parsed = (_parse_preview_entry(entry) for entry in entries)
    return [message for message in parsed if message is not None]


def fetch_channel_history(
    base_url: str,
    api_key: str,
    session: str,
    external_id: str,
    *,
    timeout: float,
    transport: httpx.BaseTransport | None = None,
) -> list[ParsedMessage] | None:
    """Trae y parsea el histórico disponible de un canal.

    `None` si la consulta falló de cualquier forma —red, status distinto de
    200, o un cuerpo que no es la lista que este endpoint siempre
    documenta— para que el llamador distinga "no se pudo consultar" de "se
    consultó bien y no había nada nuevo" (lista vacía). Nunca levanta.

    `transport` es el seam de test (mismo patrón que `eltoque.fetch_rate`):
    en producción se deja en `None` y `httpx.Client` arma el transporte real.
    """
    url = f"{base_url.rstrip('/')}/api/{session}/channels/{external_id}/messages/preview"

    try:
        with httpx.Client(transport=transport, timeout=timeout) as client:
            response = client.get(
                url,
                params={"downloadMedia": "false", "limit": _HISTORY_LIMIT},
                headers={"X-Api-Key": api_key, "accept": "application/json"},
            )
    except httpx.HTTPError:
        return None

    if response.status_code != 200:
        return None

    try:
        payload: Any = response.json()
    except ValueError:
        return None

    if not isinstance(payload, list):
        return None

    return parse_history(payload)
