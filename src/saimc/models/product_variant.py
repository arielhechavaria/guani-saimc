"""Variantes de producto — `product_variants`.

Una variante declara hasta tres ejes independientes y libremente cruzables de
un producto: `kind` («girasol», «blanca», «criollo»), `brand` («Vitalín»,
«Magdalena») y `origin` («importado», «nacional»). Es una sub-unidad del
producto, nunca una producto independiente: no cuenta contra el techo de
"3-5 productos bandera" de spec.md §9, y `is_pilot` vive únicamente en
`Product` (spec: product-variants).

Seis columnas de identidad, no tres (design.md D2): `kind`/`brand`/`origin`
conservan la ortografía tal como la escribió el operador («Vitalín»);
`kind_key`/`brand_key`/`origin_key` llevan la forma plegada por acento y
mayúscula (`variants.py::variant_key`) que cubre el `UNIQUE` — SQLite no
tiene `unaccent` ni un índice de expresión utilizable, así que plegar solo en
Python sería enforcement irreversible y no verificado en la base.

Sin vocabulario cerrado y sin CHECK de valores en ningún eje (a diferencia de
`product_formats.packaging`): estos valores nunca estuvieron guardados en
ningún campo antes de este cambio, así que no hay corpus del que derivar un
vocabulario (spec.md §12.6). El único CHECK es estructural:
`ck_product_variants_not_empty` prohíbe la fila con los tres ejes vacíos —
esa fila sería una segunda forma de decir «sin variante declarada», que ya
cubre `prices_history.variant_id = NULL` (design.md D1).
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from saimc.models.base import Base, utc_now


class ProductVariant(Base):
    __tablename__ = "product_variants"
    __table_args__ = (
        UniqueConstraint(
            "product_id",
            "kind_key",
            "brand_key",
            "origin_key",
            name="uq_product_variants_identity",
        ),
        # Estructural, no de vocabulario (design.md D1): sin este CHECK la
        # fila con los tres ejes vacíos sería indistinguible en la base de
        # `variant_id = NULL` — dos formas de decir «sin variante declarada».
        CheckConstraint(
            "kind_key <> '' OR brand_key <> '' OR origin_key <> ''",
            name="ck_product_variants_not_empty",
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
            name="fk_product_variants_product_id",
        ),
        nullable=False,
    )
    # Display: la ortografía que escribió el operador, intacta.
    kind: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", server_default=""
    )
    brand: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", server_default=""
    )
    origin: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", server_default=""
    )
    # Plegado (sin tildes, casefold, espacios colapsados). Es lo único que
    # cubre el UNIQUE — nunca se lee para mostrar (design.md D2). El único
    # escritor es `variants.py`; drift entre display y key no lleva CHECK
    # propio por la misma razón que `format_id` no lo lleva con packaging.
    kind_key: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", server_default=""
    )
    brand_key: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", server_default=""
    )
    origin_key: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", server_default=""
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
