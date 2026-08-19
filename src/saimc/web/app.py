"""Aplicación FastAPI de SAIMC.

`app` a nivel de módulo y no una factory: un solo desarrollador, un solo
despliegue (CLAUDE.md, decisión 1) — no hay necesidad de instanciar la app
más de una vez.
"""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from saimc.web.routers import catalog, control, curation, whatsapp_webhook

app = FastAPI(title="SAIMC — Cola de curaduría")

# Resuelto con `Path(__file__)` y no relativo al cwd: así funciona sin
# importar desde dónde se lance uvicorn. Vive en `app.state` (y no como
# variable de módulo importada por los routers) para que `app.py` sea quien
# monta la app sin crear un import circular con `routers/curation.py`.
app.state.templates = Jinja2Templates(
    directory=str(Path(__file__).parent / "templates")
)


def _havana(value: datetime) -> str:
    """Filtro Jinja: formatea un datetime en hora de La Habana, no UTC.

    Mismo criterio que `exchange_rate.service.anchor_date` — `captured_at` se
    guarda en UTC, y mostrarlo tal cual corre el reloj hasta 5 horas contra lo
    que el operador vio en su teléfono. Degrada a UTC si falta la base de
    datos de zonas horarias, en vez de romper el render.
    """
    try:
        habana = ZoneInfo("America/Havana")
    except ZoneInfoNotFoundError:
        return value.strftime("%Y-%m-%d %H:%M")
    return value.astimezone(habana).strftime("%Y-%m-%d %H:%M")


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
