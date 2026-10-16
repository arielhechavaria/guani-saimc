"""Cómo nace una fila de `prices_history` a partir de un anuncio.

Lo comparten los dos caminos que crean filas: la cola de curaduría
(`web.routers.curation.submit_curation`, una persona con el formulario delante)
y el carril 1 del motor (`validation.lane1`, sin nadie mirando; slice B2b-2,
`docs/spec-slice-B2b.md`). Viven acá, y no en el router, para que una fila
automática se arme con las mismas reglas que una curada: mismo formato, misma
fecha por defecto y misma tasa de cambio.

No decide nada: ni qué anuncio entra, ni qué campos son obligatorios. Eso es de
quien llama.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from saimc.exchange_rate import Fetcher, RateResolver, anchor_date, apply_resolution
from saimc.extraction.engine import guess_published_date
from saimc.formats import get_or_create_format
from saimc.models import ContentType, Currency, PriceHistory, RawIngestion, SourceType
from saimc.units import canonical_packaging, canonical_unit


def resolve_format_id(
    db: Session,
    product_id: uuid.UUID,
    *,
    packaging: str | None,
    quantity: Decimal | None,
    unit: str | None,
) -> uuid.UUID | None:
    """El `format_id` del triple (envase, cantidad, unidad), creando el
    `ProductFormat` si no existe.

    `None` cuando el triple está incompleto —falta el envase, la cantidad o la
    unidad, o alguno cae fuera del vocabulario canónico—: la fila se guarda con
    `format_id = NULL`. Nunca fabrica un formato a partir de datos parciales.
    """
    envase = canonical_packaging(packaging or "")
    unidad = canonical_unit(unit)
    if envase is None or unidad is None or quantity is None:
        return None
    return get_or_create_format(
        db, product_id=product_id, packaging=envase, quantity=quantity, unit=unidad
    ).id


def default_published_date(ingestion: RawIngestion, text: str) -> date | None:
    """Fecha de publicación que se le prellena al operador, y la que usa el
    carril 1 cuando nadie la mira.

    Para un mensaje de WhatsApp automatizado, `captured_at` YA es la fecha de
    publicación real — el timestamp que WAHA reporta del envío, no la hora en
    que alguien lo capturó a mano días después (la ambigüedad por la que
    `published_date` es un campo separado, 2026-08-10). Vale igual para la foto
    que para el texto (A7). La heurística de texto (`guess_published_date`,
    patrón "Nombre - fecha") sigue siendo lo mejor que hay para el resto de
    las fuentes: ahí `captured_at` es el mtime del archivo, no una fecha
    confiable.

    Los días se cuentan en calendario de La Habana (`anchor_date`), también los
    relativos de la heurística de texto («hoy», «ayer»).

    En una imagen, si el OCR trae una fecha explícita manda esa sobre
    `captured_at`: es la del anuncio, no la del reenvío.
    """
    if ingestion.source_type != SourceType.WHATSAPP_CHANNEL:
        return guess_published_date(text, ingestion.captured_at) if text else None
    # El día de La Habana, no el de UTC: una captura entre las 00:00 y las 04:00
    # UTC es todavía el día anterior en Cuba, y el formulario rechaza una fecha
    # posterior a ese día (T2, `docs/decisions.md` 2026-10-10).
    captured_day = anchor_date(None, ingestion.captured_at)
    if ingestion.content_type == ContentType.IMAGE and text:
        # El OCR puede traer la fecha del anuncio; si no, vale la del mensaje.
        return guess_published_date(text, ingestion.captured_at) or captured_day
    return captured_day


def resolve_rates(
    db: Session,
    entries: Sequence[PriceHistory],
    ingestion: RawIngestion,
    fetcher: Fetcher,
) -> int:
    """Convierte a CUP las filas en divisa, con la tasa del día del anuncio.

    El ancla es la de `anchor_date`: la fecha de publicación de la fila y, si
    no la tiene, el día de la captura en calendario de La Habana. Una fila en
    CUP no lleva tasa (CHECK de esquema). Devuelve cuántas filas quedaron sin
    convertir: qué se hace con ellas lo decide quien llama (la cola las guarda
    igual y avisa; el carril 1 no acepta el anuncio).
    """
    resolver = RateResolver(db, fetcher)
    unconverted = 0
    for entry in entries:
        if entry.currency == Currency.CUP.value:
            continue
        anchor = anchor_date(entry.published_date, ingestion.captured_at)
        apply_resolution(entry, resolver.resolve(entry.currency, anchor))
        if entry.exchange_rate is None:
            unconverted += 1
    return unconverted
