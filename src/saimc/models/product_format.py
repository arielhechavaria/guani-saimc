"""Formatos de venta de un producto — `product_formats`.

Un formato es el triple `(packaging, quantity, unit)` colgado de un producto:
«caja de pollo de 40 lb», «paquete de 10 lb», «pollo por libra» =
`(a granel, 1, lb)`. Es la identidad de la serie de precios (spec.md §5, «o a
unidades distintas declaradas como tales»): la caja de 40 lb y la de 33 lb son
formatos distintos porque son objetos de mercado distintos, no variaciones de
un tamaño «por defecto» (design.md D1).

`packaging` sale de un vocabulario cerrado (`saimc.units.PACKAGING`) y **sí**
lleva CHECK en la base, a diferencia de `prices_history.unit` que a propósito
no lo tiene: el envase es la columna que ya se pudrió una vez con texto libre,
y ampliar su vocabulario cuesta —correctamente— una migración chica
(design.md D2).
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from saimc.models.base import Base, utc_now


class ProductFormat(Base):
    __tablename__ = "product_formats"
    __table_args__ = (
        UniqueConstraint(
            "product_id",
            "packaging",
            "quantity",
            "unit",
            name="uq_product_formats_identity",
        ),
        # Literal SQL congelado, mismo patrón que `ck_prices_history_sale_format`
        # (design.md D2). No se deriva de `saimc.units.PACKAGING` a propósito:
        # acoplar un artefacto de migración inmutable a código de aplicación es
        # lo que el diseño archivado de la seed migration ya rechazó. Si las dos
        # listas divergen, la migración que amplíe el vocabulario actualiza
        # ambas.
        CheckConstraint(
            "packaging IN ('paquete', 'saco', 'caja', 'botella', 'pomo', 'a granel')",
            name="ck_product_formats_packaging",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "products.id",
            ondelete="RESTRICT",
            name="fk_product_formats_product_id",
        ),
        nullable=False,
    )
    packaging: Mapped[str] = mapped_column(String(32), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    unit: Mapped[str] = mapped_column(String(10), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
