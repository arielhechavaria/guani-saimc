"""Historial de precios — `prices_history` en la Processing Layer (spec.md §7.2).

Cada fila conserva linaje obligatorio hacia el registro crudo del que salió
(`source_id`, RESTRICT) y, opcionalmente, la conversión a CUP cuando existe
tasa de cambio. Sin columnas de dato personal (ver exclusiones explícitas del
spec).

Desde B1 (`docs/spec-slice-B1.md`) cada fila lleva además el resultado del
motor de validación (`validation_status`, `confidence_score`,
`validation_detail`) y su procedencia (`provenance`). Esas columnas no están en
la lista de §7.2: es un apartamiento anotado en `docs/spec-map.md`.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from saimc.models.base import Base, utc_now


class Currency(StrEnum):
    CUP = "CUP"
    USD = "USD"
    EUR = "EUR"
    MLC = "MLC"


class SaleFormat(StrEnum):
    RETAIL = "retail"
    WHOLESALE = "wholesale"


class Provenance(StrEnum):
    """Quién hizo la fila (`prices_history.provenance`, slice B1).

    `audited` solo se alcanza desde `auto` (B2b), así que no pierde el dato de
    que la fila nació automática. En B1 nada escribe `auto` ni `audited`.
    """

    HUMAN = "human"
    AUTO = "auto"
    AUDITED = "audited"


# Ancho de `prices_history.packaging`. Se exporta porque la redacción corre
# antes del insert y puede alargar el texto por encima de este límite; quien
# escriba la columna necesita el número para recortar.
PACKAGING_MAX_LENGTH = 64


class PriceHistory(Base):
    __tablename__ = "prices_history"  # nombre literal del spec.md §7.2
    __table_args__ = (
        CheckConstraint(
            "currency <> 'CUP' OR exchange_rate IS NULL",
            name="ck_prices_history_exchange_rate_cup",
        ),
        CheckConstraint(
            "exchange_rate IS NULL OR "
            "(exchange_rate_date IS NOT NULL AND exchange_rate_source IS NOT NULL)",
            name="ck_prices_history_exchange_rate_provenance",
        ),
        CheckConstraint(
            "confidence_score IS NULL OR confidence_score BETWEEN 0 AND 100",
            name="ck_prices_history_confidence_score_range",
        ),
        CheckConstraint(
            "sale_format IS NULL OR sale_format IN ('retail', 'wholesale')",
            name="ck_prices_history_sale_format",
        ),
        # Estructural, no de vocabulario: un mínimo de cero o negativo no
        # describe ninguna transacción y no hay lectura de aplicación que lo
        # vuelva válido (design.md, D6). La capa web degrada la entrada mala a
        # `NULL` antes de llegar acá, así que este CHECK es un piso, no un
        # camino de error que el operador pueda pisar.
        CheckConstraint(
            "min_purchase_quantity IS NULL OR min_purchase_quantity > 0",
            name="ck_prices_history_min_purchase_quantity",
        ),
        # Vocabulario cerrado, mismo patrón que `ck_prices_history_sale_format`
        # (design.md, D2): `price_basis` tiene exactamente dos valores posibles
        # y un tercero sería una pregunta distinta, no un vocabulario que crece
        # — a diferencia de `unit`, que sí se deja fuera de la base a propósito.
        CheckConstraint(
            "price_basis IS NULL OR price_basis IN ('unit', 'total')",
            name="ck_prices_history_price_basis",
        ),
        # Los siete estados de `saimc.validation.ValidationStatus` (B1). El
        # literal va acá y no importado: el modelo no depende del motor.
        CheckConstraint(
            "validation_status IS NULL OR validation_status IN ("
            "'approved', 'incomplete', 'anomaly', 'inconsistent', "
            "'possible_duplicate', 'untrusted_source', 'unverified')",
            name="ck_prices_history_validation_status",
        ),
        CheckConstraint(
            "provenance IN ('human', 'auto', 'audited')",
            name="ck_prices_history_provenance",
        ),
        Index("idx_prices_history_series", "product_id", "province_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "products.id",
            ondelete="RESTRICT",
            name="fk_prices_history_product_id",
        ),
        nullable=False,
    )
    # Nullable a propósito: la especificación (§6.2, Regla 3) acepta «Provincia: No
    # determinada» cuando el anuncio solo indica una zona («Oriente»).
    province_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "provinces.id",
            ondelete="RESTRICT",
            name="fk_prices_history_province_id",
        ),
        nullable=True,
    )
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    # Nulo mientras nada obtenga la tasa: un anuncio en USD se guarda sin
    # convertir en vez de bloquearse (§6.3 E, «Alcance en Fase 0»).
    price_cup: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    exchange_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    exchange_rate_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    exchange_rate_source: Mapped[str | None] = mapped_column(String(50), nullable=True)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    # Cuánto exige comprar el anuncio, **en la misma unidad que `unit`** y con
    # el mismo tipo que `quantity`, para que las dos sean comparables sin
    # tabla de conversión (design.md, D1). Un mínimo dicho en pacas no sirve:
    # una paca trae 10 o 30 paquetes según el anuncio.
    # `NULL` significa «el anuncio no declara mínimo», no «se desconoce»: un
    # anuncio que ofrece un kilo suelto sin condiciones es exactamente eso.
    min_purchase_quantity: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 3), nullable=True
    )
    # Qué representa `price` en esta fila: `unit` = ya es el precio de una
    # unidad de `unit`; `total` = es el total del lote de `quantity` unidades.
    # `NULL` es «no resuelto» (legado o pendiente de curar), NUNCA se lee como
    # equivalente a `total` — esa lectura implícita es exactamente el bug que
    # dividía «Arroz guyanés a 340lb» (`price=340, quantity=340`) en 1 CUP/lb
    # en vez de ~340 (proposal.md). Obligatorio solo cuando `quantity != 1`, y
    # esa obligatoriedad se resuelve en la capa de captura (`curation.py`), no
    # acá: con cantidad uno, unitario y total son el mismo número.
    price_basis: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # Día de publicación del anuncio, no instante — eje temporal de la serie
    # (spec.md §6.1c/§7.2). Nullable, sin default y sin CHECK: un dato
    # secundario nunca debe bloquear un insert (design.md, D2).
    published_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Unidad de venta canónica (`saimc.units.UNITS`). Nullable, sin CHECK: el
    # vocabulario cerrado se aplica en código de aplicación, no en la base,
    # para que crecer el vocabulario nunca exija una migración (design.md, D1).
    unit: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # Minorista o mayorista, juicio manual del operador — nada lo deriva a
    # partir de `unit`: un precio derivado de dividir un saco lleva el mismo
    # `unit = "lb"` que uno genuinamente minorista (design.md, D4).
    sale_format: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # `packaging` (texto libre) fue retirada por `0019`: el envase pasó a ser
    # parte de `format_id`, con vocabulario cerrado y CHECK. Solo sobrevive su
    # copia textual de respaldo, abajo.
    #
    # Copia textual de `packaging` que hace `0018`, para que `0019` pueda
    # dropear la columna y el downgrade recupere las variantes escritas a mano
    # ("Arroz importado a 320 la libra") — el mapeo a formato es muchos-a-uno y
    # con pérdida (design.md, D6). Una migración posterior la elimina cuando el
    # mapeo esté confirmado en producción.
    packaging_legacy: Mapped[str | None] = mapped_column(
        String(PACKAGING_MAX_LENGTH), nullable=True
    )
    # Formato de venta: el triple (envase, cantidad, unidad) contra el que se
    # agrega la serie (spec.md §5). `NULL` = «formato no establecido», agrega en
    # su propia celda igual que `unquantified` — nunca se adivina (design.md
    # D4). `quantity` y `unit` de la fila se escriben DESDE el formato y quedan
    # denormalizados a propósito: `resolve_row`, `basis_backfill` y `measure`
    # los leen hoy (design.md D8).
    format_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "product_formats.id",
            ondelete="RESTRICT",
            name="fk_prices_history_format_id",
        ),
        nullable=True,
    )
    # Variante declarada (kind/brand/origin) contra la que agrega la serie
    # (product-variants). `NULL` = «variante no declarada», agrega en su
    # propia celda igual que `format_id = NULL` — nunca se adivina.
    variant_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "product_variants.id",
            ondelete="RESTRICT",
            name="fk_prices_history_variant_id",
        ),
        nullable=True,
    )
    # Linaje al registro crudo. RESTRICT y nunca CASCADE: raw_ingestions es
    # inmutable y ningún precio publicado puede quedar sin su evidencia.
    source_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "raw_ingestions.id",
            ondelete="RESTRICT",
            name="fk_prices_history_source_id",
        ),
        nullable=False,
    )
    # Reglas del motor en `pass` sobre seis, por cien. Lo escribe
    # `saimc.validation.persist` al curar, al editar y al revalidar (B1). Es
    # descriptivo: nada lo compara contra un umbral. Nulo = fila sin validar.
    confidence_score: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    # El estado que le dio el motor (`ValidationStatus`). Es una foto: describe
    # la fila contra el mercado conocido cuando se validó. Nulo = sin validar
    # (fila anterior a B1 todavía no revalidada, o el motor falló al guardar).
    validation_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    # Las seis reglas, los motivos, la versión del motor, los parámetros y la
    # fecha de la validación. No repite estado ni puntaje. Solo resultados de
    # reglas: ni texto del anuncio ni datos del canal o del emisor.
    validation_detail: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # Quién hizo la fila (`Provenance`). Sin valor por defecto, a propósito:
    # quien crea una fila lo dice explícitamente.
    provenance: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
