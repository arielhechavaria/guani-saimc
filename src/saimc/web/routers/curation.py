"""Cola de curaduría — spec.md §6.1, Etapa 1c y Etapa 2.

Primera y única interfaz humana del sistema en Fase 0: presenta un elemento
`pending` por vez, propone una extracción cuando hay texto, y el operador
confirma o corrige. Cada confirmación produce la fila de `prices_history`
que alimenta H0.

Conversión de moneda (`spec.md` §6.3 E, design.md D4): cada fila con
`currency != CUP` pasa sincrónicamente por `RateResolver` antes del commit,
en ambos caminos de escritura (`submit_curation` y `submit_edit`). La
resolución nunca bloquea el guardado — si nada es resolvible, la fila se
guarda igual con `price_cup`/`exchange_rate*` en `NULL` y el operador ve un
aviso no bloqueante (`?unconverted=N`, ver `_unconverted_notice`).

Nota de esquema: `prices_history.unit` se completa con `canonical_unit()`
sobre lo que el operador confirma en el `<select>` del formulario — el
vocabulario cerrado vive en `saimc.units`, no acá. `sale_format` pasa por el
gate de `SaleFormat` (design.md, D4): solo se guarda cuando coincide con uno
de los dos valores del enum, cualquier otra cosa (incluido un envío en
blanco) degrada a `NULL`.

`packaging` no es una unidad (2026-08-12): un saco trae 25 kg, 50 kg o 110 lb
según el anuncio, y un paquete va de 1 kg a 2 lb — sin magnitud fija no sirve
para normalizar precio por unidad, para eso están `unit`+`quantity`. El
formulario pide el envase en dos campos, `packaging_format` (cerrado: Paquete
/ Saco / A granel / Otro) y `packaging_detail` (libre, solo aplica con
«Otro»), que `_packaging_from_form()` combina en el único string que guarda
la columna, redactado con `redact_personal_data()` antes de construir la fila.
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
from saimc.exchange_rate import NO_RATE, Fetcher, RateResolver, anchor_date, apply_resolution
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
    PACKAGING_MAX_LENGTH,
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
from saimc.web.deps import get_db, get_rate_fetcher

router = APIRouter()

# `sale_format` es un juicio explícito del operador (prueba de la jabita, ver
# curation.html), nunca inferido: cualquier valor posteado que no sea uno de
# los dos del enum —incluido un envío en blanco— degrada a `None` en vez de
# romper el submit (design.md, D4).
_SALE_FORMAT_VALUES = {member.value for member in SaleFormat}

# Vocabulario cerrado de presentación (2026-08-12): reemplaza el texto libre
# original. `Otro` es la válvula de escape declarada — sin ella, un formato
# real que no sea paquete/saco/a granel (funda, lata, pomo) no tendría dónde
# ir, y forzarlo dentro de las tres opciones fijas sería inventar una
# categoría que nadie eligió.
_PACKAGING_LABELS = {"paquete": "Paquete", "saco": "Saco", "a granel": "A granel"}


def _truncated_packaging(raw: str) -> str | None:
    """Redacta el envase y lo recorta al ancho de la columna, en ese orden.

    `PHONE_PLACEHOLDER` mide más que el teléfono que reemplaza, así que un
    envase que pasaba el `maxlength` del formulario puede salir de la redacción
    excedido. Sin este recorte, PostgreSQL aborta el insert y el operador pierde
    el submit entero — y justo en el caso en que escribió un teléfono.
    """
    redacted = redact_personal_data(raw)
    if not redacted:
        return None
    return redacted[:PACKAGING_MAX_LENGTH]


def _packaging_from_form(fmt: str, detail: str) -> str | None:
    """Combina el select cerrado y el detalle libre en el string que se guarda.

    El detalle solo cuenta cuando `fmt == "otro"` — con Paquete/Saco/A granel
    ya elegidos, `quantity`+`unit` dimensionan el lote y el detalle no agrega
    nada que valga la pena guardar. Un `fmt` vacío o desconocido no fabrica un
    valor a partir del detalle: es la misma regla que ya rige `sale_format`,
    el envase es un juicio explícito, no algo que se infiera de texto suelto.
    """
    fmt = fmt.strip().lower()
    if fmt == "otro":
        combined = detail.strip()
    else:
        combined = _PACKAGING_LABELS.get(fmt, "")
    if not combined:
        return None
    return _truncated_packaging(combined)


def _split_packaging(existing: str | None) -> tuple[str, str]:
    """Inverso de `_packaging_from_form`, para prellenar la corrección.

    Filas cargadas antes del 2026-08-12 traen texto libre («Saco de arroz De
    110 lb») que no calza con ninguna etiqueta cerrada: caen en «Otro» con el
    texto original completo como detalle, en vez de perderlo.
    """
    if not existing:
        return "", ""
    for fmt, label in _PACKAGING_LABELS.items():
        if existing == label:
            return fmt, ""
    return "otro", existing


def _values_from_proposal(proposal: ExtractionProposal | None) -> dict[str, str]:
    """Traduce una `ExtractionProposal` a strings listos para prellenar una fila del form.

    Campos a nivel de FILA únicamente — `original_text` y `source_reference`
    son a nivel de anuncio y viven aparte (ver `curation_queue`).
    """
    if proposal is None:
        return {
            "price_id": "",
            "product_id": "",
            "new_product_name": "",
            "product_category": "",
            "product_unit": "",
            "price": "",
            "currency": Currency.CUP.value,
            "quantity": "",
            "unit": "",
            "sale_format": "",
            "packaging_format": "",
            "packaging_detail": "",
            "province_id": "",
        }
    return {
        # Sin fila propia todavía: existe recién cuando se confirma el submit.
        "price_id": "",
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
        "packaging_format": "",
        "packaging_detail": "",
        "province_id": str(proposal.province.id) if proposal.province else "",
    }


def _values_from_price_history(price: PriceHistory) -> dict[str, str]:
    """Traduce una fila ya curada a los mismos strings que arma una propuesta.

    Mismo formato de salida que `_values_from_proposal` — es lo que permite
    que la corrección reuse la tarjeta de la cola sin distinguir origen.
    """
    packaging_format, packaging_detail = _split_packaging(price.packaging)
    return {
        "price_id": str(price.id),
        "product_id": str(price.product_id),
        "new_product_name": "",
        "product_category": "",
        "product_unit": "",
        "price": str(price.price),
        "currency": price.currency,
        "quantity": str(price.quantity) if price.quantity is not None else "",
        "unit": price.unit or "",
        "sale_format": price.sale_format or "",
        "packaging_format": packaging_format,
        "packaging_detail": packaging_detail,
        "province_id": str(price.province_id) if price.province_id else "",
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


def _unconverted_notice(unconverted: int) -> str | None:
    """Copia del aviso no bloqueante (design.md D7). `None` cuando no hay
    nada que avisar — el template lo omite con un simple `{% if notice %}`.
    """
    if unconverted <= 0:
        return None
    return (
        f"Guardado sin convertir: {unconverted} fila(s) en moneda extranjera "
        "quedaron sin tasa de cambio. Se pueden completar más tarde con el backfill."
    )


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
    unconverted: int = 0,
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
            "notice": _unconverted_notice(unconverted),
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
def curation_queue(
    request: Request, db: Session = Depends(get_db), unconverted: int = 0
) -> HTMLResponse:
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
        unconverted=unconverted,
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


def _require_curated(db: Session, ingestion_id: uuid.UUID) -> RawIngestion:
    ingestion = db.get(RawIngestion, ingestion_id)
    if ingestion is None:
        raise HTTPException(status_code=404, detail="No existe ese elemento de la cola")
    if ingestion.processing_status != ProcessingStatus.CURATED:
        raise HTTPException(status_code=409, detail="Este elemento todavía no está curado")
    return ingestion


def _curated_rows(db: Session, ingestion_id: uuid.UUID) -> list[PriceHistory]:
    stmt = (
        select(PriceHistory)
        .where(PriceHistory.source_id == ingestion_id)
        .order_by(PriceHistory.created_at)
    )
    return list(db.execute(stmt).scalars().all())


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
    """Lee los campos indexados `{campo}_{index}` de un form crudo, como strings.

    `price_id` viaja vacío en la curaduría normal (`submit_curation` no lo lee)
    y con el id real en la corrección (`submit_edit` lo usa para ubicar la fila
    exacta a actualizar, ver ahí el porqué de no matchear solo por posición).
    """
    return {
        "price_id": str(form.get(f"price_id_{index}", "")).strip(),
        "product_id": str(form.get(f"product_id_{index}", "")).strip(),
        "new_product_name": str(form.get(f"new_product_name_{index}", "")).strip(),
        "product_category": str(form.get(f"product_category_{index}", "")).strip(),
        "product_unit": str(form.get(f"product_unit_{index}", "")).strip(),
        "price": str(form.get(f"price_{index}", "")).strip(),
        "currency": str(form.get(f"currency_{index}", "")).strip() or Currency.CUP.value,
        "quantity": str(form.get(f"quantity_{index}", "")).strip(),
        "unit": str(form.get(f"unit_{index}", "")).strip(),
        "sale_format": str(form.get(f"sale_format_{index}", "")).strip(),
        "packaging_format": str(form.get(f"packaging_format_{index}", "")).strip(),
        "packaging_detail": str(form.get(f"packaging_detail_{index}", "")).strip(),
        "province_id": str(form.get(f"province_id_{index}", "")).strip(),
    }


@router.post("/curation/{ingestion_id}", response_model=None)
async def submit_curation(
    ingestion_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    rate_fetcher: Fetcher = Depends(get_rate_fetcher),
) -> HTMLResponse | RedirectResponse:
    """Confirma la curaduría: crea una fila de `prices_history` por cada renglón completo.

    Sin motor de validación todavía (docs/decisions.md, 2026-08-07): cada fila
    se crea con `confidence_score` en None. `price_cup`/`exchange_rate*` YA NO
    quedan fijos en None (2026-08-13): se resuelven sincrónicamente vía
    `RateResolver` para toda fila con `currency != CUP`, con el ancla que
    calcula `anchor_date` (`published_date` si el operador lo cargó, si no
    `raw_ingestions.captured_at` en calendario de La Habana). Nunca bloquea el
    guardado — si nada es resolvible, la fila igual se guarda con esas cuatro
    columnas en `NULL` y el redirect avisa `?unconverted=N` (design.md D4/D7).

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
                # las filas ya se construyeron para entonces. El recorte va
                # después de redactar y no antes: el placeholder es más largo
                # que el teléfono que reemplaza, así que un texto que entraba en
                # la columna deja de entrar apenas se redacta.
                packaging=_packaging_from_form(row["packaging_format"], row["packaging_detail"]),
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

    # Resolución de tasa DESPUÉS de la rama todo-o-nada (design.md D4): un
    # submit que va a fallar no debe gastar llamadas HTTP ni escribir filas
    # de cache dentro de una transacción que se está por descartar.
    resolver = RateResolver(db, rate_fetcher)
    unconverted = 0
    for entry in entries_to_create:
        if entry.currency == Currency.CUP.value:
            continue  # moneda nacional: nunca lleva tasa (CHECK de esquema)
        anchor = anchor_date(entry.published_date, ingestion.captured_at)
        apply_resolution(entry, resolver.resolve(entry.currency, anchor))
        if entry.exchange_rate is None:
            unconverted += 1

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

    redirect_url = f"/curation?unconverted={unconverted}" if unconverted else "/curation"
    return RedirectResponse(url=redirect_url, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/curation/{ingestion_id}/discard")
def discard_curation(
    ingestion_id: uuid.UUID, db: Session = Depends(get_db)
) -> RedirectResponse:
    ingestion = _require_pending(db, ingestion_id)
    ingestion.processing_status = ProcessingStatus.DISCARDED
    db.commit()
    return RedirectResponse(url="/curation", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/curation/history", response_class=HTMLResponse)
def curation_history(
    request: Request,
    db: Session = Depends(get_db),
    product: str | None = None,
    province: str | None = None,
    currency: str | None = None,
    unit: str | None = None,
    sale_format: str | None = None,
    unconverted: int = 0,
) -> HTMLResponse:
    """Lista los anuncios ya curados, más recientes primero, para ubicar cuál
    corregir o eliminar — es la puerta de entrada a `GET /curation/{id}/edit`
    y a `POST /curation/{id}/delete`.

    Los cinco filtros opcionales son la identidad exacta de una celda de
    `compute_cells` (2026-08-12): la columna `n` de `/control` enlaza acá para
    mostrar «los anuncios detrás de este número». Un parámetro **ausente** no
    filtra por esa columna; un parámetro **presente pero vacío** (`unit=`)
    filtra por «sin responder» (NULL) — mismo contrato que ya usa
    `_SALE_FORMAT_VALUES` en el submit: la cadena vacía nunca se confunde con
    "no filtrar".

    Sin paginación: al volumen de Fase 0 (decenas de anuncios) toda la lista
    entra en una sola página, y paginar antes de tener ese problema es
    complejidad sin uso (spec.md §12.6, mismo criterio que ya rige `/control`).
    Una query de filas por anuncio (N+1) es aceptable al mismo volumen —
    agregarla en una sola consulta con `string_agg` ataría la vista al
    dialecto de PostgreSQL, que los tests no corren (ver tests/conftest.py).
    """
    filtering = any(v is not None for v in (product, province, currency, unit, sale_format))

    stmt = (
        select(RawIngestion)
        .where(RawIngestion.processing_status == ProcessingStatus.CURATED)
        .order_by(RawIngestion.captured_at.desc())
    )
    all_ingestions = list(db.execute(stmt).scalars().all())

    ingestions: list[RawIngestion] = []
    rows_by_ingestion: dict[uuid.UUID, list[tuple[str, PriceHistory]]] = {}

    for ingestion in all_ingestions:
        row_stmt = (
            select(Product.name, Province.name, PriceHistory)
            .join(Product, Product.id == PriceHistory.product_id)
            .outerjoin(Province, Province.id == PriceHistory.province_id)
            .where(PriceHistory.source_id == ingestion.id)
            .order_by(PriceHistory.created_at)
        )
        matched: list[tuple[str, PriceHistory]] = []
        for product_name, province_name, price in db.execute(row_stmt).all():
            if filtering:
                if product is not None and product_name != product:
                    continue
                if province is not None and (province_name or "") != province:
                    continue
                if currency is not None and price.currency != currency:
                    continue
                if unit is not None and (price.unit or "") != unit:
                    continue
                if sale_format is not None and (price.sale_format or "") != sale_format:
                    continue
            matched.append((product_name, price))

        if filtering and not matched:
            continue  # este anuncio no tiene nada que ver con la celda filtrada

        ingestions.append(ingestion)
        rows_by_ingestion[ingestion.id] = matched

    return request.app.state.templates.TemplateResponse(
        request,
        "curation_history.html",
        {
            "ingestions": ingestions,
            "rows_by_ingestion": rows_by_ingestion,
            "filtering": filtering,
            "filter_product": product,
            "filter_province": province,
            "filter_currency": currency,
            "filter_unit": unit,
            "filter_sale_format": sale_format,
            "notice": _unconverted_notice(unconverted),
        },
    )


@router.post("/curation/{ingestion_id}/delete")
def delete_curated(
    ingestion_id: uuid.UUID, db: Session = Depends(get_db)
) -> RedirectResponse:
    """Elimina un anuncio curado: borra sus filas de `prices_history` y lo
    pasa a `discarded` — no vuelve a la cola ni al historial (2026-08-12).

    El registro crudo **nunca se toca**. No es una promesa de este endpoint,
    es una garantía del esquema: `PriceHistory.source_id` tiene
    `ondelete="RESTRICT"` contra `raw_ingestions` (spec.md §6.1, línea 321),
    así que ni intentando se podría arrastrar el archivo capturado en el
    mismo borrado. Por eso el orden acá no importa para la integridad —
    aunque de todos modos se borran las filas de precio antes de tocar el
    estado del anuncio, por claridad de lectura.
    """
    ingestion = _require_curated(db, ingestion_id)
    for price in _curated_rows(db, ingestion_id):
        db.delete(price)
    ingestion.processing_status = ProcessingStatus.DISCARDED
    db.commit()
    return RedirectResponse(url="/curation/history", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/curation/{ingestion_id}/edit", response_class=HTMLResponse)
def edit_curated(
    ingestion_id: uuid.UUID, request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    """Corrección acotada de un anuncio ya curado.

    Reabre exactamente las filas de `prices_history` que ese anuncio ya
    produjo, contra la misma evidencia — no agrega ni quita filas, y no
    vuelve a correr el motor de extracción. Es la vía para arreglar un error
    de tipeo sin SQL directo contra producción.
    """
    ingestion = _require_curated(db, ingestion_id)
    prices = _curated_rows(db, ingestion_id)
    rows = [_values_from_price_history(price) for price in prices]

    return request.app.state.templates.TemplateResponse(
        request,
        "curation_edit.html",
        {
            "ingestion": ingestion,
            "products": _all_products(db),
            "provinces": _all_provinces(db),
            "rows": rows,
            "edit_mode": True,
            "values": {
                "source_reference": ingestion.source_reference or "",
                # Una sola fecha de publicación por anuncio: todas sus filas
                # se cargaron con la misma (ver `submit_curation`), así que
                # alcanza con la de la primera.
                "published_date": (
                    prices[0].published_date.isoformat()
                    if prices and prices[0].published_date
                    else ""
                ),
            },
            "error": None,
            "currencies": [c.value for c in Currency],
            "units": UNITS,
        },
    )


@router.post("/curation/{ingestion_id}/edit", response_model=None)
async def submit_edit(
    ingestion_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    rate_fetcher: Fetcher = Depends(get_rate_fetcher),
) -> HTMLResponse | RedirectResponse:
    """Aplica la corrección: actualiza en el lugar las filas que el operador
    reabrió, sin crear ni borrar ninguna.

    Cada fila viaja con su propio `price_id_i`, a diferencia de la curaduría
    original: matchear solo por posición confundiría un reordenamiento
    accidental del formulario con una corrección real. Si el id no
    corresponde a una fila de este mismo anuncio, es un error explícito —
    nunca crea una fila nueva por accidente. Mismo contrato todo-o-nada que
    `submit_curation`: una fila con error bloquea el guardado entero.

    La tasa de cambio SOLO se re-resuelve cuando cambia `currency` o `price`
    respecto de lo que la fila ya tenía guardado (design.md D6): editar un
    campo no relacionado (provincia, unidad, formato) no debe pisar la tasa
    de la fecha en la que el anuncio se publicó con la de hoy. Una fila que
    nunca tuvo tasa (`exchange_rate is None`) igual se intenta resolver en
    cada edición, cambie o no algo más — no hay nada que preservar ahí.
    """
    ingestion = _require_curated(db, ingestion_id)
    form = await request.form()

    source_reference = str(form.get("source_reference", "")).strip()
    published_date = _parse_date(str(form.get("published_date", "")).strip())
    try:
        row_count = int(str(form.get("row_count", "0")))
    except ValueError:
        row_count = 0

    existing_by_id = {price.id: price for price in _curated_rows(db, ingestion_id)}
    products_by_name = {p.name.lower(): p for p in _all_products(db)}

    errors: list[str] = []
    typed_rows: list[dict[str, str]] = []
    updates: list[tuple[PriceHistory, Product, Decimal, Province | None, dict[str, str]]] = []

    for i in range(row_count):
        row = _row_from_form(form, i)
        typed_rows.append(row)

        price_uuid = _parse_uuid(row["price_id"])
        existing = existing_by_id.get(price_uuid) if price_uuid is not None else None
        if existing is None:
            errors.append(f"Fila {i + 1}: no corresponde a una fila curada de este anuncio")
            continue

        product = _resolve_row_product(db, products_by_name, row)
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

        updates.append((existing, product, price_value, province, row))

    if errors:
        db.rollback()
        return request.app.state.templates.TemplateResponse(
            request,
            "curation_edit.html",
            {
                "ingestion": ingestion,
                "products": _all_products(db),
                "provinces": _all_provinces(db),
                "rows": typed_rows,
                "edit_mode": True,
                "values": {
                    "source_reference": source_reference,
                    "published_date": str(form.get("published_date", "")).strip(),
                },
                "error": " ".join(errors),
                "currencies": [c.value for c in Currency],
                "units": UNITS,
            },
            status_code=422,
        )

    resolver = RateResolver(db, rate_fetcher)
    unconverted = 0
    for existing, product, price_value, province, row in updates:
        # `changed` se calcula ANTES de mutar `existing` (design.md D6): una
        # comparación posterior siempre daría `False`, porque ya se habría
        # pisado el valor viejo. `Decimal("10.00") != Decimal("10")` es
        # `False` — el round-trip de ceros de más no genera un falso positivo.
        changed = row["currency"] != existing.currency or price_value != existing.price

        existing.product_id = product.id
        existing.province_id = province.id if province is not None else None
        existing.price = price_value
        existing.currency = row["currency"]
        existing.quantity = _parse_decimal(row["quantity"])
        existing.unit = canonical_unit(row["unit"])
        existing.sale_format = (
            row["sale_format"] if row["sale_format"] in _SALE_FORMAT_VALUES else None
        )
        existing.packaging = _packaging_from_form(
            row["packaging_format"], row["packaging_detail"]
        )
        existing.published_date = published_date

        if row["currency"] == Currency.CUP.value:
            apply_resolution(existing, NO_RATE)  # moneda nacional: nunca lleva tasa
        elif changed or existing.exchange_rate is None:
            anchor = anchor_date(existing.published_date, ingestion.captured_at)
            apply_resolution(existing, resolver.resolve(existing.currency, anchor))
            if existing.exchange_rate is None:
                unconverted += 1
        # si no cambió ni moneda ni precio y ya tenía tasa: no se toca (D6).

    # Mismo criterio que `submit_curation`: solo se pisa si llegó algo. Vaciar
    # `source_reference` desde acá no está soportado en este slice.
    if source_reference:
        ingestion.source_reference = redact_personal_data(source_reference)

    db.commit()

    redirect_url = (
        f"/curation/history?unconverted={unconverted}" if unconverted else "/curation/history"
    )
    return RedirectResponse(url=redirect_url, status_code=status.HTTP_303_SEE_OTHER)
