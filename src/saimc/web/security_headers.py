"""Cabeceras de seguridad para toda respuesta (`docs/decisions.md`, 2026-10-19).

Un middleware y no cabeceras por ruta: lo que se olvida en una ruta nueva es
justo lo que un atacante busca. Cubre también los errores (404, 401, 503).

**Sin `script-src` ni `default-src` a propósito.** Las plantillas y htmx usan
código y estilos en línea; una CSP estricta rompería el sitio y habría que
reescribir la capa pública con nonces. Lo que sí cierra esta política es lo
barato y seguro: quién puede incrustar el sitio, qué `<base>` y qué `<object>`
se aceptan, y adónde puede apuntar un formulario.

`frame-ancestors 'self'` (y `X-Frame-Options: SAMEORIGIN`) y no `'none'`:
`/marketing` incrusta sus propias tarjetas en un `<iframe>` del mismo origen.
"""

from collections.abc import Awaitable, Callable

from starlette.requests import Request
from starlette.responses import Response

HSTS_MAX_AGE = 365 * 24 * 60 * 60

_CSP = "frame-ancestors 'self'; object-src 'none'; base-uri 'self'; form-action 'self'"

_BASE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Content-Security-Policy": _CSP,
}


def _es_https(request: Request) -> bool:
    # Detrás del proxy la petición llega a uvicorn en HTTP; el esquema real
    # viaja en X-Forwarded-Proto.
    forwarded = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    return (forwarded or request.url.scheme) == "https"


async def add_security_headers(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    response = await call_next(request)
    for name, value in _BASE_HEADERS.items():
        response.headers.setdefault(name, value)
    # HSTS solo por HTTPS: en HTTP el navegador lo ignora, y en desarrollo local
    # no queremos que fije `localhost` a HTTPS.
    if _es_https(request):
        response.headers.setdefault("Strict-Transport-Security", f"max-age={HSTS_MAX_AGE}")
    return response
