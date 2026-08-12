"""Cola de curaduría — whitepaper §6.1, Etapa 1c y Etapa 2 (líneas 571-611).

Primera y única interfaz humana del sistema en Fase 0: presenta un elemento
`pending` por vez, propone una extracción cuando hay texto, y el operador
confirma o corrige. Cada confirmación produce la fila de `prices_history`
que alimenta H0.

Nota de esquema: `prices_history.unit` se completa con `canonical_unit()`
sobre lo que el operador confirma en el `<select>` del formulario — el
vocabulario cerrado vive en `saimc.units`, no acá. `sale_format` pasa por el
gate de `SaleFormat` (design.md, D4): solo se guarda cuando coincide con uno
de los dos valores del enum, cualquier otra cosa (incluido un envío en
blanco) degrada a `NULL`. `packaging` es texto libre redactado con
`redact_personal_data()` antes de construir la fila.
"""

import uuid
from datetime import date
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.datastructures import FormData

from saimc.config import get_settings
from saimc.extraction import ocr
from saimc.extraction.engine import (
    ExtractionProposal,
    guess_published_date,
    guess_source_reference,
    propose_all,
)
from saimc.ingestion.inbox import ingest_inbox
from saimc.privacy import redact_personal_data
from saimc.models import (
    ContentType,
    Currency,
    PriceHistory,
    Product,
    ProcessingStatus,
    Province,
    RawIngestion,
    SaleFormat,
)
from saimc.units import UNITS, canonical_unit
from saimc.web.deps import get_db

router = APIRouter()

# `sale_format` es un juicio explícito del operador (prueba de la jabita, ver
# curation.html), nunca inferido: cualquier valor posteado que no sea uno de
# los dos del enum —incluido un envío en blanco— degrada a `None` en vez de
# romper el submit (design.md, D4).
_SALE_FORMAT_VALUES = {member.value for member in SaleFormat}


def _values_from_proposal(proposal: ExtractionProposal | None) -> dict[str, str]:
    """Traduce una `ExtractionProposal` a strings listos para prellenar una fila del form.

    Campos a nivel de FILA únicamente — `original_text` y `source_reference`
    son a nivel de anuncio y viven aparte (ver `curation_queue`).
    """
    if proposal is None:
        return {
            "product_id": "",
            "new_product_name": "",
            "product_category": "",
            "product_unit": "",
            "price": "",
            "currency": Currency.CUP.value,
            "quantity": "",
            "unit": "",
            "sale_format": "",
            "packaging": "",
            "province_id": "",
        }
    return {
        # `product_name_guess` solo viene con algo cuando NO hay match de
        # catálogo (ver `_match_product`/modificadores de corte en
        # `engine.py`) — ahí se prellena el alta nueva con el nombre
        # adivinado en vez de dejarla en blanco.
        "product_id": str(proposal.product.id) if proposal.product else "",
        "new_product_name": proposal.product_name_guess or "",
        "product_category": "",
        "product_unit": "",
        "price": str(proposal.price) if proposal.price is not None else "",
        "currency": proposal.currency,
        "quantity": str(proposal.quantity) if proposal.quantity is not None else "",
        "unit": canonical_unit(proposal.unit) or "",
        # Nunca se prellena: es un juicio del operador, no algo que el motor
        # de extracción pueda inferir (design.md, D4 y "Data Flow").
        "sale_format": "",
        "packaging": "",
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


def _status_counts(db: Session) -> dict[str, int]:
    """Cuántos elementos hay por estado — para que el operador sepa cuánto
    le queda de la cola mientras cura, sin tener que consultar la base a mano.
    """
    stmt = select(RawIngestion.processing_status, func.count(RawIngestion.id)).group_by(
        RawIngestion.processing_status
    )
    counts = dict(db.execute(stmt).all())
    return {status.value: counts.get(status, 0) for status in ProcessingStatus}


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
    status_counts: dict[str, int],
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
            "units": UNITS,
            "status_counts": status_counts,
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

    Antes de leer la cola, ingesta lo que haya en `inbox/` — reemplaza el paso
    manual de correr `python -m saimc.ingestion` (docs/decisions.md). El
    operador ya abre esta página una vez al día para curar en bloque; no hace
    falta un timer aparte para un volumen de Fase 0.
    """
    settings = get_settings()
    ingest_inbox(db, settings.inbox_dir, settings.raw_store_dir)

    ingestion = _oldest_pending(db)
    products = _all_products(db)
    provinces = _all_provinces(db)

    proposals: list[ExtractionProposal] = []
    recognized_text = ""
    source_reference_guess = ""
    published_date_guess = ""
    if ingestion is not None and ingestion.content_type == ContentType.TEXT:
        if ingestion.original_text:
            proposals = propose_all(ingestion.original_text, products, provinces)
            source_reference_guess = guess_source_reference(ingestion.original_text) or ""
            guessed = guess_published_date(ingestion.original_text, ingestion.captured_at)
            published_date_guess = guessed.isoformat() if guessed else ""
    elif ingestion is not None and ingestion.original_text is None:
        # content_type == IMAGE acá (el único otro valor posible).
        settings = get_settings()
        recognized_text = ocr.transcribe(settings.raw_store_dir / ingestion.file_path)
        if recognized_text:
            proposals = propose_all(recognized_text, products, provinces)
            source_reference_guess = guess_source_reference(recognized_text) or ""
            guessed = guess_published_date(recognized_text, ingestion.captured_at)
            published_date_guess = guessed.isoformat() if guessed else ""

    rows = [_values_from_proposal(proposal) for proposal in proposals]
    rows.extend(_values_from_proposal(None) for _ in range(_EXTRA_BLANK_ROWS))

    global_values = {
        "original_text": recognized_text,
        "source_reference": source_reference_guess,
        "published_date": published_date_guess,
    }

    return _render_queue(
        request,
        ingestion=ingestion,
        products=products,
        provinces=provinces,
        rows=rows,
        row_count=len(rows),
        global_values=global_values,
        error=None,
        status_counts=_status_counts(db),
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


def _parse_date(raw: str) -> date | None:
    """Parsea el `<input type="date">` (ISO `YYYY-MM-DD` del navegador).

    Vacío, no parseable o posterior a hoy degrada a `None`, nunca rompe el
    submit — mismo contrato que `_parse_decimal` (spec: Campo nunca
    bloqueante / Valor inválido o futuro). La validación vive acá, en la capa
    web, no en un CHECK de base (design.md, D2): un dato secundario no debe
    convertirse en un fallo de transacción.
    """
    if not raw:
        return None
    try:
        parsed = date.fromisoformat(raw)
    except ValueError:
        return None
    if parsed > date.today():
        return None
    return parsed


# Defaults para alta de producto nuevo cuando el operador no completa
# categoría o unidad en el form: sin esto la fila entera invalidaba todo el
# submit por un dato secundario. `Product.category`/`Product.unit` son
# NOT NULL sin default en el modelo (fuera de alcance tocar el esquema acá),
# así que el default se resuelve en esta capa.
_DEFAULT_PRODUCT_CATEGORY = "sin categoría"
_DEFAULT_PRODUCT_UNIT = "unidad"


def _resolve_row_product(
    db: Session, products_by_name: dict[str, Product], row: dict[str, str]
) -> Product | None:
    """Resuelve el producto de una fila: alta nueva o selección del catálogo.

    El alta nueva (`new_product_name`) tiene prioridad sobre el `<select>`
    ya elegido (`product_id`) aunque vengan los dos juntos — el operador
    decidió agregar un producto en vez de usar el que tenía seleccionado.
    Un `product_id` que no parsea como UUID o que no existe en la base se
    trata como "sin producto" para esta fila, sin romper con una excepción:
    el operador puede haber elegido "— seleccionar —" o el id puede haber
    quedado obsoleto entre el GET y el submit.
    """
    if row["new_product_name"]:
        product = products_by_name.get(row["new_product_name"].lower())
        if product is None:
            # No existe: alta de producto nuevo. Categoría y unidad son
            # opcionales en el form: el motor de extracción no las propone
            # para nombres libres y exigirlas siempre trababa el submit por
            # un dato secundario. Sin tocar el esquema (Product.category y
            # Product.unit son NOT NULL sin default): se completa acá.
            product = Product(
                name=row["new_product_name"],
                category=row["product_category"] or _DEFAULT_PRODUCT_CATEGORY,
                unit=row["product_unit"] or _DEFAULT_PRODUCT_UNIT,
                aliases=[],
            )
            db.add(product)
            db.flush()  # necesitamos product.id para PriceHistory antes del commit final
            products_by_name[row["new_product_name"].lower()] = product
        return product

    if row["product_id"]:
        product_uuid = _parse_uuid(row["product_id"])
        if product_uuid is not None:
            return db.get(Product, product_uuid)

    return None


def _row_from_form(form: FormData, index: int) -> dict[str, str]:
    """Lee los campos indexados `{campo}_{index}` de un form crudo, como strings."""
    return {
        "product_id": str(form.get(f"product_id_{index}", "")).strip(),
        "new_product_name": str(form.get(f"new_product_name_{index}", "")).strip(),
        "product_category": str(form.get(f"product_category_{index}", "")).strip(),
        "product_unit": str(form.get(f"product_unit_{index}", "")).strip(),
        "price": str(form.get(f"price_{index}", "")).strip(),
        "currency": str(form.get(f"currency_{index}", "")).strip() or Currency.CUP.value,
        "quantity": str(form.get(f"quantity_{index}", "")).strip(),
        "unit": str(form.get(f"unit_{index}", "")).strip(),
        "sale_format": str(form.get(f"sale_format_{index}", "")).strip(),
        "packaging": str(form.get(f"packaging_{index}", "")).strip(),
        "province_id": str(form.get(f"province_id_{index}", "")).strip(),
    }


@router.post("/curation/{ingestion_id}", response_model=None)
async def submit_curation(
    ingestion_id: uuid.UUID, request: Request, db: Session = Depends(get_db)
) -> HTMLResponse | RedirectResponse:
    """Confirma la curaduría: crea una fila de `prices_history` por cada renglón completo.

    Sin motor de validación todavía (docs/decisions.md, 2026-08-07): cada fila
    se crea con `confidence_score`, `price_cup` y `exchange_rate*` en None.

    La tabla manda `row_count` filas indexadas (`product_id_0`, `price_0`, ...),
    más `new_product_name_i` para el alta rápida de producto nuevo (ver
    `_resolve_row_product`). El operador descarta una tarjeta que no quiere
    cargar eliminándola del lado del cliente (botón "Eliminar" en la plantilla,
    sin submit): sus campos indexados no viajan en el POST, así que cae sola
    en la rama de fila vacía de abajo. Una fila sin producto y sin
    precio se ignora en silencio (no es una fila que el operador quiso
    cargar); con solo uno de los dos es un error explícito, porque ahí sí hay
    intención de cargar algo pero falta un dato. Categoría y unidad de
    producto nuevo son opcionales: si faltan, se completan con
    `_DEFAULT_PRODUCT_CATEGORY` / `_DEFAULT_PRODUCT_UNIT`. Todo o nada: si
    alguna fila con datos tiene un error, no se guarda ninguna — un estado a
    medias es más confuso que repetir el submit.
    """
    ingestion = _require_pending(db, ingestion_id)
    form = await request.form()

    original_text = str(form.get("original_text", "")).strip()
    source_reference = str(form.get("source_reference", "")).strip()
    published_date_raw = str(form.get("published_date", "")).strip()
    published_date = _parse_date(published_date_raw)
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
        row = _row_from_form(form, i)
        typed_rows.append(row)

        product = _resolve_row_product(db, products_by_name, row)

        if product is None and not row["price"]:
            continue  # fila vacía: el operador no la llenó, se ignora sin error

        if product is None:
            errors.append(f"Fila {i + 1}: seleccioná o agregá un producto")
            continue

        if not row["price"]:
            errors.append(f"Fila {i + 1}: falta el precio")
            continue

        price_value = _parse_decimal(row["price"])
        if price_value is None:
            errors.append(f"Fila {i + 1}: precio inválido")
            continue

        province_uuid = _parse_uuid(row["province_id"])
        province = db.get(Province, province_uuid) if province_uuid is not None else None

        entries_to_create.append(
            PriceHistory(
                product_id=product.id,
                province_id=province.id if province is not None else None,
                price=price_value,
                currency=row["currency"],
                quantity=_parse_decimal(row["quantity"]),
                unit=canonical_unit(row["unit"]),
                sale_format=(
                    row["sale_format"] if row["sale_format"] in _SALE_FORMAT_VALUES else None
                ),
                # Redactado acá mismo, no en el bloque de más abajo (D5): ese
                # bloque corre después de la rama de rollback todo-o-nada, y
                # las filas ya se construyeron para entonces.
                packaging=redact_personal_data(row["packaging"]) or None,
                published_date=published_date,
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
            global_values={
                "original_text": original_text,
                "source_reference": source_reference,
                "published_date": published_date_raw,
            },
            error=" ".join(errors),
            status_counts=_status_counts(db),
            status_code=422,
        )

    for entry in entries_to_create:
        db.add(entry)

    # Todo texto capturado que se persiste durante la curaduría pasa por
    # `redact_personal_data` antes de tocar la base — regla de `CLAUDE.md`
    # sobre datos personales. Dos puntos, no uno: `packaging` ya se redactó
    # arriba, inline en la construcción de cada `PriceHistory` (D5); acá
    # abajo se redactan `source_reference` y `original_text`, que solo se
    # conocen después de pasar la rama de error todo-o-nada.

    # `source_reference` está diseñado para completarse acá, no viola la
    # inmutabilidad de `raw_ingestions` (ver docstring del modelo).
    if source_reference:
        ingestion.source_reference = redact_personal_data(source_reference)

    # Mismo criterio para `original_text` en imágenes: nulo en la ingesta a
    # propósito (el OCR no corre ahí), lo completa el operador al confirmar
    # la transcripción en la curaduría (ver docstring del modelo). Para texto,
    # `original_text` YA es la evidencia pegada; este campo del form no la
    # reemplaza.
    if ingestion.content_type == ContentType.IMAGE and original_text:
        ingestion.original_text = redact_personal_data(original_text)

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
