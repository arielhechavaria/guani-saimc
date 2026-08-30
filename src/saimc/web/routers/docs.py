"""Gestor de documentos de Guani (`/docs`).

Lee los documentos del repositorio desde el navegador, sin SSH y sin GitHub. El
repositorio es privado, así que hasta ahora la única forma de leerlos era
clonarlos.

**Privado a propósito.** Todo el router va detrás de `require_operator`, la
misma HTTP Basic de la curaduría, porque el catálogo incluye `pitch-v3.5.md`
—el documento de inversión— y `decisions.md`, que ya filtró teléfonos reales
una vez (docs/decisions.md, 2026-08-11). No confundir con la página pública de
metodología que §6.4.1 exige: esa es otro contenido, escrito para afuera, y no
se hace abriendo esta ruta.

**El markdown se renderiza en el navegador**, con `marked` vendorizado. El
servidor manda el archivo tal cual por `/docs/{slug}/raw` y el cliente lo
convierte a HTML. Se eligió así para no sumar una dependencia de Python al
proyecto: la alternativa era `pip install markdown`, descartada por el operador
el 2026-08-30.

Degradación sin JS: `<noscript>` deja el enlace al `.md` crudo, así que la
lectura sigue siendo posible aunque austera. Es más flojo que el resto del
proyecto —la regla del 2026-08-22 pide degradar a enlace o formulario— y queda
declarado como el costo de no meter un renderer del lado del servidor.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, PlainTextResponse

from saimc.web import docs_catalog
from saimc.web.auth import require_operator

router = APIRouter(dependencies=[Depends(require_operator)])


@router.get("/docs", response_class=HTMLResponse)
def docs_index(request: Request) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse(
        request,
        "docs_index.html",
        {"stats": docs_catalog.catalog_stats()},
    )


def _no_encontrado(request: Request, motivo: str, ruta: str = "") -> HTMLResponse:
    """404 como página, no como JSON.

    `/docs/<slug>` se escribe a mano y se comparte, así que equivocarse es
    normal. Un `{"detail": ...}` pelado deja al operador sin menú y sin forma de
    volver; esto le devuelve la lista de lo que sí hay.
    """
    return request.app.state.templates.TemplateResponse(
        request,
        "docs_missing.html",
        {"motivo": motivo, "ruta": ruta, "catalogo": docs_catalog.CATALOG},
        status_code=status.HTTP_404_NOT_FOUND,
    )


@router.get("/docs/{slug}", response_class=HTMLResponse)
def docs_view(request: Request, slug: str) -> HTMLResponse:
    entry = docs_catalog.find(slug)
    if entry is None:
        return _no_encontrado(request, motivo="catalogo")

    stat = docs_catalog.stat_document(entry)
    if not stat.exists:
        # Está en el catálogo pero no en el disco: alguien lo renombró o lo
        # movió. Se distingue del slug inventado porque se arregla en otro lado
        # —el catálogo, no la URL— y el operador tiene que saber cuál de los dos
        # es.
        return _no_encontrado(request, motivo="disco", ruta=entry.relative_path)

    return request.app.state.templates.TemplateResponse(
        request,
        "docs_view.html",
        {"entry": entry, "stat": stat},
    )


@router.get("/docs/{slug}/raw", response_class=PlainTextResponse)
def docs_raw(slug: str) -> PlainTextResponse:
    """El markdown tal cual. Lo consume el renderer del navegador y también el
    botón de descarga.

    `text/plain` y no `text/markdown`: el navegador tiene que mostrarlo, no
    ofrecer una descarga, para que el `<noscript>` sirva de algo.
    """
    entry = docs_catalog.find(slug)
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Documento desconocido")

    path = entry.path
    if not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Documento no disponible"
        )

    return PlainTextResponse(
        path.read_text(encoding="utf-8"),
        media_type="text/plain; charset=utf-8",
    )
