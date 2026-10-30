"""Página 404 de la capa pública (2026-10-30).

Un navegador que pide una ruta que no existe recibe una página con la marca y
una salida (`/`, `/precios`), no el `{"detail":"Not Found"}` de FastAPI. Lo que
no es un navegador —`curl`, htmx, un `<img>`, el JSON de una API— sigue
recibiendo la respuesta de siempre: se decide por `Accept`, no por la ruta.

Solo toca el 404. Un 401, 409 o 503 conserva su cuerpo.
"""

from fastapi.exception_handlers import http_exception_handler
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import Response

from saimc.web.routers.public import _site_footer


def _quiere_html(request: Request) -> bool:
    # htmx manda `Accept: */*` y `HX-Request`: un parcial no debe recibir una
    # página completa, así que se excluye aunque el navegador acepte HTML.
    if request.headers.get("hx-request"):
        return False
    return "text/html" in request.headers.get("accept", "")


async def not_found_handler(request: Request, exc: StarletteHTTPException) -> Response:
    if exc.status_code != 404 or not _quiere_html(request):
        return await http_exception_handler(request, exc)
    return request.app.state.templates.TemplateResponse(  # type: ignore[no-any-return]
        request, "public_404.html", {"footer": _site_footer()}, status_code=404
    )
