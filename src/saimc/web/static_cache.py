"""`/static` con `Cache-Control` (slice `seo-basico`).

`StaticFiles` solo manda `ETag` y `Last-Modified`: el navegador igual vuelve a
preguntar en cada visita, y en una conexión lenta e intermitente (CLAUDE.md)
cada pregunta es un viaje. Dos reglas:

- Con `?v=` en la URL (las hojas CSS, `app.py` las versiona por mtime) el
  archivo cambia de URL cuando cambia, así que es inmutable: un año.
- Sin versión (fuentes, íconos, la tarjeta de compartir) un día: si se
  regenera, se ve al día siguiente sin que nadie limpie el caché.

Un 404 no se marca: cachear «no existe» es esconder el archivo que se suba luego.
"""

from __future__ import annotations

from urllib.parse import parse_qs

from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

IMMUTABLE = "public, max-age=31536000, immutable"
ONE_DAY = "public, max-age=86400"


class CachedStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code in (200, 304):
            query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
            response.headers["Cache-Control"] = IMMUTABLE if "v" in query else ONE_DAY
        return response
