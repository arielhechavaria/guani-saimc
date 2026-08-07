"""Processing Layer — `provinces`, `products`, `prices_history`.

Slice de esquema puro (§7.2 del whitepaper): estructura, nullability y
restricciones (CHECK/FK/único). Ningún caso evalúa exactitud de extracción
ni H0 — eso corresponde a la capa de extracción, no a esta.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from saimc.models import Product, Province


class TestGuardianInfraestructura:
    def test_sqlite_enforces_foreign_keys(self, session: Session) -> None:
        """Sin este PRAGMA activo por conexión, SQLite ignora las FKs y todos
        los casos de `ON DELETE RESTRICT` de este archivo pasarían en falso.

        Usa la misma conexión que la fixture `session` (StaticPool: una sola
        conexión compartida) para detectar el gotcha documentado en el
        diseño: si el listener del PRAGMA se registra después de
        `create_all`, la conexión ya existe y el listener nunca corre.
        """
        resultado = session.execute(text("PRAGMA foreign_keys")).scalar()
        assert resultado == 1


class TestProvinces:
    def test_insert_valido_con_is_published_false_por_defecto(
        self, session: Session
    ) -> None:
        provincia = Province(code="pri", name="Pinar del Río")
        session.add(provincia)
        session.commit()

        assert provincia.id is not None
        assert provincia.is_published is False
        assert provincia.created_at is not None

    def test_code_duplicado_rechazado(self, session: Session) -> None:
        session.add(Province(code="hab", name="La Habana"))
        session.commit()

        session.add(Province(code="hab", name="La Habana (otro nombre)"))
        with pytest.raises(IntegrityError):
            session.commit()

    def test_name_duplicado_rechazado(self, session: Session) -> None:
        session.add(Province(code="mtz", name="Matanzas"))
        session.commit()

        session.add(Province(code="mtz2", name="Matanzas"))
        with pytest.raises(IntegrityError):
            session.commit()


class TestProducts:
    def test_insert_valido_con_aliases_vacio_por_defecto(
        self, session: Session
    ) -> None:
        producto = Product(name="Arroz", category="granos", unit="lb")
        session.add(producto)
        session.commit()

        assert producto.id is not None
        assert producto.aliases == []
        assert producto.created_at is not None

    def test_aliases_round_trip(self, session: Session) -> None:
        producto = Product(
            name="Frijol negro",
            category="granos",
            unit="lb",
            aliases=["frijoles negros", "black beans"],
        )
        session.add(producto)
        session.commit()
        session.expunge(producto)

        recuperado = session.get(Product, producto.id)
        assert recuperado is not None
        assert recuperado.aliases == ["frijoles negros", "black beans"]

    def test_name_duplicado_rechazado(self, session: Session) -> None:
        session.add(Product(name="Pollo", category="cárnicos", unit="lb"))
        session.commit()

        session.add(Product(name="Pollo", category="cárnicos", unit="kg"))
        with pytest.raises(IntegrityError):
            session.commit()
