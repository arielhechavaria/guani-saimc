"""Catálogo del foco: qué productos se miden y qué canales se siguen.

Existe por la decisión del 2026-08-15 (`docs/decisions.md`): la Fase 0 se
gobierna por densidad por celda, no por volumen. De ahí salen las dos listas
que administra esta vista.

**Productos piloto.** El conjunto que la curaduría puede extraer. Antes vivía
en dos lugares que podían divergir —una tupla fija en `saimc.progress` y el
alta al vuelo del formulario de curaduría— y el resultado fue un catálogo de
60 productos con duplicados que parten la densidad de una serie en dos
(«Arroz criollo» contra «Arroz»). Ahora el foco es una columna, `is_pilot`, y
este es el único lugar donde se cambia.

**Canales seguidos.** La ronda del operador. La densidad se construye
observando los mismos canales de forma repetida, y una lista escrita es lo que
convierte esa repetición en rutina.

Va detrás de `require_operator` por lo mismo que la curaduría: cambia qué mide
el sistema.
"""

import uuid

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from saimc.models import Channel, Platform, PriceHistory, Product
from saimc.web.auth import require_operator
from saimc.web.deps import get_db

router = APIRouter(dependencies=[Depends(require_operator)])

# Mismos defaults que usaba el alta al vuelo de la curaduría: `category` y
# `unit` son NOT NULL sin default en el modelo, y trabar un alta por un dato
# secundario sería repetir el error que este slice viene a corregir.
_DEFAULT_CATEGORY = "sin categoría"
_DEFAULT_UNIT = "unidad"

_REDIRECT = status.HTTP_303_SEE_OTHER


def _back() -> RedirectResponse:
    return RedirectResponse("/catalog", status_code=_REDIRECT)


@router.get("/catalog", response_class=HTMLResponse)
def catalog(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """Las dos listas, con el conteo de observaciones de cada producto.

    El conteo no es decoración: es lo que permite decidir a qué producto
    promover con evidencia en vez de por intuición. «Azúcar blanca» llegó a
    once observaciones antes de que nadie la declarara piloto.
    """
    counts = dict(
        db.execute(
            select(PriceHistory.product_id, func.count())
            .group_by(PriceHistory.product_id)
        ).all()
    )

    products = list(
        db.execute(
            select(Product).order_by(Product.is_pilot.desc(), Product.name)
        ).scalars()
    )
    channels = list(
        db.execute(
            select(Channel).order_by(Channel.is_active.desc(), Channel.name)
        ).scalars()
    )

    return request.app.state.templates.TemplateResponse(
        request,
        "catalog.html",
        {
            "products": [(product, counts.get(product.id, 0)) for product in products],
            "channels": channels,
            "platforms": list(Platform),
            "pilot_count": sum(1 for product in products if product.is_pilot),
        },
    )


@router.post("/catalog/products")
async def create_product(request: Request, db: Session = Depends(get_db)) -> RedirectResponse:
    """Alta de producto, siempre como piloto.

    Si lo agregás acá es porque lo querés medir — eso es justamente lo que
    distingue este alta del que se quitó de la curaduría, donde el producto
    nacía como efecto colateral de tipear un nombre.

    Un nombre que ya existe no crea una fila nueva: el duplicado es lo que
    partió «Azúcar blanca» de «Azúcar blanco». Se promueve el existente, que
    es lo que el operador quería de todas formas.
    """
    form = await request.form()
    name = str(form.get("name", "")).strip()
    if not name:
        return _back()

    existing = db.execute(select(Product).where(func.lower(Product.name) == name.lower())).scalar_one_or_none()
    if existing is not None:
        existing.is_pilot = True
        db.flush()
        return _back()

    db.add(
        Product(
            name=name,
            category=str(form.get("category", "")).strip() or _DEFAULT_CATEGORY,
            unit=str(form.get("unit", "")).strip() or _DEFAULT_UNIT,
            aliases=[],
            is_pilot=True,
        )
    )
    db.flush()
    return _back()


@router.post("/catalog/products/{product_id}/pilot")
async def toggle_pilot(
    product_id: uuid.UUID, request: Request, db: Session = Depends(get_db)
) -> RedirectResponse:
    """Entra o sale del foco según venga marcado el checkbox.

    Sacar un producto del foco no borra nada: conserva sus precios históricos
    y sigue en el catálogo. Lo único que pierde es la capacidad de recibir
    precios nuevos.
    """
    product = db.get(Product, product_id)
    if product is None:
        return _back()

    form = await request.form()
    product.is_pilot = bool(form.get("is_pilot"))
    db.flush()
    return _back()


@router.post("/catalog/channels")
async def create_channel(request: Request, db: Session = Depends(get_db)) -> RedirectResponse:
    form = await request.form()
    name = str(form.get("name", "")).strip()
    if not name:
        return _back()

    platform = str(form.get("platform", "")).strip()
    if platform not in {member.value for member in Platform}:
        platform = Platform.OTHER.value

    db.add(
        Channel(
            name=name,
            platform=platform,
            url=str(form.get("url", "")).strip() or None,
            notes=str(form.get("notes", "")).strip() or None,
            is_active=True,
        )
    )
    db.flush()
    return _back()


@router.post("/catalog/channels/{channel_id}/active")
async def toggle_channel(
    channel_id: uuid.UUID, request: Request, db: Session = Depends(get_db)
) -> RedirectResponse:
    """Apaga o enciende un canal. Nunca lo borra: los registros crudos ya
    ingeridos siguen refiriéndose a él por nombre."""
    channel = db.get(Channel, channel_id)
    if channel is None:
        return _back()

    form = await request.form()
    channel.is_active = bool(form.get("is_active"))
    db.flush()
    return _back()
