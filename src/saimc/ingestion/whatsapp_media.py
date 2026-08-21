"""Descarga del archivo adjunto de un mensaje de WhatsApp desde WAHA.

Captura de fotos sin caption (`docs/decisions.md`, 2026-08-21). Cuando WAHA
descarga el adjunto de un mensaje, el payload del webhook trae `media.url`
apuntando a su propio almacén (`/api/files/...`), y esa URL hay que pedirla
con la misma `X-Api-Key` que el resto de su API.

Módulo aparte de `whatsapp_webhook` a propósito: ese declara en su docstring
que sus funciones son puras y no tocan la red ni el filesystem, y acá se abre
un socket. Separar transporte de parseo es el mismo criterio que
`exchange_rate.eltoque` y `ingestion.whatsapp_backfill`: probar la política de
error sin red, y probar el parseo sin un cliente HTTP de por medio.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import httpx


# Las tres formas de nombrar "esta misma máquina". Se normalizan a una sola
# porque en el despliegue real se mezclan de verdad: WAHA arma `media.url` con
# el hostname que tiene configurado (`http://localhost:3000/...`) mientras que
# el `.env` del operador declara `SAIMC_WAHA_BASE_URL=http://127.0.0.1:3000`.
# Comparando las cadenas tal cual, TODAS las fotos se descartarían en silencio
# — el peor modo de falla para este slice: no rompe nada y no captura nada.
_LOOPBACK_ALIASES = frozenset({"localhost", "127.0.0.1", "::1"})


def _same_host(uno: str | None, otro: str | None) -> bool:
    if uno is None or otro is None:
        return False
    if uno in _LOOPBACK_ALIASES and otro in _LOOPBACK_ALIASES:
        return True
    return uno == otro


def is_trusted_media_url(url: str, base_url: str | None) -> bool:
    """¿Esta URL apunta al WAHA que el operador configuró?

    La URL viaja DENTRO del payload que la firma HMAC protege, así que un
    tercero no la puede inyectar sin la clave. Esta comprobación es la segunda
    línea: si la clave alguna vez se filtra, el webhook no debe convertirse en
    un cliente HTTP que va a buscar lo que le digan, contra la red interna del
    VPS (SSRF).

    Compara esquema, host y puerto — no el path, que es donde WAHA nombra el
    archivo y varía en cada mensaje. El puerto sigue importando aunque el host
    sea loopback: otro servicio en la misma máquina no es el WAHA configurado.

    Sin `waha_base_url` configurado devuelve `False`: cierra en falso, mismo
    criterio que `require_operator` y que la clave del webhook.
    """
    if not base_url:
        return False
    esperado = urlsplit(base_url.rstrip("/"))
    recibido = urlsplit(url)
    return (
        recibido.scheme == esperado.scheme
        and _same_host(recibido.hostname, esperado.hostname)
        and recibido.port == esperado.port
    )


def fetch_media(
    url: str,
    api_key: str,
    *,
    timeout: float,
    transport: httpx.BaseTransport | None = None,
) -> bytes | None:
    """Trae los bytes del archivo, o `None` si no se pudo de cualquier forma.

    Nunca levanta: la falla se traduce a `None` para que el webhook devuelva
    200 y WAHA no lo reintente en loop. Perder una foto puntual porque WAHA
    estaba reiniciando es barato; devolverle un 500 a cada evento no lo es.

    Una respuesta vacía también es `None`: un archivo de cero bytes no es
    evidencia, y crearía una fila cuyo `file_path` apunta a nada.

    `transport` es el seam de test (mismo patrón que `eltoque.fetch_rate`): en
    producción se deja en `None` y `httpx.Client` arma el transporte real.
    """
    try:
        with httpx.Client(transport=transport, timeout=timeout) as client:
            response = client.get(url, headers={"X-Api-Key": api_key})
    except httpx.HTTPError:
        return None

    if response.status_code != 200 or not response.content:
        return None
    return response.content
