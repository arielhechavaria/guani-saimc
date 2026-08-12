"""Seed: productos bandera de Fase 0

Corresponde a docs/spec.md §9.0 (Fase 0): "3-5 productos bandera, ej.
Pollo, Arroz, Aceite, Leche, Huevos". Esta migración carga esas 5 filas con
sus alias de jerga cubana, que el motor de extracción usa para reconocer el
producto en texto libre (`saimc/extraction/engine.py`, `_match_product`).

Mismo patrón que 0003_seed_provinces.py: namespace propio, `id` determinista
vía `uuid.uuid5`, función de módulo testeable por `importlib` sin base real
(design.md, D4).

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-07
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Namespace propio, distinto al de provincias: cada catálogo deriva sus ids
# desde su propio espacio de nombres para que no puedan colisionar entre sí.
_PRODUCT_NS = uuid.uuid5(uuid.NAMESPACE_URL, "https://saimc.cu/products")

_PRODUCT_SEEDS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    (
        "Pollo",
        "carnes",
        "lb",
        ("pollo congelado", "pollo entero", "muslos de pollo"),
    ),
    ("Arroz", "granos", "lb", ("arroz blanco",)),
    ("Aceite", "aceites", "litro", ("aceite de cocina", "aceite vegetal")),
    ("Leche", "lacteos", "litro", ("leche en polvo", "leche fresca")),
    ("Huevos", "huevos", "unidad", ("cartón de huevos", "cajilla de huevos")),
)


def product_rows() -> list[dict[str, object]]:
    """Arma las 5 filas de productos bandera con `id` derivado de forma determinística.

    Función de módulo, no solo lógica dentro de `upgrade()`: así el test de
    la suite la carga con `importlib` y verifica el determinismo sin
    necesitar una base real.
    """
    ahora = datetime.now(UTC)
    return [
        {
            "id": uuid.uuid5(_PRODUCT_NS, name),
            "name": name,
            "category": category,
            "unit": unit,
            "aliases": list(aliases),
            "created_at": ahora,
        }
        for name, category, unit, aliases in _PRODUCT_SEEDS
    ]


def upgrade() -> None:
    products = sa.table(
        "products",
        sa.column("id", sa.Uuid(as_uuid=True)),
        sa.column("name", sa.String),
        sa.column("category", sa.String),
        sa.column("unit", sa.String),
        sa.column("aliases", sa.JSON),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(products, product_rows())


def downgrade() -> None:
    # Borra por `name IN (...)`, nunca TRUNCATE: un producto agregado a mano
    # después del seed sobrevive al rollback.
    names = [name for name, *_ in _PRODUCT_SEEDS]
    products = sa.table("products", sa.column("name", sa.String))
    op.execute(products.delete().where(products.c.name.in_(names)))
