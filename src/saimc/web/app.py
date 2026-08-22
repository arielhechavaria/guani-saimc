"""Aplicación FastAPI de SAIMC.

`app` a nivel de módulo y no una factory: un solo desarrollador, un solo
despliegue (CLAUDE.md, decisión 1) — no hay necesidad de instanciar la app
más de una vez.
"""

import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from saimc.web.routers import catalog, control, curation, public, whatsapp_webhook

# El despliegue lanza `uvicorn saimc.web.app:app` sin `--log-config`, y uvicorn
# configura solo sus propios loggers: el raíz queda sin handler. Sin esto, un
# INFO de un módulo de SAIMC no llega a journald y un WARNING sale por
# `logging.lastResort` — a stderr, sí, pero pelado, sin nivel ni nombre de
# logger, o sea imposible de filtrar. Verificado contra el arranque real el
# 2026-08-21.
#
# `basicConfig` no pisa nada: es un no-op si el raíz ya tiene handler, y los
# loggers de uvicorn traen los suyos. Va acá y no en un módulo de configuración
# porque este es el único punto de entrada del proceso servidor.
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(name)s %(message)s",
)

app = FastAPI(title="SAIMC — Cola de curaduría")

# Compresión de respuestas. Faltaba: hasta el 2026-08-22 todo viajaba sin
# comprimir, y `saimc-material.css` son 28.672 B en disco contra 6.079 B
# gzipeados. Son ~22 KB en cada primera carga contra la restricción que gobierna
# toda esta capa (CLAUDE.md: conexión lenta e intermitente).
#
# `compresslevel=6` y no el 9 que trae Starlette por defecto: medido sobre el
# CSS real, el 9 da 6.079 B y el 6 da 6.113 B. Son 34 bytes —medio por mil— a
# cambio de bastante más CPU en CADA respuesta. El 6 es el punto donde la curva
# se aplana; el 1 ya se nota (7.548 B).
#
# `minimum_size` queda en el default de 500 B: por debajo de eso el encabezado
# `Content-Encoding` y el overhead del gzip se comen la ganancia, y los
# fragmentos que devuelva el dashboard van a caer muchas veces ahí.
#
# Las fuentes NO se comprimen dos veces: `font/woff2` está en el
# `DEFAULT_EXCLUDED_CONTENT_TYPES` de Starlette, y `mimetypes` resuelve `.woff2`
# a ese tipo exacto, así que StaticFiles las etiqueta bien y el middleware las
# saltea solo. El SVG sí entra (`image/svg+xml` no está excluido), que es lo que
# queremos para el mapa de calor: es texto y comprime muy bien.
app.add_middleware(GZipMiddleware, minimum_size=500, compresslevel=6)

# Resuelto con `Path(__file__)` y no relativo al cwd: así funciona sin
# importar desde dónde se lance uvicorn. Vive en `app.state` (y no como
# variable de módulo importada por los routers) para que `app.py` sea quien
# monta la app sin crear un import circular con `routers/curation.py`.
app.state.templates = Jinja2Templates(
    directory=str(Path(__file__).parent / "templates")
)

# StaticFiles no manda cache-busting: un navegador (sobre todo uno móvil,
# con conexión intermitente que prefiere no volver a pedir un archivo) puede
# quedarse con una versión vieja de `saimc-material.css` después de un
# deploy. `css_version()` se llama por request, no una vez al arrancar —
# igual que Jinja2Templates relee cada plantilla del disco — así que un
# cambio de CSS sigue sin pedir reinicio del servicio y además invalida el
# caché del navegador solo, sin que el operador tenga que limpiarlo a mano.
_CSS_PATH = Path(__file__).parent / "static" / "saimc-material.css"


def _css_version() -> int:
    return int(_CSS_PATH.stat().st_mtime)


app.state.templates.env.globals["css_version"] = _css_version

# htmx vendorizado (ADR 001, `docs/decisions/001-stack-python-unico.md`). No se
# carga en ninguna plantilla todavía: el dashboard es quien lo va a usar, y la
# curaduría se queda deliberadamente sin él. Ver el comentario de
# `{% block scripts %}` en `_shell.html` para cómo se opta.
#
# Procedencia, verificada el 2026-08-22: el archivo es `package/dist/htmx.min.js`
# del tarball oficial de npm
# (https://registry.npmjs.org/htmx.org/-/htmx.org-2.0.10.tgz), cuyo `integrity`
# sha512 publicado se comprobó ANTES de extraerlo, y salió byte a byte idéntico
# al que sirve unpkg. `tests/test_static_assets.py` fija el sha256, así que
# cambiar el archivo sin cambiar la versión pone la suite en rojo.
#
# El cache-busting va por versión y no por mtime como el CSS: este archivo es
# inmutable entre bumps, y un `git clone` le cambia el mtime sin cambiar un byte.
HTMX_VERSION = "2.0.10"

app.state.templates.env.globals["htmx_version"] = lambda: HTMX_VERSION


def _havana(value: datetime, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Filtro Jinja: formatea un datetime en hora de La Habana, no UTC.

    Mismo criterio que `exchange_rate.service.anchor_date` — `captured_at` se
    guarda en UTC, y mostrarlo tal cual corre el reloj hasta 5 horas contra lo
    que el operador vio en su teléfono. Degrada a UTC si falta la base de
    datos de zonas horarias, en vez de romper el render.

    `fmt` con default de fecha+hora completas (uso en `curation_history.html`);
    la cola de curaduría pide solo `%H:%M` junto al date-picker de
    `published_date`, que ya muestra la fecha por su cuenta (docs/decisions.md,
    2026-08-19) — solo visible, `published_date` sigue siendo únicamente
    fecha, sin cambio de esquema.
    """
    try:
        habana = ZoneInfo("America/Havana")
    except ZoneInfoNotFoundError:
        return value.strftime(fmt)
    return value.astimezone(habana).strftime(fmt)


app.state.templates.env.filters["havana"] = _havana

# Bootstrap CSS vendorizado (no CDN): conexión lenta e intermitente
# (CLAUDE.md), y esta es una herramienta de uso diario, no una carga puntual.
app.mount(
    "/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static"
)

app.include_router(public.router)
app.include_router(curation.router)
app.include_router(control.router)
app.include_router(catalog.router)
app.include_router(whatsapp_webhook.router)
