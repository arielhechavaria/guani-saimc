"""`/robots.txt` y `/sitemap.xml` (slice `seo-basico`).

Abiertos a propósito, sin credenciales: un buscador no tiene contraseña. No
están en `access._PUBLIC_EXACT`, así que la puerta del sitio tampoco los
cierra; si la puerta está encendida, el sitemap lista URL que redirigen al
formulario y no rinde hasta apagarla (`docs/plan-bloque-mvp.md`, T10).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from saimc.progress import pilot_product_names
from saimc.web import seo
from saimc.web.deps import get_db

router = APIRouter()


@router.get("/robots.txt", include_in_schema=False)
def robots(request: Request) -> Response:
    return Response(seo.robots_txt(request), media_type="text/plain; charset=utf-8")


@router.get("/sitemap.xml", include_in_schema=False)
def sitemap(request: Request, db: Session = Depends(get_db)) -> Response:
    xml = seo.sitemap_xml(request, pilot_product_names(db))
    return Response(xml, media_type="application/xml; charset=utf-8")


# Search Console verifica la propiedad pidiendo este archivo en la raíz y
# comparando el contenido exacto. Es un dato fijo que entrega Google, no un
# secreto: quitarlo desverifica la propiedad.
GOOGLE_VERIFICATION_FILE = "google06eec30faab9a5d5.html"


@router.get(f"/{GOOGLE_VERIFICATION_FILE}", include_in_schema=False)
def google_verification() -> Response:
    return Response(
        f"google-site-verification: {GOOGLE_VERIFICATION_FILE}",
        media_type="text/html; charset=utf-8",
    )
