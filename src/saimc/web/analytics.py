"""Instrumentación mínima de la capa pública, para spec.md §9 (Fase 1: «100
usuarios activos diarios»). Hoy no hay ninguna forma de saber si esa meta se
cumple: cero analítica en la home. Esto no la mide —eso sigue siendo trabajo
de agregación manual sobre el log— pero deja de estar ciego.

Deliberadamente NO es un producto de analítica: sin tabla nueva (no toca el
esquema — CLAUDE.md reserva el flujo de especificación completo para eso),
sin JS de tracking, sin cookies. Una línea de log INFO por visita real a una
vista pública, agregable a mano con algo como:

    journalctl -u saimc --since today | grep 'public.pageview' \\
        | grep -oP 'visitor=\\K\\S+' | sort -u | wc -l

`visitor` es un hash de IP+día, nunca la IP en texto plano — no es
criptografía fuerte a propósito: el objetivo es no dejar la IP en claro en
los logs del VPS, no blindarla contra quien ya tiene acceso al servidor.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)

# Vistas públicas reales que un visitante abre — no assets, no rutas de
# operador (/control, /curation, /catalog, /docs, /marketing, webhooks).
_TRACKED_PATH_PREFIXES = ("/producto/", "/anuncio/")
# `/` es la landing y `/precios` la home de precios (tablero/mapa/explorar)
# desde el 2026-10-05: las dos son visitas reales y se cuentan por separado.
_TRACKED_EXACT_PATHS = frozenset({"/", "/precios"})


def es_vista_publica_rastreable(path: str) -> bool:
    return path in _TRACKED_EXACT_PATHS or path.startswith(_TRACKED_PATH_PREFIXES)


def hash_visitante(ip: str, dia: str) -> str:
    cruda = f"saimc-analytics:{dia}:{ip}".encode()
    return hashlib.sha256(cruda).hexdigest()[:12]


async def registrar_visitas_publicas(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Middleware ASGI: deja un rastro por cada GET exitoso a una vista
    pública. Excluye los `hx-get` de la propia página (chips, selector de
    provincia): son la misma visita reabriendo `/precios`, no una nueva."""
    response = await call_next(request)

    if (
        request.method == "GET"
        and response.status_code == 200
        and request.headers.get("hx-request") is None
        and es_vista_publica_rastreable(request.url.path)
    ):
        ip = request.client.host if request.client else "desconocido"
        dia = datetime.now(UTC).date().isoformat()
        logger.info(
            "public.pageview path=%s day=%s visitor=%s",
            request.url.path,
            dia,
            hash_visitante(ip, dia),
        )

    return response
