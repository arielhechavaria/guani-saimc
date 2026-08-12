"""Seed: provincias oficiales de Cuba

Corresponde a docs/spec.md §9.0 («Consecuencias de diseño»): incorporar una
provincia debe ser configuración, no desarrollo. Esta migración carga las
16 filas (15 provincias más el municipio especial Isla de la Juventud),
todas sin publicar (`is_published=False`): la publicación se habilita por
criterio de densidad de datos, no por existir la fila (§9.0).

Autocontenida a propósito (ver design.md, D4): no importa nada de `saimc`
para que la migración quede congelada como artefacto y sea testeable con
`importlib`, sin necesitar una base real.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-07
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Namespace fijo para derivar `id` de forma determinística a partir del
# código de provincia. Constante y no `uuid.uuid4()`: dos corridas de esta
# migración (o dos llamadas a `province_rows()`) deben producir los mismos
# ids para la misma fila.
_PROVINCE_NS = uuid.uuid5(uuid.NAMESPACE_URL, "https://saimc.cu/provinces")

_PROVINCE_CODES: tuple[tuple[str, str], ...] = (
    ("pri", "Pinar del Río"),
    ("art", "Artemisa"),
    ("hab", "La Habana"),
    ("may", "Mayabeque"),
    ("mtz", "Matanzas"),
    ("cfg", "Cienfuegos"),
    ("vcl", "Villa Clara"),
    ("ssp", "Sancti Spíritus"),
    ("cav", "Ciego de Ávila"),
    ("cmg", "Camagüey"),
    ("ltu", "Las Tunas"),
    ("hlg", "Holguín"),
    ("gra", "Granma"),
    ("scu", "Santiago de Cuba"),
    ("gtm", "Guantánamo"),
    ("iju", "Isla de la Juventud"),
)


def province_rows() -> list[dict[str, object]]:
    """Arma las 16 filas de provincias con `id` derivado de forma determinística.

    Función de módulo, no solo lógica dentro de `upgrade()`: así el test de
    la suite la carga con `importlib` y verifica el determinismo sin
    necesitar una base real.
    """
    ahora = datetime.now(UTC)
    return [
        {
            "id": uuid.uuid5(_PROVINCE_NS, code),
            "code": code,
            "name": name,
            "is_published": False,
            "created_at": ahora,
        }
        for code, name in _PROVINCE_CODES
    ]


def upgrade() -> None:
    provinces = sa.table(
        "provinces",
        sa.column("id", sa.Uuid(as_uuid=True)),
        sa.column("code", sa.String),
        sa.column("name", sa.String),
        sa.column("is_published", sa.Boolean),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(provinces, province_rows())


def downgrade() -> None:
    # Borra por `code IN (...)`, nunca TRUNCATE: una provincia agregada a
    # mano después del seed sobrevive al rollback.
    codes = [code for code, _ in _PROVINCE_CODES]
    provinces = sa.table("provinces", sa.column("code", sa.String))
    op.execute(provinces.delete().where(provinces.c.code.in_(codes)))
