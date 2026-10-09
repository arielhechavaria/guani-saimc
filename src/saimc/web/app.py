"""Aplicación FastAPI de SAIMC.

`app` a nivel de módulo y no una factory: un solo desarrollador, un solo
despliegue (CLAUDE.md, decisión 1) — no hay necesidad de instanciar la app
más de una vez.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware

from saimc.config import get_settings
from saimc.ingestion.sender import warn_if_key_too_short
from saimc.validation.jev_signal import warn_if_enabled_without_key
from saimc.web.analytics import registrar_visitas_publicas
from saimc.web.routers import (
    catalog,
    control,
    curation,
    docs,
    marketing,
    public,
    whatsapp_webhook,
)

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

# `docs_url`/`redoc_url`/`openapi_url` en None, por dos motivos que apuntan al
# mismo lado (docs/decisions.md, 2026-08-30):
#
# 1. **Cierra una fuga.** El servicio escucha en 0.0.0.0:8000 sin proxy delante,
#    así que Swagger y `/openapi.json` se servían SIN AUTENTICACIÓN y publicaban
#    el mapa completo de endpoints —`/curation/*`, `/catalog/*` y sobre todo
#    `/webhooks/whatsapp`— a cualquiera que preguntara. Verificado contra
#    producción antes de apagarlo. La firma HMAC del webhook sigue siendo la
#    defensa real; esto le saca el cartel de encima.
# 2. **Libera `/docs`** para el gestor de documentos, que es una vista del
#    operador y no una consola de API.
#
# Nada del proyecto los consume: un solo operador, sin clientes de API, y
# Angular está diferido a la Fase 2 (§13.1). Si algún día hace falta la consola,
# vuelve detrás de `require_operator` y en otra ruta.


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Avisos de configuración al arrancar el servidor.

    Dos, y ninguno incluye una clave:

    - una `SAIMC_SENDER_HASH_KEY` demasiado corta se trata como ausente
      (`docs/spec-slice-B5.md`), y sin este aviso el operador creería que el
      emisor de los grupos se está guardando;
    - `SAIMC_LLM_JEV_ENABLED` encendido sin `SAIMC_VERCEL_API_KEY`
      (`docs/spec-slice-A6b.md`): Jev queda inactivo, que es un estado
      soportado, pero el operador creería que la señal se está juntando.
    """
    settings = get_settings()
    warn_if_key_too_short(settings.sender_hash_key)
    warn_if_enabled_without_key(settings)
    yield


app = FastAPI(
    title="SAIMC — Cola de curaduría",
    lifespan=_lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

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

# Instrumentación mínima de la capa pública (spec.md §9, Fase 1: «100
# usuarios activos diarios») — ver el docstring de `analytics.py`. Sin esto
# no hay ninguna forma de saber si esa meta se cumplió o no.
app.add_middleware(BaseHTTPMiddleware, dispatch=registrar_visitas_publicas)

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

# `saimc-public.css` es la hoja liviana que carga `_public_shell.html` — el
# subconjunto de tokens/reset/fuentes que la home pública necesita, sin
# arrastrar los ~22 KB del CSS del operador. Mismo motivo que `_CSS_PATH`
# para el versionado por mtime: cambiarla no debe pedir reinicio ni dejar al
# navegador con una copia vieja.
_PUBLIC_CSS_PATH = Path(__file__).parent / "static" / "saimc-public.css"

# Geometría de las 16 provincias para el mapa Leaflet (2026-09-12,
# `tools/build_cuba_geojson.py`), lon/lat de Natural Earth. Mismo motivo de
# versionado por mtime: el navegador lo cachea (66 KB que no cambian salvo
# que se regenere el asset) sin quedarse con una copia vieja si algún día
# cambia la geometría.
_GEOJSON_PATH = Path(__file__).parent / "static" / "cuba_provincias.geojson"


def _css_version() -> int:
    return int(_CSS_PATH.stat().st_mtime)


def _public_css_version() -> int:
    return int(_PUBLIC_CSS_PATH.stat().st_mtime)


def _geojson_version() -> int:
    return int(_GEOJSON_PATH.stat().st_mtime)


app.state.templates.env.globals["css_version"] = _css_version
app.state.templates.env.globals["public_css_version"] = _public_css_version
app.state.templates.env.globals["geojson_version"] = _geojson_version

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

# marked vendorizado, para el gestor de documentos (`/docs`). Renderiza el
# markdown en el navegador y por eso no hace falta una dependencia de Python:
# `pip install markdown` se descartó el 2026-08-30.
#
# Procedencia, verificada el 2026-08-30: el archivo es `package/lib/marked.umd.js`
# del tarball oficial de npm
# (https://registry.npmjs.org/marked/-/marked-18.0.11.tgz), cuyo `integrity`
# sha512 publicado se comprobó ANTES de extraerlo. MIT.
# `tests/test_static_assets.py` fija su sha256 y su tamaño, igual que con htmx.
#
# Cache-busting por versión y no por mtime, mismo motivo que htmx: el archivo es
# inmutable entre bumps y un `git clone` le cambia el mtime sin cambiar un byte.
MARKED_VERSION = "18.0.11"

app.state.templates.env.globals["marked_version"] = lambda: MARKED_VERSION

# Leaflet vendorizado, para el mapa interactivo de la home pública
# (docs/decisions.md, 2026-09-12 — mapa de tiles en vivo, apartamiento
# consciente de la decisión cerrada 1: el operador lo pidió sabiendo el
# costo, después de que el fondo de relieve estático se pixelara al hacer
# zoom a una provincia).
#
# Procedencia, verificada el 2026-09-12: los archivos son `package/dist/leaflet.js`
# y `package/dist/leaflet.css` del tarball oficial de npm
# (https://registry.npmjs.org/leaflet/-/leaflet-1.9.4.tgz), cuyo `integrity`
# sha512 publicado se comprobó ANTES de extraerlo. BSD-2-Clause.
# `tests/test_static_assets.py` fija su sha256 y su tamaño, igual que con htmx
# y marked. Los íconos de `dist/images/` viajan aparte (no tienen hash de
# npm publicado, son binarios chicos): `layers*.png` los pide `leaflet.css`
# para el control de capas, `marker*.png` para el marcador por defecto —
# ninguno de los dos se usa hoy (el mapa es solo polígonos de provincia),
# pero se vendorizan igual para que la hoja no intente pedirlos a la red y
# los reciba en silencio como 404 rotos.
#
# Cache-busting por versión y no por mtime, mismo motivo que htmx: los
# archivos son inmutables entre bumps y un `git clone` les cambia el mtime
# sin cambiar un byte.
LEAFLET_VERSION = "1.9.4"

app.state.templates.env.globals["leaflet_version"] = lambda: LEAFLET_VERSION


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
app.include_router(marketing.router)
app.include_router(docs.router)
app.include_router(whatsapp_webhook.router)
