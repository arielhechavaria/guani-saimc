"""Aplicación FastAPI de SAIMC.

`app` a nivel de módulo y no una factory: un solo desarrollador, un solo
despliegue (CLAUDE.md, decisión 1) — no hay necesidad de instanciar la app
más de una vez.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates

from saimc.web.routers import curation

app = FastAPI(title="SAIMC — Cola de curaduría")

# Resuelto con `Path(__file__)` y no relativo al cwd: así funciona sin
# importar desde dónde se lance uvicorn. Vive en `app.state` (y no como
# variable de módulo importada por los routers) para que `app.py` sea quien
# monta la app sin crear un import circular con `routers/curation.py`.
app.state.templates = Jinja2Templates(
    directory=str(Path(__file__).parent / "templates")
)

app.include_router(curation.router)
