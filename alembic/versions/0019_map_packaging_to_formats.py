"""Mapea `prices_history.packaging` a `product_formats` y dropea la columna.

Segunda mitad del cambio que `0018` dejó aditivo (design.md D4a). Va acá y no
en `0018` porque `curation.py` escribió `packaging` hasta el commit 4 de la
cadena: dropear la columna antes habría roto el formulario de curaduría
durante todo el intervalo.

**Solo toca filas con `format_id IS NULL AND packaging IS NOT NULL`.** Las
curadas entre el commit 4 y este ya traen su formato resuelto por
`get_or_create_format`; re-mapearlas desde el texto viejo las pisaría con una
lectura peor.

**Mapea solo lo determinista y rechaza el resto** (design.md D4). Lo que no
resuelve queda en `format_id = NULL`, visible como «sin formato», nunca
adivinado — migrar un error lo vuelve permanente. Tres rechazos conocidos en
el corpus del 2026-09-04:

1. `Arroz importado a 320 la libra` — un pedazo del anuncio guardado como
   nombre de envase, no un envase.
2 y 3. Dos filas con `price == quantity` y `price_basis = 'unit'`: es el
   artefacto de parseo de «Arroz guyanes a $340lb», donde la cantidad es el
   precio leído dos veces (design.md D5, y `cells.py` sobre el bug de
   2026-08-13). No hay lote de 340 lb que mapear; mapearlas acuñaría una
   serie fantasma `(a granel, 340, lb)`.

Las otras 12 filas con `price_basis = 'unit'` y cantidad distinta de 1 **sí**
se mapean: su cantidad dimensiona bien el lote («Picadillo de pollo de 50lb a
545 / La caja a 27 250» → 545 × 50 = 27.250, la aritmética confirma las dos
cosas).

`contenedor` y `paca` **no** son envases (design.md D3): son vasijas de
transporte de contenido variable, y en las dos el precio guardado ya es el
del formato que transportan. Cada una se remapea al formato real que lleva
debajo, así que excluirlas del vocabulario no pierde ninguna observación.

El drop es reversible: `0018` copió la columna a `packaging_legacy` y el
`downgrade` restituye desde ahí. Hace falta porque el mapeo es muchos-a-uno y
con pérdida — `Arroz importado a 320 la libra` no se reconstruye desde un
formato (design.md D6). El `downgrade` **no** deshace los `format_id` ni
borra los `product_formats` creados: no hay forma de distinguir los que puso
esta migración de los que puso la curaduría, y dejarlos es inofensivo — la
columna sigue existiendo hasta que baje `0018`.

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-04
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Texto guardado (en minúsculas) -> envase canónico. La cantidad y la unidad
# salen de la propia fila: `Saco de arroz De 110 lb` ya trae `(110, lb)` y
# `Paquete de arroz 1kg` ya trae `(1, kg)`, así que el texto solo aporta el
# nombre del envase.
_PACKAGING_MAP: dict[str, str] = {
    "paquete": "paquete",
    "saco": "saco",
    "caja": "caja",
    "botella": "botella",
    "a granel": "a granel",
    "saco de arroz de 110 lb": "saco",
    "paquete de arroz 1kg": "paquete",
    # `paca` trae 10 o 30 paquetes según el anuncio y el precio guardado es el
    # del paquete: «Paquete de arroz de 1kg a 770, la paca trae 10» (D3).
    "paca que contiene 10 paquetes": "paquete",
    "paca que contiene 30 paquetes": "paquete",
}

# `contenedor` depende de la fila, no del texto: dimensiona el pedido, no el
# producto. Las cuatro combinaciones `(cantidad, unidad)` del corpus, con su
# evidencia en `proposal.md` — «1475 cajas × 31,8 USD» (muslo Pilgrim's 4×10
# lb), «Venta por contenedor — 1030 sacos… 25 KG», «1 contenedor de aceite de
# 900ml… 4.45 USD c/u», y las seis de carapacho de pollo cuyo anuncio dice
# «CAJA DE 10 KG». Una combinación que no esté acá se rechaza, no se adivina.
_CONTENEDOR_MAP: dict[tuple[str, str], str] = {
    ("10", "kg"): "caja",
    ("40", "lb"): "caja",
    ("25", "kg"): "saco",
    ("900", "ml"): "botella",
}

# Unidades canónicas admitidas (`saimc.units.UNITS`). Se replica acá y no se
# importa: una migración es un artefacto congelado y no debe romperse porque
# el vocabulario de la aplicación crezca después.
_UNITS = frozenset({"lb", "kg", "l", "ml", "unidad", "docena", "caja"})


def _normalizar_cantidad(quantity: Decimal) -> str:
    """Clave textual de la cantidad, sin ceros de cola: `Decimal("10.000")` y
    `Decimal("10")` son la misma cantidad y tienen que dar la misma clave."""
    return format(quantity.normalize(), "f")


def envase_para(
    *,
    packaging: str | None,
    quantity: Decimal | None,
    unit: str | None,
    price: Decimal,
    price_basis: str | None,
) -> str | None:
    """Envase canónico de una fila, o `None` si el mapeo no es determinista.

    Función pura y exportada para que los tests la recorran caso por caso sin
    montar la migración entera.
    """
    if not packaging or quantity is None or unit is None:
        return None
    if quantity <= 0 or unit not in _UNITS:
        return None

    # D5: `price == quantity` con basis 'unit' es el artefacto de parseo de
    # «Arroz guyanes a $340lb». La cantidad no dimensiona ningún lote.
    if price_basis == "unit" and price == quantity:
        return None

    texto = packaging.strip().lower()
    if texto in {"contenedor"}:
        return _CONTENEDOR_MAP.get((_normalizar_cantidad(quantity), unit))
    return _PACKAGING_MAP.get(texto)


def upgrade() -> None:
    conn = op.get_bind()

    filas = conn.execute(
        sa.text(
            "select id, product_id, packaging, quantity, unit, price, price_basis "
            "from prices_history "
            "where format_id is null and packaging is not null"
        )
    ).all()

    ahora = datetime.now(UTC)
    # Cache local del get-or-create: seis filas del mismo vendedor comparten
    # `(caja, 10, kg)` y tienen que converger a un solo `product_formats`.
    cache: dict[tuple[str, str, str, str], str] = {}

    for fila in filas:
        envase = envase_para(
            packaging=fila.packaging,
            quantity=Decimal(str(fila.quantity)) if fila.quantity is not None else None,
            unit=fila.unit,
            price=Decimal(str(fila.price)),
            price_basis=fila.price_basis,
        )
        if envase is None:
            continue  # queda en `format_id = NULL`, visible como sin formato

        cantidad = _normalizar_cantidad(Decimal(str(fila.quantity)))
        clave = (str(fila.product_id), envase, cantidad, fila.unit)
        format_id = cache.get(clave)

        if format_id is None:
            existente = conn.execute(
                sa.text(
                    "select id from product_formats where product_id = :p "
                    "and packaging = :e and quantity = :c and unit = :u"
                ),
                {"p": str(fila.product_id), "e": envase, "c": cantidad, "u": fila.unit},
            ).scalar()
            if existente is not None:
                format_id = str(existente)
            else:
                format_id = str(uuid.uuid4())
                conn.execute(
                    sa.text(
                        "insert into product_formats "
                        "(id, product_id, packaging, quantity, unit, created_at) "
                        "values (:i, :p, :e, :c, :u, :t)"
                    ),
                    {
                        "i": format_id,
                        "p": str(fila.product_id),
                        "e": envase,
                        "c": cantidad,
                        "u": fila.unit,
                        "t": ahora,
                    },
                )
            cache[clave] = format_id

        conn.execute(
            sa.text("update prices_history set format_id = :f where id = :i"),
            {"f": format_id, "i": str(fila.id)},
        )

    with op.batch_alter_table("prices_history") as batch_op:
        batch_op.drop_column("packaging")


def downgrade() -> None:
    with op.batch_alter_table("prices_history") as batch_op:
        batch_op.add_column(sa.Column("packaging", sa.String(length=64), nullable=True))

    # Textual desde el respaldo que dejó `0018`: el mapeo a formato no es
    # reversible por sí solo (design.md D6).
    op.execute("UPDATE prices_history SET packaging = packaging_legacy")
