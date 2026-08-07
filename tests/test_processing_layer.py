"""Processing Layer — `provinces`, `products`, `prices_history`.

Slice de esquema puro (§7.2 del whitepaper): estructura, nullability y
restricciones (CHECK/FK/único). Ningún caso evalúa exactitud de extracción
ni H0 — eso corresponde a la capa de extracción, no a esta.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from saimc.models import (
    ContentType,
    Currency,
    PriceHistory,
    Product,
    Province,
    RawIngestion,
    SourceType,
)


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


def _crear_raw_ingestion(session: Session, *, hash_: str = "hash-precio-1") -> RawIngestion:
    """Crea y persiste un `RawIngestion` mínimo para usar como linaje de un precio."""
    fuente = RawIngestion(
        source_type=SourceType.INBOX_TEXT,
        file_path=f"raw/{hash_}.txt",
        content_hash=hash_,
        content_type=ContentType.TEXT,
        captured_at=datetime.now(UTC),
    )
    session.add(fuente)
    session.flush()
    return fuente


def _crear_producto(session: Session, *, name: str = "Arroz") -> Product:
    producto = Product(name=name, category="granos", unit="lb")
    session.add(producto)
    session.flush()
    return producto


def _crear_provincia(
    session: Session, *, code: str = "hab", name: str = "La Habana"
) -> Province:
    provincia = Province(code=code, name=name)
    session.add(provincia)
    session.flush()
    return provincia


def _crear_precio(producto: Product, fuente: RawIngestion, **overrides: object) -> PriceHistory:
    """Construye un `PriceHistory` válido con overrides puntuales por caso."""
    datos: dict[str, object] = {
        "product_id": producto.id,
        "price": Decimal("10.00"),
        "currency": Currency.CUP,
        "source_id": fuente.id,
    }
    datos.update(overrides)
    return PriceHistory(**datos)


class TestPricesHistoryNullability:
    def test_province_id_none_aceptado(self, session: Session) -> None:
        producto = _crear_producto(session)
        fuente = _crear_raw_ingestion(session)

        precio = _crear_precio(producto, fuente, province_id=None)
        session.add(precio)
        session.commit()

        assert precio.id is not None
        assert precio.province_id is None

    def test_product_id_ausente_rechazado(self, session: Session) -> None:
        fuente = _crear_raw_ingestion(session)

        precio = PriceHistory(
            product_id=None,
            price=Decimal("10.00"),
            currency=Currency.CUP,
            source_id=fuente.id,
        )
        session.add(precio)
        with pytest.raises(IntegrityError):
            session.commit()

    def test_source_id_ausente_rechazado(self, session: Session) -> None:
        producto = _crear_producto(session)

        precio = PriceHistory(
            product_id=producto.id,
            price=Decimal("10.00"),
            currency=Currency.CUP,
            source_id=None,
        )
        session.add(precio)
        with pytest.raises(IntegrityError):
            session.commit()


class TestPricesHistoryForeignKeys:
    def test_borrar_raw_ingestion_referenciado_rechazado(self, session: Session) -> None:
        producto = _crear_producto(session)
        fuente = _crear_raw_ingestion(session)
        session.add(_crear_precio(producto, fuente))
        session.commit()

        session.delete(fuente)
        with pytest.raises(IntegrityError):
            session.commit()

    def test_borrar_producto_referenciado_rechazado(self, session: Session) -> None:
        producto = _crear_producto(session)
        fuente = _crear_raw_ingestion(session)
        session.add(_crear_precio(producto, fuente))
        session.commit()

        session.delete(producto)
        with pytest.raises(IntegrityError):
            session.commit()

    def test_borrar_provincia_referenciada_rechazado(self, session: Session) -> None:
        producto = _crear_producto(session)
        provincia = _crear_provincia(session)
        fuente = _crear_raw_ingestion(session)
        session.add(_crear_precio(producto, fuente, province_id=provincia.id))
        session.commit()

        session.delete(provincia)
        with pytest.raises(IntegrityError):
            session.commit()


class TestPricesHistoryChecks:
    def test_cup_sin_tasa_aceptado(self, session: Session) -> None:
        producto = _crear_producto(session)
        fuente = _crear_raw_ingestion(session)

        session.add(_crear_precio(producto, fuente, currency=Currency.CUP))
        session.commit()

    def test_cup_con_tasa_rechazado(self, session: Session) -> None:
        producto = _crear_producto(session)
        fuente = _crear_raw_ingestion(session)

        session.add(
            _crear_precio(
                producto,
                fuente,
                currency=Currency.CUP,
                exchange_rate=Decimal("1.0000"),
                exchange_rate_date=date(2026, 8, 7),
                exchange_rate_source="oficial",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()

    def test_tasa_sin_fecha_rechazada(self, session: Session) -> None:
        producto = _crear_producto(session)
        fuente = _crear_raw_ingestion(session)

        session.add(
            _crear_precio(
                producto,
                fuente,
                currency=Currency.USD,
                exchange_rate=Decimal("120.0000"),
                exchange_rate_source="oficial",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()

    def test_tasa_sin_fuente_rechazada(self, session: Session) -> None:
        producto = _crear_producto(session)
        fuente = _crear_raw_ingestion(session)

        session.add(
            _crear_precio(
                producto,
                fuente,
                currency=Currency.USD,
                exchange_rate=Decimal("120.0000"),
                exchange_rate_date=date(2026, 8, 7),
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()

    def test_confidence_score_valido_aceptado(self, session: Session) -> None:
        producto = _crear_producto(session)
        fuente = _crear_raw_ingestion(session)

        precio = _crear_precio(producto, fuente, confidence_score=94)
        session.add(precio)
        session.commit()

        assert precio.confidence_score == 94

    @pytest.mark.parametrize("valor", [150, -1])
    def test_confidence_score_fuera_de_rango_rechazado(
        self, session: Session, valor: int
    ) -> None:
        producto = _crear_producto(session)
        fuente = _crear_raw_ingestion(session)

        session.add(_crear_precio(producto, fuente, confidence_score=valor))
        with pytest.raises(IntegrityError):
            session.commit()

    def test_confidence_score_none_aceptado(self, session: Session) -> None:
        producto = _crear_producto(session)
        fuente = _crear_raw_ingestion(session)

        precio = _crear_precio(producto, fuente, confidence_score=None)
        session.add(precio)
        session.commit()

        assert precio.confidence_score is None


class TestPricesHistoryExclusiones:
    def test_sin_columnas_fuera_de_alcance(self) -> None:
        nombres_columnas = {c.name for c in PriceHistory.__table__.columns}

        assert not (nombres_columnas & {"validation_status", "rules"})

        palabras_dato_personal = ("phone", "telefono", "contact", "address", "direccion")
        for nombre in nombres_columnas:
            assert not any(palabra in nombre for palabra in palabras_dato_personal)
