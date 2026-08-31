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

import re
import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

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
    mentions_any_product,
    probable_discard_reasons,
    propose_all,
)
from saimc.ingestion import origin
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
    SourceType,
)
from saimc.units import UNITS, canonical_unit
from saimc.web.auth import require_operator
from saimc.web.deps import get_db, get_rate_fetcher

router = APIRouter(dependencies=[Depends(require_operator)])

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
            "price": "",
            "currency": Currency.CUP.value,
            "quantity": "",
            "unit": "",
            "sale_format": "",
            "packaging_format": "",
            "packaging_detail": "",
            "min_purchase": "",
            "province_id": "",
            "price_basis": "",
        }
    return {
        # Sin fila propia todavía: existe recién cuando se confirma el submit.
        "price_id": "",
        # Solo llegan acá propuestas con producto piloto ya resuelto (ver el
        # filtro en `curation_queue`), así que el `else` es defensivo.
        "product_id": str(proposal.product.id) if proposal.product else "",
        "price": str(proposal.price) if proposal.price is not None else "",
        "currency": proposal.currency,
        "quantity": str(proposal.quantity) if proposal.quantity is not None else "",
        "unit": canonical_unit(proposal.unit) or "",
        # Nunca se prellena: es un juicio del operador, no algo que el motor
        # de extracción pueda inferir (design.md, D4 y "Data Flow").
        "sale_format": "",
        "packaging_format": "",
        "packaging_detail": "",
        # El slice que design.md D7 dejaba pendiente, resuelto solo para la
        # forma que el motor sabe leer sin adivinar: el precio escalonado de
        # un anuncio mayorista («Más de 100 cajas 27,5»), donde el mínimo se
        # deriva del tamaño de lote que el propio anuncio declara. Cualquier
        # otra forma sigue llegando vacía, que es lo correcto: el operador
        # corrige un campo en blanco más rápido que uno mal prellenado.
        "min_purchase": (
            str(proposal.min_purchase_quantity)
            if proposal.min_purchase_quantity is not None
            else ""
        ),
        "province_id": str(proposal.province.id) if proposal.province else "",
        # D5 fijaba no prellenarlo NUNCA, porque un basis equivocado multiplica
        # el precio final por `quantity` en vez de ser cosmético. Ese riesgo
        # sigue siendo cierto y por eso la propuesta llega vacía salvo en un
        # caso: que el propio anuncio declare a cuánto sale la unidad («Sale
        # 515 Cup Lb») **y** la aritmética confirme cuál de las dos lecturas
        # cierra (`_declared_price_basis`). Ahí no se hereda ni se adivina, se
        # lee. Apartamiento registrado en docs/decisions.md, 2026-08-25, con
        # las 7 filas mal marcadas que lo motivaron.
        "price_basis": proposal.price_basis or "",
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
        "price": str(price.price),
        "currency": price.currency,
        "quantity": str(price.quantity) if price.quantity is not None else "",
        "unit": price.unit or "",
        "sale_format": price.sale_format or "",
        "packaging_format": packaging_format,
        "packaging_detail": packaging_detail,
        "min_purchase": (
            str(price.min_purchase_quantity)
            if price.min_purchase_quantity is not None
            else ""
        ),
        "province_id": str(price.province_id) if price.province_id else "",
        "price_basis": price.price_basis or "",
    }


# Qué se cura primero (docs/decisions.md, 2026-08-18). Número más bajo, antes
# en la cola; dentro de un mismo nivel sigue mandando `captured_at`.
_FOCUS_MATCH = 0  # el texto menciona un producto del foco: aporta a una celda
_FOCUS_UNKNOWN = 1  # imagen sin OCR: no se sabe sin transcribirla
_FOCUS_NONE = 2  # texto sin ningún producto del foco


def _burst_window() -> timedelta:
    return timedelta(minutes=get_settings().burst_window_minutes)


def _focus_rank(ingestion: RawIngestion, products: Sequence[Product]) -> int:
    """Cuánto aporta esta ingesta a las celdas que se están midiendo.

    "Todavía no sé" (una imagen, cuyo texto solo existe después del OCR) va
    entre "sí aporta" y "no aporta" a propósito: dejarla al final la mata de
    hambre en cuanto entra volumen de WhatsApp, y transcribir la cola entera
    en cada carga de página para saberlo sería pagar el OCR de todo para
    ordenar.

    La ráfaga (2026-08-21) NO toca este ranking, a propósito. Ascender al
    mensaje huérfano porque un vecino nombra un producto del foco se midió
    contra producción y rescataba 18 pendientes de los cuales 1 era el caso
    buscado: en un canal activo, cualquier anuncio de gasolina o de keratina
    cae a menos de diez minutos de uno del foco. Eso anulaba el filtro del
    2026-08-20 sin comprar casi nada. El huérfano se resuelve por el otro
    lado: se muestra como contexto del anuncio que sí entra (`_burst_context`),
    donde no le cuesta un clic al operador.
    """
    if ingestion.original_text is None:
        return _FOCUS_UNKNOWN
    if mentions_any_product(ingestion.original_text, products):
        return _FOCUS_MATCH
    return _FOCUS_NONE


def _pending_in_focus(db: Session, products: Sequence[Product]) -> list[RawIngestion]:
    """Los pendientes que el operador va a ver, en orden de curaduría.

    Hasta el 2026-08-18 el foco solo ordenaba: un anuncio de cemento seguía
    en la cola, al final. Con volumen real de WhatsApp eso no alcanzó — los
    negocios publican saludos, alertas de clima, links y ofertas de productos
    ajenos, y todo eso llegaba igual a la cola (`docs/decisions.md`,
    2026-08-20). Ahora el texto que no menciona ningún producto del foco no se
    muestra: es el mismo criterio que 2026-08-15 ya aplicaba a las filas del
    formulario, subido al nivel del anuncio entero.

    Esconder no es descartar. La fila sigue `PENDING` y su relevancia se
    recalcula en cada carga, así que un producto que entra al foco (`is_pilot`
    es un clic en /catalog) hace reaparecer sus anuncios sin reprocesar nada —
    por eso la relevancia no se congela en una columna al ingerir.

    `_FOCUS_UNKNOWN` (imagen sin OCR) sigue entrando: su relevancia es
    desconocida, no negativa, y descubrirla exigiría transcribir la cola
    entera en cada carga de página.
    """
    stmt = (
        select(RawIngestion)
        .where(RawIngestion.processing_status == ProcessingStatus.PENDING)
        .order_by(RawIngestion.captured_at.asc())
    )
    pending = list(db.execute(stmt).scalars().all())
    # Sin foco definido no hay relevancia que medir: filtrar contra un catálogo
    # vacío escondería la cola entera, que es peor que el ruido que el filtro
    # existe para sacar. Pasa de verdad — al arrancar, o si el operador
    # desmarca el último piloto en /catalog.
    if not products:
        return pending
    ranked = [(ingestion, _focus_rank(ingestion, products)) for ingestion in pending]
    # `sorted` es estable y la consulta ya vino ordenada por `captured_at`:
    # eso sale gratis el desempate por antigüedad dentro de cada nivel.
    return [
        ingestion
        for ingestion, rank in sorted(ranked, key=lambda par: par[1])
        if rank != _FOCUS_NONE
    ]


def _pending_out_of_focus(db: Session, products: Sequence[Product]) -> list[RawIngestion]:
    """Los pendientes SIN ningún producto del foco — la contracara exacta de
    `_pending_in_focus`, acotada al descarte en lote (`docs/decisions.md`,
    2026-08-31).

    Filtra por `_FOCUS_NONE` únicamente, nunca por `content_type`: lo que
    importa es que se haya CONFIRMADO que ningún renglón del texto menciona
    un piloto, así que no hay precio real que perder al descartar en bloque.
    `_FOCUS_UNKNOWN` (imagen sin OCR) queda afuera a propósito — su relevancia
    es desconocida, no negativa, y confundirla con "sin foco" descartaría
    imágenes que nadie llegó a mirar.

    Sin catálogo piloto no hay señal que confirmar: devuelve vacío en vez de
    tratar la cola entera como "sin foco". Es la guarda inversa de
    `_pending_in_focus` (ahí "sin foco definido" no filtra nada, para no
    esconder de más; acá no afirma nada, para no descartar de más — afirmar
    "sin foco" sin foco definido sería inventar la señal, no leerla).
    """
    if not products:
        return []
    stmt = (
        select(RawIngestion)
        .where(RawIngestion.processing_status == ProcessingStatus.PENDING)
        .order_by(RawIngestion.captured_at.asc())
    )
    pending = list(db.execute(stmt).scalars().all())
    return [
        ingestion for ingestion in pending if _focus_rank(ingestion, products) == _FOCUS_NONE
    ]


def _burst_context(db: Session, ingestion: RawIngestion) -> list[RawIngestion]:
    """Los mensajes que rodean al que se está curando, en orden cronológico.

    El vendedor parte el anuncio en varios mensajes seguidos: "Para mañana
    entrará aceite de 900ml" y, cuatro minutos y medio después, "A 4700".
    Curar el primero sin ver el segundo es guardar un anuncio sin precio —
    o descartarlo por creerlo inútil, que es lo que pasó con ese par real
    (docs/decisions.md, 2026-08-21).

    A diferencia del ranking, acá NO se filtra por estado: que el vecino ya
    esté curado o descartado no lo borra del anuncio, y leerlo sigue siendo lo
    que explica el mensaje que el operador tiene delante.

    Una consulta, y solo para la cabeza de la cola — no para los cientos de
    pendientes que `_pending_in_focus` acaba de ordenar.
    """
    if ingestion.channel_id is None:
        return []
    window = _burst_window()
    stmt = (
        select(RawIngestion)
        .where(
            RawIngestion.channel_id == ingestion.channel_id,
            RawIngestion.id != ingestion.id,
            RawIngestion.captured_at >= ingestion.captured_at - window,
            RawIngestion.captured_at <= ingestion.captured_at + window,
        )
        .order_by(RawIngestion.captured_at.asc())
    )
    return list(db.execute(stmt).scalars().all())


def _channel_province_id(db: Session, ingestion: RawIngestion) -> str | None:
    """La provincia fija del canal, como id de formulario.

    La regla en sí ("el canal siempre manda", docs/decisions.md 2026-08-17)
    vive en `saimc.ingestion.origin`, junto con la resolución del canal de
    origen. Se mudó allá el 2026-08-21 porque la medición de H0 necesita
    aplicar exactamente la misma regla y no puede importar de un router web.
    Acá queda solo la conversión a string que el `<select>` de la plantilla
    necesita.
    """
    province = origin.channel_province(db, ingestion)
    return str(province.id) if province is not None else None


def _channel_discard_stats(db: Session, ingestion: RawIngestion) -> tuple[int, int] | None:
    """Cuántos de los anuncios ya resueltos de este canal se descartaron.

    Puramente informativo (docs/decisions.md, 2026-08-22): el operador ya
    conoce sus canales ruidosos, esto solo se lo confirma con un número en
    vez de exigirle que lo recuerde. No dispara ningún automatismo — no hay
    umbral de corte, `spec-map.md` marca los umbrales nuevos sin valor
    asignado como "no inventar" y acá no hace falta ninguno.
    """
    if ingestion.channel_id is None:
        return None
    stmt = select(RawIngestion.processing_status, func.count(RawIngestion.id)).where(
        RawIngestion.channel_id == ingestion.channel_id,
        RawIngestion.processing_status != ProcessingStatus.PENDING,
    ).group_by(RawIngestion.processing_status)
    counts = dict(db.execute(stmt).all())
    total = sum(counts.values())
    if total == 0:
        return None
    return (counts.get(ProcessingStatus.DISCARDED, 0), total)


def _pilot_products(db: Session) -> list[Product]:
    """Los productos del conjunto foco, y solo esos.

    Único punto por donde el catálogo entra a la curaduría: alimenta las
    propuestas del motor, el `<select>` de cada fila y la resolución por
    nombre. Filtrar acá restringe las tres cosas a la vez, que es lo que hace
    del foco una propiedad de la herramienta y no una intención del operador
    (`docs/decisions.md`, 2026-08-15).

    Un producto fuera del foco sigue en el catálogo y conserva sus precios
    históricos; lo que no puede es recibir precios nuevos. Volver a incluirlo
    es un clic en /catalog, no una migración.
    """
    stmt = select(Product).where(Product.is_pilot.is_(True)).order_by(Product.name)
    return list(db.execute(stmt).scalars().all())


def _all_provinces(db: Session) -> list[Province]:
    return list(db.execute(select(Province).order_by(Province.name)).scalars().all())


def _status_counts(db: Session, products: Sequence[Product]) -> dict[str, int]:
    """Cuántos elementos hay por estado — para que el operador sepa cuánto
    le queda de la cola mientras cura, sin tener que consultar la base a mano.

    `pending` cuenta lo que se va a mostrar, no lo que hay en la tabla, y el
    resto va aparte en `out_of_focus`: desde que la cola esconde el texto
    ajeno al foco (`docs/decisions.md`, 2026-08-20), un `pending` crudo
    anunciaría cuarenta anuncios sobre una cola que se vacía en el tercero.
    Declarar los escondidos es lo que separa "no entró nada" de "entró ruido".
    """
    stmt = select(RawIngestion.processing_status, func.count(RawIngestion.id)).group_by(
        RawIngestion.processing_status
    )
    counts = dict(db.execute(stmt).all())
    resultado = {status.value: counts.get(status, 0) for status in ProcessingStatus}
    en_foco = len(_pending_in_focus(db, products))
    resultado["out_of_focus"] = resultado[ProcessingStatus.PENDING.value] - en_foco
    resultado[ProcessingStatus.PENDING.value] = en_foco
    return resultado


def _last_capture_notice(db: Session, *, now: datetime | None = None) -> str | None:
    """Hace cuánto entró la ingesta más reciente, en cualquier estado.

    Medición, no veredicto — mismo criterio que `age_days` en
    `exchange_rate/status.py` (§12.6: sin ventana de vigencia asignada, no se
    inventa). Esto no marca nada como "atrasado", solo informa la antigüedad;
    el operador es quien decide si eso es normal. Existe porque el bache de
    captura del 2026-08-23 (WAHA deslogueado remotamente) se leyó al
    principio como "cola vacía" por foco, cuando el problema real era que no
    entraba nada nuevo desde hacía más de un día — nada en la pantalla lo
    decía.
    """
    latest = db.execute(select(func.max(RawIngestion.captured_at))).scalar()
    if latest is None:
        return None
    # SQLite (solo en tests, ver tests/conftest.py) no conserva el offset en
    # el viaje de ida y vuelta pese a `DateTime(timezone=True)` — PostgreSQL sí.
    # La columna es UTC por contrato (docstring del modelo), así que asumirlo
    # acá es fiel al dato, no una adivinanza.
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=UTC)
    hours = int(((now or datetime.now(UTC)) - latest).total_seconds() // 3600)
    if hours < 1:
        return "Última captura: hace menos de una hora."
    if hours < 24:
        return f"Última captura: hace {hours} hora{'s' if hours != 1 else ''}."
    days = hours // 24
    return f"Última captura: hace {days} día{'s' if days != 1 else ''}."


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
    pasted: int = 0,
    no_candidates: bool = False,
    burst: Sequence[RawIngestion] = (),
    discard_hints: Sequence[str] = (),
    channel_discard_stats: tuple[int, int] | None = None,
    last_capture_notice: str | None = None,
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
            "last_capture_notice": last_capture_notice,
            "pasted": pasted,
            "currencies": [c.value for c in Currency],
            "units": UNITS,
            "status_counts": status_counts,
            # Solo la vuelve True el GET normal (docs/decisions.md,
            # 2026-08-17): tras un error de validación en el POST (segundo
            # llamador de esta función) el operador está corrigiendo algo que
            # SÍ escribió, mostrarle acá "sin candidatos, descartar" sería
            # confuso — ese llamador nunca pasa este parámetro, usa el
            # default.
            "no_candidates": no_candidates,
            # Los mensajes vecinos del mismo canal (docs/decisions.md,
            # 2026-08-21). Vacío para una captura suelta de la bandeja, que no
            # tiene canal, y para el POST con error de validación: ahí el
            # operador está corrigiendo lo que ya escribió, no leyendo el
            # anuncio por primera vez.
            "burst": burst,
            # Sugerencia asistida de descarte (docs/decisions.md, 2026-08-22):
            # razones calculadas sobre el texto disponible (original o el que
            # salió del OCR), nunca vacío por defecto en el POST con error —
            # el operador está corrigiendo algo que escribió, no leyendo el
            # anuncio por primera vez, mismo criterio que `burst` arriba.
            "discard_hints": discard_hints,
            "channel_discard_stats": channel_discard_stats,
        },
        status_code=status_code,
    )


# Filas en blanco de más que se ofrecen siempre, además de las prellenadas con
# candidatos del motor: el operador carga productos que `propose_all` no detectó
# sin tener que recargar la página (ver diseño del slice que reemplaza
# "guardar y agregar otro").
_EXTRA_BLANK_ROWS = 3


# Separador entre anuncios en el pegado rápido: una línea que sea solo guiones.
# Tolerante a propósito (espacios alrededor, tres guiones o siete): se pega
# desde el teléfono, y exigir una forma exacta convertiría la vía rápida en una
# fuente de errores silenciosos.
_PASTE_SEPARATOR = re.compile(r"^\s*-{3,}\s*$", re.MULTILINE)


def _split_pasted(pasted: str) -> list[str]:
    """Parte el pegado en anuncios y descarta los bloques vacíos.

    Un anuncio por bloque, porque el registro crudo es la unidad de curaduría:
    pegar tres anuncios como uno solo los volvería un único elemento de la cola
    y perdería la trazabilidad de cada precio a su anuncio (spec.md §6.5).
    """
    return [bloque.strip() for bloque in _PASTE_SEPARATOR.split(pasted) if bloque.strip()]


@router.post("/curation/paste")
async def paste_ads(request: Request, db: Session = Depends(get_db)) -> RedirectResponse:
    """Entrada rápida: pegar texto de anuncios sin pasar por un archivo.

    Existe por la medición del 2026-08-15: el 98% del material capturado eran
    imágenes, no por preferencia sino porque desde el teléfono una captura se
    sincroniza sola mientras que meter texto exigía abrir un editor, crear un
    `.txt` y guardarlo en la carpeta sincronizada. No había dónde pegar, y un
    registro de texto rinde varias veces más precios que uno de imagen.

    No es un camino de ingesta paralelo: escribe cada bloque como archivo en la
    bandeja y llama al mismo `ingest_inbox`, así que hereda tal cual el hash de
    contenido, la deduplicación, la redacción de datos personales y el archivo
    de evidencia en el almacén. Una vía rápida con menos garantías que la
    bandeja sería exactamente el agujero que §10.6 prohíbe.
    """
    form = await request.form()
    bloques = _split_pasted(str(form.get("pasted", "")))
    if not bloques:
        return RedirectResponse("/curation", status_code=status.HTTP_303_SEE_OTHER)

    settings = get_settings()
    settings.inbox_dir.mkdir(parents=True, exist_ok=True)

    temporales: list[Path] = []
    for bloque in bloques:
        # Nombre irrelevante: `ingest_file` renombra al hash del contenido al
        # moverlo al almacén. El `.txt` es lo que hace que `classify()` lo
        # reconozca como texto.
        destino = settings.inbox_dir / f"pegado-{uuid.uuid4().hex}.txt"
        destino.write_text(bloque, encoding="utf-8")
        temporales.append(destino)

    try:
        report = ingest_inbox(db, settings.inbox_dir, settings.raw_store_dir, paths=temporales)
    finally:
        # Los duplicados y los salteados no se mueven al almacén: sin esta
        # limpieza el temporal quedaría en la bandeja y volvería a entrar en el
        # próximo GET /curation.
        for temporal in temporales:
            temporal.unlink(missing_ok=True)

    return RedirectResponse(
        f"/curation?pasted={len(report.ingested)}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/curation", response_class=HTMLResponse)
def curation_queue(
    request: Request,
    db: Session = Depends(get_db),
    unconverted: int = 0,
    pasted: int = 0,
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

    # El catálogo del foco se lee antes que la cola: `_pending_in_focus` lo
    # necesita para decidir cuál de los pendientes aporta a una celda.
    products = _pilot_products(db)
    en_foco = _pending_in_focus(db, products)
    ingestion = en_foco[0] if en_foco else None
    provinces = _all_provinces(db)

    proposals: list[ExtractionProposal] = []
    recognized_text = ""
    source_reference_guess = ""
    published_date_guess = ""
    if ingestion is not None and ingestion.content_type == ContentType.TEXT:
        if ingestion.original_text:
            proposals = propose_all(ingestion.original_text, products, provinces)
            # `ingestion.source_reference` gana sobre la adivinanza de texto
            # cuando ya viene cargado (docs/decisions.md, 2026-08-17): el
            # webhook y el backfill de WAHA lo escriben con el nombre exacto
            # del canal al ingerir — un dato cierto, no una heurística.
            # `guess_source_reference` busca el patrón "Nombre - fecha" de
            # capturas estilo Facebook (2026-08-08), que un mensaje de
            # WhatsApp nunca trae, así que sin este orden el campo quedaba
            # vacío pese a que el dato ya estaba en la fila.
            source_reference_guess = (
                ingestion.source_reference or guess_source_reference(ingestion.original_text) or ""
            )
            # Mismo criterio que el canal origen, de arriba: para un mensaje
            # de WhatsApp automatizado, `captured_at` YA es la fecha de
            # publicación real — es el timestamp que WAHA reporta del envío
            # del mensaje, no la hora en que alguien lo capturó a mano horas
            # o días después (que es justo la ambigüedad por la que
            # `published_date` existe como campo separado, 2026-08-10). La
            # heurística de texto (`guess_published_date`, mismo patrón
            # "Nombre - fecha" que `guess_source_reference`) sigue siendo lo
            # mejor que hay para el resto de las fuentes: ahí `captured_at`
            # es el mtime del archivo, no una fecha confiable.
            known_date = (
                ingestion.captured_at.date()
                if ingestion.source_type == SourceType.WHATSAPP_CHANNEL
                else None
            )
            guessed = known_date or guess_published_date(
                ingestion.original_text, ingestion.captured_at
            )
            published_date_guess = guessed.isoformat() if guessed else ""
    elif ingestion is not None and ingestion.original_text is None:
        # content_type == IMAGE acá (el único otro valor posible).
        settings = get_settings()
        recognized_text = ocr.transcribe(settings.raw_store_dir / ingestion.file_path)
        if recognized_text:
            proposals = propose_all(recognized_text, products, provinces)
            source_reference_guess = (
                ingestion.source_reference or guess_source_reference(recognized_text) or ""
            )
            guessed = guess_published_date(recognized_text, ingestion.captured_at)
            published_date_guess = guessed.isoformat() if guessed else ""

    # Solo llegan al formulario las propuestas que cayeron en un producto del
    # foco. Una lista de precios de 25 renglones produce 25 candidatos, de los
    # cuales importan los pocos que son piloto: mostrar el resto obligaría al
    # operador a descartarlos de a uno, que es justamente el trabajo que esta
    # decisión elimina (`docs/decisions.md`, 2026-08-15). Lo descartado no se
    # pierde: el registro crudo es inmutable y conserva el anuncio entero.
    rows = [
        _values_from_proposal(proposal) for proposal in proposals if proposal.product is not None
    ]
    rows.extend(_values_from_proposal(None) for _ in range(_EXTRA_BLANK_ROWS))

    # "El canal siempre manda" (docs/decisions.md, 2026-08-17): pisa lo que
    # haya propuesto `_match_province` en TODAS las filas, incluidas las
    # filas en blanco de más — el operador conoce la provincia del canal de
    # antemano, así que hasta una fila cargada a mano se beneficia de no
    # tener que elegirla.
    channel_province_id = _channel_province_id(db, ingestion) if ingestion is not None else None
    if channel_province_id is not None:
        for row in rows:
            row["province_id"] = channel_province_id

    global_values = {
        "original_text": recognized_text,
        "source_reference": source_reference_guess,
        "published_date": published_date_guess,
    }

    # Mismo texto que ya se usó para `propose_all` arriba: el original si es
    # un ítem de texto, lo que salió del OCR si es una imagen. Vacío para
    # imágenes sin texto reconocido — ahí no hay señal que calcular.
    text_for_hints = (
        ingestion.original_text if ingestion.content_type == ContentType.TEXT else recognized_text
    ) if ingestion is not None else None
    discard_hints = (
        probable_discard_reasons(text_for_hints, products) if text_for_hints else []
    )

    return _render_queue(
        request,
        ingestion=ingestion,
        products=products,
        provinces=provinces,
        rows=rows,
        row_count=len(rows),
        global_values=global_values,
        error=None,
        status_counts=_status_counts(db, products),
        unconverted=unconverted,
        pasted=pasted,
        # `proposals` es la salida cruda de `propose_all`, antes del filtro
        # por foco de más arriba: vacía significa que no encontró NI producto
        # NI precio en ningún renglón del anuncio entero (docs/decisions.md,
        # 2026-08-17) — un saludo del canal con solo contacto, por ejemplo.
        # Distinto de "no matcheó ningún piloto": eso sí puede traer un
        # precio real de un producto fuera del foco, que vale la pena mirar.
        no_candidates=ingestion is not None and not proposals,
        burst=_burst_context(db, ingestion) if ingestion is not None else (),
        discard_hints=discard_hints,
        channel_discard_stats=(
            _channel_discard_stats(db, ingestion) if ingestion is not None else None
        ),
        last_capture_notice=_last_capture_notice(db),
    )


# Las dos rutas de acá abajo van declaradas ANTES que `submit_curation`
# (`POST /curation/{ingestion_id}`) y `discard_curation`
# (`POST /curation/{ingestion_id}/discard`): FastAPI intenta las rutas en
# orden de registro y `{ingestion_id}` matchea cualquier string por igual, así
# que si esas quedaran primero, un POST a `/curation/sin-foco/discard` caería
# ahí con `ingestion_id="sin-foco"` y el 422 de validación de UUID lo tumbaría
# antes de llegar acá.
@router.get("/curation/sin-foco", response_class=HTMLResponse)
def curation_out_of_focus(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """Descarte en lote de pendientes sin producto del foco (`docs/decisions.md`,
    2026-08-31).

    Muestra la lista completa antes de que el operador confirme nada —
    spec.md §6.1, "el operador siempre confirma o corrige": nunca es un botón
    ciego. Cada id que se ve acá viaja como campo oculto en el formulario de
    confirmación (`curation_sin_foco.html`), así que lo que se descarta es
    exactamente lo que esta respuesta mostró, no una lista recalculada al
    momento del submit (ver `discard_out_of_focus`).

    Acotada exclusivamente a la señal "sin producto del foco"
    (`_pending_out_of_focus`). Las otras dos de `probable_discard_reasons`
    (agotado, mayorista) corren sobre el texto completo mientras el precio se
    extrae línea por línea: un anuncio puede decir "agotado" en un renglón y
    tener un precio válido en otro, así que agruparlas acá arriesgaría tirar
    una fila real. Sin foco no corre ese riesgo — si ningún renglón nombra un
    piloto, no hay precio que perder — y por eso siguen descartándose de a una
    desde la cola normal.
    """
    products = _pilot_products(db)
    items = _pending_out_of_focus(db, products)
    return request.app.state.templates.TemplateResponse(
        request,
        "curation_sin_foco.html",
        {"items": items},
    )


@router.post("/curation/sin-foco/discard")
async def discard_out_of_focus(request: Request, db: Session = Depends(get_db)) -> RedirectResponse:
    """Confirma el descarte en lote de exactamente lo que el operador vio.

    Los ids viajan como campos ocultos repetidos (`ingestion_id`, uno por fila
    de `curation_sin_foco.html`) en vez de recalcular `_pending_out_of_focus`
    en este POST: así lo descartado es lo que el operador efectivamente miró
    en esa carga de página, no lo que haya en la tabla al momento del click
    (mismo principio de spec.md §6.1 que motiva mostrar la lista en primer
    lugar).

    Cada id se revalida igual contra `PENDING` + `_FOCUS_NONE` antes de
    tocarlo — nunca confía ciegamente en el form: entre el GET y este POST
    pudo curarse o descartarse a mano en otra pestaña, o pudo sumarse un
    piloto nuevo en /catalog que reclasifica el anuncio. Lo que ya no
    califica se ignora en silencio, sin romper el resto del lote; ningún otro
    pendiente se toca (mismo contrato "todo o nada solo sobre lo pedido" que
    `discard_curation`, pero por lote en vez de por uno).
    """
    form = await request.form()
    products = _pilot_products(db)

    for raw_id in form.getlist("ingestion_id"):
        ingestion_id = _parse_uuid(str(raw_id))
        if ingestion_id is None:
            continue
        ingestion = db.get(RawIngestion, ingestion_id)
        if ingestion is None or ingestion.processing_status != ProcessingStatus.PENDING:
            continue
        if _focus_rank(ingestion, products) != _FOCUS_NONE:
            continue
        ingestion.processing_status = ProcessingStatus.DISCARDED

    db.commit()
    return RedirectResponse(url="/curation/sin-foco", status_code=status.HTTP_303_SEE_OTHER)


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


def _parse_min_purchase(raw: str) -> Decimal | None:
    """Compra mínima que exige el anuncio, en la unidad de la propia fila.

    Vacío, no parseable o no positivo degrada a `None`, nunca rompe el submit
    — mismo contrato que `quantity`. Es lo contrario de lo que se decidió para
    `published_date` el 2026-08-14, y a propósito: allá el vacío **fabricaba**
    un valor equivocado (la fecha de carga sobre un anuncio viejo), acá no
    puede. «Sin mínimo declarado» y «el mínimo es la cantidad de esta fila»
    producen el mismo resultado en el filtro del rango público, así que un
    vacío no afirma nada falso (design.md, D2).

    El no-positivo se ataja acá y no en la base: `ck_prices_history_min_
    purchase_quantity` existe como piso estructural, no como camino de error
    que el operador pueda pisar desde el formulario.
    """
    value = _parse_decimal(raw)
    if value is None or value <= 0:
        return None
    return value


_PRICE_BASIS_VALUES = {"unit", "total"}


def _price_basis_value(raw: str) -> str | None:
    """Gate de vocabulario: cualquier otra cosa —incluido el envío en blanco—
    es «sin resolver», nunca un valor fabricado (mismo contrato que
    `_SALE_FORMAT_VALUES`, design.md D3)."""
    return raw if raw in _PRICE_BASIS_VALUES else None


def _price_basis_required(quantity: Decimal | None) -> bool:
    """Con cantidad uno (o sin cantidad) unitario y total son el mismo
    número: la pregunta es aritméticamente inerte y exigirla ahí solo
    costaría trabajo manual sin evitar ningún error (design.md, D3)."""
    return quantity is not None and quantity != 1


def _parse_date(raw: str) -> date | None:
    """Parsea el `<input type="date">` (ISO `YYYY-MM-DD` del navegador).

    Solo parsea: vacío o no parseable devuelve `None`. Qué se hace con ese
    `None` lo decide `_validated_published_date`, que es quien conoce la
    regla de negocio.
    """
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _validated_published_date(raw: str, captured_at: datetime) -> tuple[date | None, str | None]:
    """Valida la fecha de publicación del anuncio: `(fecha, error)`.

    **Campo obligatorio y bloqueante desde el 2026-08-14**, reemplazando el
    contrato original de «campo nunca bloqueante / degrada a NULL»
    (docs/decisions.md, 2026-08-10). Aquella decisión se apoyaba en que
    ningún consumidor leía la columna; hoy la leen dos, y los dos se
    equivocan en silencio cuando falta:

    - `cells.compute_cells` usa `published_date` como eje temporal de la
      ventana de N días, con repliegue a `created_at`: un anuncio de hace
      tres semanas cargado sin fecha entra al rango vigente como si fuera
      de hoy.
    - `anchor_date` ancla la tasa de cambio en `published_date`, con
      repliegue a `captured_at`: ese mismo anuncio viejo se convierte a CUP
      con la tasa de hoy, que no es la que regía cuando se publicó.

    El repliegue no se saca (las filas anteriores a esta regla tienen la
    columna en `NULL` y hay que seguir leyéndolas): lo que se saca es la
    posibilidad de crear filas nuevas sin fecha.

    La cota superior es el día de la captura en calendario de La Habana
    —vía `anchor_date`, el mismo criterio que usa la conversión monetaria—
    y no `date.today()`: un anuncio no puede haberse publicado después de
    que el operador lo capturó.
    """
    if not raw:
        return None, "Falta la fecha de publicación del anuncio."

    parsed = _parse_date(raw)
    if parsed is None:
        return None, "Fecha de publicación inválida."

    tope = anchor_date(None, captured_at)
    if parsed > tope:
        return None, f"La fecha de publicación no puede ser posterior a la captura ({tope})."

    return parsed, None


def _resolve_row_product(
    db: Session, products_by_name: dict[str, Product], row: dict[str, str]
) -> Product | None:
    """Resuelve el producto de una fila contra el conjunto piloto.

    Ya no da de alta productos. El alta al vuelo desde este formulario es lo
    que produjo 55 productos de una sola observación y duplicados que parten
    la densidad de una serie en dos («Arroz criollo» contra «Arroz», «Azúcar
    blanco» contra «Azúcar blanca»). El catálogo se administra en `/catalog`,
    donde agregar un producto es una decisión explícita y no el efecto
    colateral de haber tipeado un nombre (`docs/decisions.md`, 2026-08-15).

    `products_by_name` sigue en la firma porque los dos caminos de escritura
    lo arman una sola vez por submit; acá se usa como caché de lectura.

    Un `product_id` que no parsea como UUID o que no existe se trata como
    "sin producto" para esta fila, sin romper con una excepción: el operador
    puede haber elegido "— seleccionar —" o el id puede haber quedado obsoleto
    entre el GET y el submit.
    """
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
        "price": str(form.get(f"price_{index}", "")).strip(),
        "currency": str(form.get(f"currency_{index}", "")).strip() or Currency.CUP.value,
        "quantity": str(form.get(f"quantity_{index}", "")).strip(),
        "unit": str(form.get(f"unit_{index}", "")).strip(),
        "sale_format": str(form.get(f"sale_format_{index}", "")).strip(),
        "packaging_format": str(form.get(f"packaging_format_{index}", "")).strip(),
        "packaging_detail": str(form.get(f"packaging_detail_{index}", "")).strip(),
        "min_purchase": str(form.get(f"min_purchase_{index}", "")).strip(),
        "province_id": str(form.get(f"province_id_{index}", "")).strip(),
        "price_basis": str(form.get(f"price_basis_{index}", "")).strip(),
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

    La tabla manda `row_count` filas indexadas (`product_id_0`, `price_0`, ...).
    El producto de cada fila sale del `<select>`, que desde el 2026-08-15 solo
    lista pilotos: acá no se dan de alta productos (ver `_resolve_row_product`).
    El operador descarta una tarjeta que no quiere cargar eliminándola del lado
    del cliente (botón "Eliminar" en la plantilla, sin submit): sus campos
    indexados no viajan en el POST, así que cae sola en la rama de fila vacía
    de abajo. Una fila sin producto y sin precio se ignora en silencio (no es
    una fila que el operador quiso cargar); con solo uno de los dos es un error
    explícito, porque ahí sí hay intención de cargar algo pero falta un dato.
    Todo o nada: si alguna fila con datos tiene un error, no se guarda
    ninguna — un estado a medias es más confuso que repetir el submit. La
    fecha de publicación
    entra en ese mismo todo-o-nada desde el 2026-08-14: es obligatoria y
    bloquea el submit entero (ver `_validated_published_date`).
    """
    ingestion = _require_pending(db, ingestion_id)
    form = await request.form()

    original_text = str(form.get("original_text", "")).strip()
    source_reference = str(form.get("source_reference", "")).strip()
    published_date_raw = str(form.get("published_date", "")).strip()
    published_date, published_date_error = _validated_published_date(
        published_date_raw, ingestion.captured_at
    )
    try:
        row_count = int(str(form.get("row_count", "0")))
    except ValueError:
        row_count = 0

    # Catálogo existente + altas de esta misma request: dos filas con el mismo
    # producto nuevo (mismo submit) deben reusar el Product, no duplicarlo.
    products_by_name = {p.name.lower(): p for p in _pilot_products(db)}

    errors: list[str] = []
    if published_date_error is not None:
        errors.append(published_date_error)
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

        quantity_value = _parse_decimal(row["quantity"])
        price_basis = _price_basis_value(row["price_basis"])
        if price_basis is None and _price_basis_required(quantity_value):
            errors.append(
                f"Fila {i + 1}: declará si el precio es por unidad o por el lote entero"
            )
            continue

        entries_to_create.append(
            PriceHistory(
                product_id=product.id,
                province_id=province.id if province is not None else None,
                price=price_value,
                currency=row["currency"],
                quantity=quantity_value,
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
                min_purchase_quantity=_parse_min_purchase(row["min_purchase"]),
                price_basis=price_basis,
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
            products=_pilot_products(db),
            provinces=_all_provinces(db),
            rows=typed_rows,
            row_count=row_count,
            global_values={
                "original_text": original_text,
                "source_reference": source_reference,
                "published_date": published_date_raw,
            },
            error=" ".join(errors),
            status_counts=_status_counts(db, _pilot_products(db)),
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
            "products": _pilot_products(db),
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
    published_date_raw = str(form.get("published_date", "")).strip()
    published_date, published_date_error = _validated_published_date(
        published_date_raw, ingestion.captured_at
    )
    try:
        row_count = int(str(form.get("row_count", "0")))
    except ValueError:
        row_count = 0

    existing_by_id = {price.id: price for price in _curated_rows(db, ingestion_id)}
    products_by_name = {p.name.lower(): p for p in _pilot_products(db)}

    errors: list[str] = []
    if published_date_error is not None:
        errors.append(published_date_error)
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

        quantity_value = _parse_decimal(row["quantity"])
        price_basis = _price_basis_value(row["price_basis"])
        if price_basis is None and _price_basis_required(quantity_value):
            errors.append(
                f"Fila {i + 1}: declará si el precio es por unidad o por el lote entero"
            )
            continue

        updates.append((existing, product, price_value, province, row))

    if errors:
        db.rollback()
        return request.app.state.templates.TemplateResponse(
            request,
            "curation_edit.html",
            {
                "ingestion": ingestion,
                "products": _pilot_products(db),
                "provinces": _all_provinces(db),
                "rows": typed_rows,
                "edit_mode": True,
                "values": {
                    "source_reference": source_reference,
                    "published_date": published_date_raw,
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
        existing.min_purchase_quantity = _parse_min_purchase(row["min_purchase"])
        # Re-derivados de `row` con los mismos dos helpers puros que ya
        # validaron esta fila en el primer loop (D4): son funciones puras de
        # los strings crudos, así que no pueden derivar del valor validado
        # arriba — ensanchar la tupla `updates` de 5 a 7 elementos para
        # cargarlos ya validados haría lo mismo con más superficie.
        existing.price_basis = _price_basis_value(row["price_basis"])
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
