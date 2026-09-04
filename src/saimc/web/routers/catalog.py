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
from decimal import Decimal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from saimc.config import get_settings
from saimc.extraction.engine import mentions_any_product
from saimc.formats import get_or_create_format
from saimc.ingestion.inbox import ingest_inbox
from saimc.ingestion.whatsapp_backfill import fetch_channel_history
from saimc.models import (
    Channel,
    Platform,
    PriceHistory,
    Product,
    ProductFormat,
    Province,
    RawIngestion,
)
from saimc.models.raw_ingestion import SourceType
from saimc.units import PACKAGING, UNITS, canonical_unit
from saimc.web.auth import require_operator
from saimc.web.deps import get_db

router = APIRouter(dependencies=[Depends(require_operator)])

# Mismos defaults que usaba el alta al vuelo de la curaduría: `category` y
# `unit` son NOT NULL sin default en el modelo, y trabar un alta por un dato
# secundario sería repetir el error que este slice viene a corregir.
_DEFAULT_CATEGORY = "sin categoría"
_DEFAULT_UNIT = "unidad"  # canónico: está en `saimc.units.UNITS`

_REDIRECT = status.HTTP_303_SEE_OTHER


def _parse_uuid(raw: str) -> uuid.UUID | None:
    # Mismo helper que `curation._parse_uuid` (privado ahí, no se importa
    # entre routers para un helper de cinco líneas) — un `<select>` vacío o
    # una fila que el operador nunca completó no debe reventar el submit.
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


def _back(*, message: str | None = None, error: str | None = None) -> RedirectResponse:
    # Flash sin sesión ni cookie: el mensaje viaja en la URL del redirect y se
    # lee una sola vez del lado del GET. No hay otro flash en el proyecto —
    # se mantiene deliberadamente así de chico, sin categorías ni auto-cierre.
    params = {}
    if message:
        params["message"] = message
    if error:
        params["error"] = error
    query = f"?{urlencode(params)}" if params else ""
    return RedirectResponse(f"/catalog{query}", status_code=_REDIRECT)


@router.get("/catalog", response_class=HTMLResponse)
def catalog(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """Las dos listas, con el conteo de observaciones de cada producto.

    El conteo no es decoración: es lo que permite decidir a qué producto
    promover con evidencia en vez de por intuición. «Azúcar blanca» llegó a
    once observaciones antes de que nadie la declarara piloto.

    Búsqueda y filtro de plataforma van por querystring, no por JavaScript —
    mismo criterio que «Solo el foco» (`focus=1`), ya documentado en el
    template. Con 10 canales y 60 productos medidos en producción (2026-08-21)
    no hay volumen que justifique paginación; se deja fuera de alcance.
    """
    q_canal = (request.query_params.get("q_canal") or "").strip()
    platform_filter = (request.query_params.get("platform") or "").strip()
    q_producto = (request.query_params.get("q_producto") or "").strip()

    counts = dict(
        db.execute(
            select(PriceHistory.product_id, func.count())
            .group_by(PriceHistory.product_id)
        ).all()
    )

    pilot_count = db.execute(
        select(func.count()).select_from(Product).where(Product.is_pilot.is_(True))
    ).scalar_one()

    products_stmt = select(Product).order_by(Product.is_pilot.desc(), Product.name)
    if q_producto:
        products_stmt = products_stmt.where(Product.name.ilike(f"%{q_producto}%"))
    products = list(db.execute(products_stmt).scalars())

    # Formatos por producto, con su conteo de observaciones. El conteo decide
    # qué formato es borrable (cero = corrección de tipeo) y cuál no se toca —
    # design.md D7: los formatos no se retiran, la lista se ordena.
    format_rows = db.execute(
        select(ProductFormat, func.count(PriceHistory.id))
        .outerjoin(PriceHistory, PriceHistory.format_id == ProductFormat.id)
        .group_by(ProductFormat.id)
        .order_by(func.count(PriceHistory.id).desc(), ProductFormat.created_at.desc())
    ).all()
    formats_by_product: dict[uuid.UUID, list[tuple[ProductFormat, int]]] = {}
    for fmt, obs in format_rows:
        formats_by_product.setdefault(fmt.product_id, []).append((fmt, obs))

    channels_stmt = select(Channel).order_by(Channel.is_active.desc(), Channel.name)
    if q_canal:
        channels_stmt = channels_stmt.where(Channel.name.ilike(f"%{q_canal}%"))
    if platform_filter:
        channels_stmt = channels_stmt.where(Channel.platform == platform_filter)
    channels = list(db.execute(channels_stmt).scalars())

    provinces = list(db.execute(select(Province).order_by(Province.name)).scalars())

    return request.app.state.templates.TemplateResponse(
        request,
        "catalog.html",
        {
            "products": [(product, counts.get(product.id, 0)) for product in products],
            "formats_by_product": formats_by_product,
            "units": UNITS,
            "packaging": PACKAGING,
            "channels": channels,
            "provinces": provinces,
            "platforms": list(Platform),
            "pilot_count": pilot_count,
            "q_canal": q_canal,
            "platform_filter": platform_filter,
            "q_producto": q_producto,
            "flash_message": request.query_params.get("message"),
            "flash_error": request.query_params.get("error"),
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

    # `unit` se canonicaliza; un valor fuera del vocabulario no traba el alta
    # —sería el error que este slice corrige— pero tampoco se guarda crudo:
    # cae al default y el operador lo ajusta después con el `<select>`.
    db.add(
        Product(
            name=name,
            category=str(form.get("category", "")).strip() or _DEFAULT_CATEGORY,
            unit=canonical_unit(str(form.get("unit", ""))) or _DEFAULT_UNIT,
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


@router.post("/catalog/products/{product_id}/unit")
async def set_product_unit(
    product_id: uuid.UUID, request: Request, db: Session = Depends(get_db)
) -> RedirectResponse:
    """Corrige la unidad de un producto ya existente.

    El alta no tiene forma de editarse después: un producto cargado con la
    unidad equivocada (Picadillo entró como "unidad" cuando sus observaciones
    vienen en lb) queda fuera de la vista minorista para siempre, porque masa
    y conteo no son convertibles entre sí. `unit` deja de ser texto libre: un
    valor fuera de `saimc.units.UNITS` se rechaza con error, no se guarda ni
    se reemplaza por un default silencioso (proposal: `products.unit`).
    """
    product = db.get(Product, product_id)
    if product is None:
        return _back()

    form = await request.form()
    unit = canonical_unit(str(form.get("unit", "")))
    if unit is None:
        return _back(error="Unidad fuera del vocabulario.")
    product.unit = unit
    db.flush()
    return _back()


@router.post("/catalog/products/{product_id}/formats")
async def create_format(
    product_id: uuid.UUID, request: Request, db: Session = Depends(get_db)
) -> RedirectResponse:
    """Declara un formato de venta `(envase, cantidad, unidad)` de un producto.

    Es el mismo `get_or_create_format` que usa la curaduría al vuelo, así que
    declarar acá y crear desde el formulario convergen en la misma fila: el
    `UNIQUE` hace idempotente el alta. Un envase o unidad fuera de vocabulario,
    o una cantidad no numérica, se rechazan con error sin tocar nada.
    """
    product = db.get(Product, product_id)
    if product is None:
        return _back()

    form = await request.form()
    raw_qty = str(form.get("quantity", "")).strip().replace(",", ".")
    try:
        quantity = Decimal(raw_qty)
    except (ArithmeticError, ValueError):
        return _back(error="Cantidad inválida.")
    if quantity <= 0:
        return _back(error="La cantidad debe ser mayor que cero.")

    try:
        get_or_create_format(
            db,
            product_id=product.id,
            packaging=str(form.get("packaging", "")).strip().lower(),
            quantity=quantity,
            unit=str(form.get("unit", "")).strip().lower(),
        )
    except ValueError as exc:
        return _back(error=str(exc))
    db.flush()
    return _back(message=f"Formato agregado a «{product.name}».")


@router.post("/catalog/formats/{format_id}/delete")
def delete_format(format_id: uuid.UUID, db: Session = Depends(get_db)) -> RedirectResponse:
    """Borra un formato, solo si nunca recibió una observación.

    Los formatos no se retiran (design.md D7): que una caja de 40 lb deje de
    aparecer un mes es escasez medida, no un registro obsoleto. La única
    excepción es un formato con cero filas de precio — un typo (400 por 40) no
    tiene historial que proteger y borrarlo es una corrección, no un retiro.
    Pre-chequeo con SELECT, igual que `delete_channel`: capturar el
    IntegrityError del FK `RESTRICT` dejaría la sesión envenenada.
    """
    formato = db.get(ProductFormat, format_id)
    if formato is None:
        return _back()

    tiene_precios = db.execute(
        select(PriceHistory.id).where(PriceHistory.format_id == format_id).limit(1)
    ).first()
    if tiene_precios is not None:
        return _back(
            error="Ese formato ya tiene observaciones: no se retira, su ausencia es dato."
        )

    db.delete(formato)
    db.flush()
    return _back(message="Formato eliminado.")


@router.post("/catalog/channels")
async def create_channel(request: Request, db: Session = Depends(get_db)) -> RedirectResponse:
    form = await request.form()
    name = str(form.get("name", "")).strip()
    if not name:
        return _back()

    platform = str(form.get("platform", "")).strip()
    if platform not in {member.value for member in Platform}:
        platform = Platform.OTHER.value

    # Vacío -> None, no "": la columna es UNIQUE (migración 0011) y dos
    # canales manuales con cadena vacía chocarían entre sí.
    external_id = str(form.get("external_id", "")).strip() or None
    if external_id is not None:
        # Pre-chequeo con SELECT, igual que `create_product` (`:133`), y no
        # `except IntegrityError`: el error en `db.flush()` deja la sesión
        # envenenada y obligaría a un `db.rollback()` explícito antes de que
        # `_back(error=...)` pudiera siquiera renderizar. Con un solo
        # operador no hay concurrencia que pueda perder la carrera. El
        # nombre ya NO se valida acá (migración 0014): `idx_channels_name`
        # dejó de ser único, así que dos filas con el mismo nombre son un
        # alta legítima, no un choque.
        existing = db.execute(
            select(Channel).where(Channel.external_id == external_id)
        ).scalar_one_or_none()
        if existing is not None:
            return _back(error=f"Ese Id. WAHA ya está cargado en «{existing.name}».")

    db.add(
        Channel(
            name=name,
            platform=platform,
            url=str(form.get("url", "")).strip() or None,
            notes=str(form.get("notes", "")).strip() or None,
            external_id=external_id,
            province_id=_parse_uuid(str(form.get("province_id", ""))),
            address=str(form.get("address", "")).strip() or None,
            is_active=True,
        )
    )
    db.flush()
    return _back()


@router.post("/catalog/channels/{channel_id}/province")
async def set_channel_province(
    channel_id: uuid.UUID, request: Request, db: Session = Depends(get_db)
) -> RedirectResponse:
    """Fija o cambia la provincia de un canal ya existente.

    Separado del alta (docs/decisions.md, 2026-08-17) porque el caso real
    que lo motivó —Groupej Morales Surl— ya estaba en la tabla antes de que
    esta columna existiera: sin esto, el operador nunca podría cargarle la
    provincia a un canal que ya sigue.
    """
    channel = db.get(Channel, channel_id)
    if channel is None:
        return _back()

    form = await request.form()
    channel.province_id = _parse_uuid(str(form.get("province_id", "")))
    db.flush()
    return _back()


@router.post("/catalog/channels/{channel_id}/address")
async def set_channel_address(
    channel_id: uuid.UUID, request: Request, db: Session = Depends(get_db)
) -> RedirectResponse:
    """Fija o cambia la dirección del local de un canal ya existente.

    Mismo motivo que `set_channel_province`: los canales que ya seguía el
    operador antes de esta columna (docs/decisions.md, 2026-08-19) necesitan
    una forma de cargarla después del alta, no solo al crear el canal.
    """
    channel = db.get(Channel, channel_id)
    if channel is None:
        return _back()

    form = await request.form()
    channel.address = str(form.get("address", "")).strip() or None
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


@router.post("/catalog/channels/{channel_id}/edit")
async def edit_channel(
    channel_id: uuid.UUID, request: Request, db: Session = Depends(get_db)
) -> RedirectResponse:
    """Corrige los datos de alta de un canal ya existente.

    Mismos campos que `create_channel` — nombre, plataforma, enlace, notas e
    Id. WAHA. Provincia y dirección quedan afuera: ya tienen su propio
    endpoint de un solo campo con auto-envío (`set_channel_province`,
    `set_channel_address`) y no hace falta duplicarlos acá.
    """
    channel = db.get(Channel, channel_id)
    if channel is None:
        return _back()

    form = await request.form()
    name = str(form.get("name", "")).strip()
    if not name:
        return _back(error="El nombre no puede quedar vacío.")

    platform = str(form.get("platform", "")).strip()
    if platform not in {member.value for member in Platform}:
        platform = Platform.OTHER.value

    # Mismo pre-chequeo que `create_channel` (:195), excluyendo este propio
    # canal: si no se tocó el campo, no debe chocar contra sí mismo.
    external_id = str(form.get("external_id", "")).strip() or None
    if external_id is not None:
        existing = db.execute(
            select(Channel).where(
                Channel.external_id == external_id, Channel.id != channel_id
            )
        ).scalar_one_or_none()
        if existing is not None:
            return _back(error=f"Ese Id. WAHA ya está cargado en «{existing.name}».")

    channel.name = name
    channel.platform = platform
    channel.url = str(form.get("url", "")).strip() or None
    channel.notes = str(form.get("notes", "")).strip() or None
    channel.external_id = external_id
    db.flush()
    return _back()


@router.post("/catalog/channels/{channel_id}/delete")
def delete_channel(channel_id: uuid.UUID, db: Session = Depends(get_db)) -> RedirectResponse:
    """Borra un canal, solo si nunca capturó nada.

    `raw_ingestions.channel_id` tiene `ondelete="RESTRICT"` a propósito
    (migración 0015): un canal con historial se apaga, no se borra —
    `toggle_channel` es la vía para ese caso. Acá se verifica antes de
    borrar, mismo criterio que el chequeo de `external_id` repetido en
    `create_channel`: un pre-chequeo con SELECT en vez de capturar el
    IntegrityError, que dejaría la sesión envenenada. Esto cubre el alta
    por error — nombre mal tipeado, canal duplicado — que hoy no tiene
    forma de sacarse del catálogo salvo apagándolo para siempre.
    """
    channel = db.get(Channel, channel_id)
    if channel is None:
        return _back()

    tiene_ingestas = db.execute(
        select(RawIngestion.id).where(RawIngestion.channel_id == channel_id).limit(1)
    ).first()
    if tiene_ingestas is not None:
        return _back(
            error=f"«{channel.name}» ya capturó anuncios: no se puede eliminar, apagalo en su lugar."
        )

    nombre = channel.name
    db.delete(channel)
    db.flush()
    return _back(message=f"«{nombre}» eliminado.")


@router.post("/catalog/channels/{channel_id}/backfill")
def backfill_channel(channel_id: uuid.UUID, db: Session = Depends(get_db)) -> RedirectResponse:
    """Importa de una sola vez el histórico que WAHA ya tenga sincronizado
    de un canal (docs/decisions.md, 2026-08-17) — disparador manual, no
    automático: a diferencia del webhook, esto no reacciona solo, el
    operador decide cuándo correrlo por canal.

    Reusa `ingest_inbox` exactamente igual que el webhook (mismo hash,
    misma deduplicación, misma redacción de datos personales): correrlo dos
    veces sobre el mismo canal no duplica nada, un mensaje ya ingerido cae
    en `report.duplicates` en vez de crear una fila nueva.
    """
    channel = db.get(Channel, channel_id)
    if channel is None:
        return _back()
    if not channel.external_id:
        return _back(error=f"«{channel.name}»: sin Id. WAHA, no hay de dónde importar.")

    settings = get_settings()
    if not settings.waha_base_url or not settings.waha_api_key:
        return _back(error="Falta configurar SAIMC_WAHA_BASE_URL / SAIMC_WAHA_API_KEY.")

    messages = fetch_channel_history(
        settings.waha_base_url,
        settings.waha_api_key,
        settings.waha_session,
        channel.external_id,
        timeout=settings.waha_timeout,
    )
    if messages is None:
        return _back(error=f"No se pudo consultar WAHA para «{channel.name}».")
    if not messages:
        return _back(message=f"«{channel.name}»: WAHA no tiene histórico con texto para traer.")

    pilot_products = list(
        db.execute(select(Product).where(Product.is_pilot.is_(True))).scalars()
    )

    settings.inbox_dir.mkdir(parents=True, exist_ok=True)
    ingested = duplicates = visible = 0
    for parsed in messages:
        temp_path = settings.inbox_dir / f"whatsapp-backfill-{uuid.uuid4().hex}.txt"
        temp_path.write_text(parsed.text, encoding="utf-8")
        try:
            # Un `ingest_inbox` por mensaje, no un solo llamado con todos los
            # `paths`: los overrides (acá, `captured_at`) se aplican a TODOS
            # los paths de una llamada, y cada mensaje trae su propia fecha
            # real — agruparlos les pondría a todos el mismo `captured_at`.
            report = ingest_inbox(
                db,
                settings.inbox_dir,
                settings.raw_store_dir,
                paths=[temp_path],
                source_type=SourceType.WHATSAPP_CHANNEL,
                source_reference=channel.name,
                captured_at=parsed.timestamp,
                # `channel.id`, no la variable local `channel_id`: acá ese
                # nombre ya está atado al parámetro de la ruta.
                channel_id=channel.id,
            )
        finally:
            temp_path.unlink(missing_ok=True)
        if report.ingested:
            ingested += len(report.ingested)
            # Sin foco definido, `_pending_in_focus` no filtra nada (mismo
            # criterio ahí: filtrar contra un catálogo vacío escondería la
            # cola entera) — acá se replica esa excepción.
            if not pilot_products or mentions_any_product(parsed.text, pilot_products):
                visible += 1
        duplicates += len(report.duplicates)

    resumen = f"«{channel.name}»: {ingested} anuncios nuevos capturados"
    if ingested:
        # El filtro de foco (curation.py) esconde de la cola lo que no
        # menciona ningún producto piloto — sin eso, "traje 40" y "la cola
        # no se movió" parece un bug (docs/decisions.md, 2026-08-21).
        resumen += f", {visible} van a verse en la cola de curaduría"
    if duplicates:
        resumen += f", {duplicates} ya estaban."
    else:
        resumen += "."
    return _back(message=resumen)
