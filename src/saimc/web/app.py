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
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from saimc.web.routers import catalog, control, curation, whatsapp_webhook

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

app.include_router(curation.router)
app.include_router(control.router)
app.include_router(catalog.router)
app.include_router(whatsapp_webhook.router)
