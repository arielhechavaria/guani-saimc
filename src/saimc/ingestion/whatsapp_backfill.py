"""Backfill puntual del histórico de un canal o grupo de WhatsApp vía WAHA.

Disparador manual desde `/catalog` (docs/decisions.md, 2026-08-17): a
diferencia del webhook (`saimc.web.routers.whatsapp_webhook`), que reacciona
a mensajes nuevos uno por uno a medida que llegan, esto trae de una sola vez
lo que WAHA ya tiene sincronizado — típicamente unos días a unas semanas,
según cuánto se publique en esa fuente en particular.

WAHA expone canales y grupos por endpoints distintos, con formas de respuesta
distintas (medido contra la instancia del operador, 2026-08-18):

    canal (`…@newsletter`)  →  /api/{s}/channels/{id}/messages/preview
                               cada entrada envuelta en {"message", "reactions"}
    grupo (`…@g.us`)        →  /api/{s}/chats/{id}/messages
                               cada entrada es el objeto de mensaje, plano

El ruteo sale del sufijo del JID, no de `Channel.platform`: el sufijo lo
escribe WhatsApp, la plataforma la tipea el operador. Cuando los dos podrían
contradecirse, gana el que no se puede equivocar.

Separa transporte (`fetch_channel_history`) de parseo puro (`parse_history`,
`parse_group_history`) por el mismo motivo que `saimc.exchange_rate.eltoque`:
probar la política de error sin abrir un socket, y probar el parseo sin un
cliente HTTP de por medio.
"""

from __future__ import annotations

from typing import Any

import httpx

from saimc.ingestion.whatsapp_webhook import GROUP_JID_SUFFIX, ParsedMessage, parse_message

# Acá solo interesa distinguir grupo del resto: todo lo que no es grupo ya
# funciona contra el endpoint de canales.
_GROUP_JID_SUFFIX = GROUP_JID_SUFFIX

# WAHA ignora en silencio `startCursor`/`endCursor`/`offset`, así que este
# número es la única palanca que hay para mirar más atrás: no se pagina, se
# pide más de una vez.
#
# El techo lo pone el endpoint de CANALES, medido contra la instancia real el
# 2026-08-20 (WAHA 2026.8.1, motor WEBJS), subiendo de a poco y mirando el
# estado de la sesión después de cada consulta:
#
#     100 → 1-2s          250 → 1.4-2.2s (repetido x3)   300 → 1.5-2.9s
#     350 → colgado >300s, sesión a STARTING
#     400 → 266s y HTTP 500, sesión a FAILED
#     500 → colgado >300s
#
# El corte es entre 300 y 350, y del lado malo no hay degradación suave: la
# consulta no vuelve, `waha_timeout` (30s) corta del lado de SAIMC mucho antes
# —dando "no se pudo consultar"— pero WAHA sigue moliendo y termina
# reiniciando la sesión. Ese reinicio dura entre 10s y 20s y durante esa
# ventana se pierden los webhooks entrantes, que es el costo real. Por eso 250
# y no 300: el valor más alto que se probó bueno queda pegado al filo.
#
# Para GRUPOS el mismo número no cuesta nada: el endpoint sirve desde el store
# local de WEBJS, responde en menos de 0.2s y devuelve lo mismo con `limit` de
# 100, 1000 o 5000. El re-pareo de la sesión vacía ese store, así que hoy son
# unidades de mensajes y crece solo hacia adelante.
_HISTORY_LIMIT = 250


def _parse_preview_entry(entry: Any, sender_key: str | None = None) -> ParsedMessage | None:
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
    return parse_message(entry.get("message"), sender_key)


def _with_text(parsed: Any) -> list[ParsedMessage]:
    """Se queda solo con los mensajes que traen texto.

    El backfill es de TEXTO, y lo sigue siendo desde que `parse_message`
    aceptó las fotos (`docs/decisions.md`, 2026-08-21). El llamador escribe
    `parsed.text` a un `.txt` y lo ingiere: un mensaje con `text` vacío le
    crearía un archivo vacío y una fila sin contenido.

    Traer también las fotos del histórico va en su propio slice, y no es
    gratis: `fetch_channel_history` pide `downloadMedia=false`, y el techo de
    `_HISTORY_LIMIT` se midió con esa bandera apagada. Encenderla cambia el
    costo de la consulta por completo y del lado malo la sesión de WAHA se
    reinicia, perdiendo webhooks entrantes.
    """
    return [message for message in parsed if message is not None and message.text]


def parse_history(entries: list[Any], sender_key: str | None = None) -> list[ParsedMessage]:
    """Filtra y parsea entradas ya decodificadas de `.../messages/preview`.

    Puro, sin red — mismo criterio que `eltoque.parse_rate`: una entrada sin
    la forma esperada se descarta, nunca rompe el resto del lote.
    """
    return _with_text(_parse_preview_entry(entry, sender_key) for entry in entries)


def parse_group_history(
    entries: list[Any], sender_key: str | None = None
) -> list[ParsedMessage]:
    """Filtra y parsea entradas ya decodificadas de `/chats/{id}/messages`.

    A diferencia del endpoint de canales, acá cada entrada YA es el objeto de
    mensaje —sin envoltorio— y trae los mismos campos que `parse_message` lee
    (`from`, `body`, `timestamp`, `fromMe`, `hasMedia`), verificado contra la
    instancia real el 2026-08-18. Por eso no hay un `_parse_group_entry`: no
    hay nada que desenvolver.

    En un grupo, `from` es el JID del GRUPO —no el del emisor—, que es
    justamente lo que el webhook matchea contra `Channel.external_id`. El
    emisor individual viaja aparte en `participant`. Desde el slice B5
    (`docs/spec-slice-B5.md`) `parse_message` lo convierte en un hash con
    clave (`sender_key`) y es lo único que sale de acá: el JID en claro se
    sigue descartando, porque suele ser el teléfono (`spec.md` §10.6).

    Dos descartes pesan mucho más acá que en un canal, medidos el 2026-08-18:
    los mensajes propios del operador (la mitad del lote en un grupo donde él
    también escribe) y las imágenes sin caption (183 de 200 en un grupo que
    publica solo fotos). El primero lo filtra `parse_message`; el segundo lo
    filtra `_with_text` desde el 2026-08-21, porque `parse_message` pasó a
    aceptar la foto para que el WEBHOOK pueda ingerirla — ese camino sí baja
    el archivo, este no.
    """
    return _with_text(parse_message(entry, sender_key) for entry in entries)


def fetch_channel_history(
    base_url: str,
    api_key: str,
    session: str,
    external_id: str,
    *,
    timeout: float,
    transport: httpx.BaseTransport | None = None,
    sender_key: str | None = None,
) -> list[ParsedMessage] | None:
    """Trae y parsea el histórico disponible de un canal o grupo.

    "Channel" acá es la fila de `channels` —que el modelo define como
    «canales y grupos que el operador sigue»—, no el canal de difusión de
    WhatsApp: el mismo `Channel` puede ser una cosa o la otra, y el sufijo de
    su `external_id` decide contra qué endpoint se pregunta.

    `None` si la consulta falló de cualquier forma —red, status distinto de
    200, o un cuerpo que no es la lista que estos endpoints siempre
    documentan— para que el llamador distinga "no se pudo consultar" de "se
    consultó bien y no había nada nuevo" (lista vacía). Nunca levanta.

    `transport` es el seam de test (mismo patrón que `eltoque.fetch_rate`):
    en producción se deja en `None` y `httpx.Client` arma el transporte real.

    `sender_key` es `SAIMC_SENDER_HASH_KEY` y baja tal cual hasta
    `parse_message` (slice B5).
    """
    base = base_url.rstrip("/")
    if external_id.endswith(_GROUP_JID_SUFFIX):
        url = f"{base}/api/{session}/chats/{external_id}/messages"
        parse = parse_group_history
    else:
        url = f"{base}/api/{session}/channels/{external_id}/messages/preview"
        parse = parse_history

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

    return parse(payload, sender_key)
