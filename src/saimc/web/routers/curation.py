"""Cola de curaduría — whitepaper §6.1, Etapa 1c y Etapa 2 (líneas 571-611).

Primera y única interfaz humana del sistema en Fase 0: presenta un elemento
`pending` por vez, propone una extracción cuando hay texto, y el operador
confirma o corrige. Cada confirmación produce la fila de `prices_history`
que alimenta H0.

Nota de esquema: `prices_history` no tiene columna `unit` (whitepaper §7.2) —
la cantidad se asume en la unidad canónica del producto (`Product.unit`). El
campo "unidad" del formulario solo se usa para mostrarle al operador lo que
el motor de extracción propuso; no se persiste. Ver CLAUDE.md: este slice no
agrega columnas nuevas.
"""

import uuid
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from saimc.config import get_settings
from saimc.extraction import ocr
from saimc.extraction.engine import ExtractionProposal, propose
from saimc.models import (
    ContentType,
    Currency,
    PriceHistory,
    Product,
    ProcessingStatus,
    Province,
    RawIngestion,
)
from saimc.web.deps import get_db

router = APIRouter()


def _values_from_proposal(proposal: ExtractionProposal | None) -> dict[str, str]:
    """Traduce una `ExtractionProposal` a strings listos para prellenar el form."""
    if proposal is None:
        return {
            "product_name": "",
            "product_category": "",
            "product_unit": "",
            "price": "",
            "currency": Currency.CUP.value,
            "quantity": "",
            "unit": "",
            "province_id": "",
            "source_reference": "",
            "original_text": "",
        }
    return {
        "product_name": proposal.product.name if proposal.product else "",
        "product_category": "",
        "product_unit": "",
        "price": str(proposal.price) if proposal.price is not None else "",
        "currency": proposal.currency,
        "quantity": str(proposal.quantity) if proposal.quantity is not None else "",
        "unit": proposal.unit or "",
        "province_id": str(proposal.province.id) if proposal.province else "",
        "source_reference": "",
        "original_text": "",
    }


def _oldest_pending(db: Session) -> RawIngestion | None:
    stmt = (
        select(RawIngestion)
        .where(RawIngestion.processing_status == ProcessingStatus.PENDING)
        .order_by(RawIngestion.captured_at.asc())
        .limit(1)
    )
    return db.execute(stmt).scalars().first()


def _all_products(db: Session) -> list[Product]:
    return list(db.execute(select(Product).order_by(Product.name)).scalars().all())


def _all_provinces(db: Session) -> list[Province]:
    return list(db.execute(select(Province).order_by(Province.name)).scalars().all())


def _render_queue(
    request: Request,
    *,
    ingestion: RawIngestion | None,
    products: list[Product],
    provinces: list[Province],
    values: dict[str, str],
    error: str | None,
    status_code: int = 200,
) -> HTMLResponse:
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "curation.html",
        {
            "ingestion": ingestion,
            "products": products,
            "provinces": provinces,
            "values": values,
            "error": error,
            "currencies": [c.value for c in Currency],
        },
        status_code=status_code,
    )


@router.get("/curation", response_class=HTMLResponse)
def curation_queue(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """Muestra el elemento `pending` más antiguo, con propuesta pre-llenada si hay texto.

    Para imágenes sin `original_text` (el caso normal: el OCR no corre en la
    ingesta), se transcribe acá mismo para tener un texto candidato con el que
    correr `propose()` — el mismo camino que ya usan los ítems de texto. El
    operador ve ese texto en un campo editable y lo confirma o corrige al curar.
    """
    ingestion = _oldest_pending(db)
    products = _all_products(db)
    provinces = _all_provinces(db)

    proposal = None
    recognized_text = ""
    if ingestion is not None and ingestion.content_type == ContentType.TEXT:
        if ingestion.original_text:
            proposal = propose(ingestion.original_text, products, provinces)
    elif ingestion is not None and ingestion.original_text is None:
        # content_type == IMAGE acá (el único otro valor posible).
        settings = get_settings()
        recognized_text = ocr.transcribe(settings.raw_store_dir / ingestion.file_path)
        if recognized_text:
            proposal = propose(recognized_text, products, provinces)

    values = _values_from_proposal(proposal)
    values["original_text"] = recognized_text

    return _render_queue(
        request,
        ingestion=ingestion,
        products=products,
        provinces=provinces,
        values=values,
        error=None,
    )


@router.get("/evidence/{ingestion_id}")
def evidence(ingestion_id: uuid.UUID, db: Session = Depends(get_db)) -> FileResponse:
    """Sirve el archivo de evidencia de una imagen. El texto se muestra inline, no acá."""
    ingestion = db.get(RawIngestion, ingestion_id)
    if ingestion is None or ingestion.content_type != ContentType.IMAGE:
        raise HTTPException(status_code=404, detail="No hay evidencia de imagen para ese id")

    file_path = get_settings().raw_store_dir / ingestion.file_path
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="El archivo de evidencia no existe")

    return FileResponse(file_path)


def _require_pending(db: Session, ingestion_id: uuid.UUID) -> RawIngestion:
    ingestion = db.get(RawIngestion, ingestion_id)
    if ingestion is None:
        raise HTTPException(status_code=404, detail="No existe ese elemento de la cola")
    if ingestion.processing_status != ProcessingStatus.PENDING:
        raise HTTPException(
            status_code=409, detail="Este elemento ya fue curado o descartado"
        )
    return ingestion


def _parse_uuid(raw: str) -> uuid.UUID | None:
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


def _parse_decimal(raw: str) -> Decimal | None:
    if not raw:
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


@router.post("/curation/{ingestion_id}", response_model=None)
def submit_curation(
    ingestion_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    product_name: str = Form(""),
    product_category: str = Form(""),
    product_unit: str = Form(""),
    price: str = Form(""),
    currency: str = Form(Currency.CUP.value),
    quantity: str = Form(""),
    unit: str = Form(""),
    province_id: str = Form(""),
    source_reference: str = Form(""),
    original_text: str = Form(""),
) -> HTMLResponse | RedirectResponse:
    """Confirma la curaduría: crea la fila de `prices_history` y marca `curated`.

    Sin motor de validación todavía (docs/decisions.md, 2026-08-07): la fila
    se crea con `confidence_score`, `price_cup` y `exchange_rate*` en None.
    """
    ingestion = _require_pending(db, ingestion_id)

    product_name_clean = product_name.strip()
    price_value = _parse_decimal(price)

    errors: list[str] = []

    product: Product | None = None
    if not product_name_clean:
        errors.append("Ingresá un producto.")
    else:
        # Búsqueda case-insensitive: el operador puede tipear "pollo" o "Pollo"
        # y debe matchear el mismo producto sin crear un duplicado.
        stmt = select(Product).where(func.lower(Product.name) == product_name_clean.lower())
        product = db.execute(stmt).scalars().first()
        if product is None:
            # No existe: es alta de producto nuevo, requiere categoría y unidad
            # porque el motor de extracción no las propone para nombres libres.
            product_category_clean = product_category.strip()
            product_unit_clean = product_unit.strip()
            if not product_category_clean or not product_unit_clean:
                errors.append("Para un producto nuevo, indicá categoría y unidad.")

    if price_value is None:
        errors.append("Ingresá un precio válido.")

    if errors:
        return _render_queue(
            request,
            ingestion=ingestion,
            products=_all_products(db),
            provinces=_all_provinces(db),
            values={
                "product_name": product_name,
                "product_category": product_category,
                "product_unit": product_unit,
                "price": price,
                "currency": currency or Currency.CUP.value,
                "quantity": quantity,
                "unit": unit,
                "province_id": province_id,
                "source_reference": source_reference,
                "original_text": original_text,
            },
            error=" ".join(errors),
            status_code=422,
        )

    if product is None:
        # Producto nuevo confirmado más arriba (categoría y unidad presentes).
        product = Product(
            name=product_name_clean,
            category=product_category.strip(),
            unit=product_unit.strip(),
            aliases=[],
        )
        db.add(product)
        db.flush()  # necesitamos product.id para PriceHistory antes del commit final

    assert product is not None and price_value is not None  # para el type checker

    province_uuid = _parse_uuid(province_id)
    province = db.get(Province, province_uuid) if province_uuid is not None else None

    quantity_value = _parse_decimal(quantity)

    entry = PriceHistory(
        product_id=product.id,
        province_id=province.id if province is not None else None,
        price=price_value,
        currency=currency or Currency.CUP.value,
        quantity=quantity_value,
        source_id=ingestion.id,
    )
    db.add(entry)

    # `source_reference` está diseñado para completarse acá, no viola la
    # inmutabilidad de `raw_ingestions` (ver docstring del modelo).
    if source_reference:
        ingestion.source_reference = source_reference

    # Mismo criterio para `original_text` en imágenes: nulo en la ingesta a
    # propósito (el OCR no corre ahí), lo completa el operador al confirmar
    # la transcripción en la curaduría (ver docstring del modelo). Para texto,
    # `original_text` YA es la evidencia pegada; este campo del form no la
    # reemplaza.
    if ingestion.content_type == ContentType.IMAGE and original_text:
        ingestion.original_text = original_text

    ingestion.processing_status = ProcessingStatus.CURATED

    db.commit()

    return RedirectResponse(url="/curation", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/curation/{ingestion_id}/discard")
def discard_curation(
    ingestion_id: uuid.UUID, db: Session = Depends(get_db)
) -> RedirectResponse:
    ingestion = _require_pending(db, ingestion_id)
    ingestion.processing_status = ProcessingStatus.DISCARDED
    db.commit()
    return RedirectResponse(url="/curation", status_code=status.HTTP_303_SEE_OTHER)
