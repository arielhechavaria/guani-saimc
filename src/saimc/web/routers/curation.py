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

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.datastructures import FormData

from saimc.config import get_settings
from saimc.extraction import ocr
from saimc.extraction.engine import ExtractionProposal, propose_all
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
    """Traduce una `ExtractionProposal` a strings listos para prellenar una fila del form.

    Campos a nivel de FILA únicamente — `original_text` y `source_reference`
    son a nivel de anuncio y viven aparte (ver `curation_queue`).
    """
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
    rows: list[dict[str, str]],
    row_count: int,
    global_values: dict[str, str],
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
            "rows": rows,
            "row_count": row_count,
            "values": global_values,
            "error": error,
            "currencies": [c.value for c in Currency],
        },
        status_code=status_code,
    )


# Filas en blanco de más que se ofrecen siempre, además de las prellenadas con
# candidatos del motor: el operador carga productos que `propose_all` no detectó
# sin tener que recargar la página (ver diseño del slice que reemplaza
# "guardar y agregar otro").
_EXTRA_BLANK_ROWS = 3


@router.get("/curation", response_class=HTMLResponse)
def curation_queue(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """Muestra el elemento `pending` más antiguo, con propuesta pre-llenada si hay texto.

    Para imágenes sin `original_text` (el caso normal: el OCR no corre en la
    ingesta), se transcribe acá mismo para tener un texto candidato con el que
    correr `propose_all()` — el mismo camino que ya usan los ítems de texto. El
    operador ve ese texto en un campo editable y lo confirma o corrige al curar.

    Un anuncio puede ofrecer varios productos (`propose_all` devuelve un
    candidato por línea). Se muestra una fila por candidato, más un puñado de
    filas en blanco para que el operador agregue productos que el motor no
    detectó — todo en una sola tabla que se manda con un único submit
    (reemplaza el flujo anterior de "guardar y agregar otro", tedioso contra
    anuncios reales de varios productos: ver docs/decisions.md).
    """
    ingestion = _oldest_pending(db)
    products = _all_products(db)
    provinces = _all_provinces(db)

    proposals: list[ExtractionProposal] = []
    recognized_text = ""
    if ingestion is not None and ingestion.content_type == ContentType.TEXT:
        if ingestion.original_text:
            proposals = propose_all(ingestion.original_text, products, provinces)
    elif ingestion is not None and ingestion.original_text is None:
        # content_type == IMAGE acá (el único otro valor posible).
        settings = get_settings()
        recognized_text = ocr.transcribe(settings.raw_store_dir / ingestion.file_path)
        if recognized_text:
            proposals = propose_all(recognized_text, products, provinces)

    rows = [_values_from_proposal(proposal) for proposal in proposals]
    rows.extend(_values_from_proposal(None) for _ in range(_EXTRA_BLANK_ROWS))

    global_values = {"original_text": recognized_text, "source_reference": ""}

    return _render_queue(
        request,
        ingestion=ingestion,
        products=products,
        provinces=provinces,
        rows=rows,
        row_count=len(rows),
        global_values=global_values,
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


# Defaults para alta de producto nuevo cuando el operador no completa
# categoría o unidad en el form: sin esto la fila entera invalidaba todo el
# submit por un dato secundario. `Product.category`/`Product.unit` son
# NOT NULL sin default en el modelo (fuera de alcance tocar el esquema acá),
# así que el default se resuelve en esta capa.
_DEFAULT_PRODUCT_CATEGORY = "sin categoría"
_DEFAULT_PRODUCT_UNIT = "unidad"


def _row_from_form(form: FormData, index: int) -> dict[str, str]:
    """Lee los campos indexados `{campo}_{index}` de un form crudo, como strings."""
    return {
        "product_name": str(form.get(f"product_name_{index}", "")).strip(),
        "product_category": str(form.get(f"product_category_{index}", "")).strip(),
        "product_unit": str(form.get(f"product_unit_{index}", "")).strip(),
        "price": str(form.get(f"price_{index}", "")).strip(),
        "currency": str(form.get(f"currency_{index}", "")).strip() or Currency.CUP.value,
        "quantity": str(form.get(f"quantity_{index}", "")).strip(),
        "unit": str(form.get(f"unit_{index}", "")).strip(),
        "province_id": str(form.get(f"province_id_{index}", "")).strip(),
    }


@router.post("/curation/{ingestion_id}", response_model=None)
async def submit_curation(
    ingestion_id: uuid.UUID, request: Request, db: Session = Depends(get_db)
) -> HTMLResponse | RedirectResponse:
    """Confirma la curaduría: crea una fila de `prices_history` por cada renglón completo.

    Sin motor de validación todavía (docs/decisions.md, 2026-08-07): cada fila
    se crea con `confidence_score`, `price_cup` y `exchange_rate*` en None.

    La tabla manda `row_count` filas indexadas (`product_name_0`, `price_0`, ...).
    Cada fila trae además un checkbox `include_i`: si el navegador no lo manda
    (desmarcado), la fila se descarta completa sin leer ni validar nada más —
    así el operador excluye una fila prellenada con datos que no quiere cargar
    sin tener que vaciar cada campo a mano. Entre las filas incluidas, una sin
    producto o sin precio se ignora en silencio (no es una fila que el
    operador quiso cargar). Categoría y unidad de producto nuevo son
    opcionales: si faltan, se completan con `_DEFAULT_PRODUCT_CATEGORY` /
    `_DEFAULT_PRODUCT_UNIT`. Todo o nada: si alguna fila incluida y con datos
    tiene un error (precio inválido), no se guarda ninguna — un estado a
    medias es más confuso que repetir el submit.
    """
    ingestion = _require_pending(db, ingestion_id)
    form = await request.form()

    original_text = str(form.get("original_text", "")).strip()
    source_reference = str(form.get("source_reference", "")).strip()
    try:
        row_count = int(str(form.get("row_count", "0")))
    except ValueError:
        row_count = 0

    # Catálogo existente + altas de esta misma request: dos filas con el mismo
    # producto nuevo (mismo submit) deben reusar el Product, no duplicarlo.
    products_by_name = {p.name.lower(): p for p in _all_products(db)}

    errors: list[str] = []
    typed_rows: list[dict[str, str]] = []
    entries_to_create: list[PriceHistory] = []

    for i in range(row_count):
        if form.get(f"include_{i}") is None:
            # Checkbox "Incluir" desmarcado: el navegador no manda ese campo
            # (comportamiento estándar de un checkbox sin marcar). La fila se
            # descarta completa sin siquiera leer o validar el resto de sus
            # campos, tenga o no producto y precio. Se guarda una fila vacía
            # en su lugar (no se lee el form) para no correr los índices del
            # resto de la tabla si hay que re-mostrarla tras un error.
            typed_rows.append(_values_from_proposal(None))
            continue

        row = _row_from_form(form, i)
        typed_rows.append(row)

        if not row["product_name"] or not row["price"]:
            continue  # fila incompleta: el operador no la llenó, se ignora sin error

        price_value = _parse_decimal(row["price"])
        if price_value is None:
            errors.append(f"Fila {i + 1}: precio inválido")
            continue

        product = products_by_name.get(row["product_name"].lower())
        if product is None:
            # No existe: alta de producto nuevo. Categoría y unidad son
            # opcionales en el form: el motor de extracción no las propone
            # para nombres libres y exigirlas siempre trababa el submit por
            # un dato secundario. Sin tocar el esquema (Product.category y
            # Product.unit son NOT NULL sin default): se completa acá.
            product = Product(
                name=row["product_name"],
                category=row["product_category"] or _DEFAULT_PRODUCT_CATEGORY,
                unit=row["product_unit"] or _DEFAULT_PRODUCT_UNIT,
                aliases=[],
            )
            db.add(product)
            db.flush()  # necesitamos product.id para PriceHistory antes del commit final
            products_by_name[row["product_name"].lower()] = product

        province_uuid = _parse_uuid(row["province_id"])
        province = db.get(Province, province_uuid) if province_uuid is not None else None

        entries_to_create.append(
            PriceHistory(
                product_id=product.id,
                province_id=province.id if province is not None else None,
                price=price_value,
                currency=row["currency"],
                quantity=_parse_decimal(row["quantity"]),
                source_id=ingestion.id,
            )
        )

    if not errors and not entries_to_create:
        errors.append("Cargá al menos un producto con precio.")

    if errors:
        # Todo o nada: cualquier alta de producto que haya llegado a hacer
        # `flush()` en este loop se descarta junto con el resto de la request.
        db.rollback()
        return _render_queue(
            request,
            ingestion=ingestion,
            products=_all_products(db),
            provinces=_all_provinces(db),
            rows=typed_rows,
            row_count=row_count,
            global_values={"original_text": original_text, "source_reference": source_reference},
            error=" ".join(errors),
            status_code=422,
        )

    for entry in entries_to_create:
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
