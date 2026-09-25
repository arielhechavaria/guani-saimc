"""Processing Layer — `provinces`, `products`, `prices_history`.

Slice de esquema puro (§7.2 de la especificación): estructura, nullability y
restricciones (CHECK/FK/único). Ningún caso evalúa exactitud de extracción
ni H0 — eso corresponde a la capa de extracción, no a esta.
"""

import importlib.util
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
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

# Solo para las anotaciones de retorno entre comillas de `_crear_formato` y
# `_crear_variante` más abajo: el import real para construirlos vive adentro
# de cada función a propósito (mismo patrón que `_crear_precio`), así que acá
# alcanza con lo que el chequeo estático necesita ver.
if TYPE_CHECKING:
    from saimc.models import ProductFormat, ProductVariant

_MIGRACIONES_DIR = Path(__file__).resolve().parent.parent / "alembic" / "versions"


def _cargar_modulo_migracion(nombre_archivo: str) -> ModuleType:
    """Carga un archivo de migración de Alembic como módulo, sin `alembic upgrade`.

    Los nombres de archivo de las migraciones empiezan con dígitos, por lo que
    no son importables como paquete normal: `importlib.util` con la ruta
    exacta es el mecanismo documentado para este caso (ver design.md, D4).
    """
    ruta = _MIGRACIONES_DIR / nombre_archivo
    spec = importlib.util.spec_from_file_location(nombre_archivo, ruta)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


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
    def test_published_date_none_aceptado(self, session: Session) -> None:
        producto = _crear_producto(session)
        fuente = _crear_raw_ingestion(session)

        precio = _crear_precio(producto, fuente, published_date=None)
        session.add(precio)
        session.commit()

        assert precio.id is not None
        assert precio.published_date is None

    def test_published_date_con_valor_persiste(self, session: Session) -> None:
        producto = _crear_producto(session)
        fuente = _crear_raw_ingestion(session)

        precio = _crear_precio(producto, fuente, published_date=date(2026, 8, 5))
        session.add(precio)
        session.commit()
        session.expunge(precio)

        recuperado = session.get(PriceHistory, precio.id)
        assert recuperado is not None
        assert recuperado.published_date == date(2026, 8, 5)

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

    def test_fila_convertida_satisface_ambos_checks(self, session: Session) -> None:
        """Primera fila de la suite con dato no NULO real en las cuatro
        columnas de conversión — hasta este cambio ningún camino de escritura
        producía un `exchange_rate` no NULL (spec: processing-layer-schema —
        CHECKs de conversión monetaria, escenario nuevo)."""
        producto = _crear_producto(session)
        fuente = _crear_raw_ingestion(session)

        precio = _crear_precio(
            producto,
            fuente,
            currency=Currency.USD,
            price=Decimal("100.00"),
            price_cup=Decimal("67156.00"),
            exchange_rate=Decimal("671.56"),
            exchange_rate_date=date(2026, 8, 13),
            exchange_rate_source="elTOQUE",
        )
        session.add(precio)
        session.commit()

        assert precio.exchange_rate == Decimal("671.56")

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


class TestPricesHistorySaleFormatCheck:
    def test_sale_format_none_aceptado(self, session: Session) -> None:
        producto = _crear_producto(session)
        fuente = _crear_raw_ingestion(session)

        precio = _crear_precio(producto, fuente, sale_format=None)
        session.add(precio)
        session.commit()

        assert precio.sale_format is None

    @pytest.mark.parametrize("valor", ["retail", "wholesale"])
    def test_sale_format_valor_del_dominio_aceptado(
        self, session: Session, valor: str
    ) -> None:
        producto = _crear_producto(session)
        fuente = _crear_raw_ingestion(session)

        precio = _crear_precio(producto, fuente, sale_format=valor)
        session.add(precio)
        session.commit()

        assert precio.sale_format == valor

    def test_sale_format_fuera_del_dominio_rechazado(self, session: Session) -> None:
        producto = _crear_producto(session)
        fuente = _crear_raw_ingestion(session)

        session.add(_crear_precio(producto, fuente, sale_format="mayorista"))
        with pytest.raises(IntegrityError):
            session.commit()

    def test_unit_sin_restriccion_acepta_cualquier_cadena_corta(
        self, session: Session
    ) -> None:
        """`unit` no tiene CHECK: el vocabulario cerrado vive en `units.py`,
        no en la base — así crece sin exigir una migración (design.md, D1)."""
        producto = _crear_producto(session)
        fuente = _crear_raw_ingestion(session)

        precio = _crear_precio(producto, fuente, unit="quintal")
        session.add(precio)
        session.commit()

        assert precio.unit == "quintal"


class TestPricesHistoryMinPurchaseQuantityCheck:
    """`min_purchase_quantity` — la cantidad mínima que el anuncio exige
    comprar, en la unidad de la propia fila. `NULL` significa «no declara
    mínimo», y el CHECK solo impide lo que no describe transacción alguna."""

    def test_min_purchase_quantity_none_aceptado(self, session: Session) -> None:
        producto = _crear_producto(session)
        fuente = _crear_raw_ingestion(session)

        precio = _crear_precio(producto, fuente, min_purchase_quantity=None)
        session.add(precio)
        session.commit()

        assert precio.min_purchase_quantity is None

    def test_min_purchase_quantity_con_decimales_persiste(self, session: Session) -> None:
        producto = _crear_producto(session)
        fuente = _crear_raw_ingestion(session)

        precio = _crear_precio(producto, fuente, min_purchase_quantity=Decimal("2.500"))
        session.add(precio)
        session.commit()
        session.refresh(precio)

        assert precio.min_purchase_quantity == Decimal("2.500")

    @pytest.mark.parametrize("valor", [Decimal("0"), Decimal("-1")])
    def test_min_purchase_quantity_no_positivo_rechazado(
        self, session: Session, valor: Decimal
    ) -> None:
        producto = _crear_producto(session)
        fuente = _crear_raw_ingestion(session)

        session.add(_crear_precio(producto, fuente, min_purchase_quantity=valor))
        with pytest.raises(IntegrityError):
            session.commit()


class TestPricesHistoryPriceBasisCheck:
    """`price_basis` declara qué representa `price`: `unit` (ya es precio de
    una unidad) o `total` (precio del lote de `quantity` unidades). `NULL` es
    «no resuelto» — legado o pendiente de curar —, nunca se lee como `total`
    (esa lectura implícita es exactamente el bug de la fila «Arroz guyanés a
    340lb», que quedó a 1 CUP/lb en vez de ~340). El CHECK es de vocabulario
    cerrado, mismo patrón que `ck_prices_history_sale_format`."""

    def test_price_basis_none_aceptado(self, session: Session) -> None:
        producto = _crear_producto(session)
        fuente = _crear_raw_ingestion(session)

        precio = _crear_precio(producto, fuente, price_basis=None)
        session.add(precio)
        session.commit()

        assert precio.price_basis is None

    @pytest.mark.parametrize("valor", ["unit", "total"])
    def test_price_basis_valor_del_dominio_aceptado(
        self, session: Session, valor: str
    ) -> None:
        producto = _crear_producto(session)
        fuente = _crear_raw_ingestion(session)

        precio = _crear_precio(producto, fuente, price_basis=valor)
        session.add(precio)
        session.commit()

        assert precio.price_basis == valor

    def test_price_basis_fuera_del_dominio_rechazado(self, session: Session) -> None:
        producto = _crear_producto(session)
        fuente = _crear_raw_ingestion(session)

        session.add(_crear_precio(producto, fuente, price_basis="lote"))
        with pytest.raises(IntegrityError):
            session.commit()


class TestPricesHistoryExclusiones:
    def test_sin_columnas_fuera_de_alcance(self) -> None:
        nombres_columnas = {c.name for c in PriceHistory.__table__.columns}

        assert not (nombres_columnas & {"validation_status", "rules"})

        palabras_dato_personal = ("phone", "telefono", "contact", "address", "direccion")
        for nombre in nombres_columnas:
            assert not any(palabra in nombre for palabra in palabras_dato_personal)


class TestPublishedDateMigration:
    """`0005_prices_history_published_date.py` — `upgrade()`/`downgrade()`
    corridos contra un engine SQLite real vía `Operations.context()`, no solo
    cargados con `importlib` (a diferencia de 0003/0004, acá el módulo no
    expone una función de datos pura: son dos `op.add_column`/`op.drop_column`
    de una línea, así que probar el módulo entero es correr esos dos)."""

    def test_upgrade_agrega_la_columna_y_downgrade_la_elimina(self) -> None:
        modulo = _cargar_modulo_migracion("0005_prices_history_published_date.py")

        engine = create_engine("sqlite://")
        metadata = sa.MetaData()
        sa.Table(
            "prices_history",
            metadata,
            sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        )
        metadata.create_all(engine)

        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                modulo.upgrade()
            connection.commit()
            columnas = {c["name"] for c in inspect(connection).get_columns("prices_history")}
            assert "published_date" in columnas

        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                modulo.downgrade()
            connection.commit()
            columnas = {c["name"] for c in inspect(connection).get_columns("prices_history")}
            assert "published_date" not in columnas
            assert "id" in columnas  # el downgrade no se llevó ninguna otra columna

        engine.dispose()


class TestUnitSaleFormatPackagingMigration:
    """`0006_prices_history_unit_sale_format_packaging.py` — `upgrade()`/
    `downgrade()` corridos contra un engine SQLite real vía
    `Operations.context()`, igual que `TestPublishedDateMigration` (design.md,
    D2): usa `batch_alter_table` porque SQLite no soporta
    `ALTER TABLE ... ADD CONSTRAINT`."""

    def test_upgrade_agrega_las_tres_columnas_y_downgrade_las_elimina(self) -> None:
        modulo = _cargar_modulo_migracion(
            "0006_prices_history_unit_sale_format_packaging.py"
        )

        engine = create_engine("sqlite://")
        metadata = sa.MetaData()
        sa.Table(
            "prices_history",
            metadata,
            sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        )
        metadata.create_all(engine)

        # Una fila anterior a la migración: es el guardarraíl de «sin backfill».
        # Sin ella el test no distingue una migración aditiva de una que
        # rellena, y rellenar `unit = product.unit` sobre las filas viejas
        # fabricaría dato para los productos donde la mezcla nunca se controló.
        with engine.connect() as connection:
            connection.execute(
                sa.insert(metadata.tables["prices_history"]).values(id=uuid.uuid4())
            )
            connection.commit()

        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                modulo.upgrade()
            connection.commit()
            columnas = {c["name"] for c in inspect(connection).get_columns("prices_history")}
            assert {"unit", "sale_format", "packaging"} <= columnas

            valores = connection.execute(
                sa.text("select unit, sale_format, packaging from prices_history")
            ).one()
            assert valores == (None, None, None)

        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                modulo.downgrade()
            connection.commit()
            columnas = {c["name"] for c in inspect(connection).get_columns("prices_history")}
            assert not ({"unit", "sale_format", "packaging"} & columnas)
            assert "id" in columnas  # el downgrade no se llevó ninguna otra columna


class TestMinPurchaseQuantityMigration:
    """`0008_prices_history_min_purchase_quantity.py` — mismo patrón que
    `TestUnitSaleFormatPackagingMigration`: `batch_alter_table` porque la
    migración crea un CHECK y SQLite no soporta `ADD CONSTRAINT`."""

    def test_upgrade_agrega_la_columna_y_downgrade_la_elimina(self) -> None:
        modulo = _cargar_modulo_migracion("0008_prices_history_min_purchase_quantity.py")

        engine = create_engine("sqlite://")
        metadata = sa.MetaData()
        sa.Table(
            "prices_history",
            metadata,
            sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        )
        metadata.create_all(engine)

        # Guardarraíl de «sin backfill», igual que en 0006: una fila anterior
        # a la migración tiene que quedar en NULL, no en un mínimo fabricado.
        with engine.connect() as connection:
            connection.execute(
                sa.insert(metadata.tables["prices_history"]).values(id=uuid.uuid4())
            )
            connection.commit()

        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                modulo.upgrade()
            connection.commit()
            columnas = {c["name"] for c in inspect(connection).get_columns("prices_history")}
            assert "min_purchase_quantity" in columnas

            valor = connection.execute(
                sa.text("select min_purchase_quantity from prices_history")
            ).scalar_one()
            assert valor is None

        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                modulo.downgrade()
            connection.commit()
            columnas = {c["name"] for c in inspect(connection).get_columns("prices_history")}
            assert "min_purchase_quantity" not in columnas
            assert "id" in columnas  # el downgrade no se llevó ninguna otra columna

        engine.dispose()

        engine.dispose()


class TestPriceBasisMigration:
    """`0016_prices_history_price_basis.py` — mismo patrón que
    `TestMinPurchaseQuantityMigration`: `batch_alter_table` porque la
    migración crea un CHECK y SQLite no soporta `ADD CONSTRAINT`.

    El número real es `0016`, no `0009` (design.md, D1): `0009` ya está
    tomado por `products_is_pilot` y el head real de alembic es `0015`.
    """

    def test_upgrade_agrega_la_columna_y_downgrade_la_elimina(self) -> None:
        modulo = _cargar_modulo_migracion("0016_prices_history_price_basis.py")

        engine = create_engine("sqlite://")
        metadata = sa.MetaData()
        sa.Table(
            "prices_history",
            metadata,
            sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        )
        metadata.create_all(engine)

        # Guardarraíl de «sin backfill», igual que en 0008: una fila anterior
        # a la migración tiene que quedar en NULL, no en un basis fabricado
        # (leer NULL como 'total' es exactamente el bug que este cambio corrige).
        with engine.connect() as connection:
            connection.execute(
                sa.insert(metadata.tables["prices_history"]).values(id=uuid.uuid4())
            )
            connection.commit()

        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                modulo.upgrade()
            connection.commit()
            columnas = {c["name"] for c in inspect(connection).get_columns("prices_history")}
            assert "price_basis" in columnas

            valor = connection.execute(
                sa.text("select price_basis from prices_history")
            ).scalar_one()
            assert valor is None

        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                modulo.downgrade()
            connection.commit()
            columnas = {c["name"] for c in inspect(connection).get_columns("prices_history")}
            assert "price_basis" not in columnas
            assert "id" in columnas  # el downgrade no se llevó ninguna otra columna

        engine.dispose()


class TestDiscardReasonMigration:
    """`0017_raw_ingestions_discard_reason.py` — mismo patrón que `0016`:
    `batch_alter_table` porque crea un CHECK y SQLite no soporta `ADD
    CONSTRAINT`. Aditiva nullable, sin backfill: una fila descartada anterior
    a la migración queda en NULL (motivo no registrado)."""

    def test_upgrade_agrega_la_columna_y_downgrade_la_elimina(self) -> None:
        modulo = _cargar_modulo_migracion("0017_raw_ingestions_discard_reason.py")

        engine = create_engine("sqlite://")
        metadata = sa.MetaData()
        sa.Table(
            "raw_ingestions",
            metadata,
            sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        )
        metadata.create_all(engine)

        with engine.connect() as connection:
            connection.execute(
                sa.insert(metadata.tables["raw_ingestions"]).values(id=uuid.uuid4())
            )
            connection.commit()

        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                modulo.upgrade()
            connection.commit()
            columnas = {c["name"] for c in inspect(connection).get_columns("raw_ingestions")}
            assert "discard_reason" in columnas

            valor = connection.execute(
                sa.text("select discard_reason from raw_ingestions")
            ).scalar_one()
            assert valor is None

        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                modulo.downgrade()
            connection.commit()
            columnas = {c["name"] for c in inspect(connection).get_columns("raw_ingestions")}
            assert "discard_reason" not in columnas
            assert "id" in columnas

        engine.dispose()


class TestExchangeRateCacheMigration:
    """`0007_exchange_rate_cache.py` — `upgrade()`/`downgrade()` corridos
    contra un engine SQLite real vía `Operations.context()`, igual que
    `TestPublishedDateMigration` (design.md, D2). Tabla nueva, sin `ALTER`:
    no hace falta `batch_alter_table`."""

    def test_upgrade_crea_la_tabla_y_downgrade_la_elimina(self) -> None:
        modulo = _cargar_modulo_migracion("0007_exchange_rate_cache.py")

        engine = create_engine("sqlite://")

        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                modulo.upgrade()
            connection.commit()
            tablas = inspect(connection).get_table_names()
            assert "exchange_rate_cache" in tablas
            columnas = {
                c["name"] for c in inspect(connection).get_columns("exchange_rate_cache")
            }
            assert {"currency", "rate_date", "rate", "source", "fetched_at"} <= columnas

        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                modulo.downgrade()
            connection.commit()
            tablas = inspect(connection).get_table_names()
            assert "exchange_rate_cache" not in tablas

        engine.dispose()


class TestExchangeRateCacheModel:
    def test_par_moneda_fecha_duplicado_rechazado(self, session: Session) -> None:
        from saimc.models import ExchangeRateCache

        session.add(
            ExchangeRateCache(
                currency="USD",
                rate_date=date(2026, 8, 13),
                rate=Decimal("671.5600"),
                source="elTOQUE",
            )
        )
        session.commit()

        session.add(
            ExchangeRateCache(
                currency="USD",
                rate_date=date(2026, 8, 13),
                rate=Decimal("999.9900"),
                source="elTOQUE",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()

    def test_misma_moneda_distinta_fecha_aceptado(self, session: Session) -> None:
        """No hay FK a ninguna otra tabla — tabla de primera clase (D2)."""
        from saimc.models import ExchangeRateCache

        session.add(
            ExchangeRateCache(
                currency="USD",
                rate_date=date(2026, 8, 13),
                rate=Decimal("671.5600"),
                source="elTOQUE",
            )
        )
        session.add(
            ExchangeRateCache(
                currency="USD",
                rate_date=date(2026, 8, 12),
                rate=Decimal("670.0000"),
                source="elTOQUE",
            )
        )
        session.commit()


class TestSeedProvinces:
    """`0003_seed_provinces.py` cargada por `importlib`, sin base real (D4)."""

    def test_province_rows_devuelve_16_filas_deterministas(self) -> None:
        modulo = _cargar_modulo_migracion("0003_seed_provinces.py")

        filas = modulo.province_rows()

        assert len(filas) == 16

        codigos = [fila["code"] for fila in filas]
        assert len(codigos) == len(set(codigos))

        for fila in filas:
            assert fila["id"] == uuid.uuid5(modulo._PROVINCE_NS, fila["code"])
            assert fila["is_published"] is False


def _crear_formato(
    producto: Product,
    *,
    packaging: str = "caja",
    quantity: str = "40",
    unit: str = "lb",
) -> "ProductFormat":
    """Constructor puro, mismo patrón que `_crear_precio`: no toca la sesión,
    así el `IntegrityError` de un caso negativo salta en el `commit()` del test
    y no acá dentro."""
    from saimc.models import ProductFormat

    return ProductFormat(
        product_id=producto.id,
        packaging=packaging,
        quantity=Decimal(quantity),
        unit=unit,
    )


class TestProductFormatIdentity:
    """`uq_product_formats_identity` sobre (product_id, packaging, quantity, unit)
    — design.md D1. Es lo que hace segura la creación al vuelo: no hay texto que
    se pueda escribir distinto, así que el segundo intento encuentra la fila."""

    def test_triple_repetido_bajo_el_mismo_producto_rechazado(
        self, session: Session
    ) -> None:
        producto = _crear_producto(session)
        session.add(_crear_formato(producto, packaging="caja", quantity="40", unit="lb"))
        session.commit()

        session.add(_crear_formato(producto, packaging="caja", quantity="40", unit="lb"))
        with pytest.raises(IntegrityError):
            session.commit()

    def test_el_mismo_triple_bajo_otro_producto_se_acepta(
        self, session: Session
    ) -> None:
        pollo = _crear_producto(session, name="Pollo")
        arroz = _crear_producto(session, name="Arroz")
        session.add(_crear_formato(pollo, packaging="caja", quantity="40", unit="lb"))
        session.add(_crear_formato(arroz, packaging="caja", quantity="40", unit="lb"))
        session.commit()  # no levanta: la unicidad es por producto

    def test_misma_terna_distinta_cantidad_se_acepta(self, session: Session) -> None:
        producto = _crear_producto(session)
        session.add(_crear_formato(producto, packaging="caja", quantity="40", unit="lb"))
        session.add(_crear_formato(producto, packaging="caja", quantity="33", unit="lb"))
        session.commit()  # el tamaño es identidad (design.md D1): son dos formatos


class TestProductFormatPackagingCheck:
    """`ck_product_formats_packaging` — vocabulario cerrado en la base, a
    diferencia de `prices_history.unit` que a propósito no lo tiene (design.md
    D2). `contenedor` y `paca` no son envases (D3)."""

    @pytest.mark.parametrize("envase", ["paquete", "saco", "caja", "botella", "pomo", "a granel"])
    def test_los_seis_envases_del_vocabulario_se_aceptan(
        self, session: Session, envase: str
    ) -> None:
        producto = _crear_producto(session)
        session.add(_crear_formato(producto, packaging=envase, quantity="1", unit="lb"))
        session.commit()

    def test_contenedor_rechazado(self, session: Session) -> None:
        producto = _crear_producto(session)
        session.add(_crear_formato(producto, packaging="contenedor", quantity="1", unit="lb"))
        with pytest.raises(IntegrityError):
            session.commit()

    def test_paca_rechazada(self, session: Session) -> None:
        producto = _crear_producto(session)
        session.add(_crear_formato(producto, packaging="paca", quantity="1", unit="kg"))
        with pytest.raises(IntegrityError):
            session.commit()


class TestProductFormatForeignKeyRestrict:
    """FK `RESTRICT` — un formato referenciado por una fila de precio no se
    puede borrar (design.md D7: los formatos no se retiran, solo se ordena la
    lista; la única excepción es un formato con cero observaciones)."""

    def test_borrar_formato_referenciado_por_un_precio_rechazado(
        self, session: Session
    ) -> None:
        producto = _crear_producto(session)
        fuente = _crear_raw_ingestion(session)
        formato = _crear_formato(producto)
        session.add(formato)
        session.flush()
        session.add(_crear_precio(producto, fuente, format_id=formato.id))
        session.commit()

        session.delete(formato)
        with pytest.raises(IntegrityError):
            session.commit()

    def test_borrar_formato_sin_precios_se_permite(self, session: Session) -> None:
        producto = _crear_producto(session)
        formato = _crear_formato(producto)
        session.add(formato)
        session.commit()

        session.delete(formato)
        session.commit()  # sin filas que lo referencien, se puede


class TestPricesHistoryFormatId:
    def test_format_id_none_aceptado(self, session: Session) -> None:
        """NULL = «formato no establecido», agrega en su propia celda igual que
        `unquantified` y `unresolved_basis` (spec: processing-layer-schema)."""
        producto = _crear_producto(session)
        fuente = _crear_raw_ingestion(session)
        precio = _crear_precio(producto, fuente, format_id=None)
        session.add(precio)
        session.commit()
        assert precio.format_id is None

    def test_packaging_legacy_guarda_el_texto_original(self, session: Session) -> None:
        """Columna de respaldo para que `0019` pueda dropear `packaging` y el
        downgrade lo recupere textual (design.md D6)."""
        producto = _crear_producto(session)
        fuente = _crear_raw_ingestion(session)
        precio = _crear_precio(
            producto, fuente, packaging_legacy="Arroz importado a 320 la libra"
        )
        session.add(precio)
        session.commit()
        assert precio.packaging_legacy == "Arroz importado a 320 la libra"


def _crear_variante(
    producto: Product,
    *,
    kind: str = "girasol",
    brand: str = "",
    origin: str = "",
) -> "ProductVariant":
    """Constructor puro, mismo patrón que `_crear_formato`. Los valores ya
    llegan canónicos: el plegado por acento/mayúscula es responsabilidad de
    `variant_key` (`test_variants.py`), no de esta capa — acá las columnas
    `_key` son una copia literal de las de display."""
    from saimc.models import ProductVariant

    return ProductVariant(
        product_id=producto.id,
        kind=kind,
        brand=brand,
        origin=origin,
        kind_key=kind,
        brand_key=brand,
        origin_key=origin,
    )


class TestProductVariantIdentity:
    """`uq_product_variants_identity` sobre las columnas plegadas
    (product_id, kind_key, brand_key, origin_key) — design.md D1/D2. Las
    columnas son `NOT NULL DEFAULT ''`: con columnas nullable, NULL no
    deduplica contra NULL y la unicidad no cumpliría nada."""

    def test_terna_repetida_bajo_el_mismo_producto_rechazada(
        self, session: Session
    ) -> None:
        producto = _crear_producto(session)
        session.add(
            _crear_variante(producto, kind="girasol", brand="vitalin", origin="importado")
        )
        session.commit()

        session.add(
            _crear_variante(producto, kind="girasol", brand="vitalin", origin="importado")
        )
        with pytest.raises(IntegrityError):
            session.commit()

    def test_la_misma_terna_bajo_otro_producto_se_acepta(self, session: Session) -> None:
        aceite = _crear_producto(session, name="Aceite")
        arroz = _crear_producto(session, name="Arroz")
        session.add(
            _crear_variante(aceite, kind="girasol", brand="vitalin", origin="importado")
        )
        session.add(
            _crear_variante(arroz, kind="girasol", brand="vitalin", origin="importado")
        )
        session.commit()  # no levanta: la unicidad es por producto

    def test_identidad_parcial_repetida_rechazada(self, session: Session) -> None:
        """Solo `kind` declarado, brand/origin vacíos en las dos filas: debe
        deduplicar igual que una terna completa (spec: product-variants —
        «una variante declarada con un solo eje también deduplica»)."""
        producto = _crear_producto(session)
        session.add(_crear_variante(producto, kind="girasol", brand="", origin=""))
        session.commit()

        session.add(_crear_variante(producto, kind="girasol", brand="", origin=""))
        with pytest.raises(IntegrityError):
            session.commit()


class TestProductVariantNotEmptyCheck:
    """`ck_product_variants_not_empty` — estructural, no de vocabulario
    (design.md D1): sin ella la fila con los tres ejes vacíos sería una
    segunda forma de decir «sin variante declarada», que ya cubre
    `prices_history.variant_id = NULL`."""

    def test_las_tres_columnas_vacias_rechazadas(self, session: Session) -> None:
        producto = _crear_producto(session)
        session.add(_crear_variante(producto, kind="", brand="", origin=""))
        with pytest.raises(IntegrityError):
            session.commit()


class TestProductVariantForeignKeyRestrict:
    """FK `RESTRICT` — una variante referenciada por una fila de precio nunca
    se puede borrar (spec: product-variants — «una variante con historial no
    se puede borrar»)."""

    def test_borrar_variante_referenciada_por_un_precio_rechazado(
        self, session: Session
    ) -> None:
        producto = _crear_producto(session)
        fuente = _crear_raw_ingestion(session)
        variante = _crear_variante(producto)
        session.add(variante)
        session.flush()
        session.add(_crear_precio(producto, fuente, variant_id=variante.id))
        session.commit()

        session.delete(variante)
        with pytest.raises(IntegrityError):
            session.commit()

    def test_borrar_variante_sin_precios_se_permite(self, session: Session) -> None:
        producto = _crear_producto(session)
        variante = _crear_variante(producto)
        session.add(variante)
        session.commit()

        session.delete(variante)
        session.commit()  # sin filas que la referencien, se puede


class TestPricesHistoryVariantId:
    def test_variant_id_none_aceptado(self, session: Session) -> None:
        """NULL = «variante no declarada», agrega en su propia celda igual
        que `format_id = NULL` (spec: processing-layer-schema)."""
        producto = _crear_producto(session)
        fuente = _crear_raw_ingestion(session)
        precio = _crear_precio(producto, fuente, variant_id=None)
        session.add(precio)
        session.commit()
        assert precio.variant_id is None

    def test_product_variant_no_admite_is_pilot(self) -> None:
        """Una variante es una sub-unidad, nunca una producto independiente:
        no existe columna `is_pilot` en `ProductVariant` — `is_pilot` vive
        solo en `Product` (spec: product-variants)."""
        from saimc.models import ProductVariant

        with pytest.raises(TypeError):
            ProductVariant(kind="girasol", is_pilot=True)  # type: ignore[call-arg]


class TestProductFormatsMigration:
    """`0018_product_formats.py` — **puramente aditiva** (design.md D4a): crea
    `product_formats`, agrega `format_id` y `packaging_legacy` a
    `prices_history`, y copia `packaging` → `packaging_legacy`. No mapea ni
    dropea nada — eso es `0019`, que aterriza con el cambio de agregación.
    Patrón de `TestUnitSaleFormatPackagingMigration`: SQLite real vía
    `Operations.context()`, `batch_alter_table` porque hay un CHECK."""

    def test_upgrade_es_aditiva_y_copia_packaging_downgrade_revierte(self) -> None:
        modulo = _cargar_modulo_migracion("0018_product_formats.py")

        engine = create_engine("sqlite://")
        metadata = sa.MetaData()
        # `product_formats` lleva una FK a `products`; en producción existe.
        sa.Table(
            "products",
            metadata,
            sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        )
        sa.Table(
            "prices_history",
            metadata,
            sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
            sa.Column("packaging", sa.String(length=64)),
        )
        metadata.create_all(engine)

        # Fila anterior a la migración con envase en texto libre: el
        # guardarraíl es que `packaging` NO se toca y `packaging_legacy` queda
        # con la copia textual — es lo que hace reversible al `0019` (D6).
        fila_id = uuid.uuid4()
        with engine.connect() as connection:
            connection.execute(
                sa.insert(metadata.tables["prices_history"]).values(
                    id=fila_id, packaging="Arroz importado a 320 la libra"
                )
            )
            connection.commit()

        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                modulo.upgrade()
            connection.commit()

            tablas = inspect(connection).get_table_names()
            assert "product_formats" in tablas
            cols_fmt = {c["name"] for c in inspect(connection).get_columns("product_formats")}
            assert {"id", "product_id", "packaging", "quantity", "unit", "created_at"} <= cols_fmt

            cols_ph = {c["name"] for c in inspect(connection).get_columns("prices_history")}
            assert {"format_id", "packaging_legacy"} <= cols_ph
            assert "packaging" in cols_ph  # aditiva: `packaging` sigue

            fila = connection.execute(
                sa.text(
                    "select packaging, packaging_legacy, format_id from prices_history"
                )
            ).one()
            assert fila == ("Arroz importado a 320 la libra", "Arroz importado a 320 la libra", None)

        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                modulo.downgrade()
            connection.commit()

            assert "product_formats" not in inspect(connection).get_table_names()
            cols_ph = {c["name"] for c in inspect(connection).get_columns("prices_history")}
            assert not ({"format_id", "packaging_legacy"} & cols_ph)
            assert {"id", "packaging"} <= cols_ph
            valor = connection.execute(
                sa.text("select packaging from prices_history")
            ).scalar_one()
            assert valor == "Arroz importado a 320 la libra"

        engine.dispose()


class TestProductVariantsMigration:
    """`0020_product_variants.py` — puramente aditiva (design.md, Migration /
    Rollout): crea `product_variants` y agrega `prices_history.variant_id`.
    Sin backfill: a diferencia de `packaging`, la marca y el tipo nunca
    estuvieron en ninguna columna, así que no hay dato de origen que copiar."""

    def test_upgrade_crea_tabla_y_columna_downgrade_revierte(self) -> None:
        modulo = _cargar_modulo_migracion("0020_product_variants.py")

        engine = create_engine("sqlite://")
        metadata = sa.MetaData()
        sa.Table(
            "products",
            metadata,
            sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        )
        sa.Table(
            "prices_history",
            metadata,
            sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        )
        metadata.create_all(engine)

        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                modulo.upgrade()
            connection.commit()

            tablas = inspect(connection).get_table_names()
            assert "product_variants" in tablas
            cols_var = {
                c["name"] for c in inspect(connection).get_columns("product_variants")
            }
            assert {
                "id",
                "product_id",
                "kind",
                "brand",
                "origin",
                "kind_key",
                "brand_key",
                "origin_key",
                "created_at",
            } <= cols_var

            cols_ph = {c["name"] for c in inspect(connection).get_columns("prices_history")}
            assert "variant_id" in cols_ph

        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                modulo.downgrade()
            connection.commit()

            assert "product_variants" not in inspect(connection).get_table_names()
            cols_ph = {c["name"] for c in inspect(connection).get_columns("prices_history")}
            assert "variant_id" not in cols_ph

        engine.dispose()


class TestMapPackagingToFormatsMigration:
    """`0019_map_packaging_to_formats.py` — mapea las variantes de `packaging`
    a `product_formats` y recién entonces dropea la columna (design.md D4a).
    Solo toca filas con `format_id IS NULL AND packaging IS NOT NULL`: las
    curadas durante la cadena ya traen formato.
    """

    @staticmethod
    def _schema(engine: object) -> sa.MetaData:
        metadata = sa.MetaData()
        sa.Table(
            "products",
            metadata,
            sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        )
        sa.Table(
            "product_formats",
            metadata,
            sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
            sa.Column("product_id", sa.Uuid(as_uuid=True), nullable=False),
            sa.Column("packaging", sa.String(length=32), nullable=False),
            sa.Column("quantity", sa.Numeric(precision=12, scale=3), nullable=False),
            sa.Column("unit", sa.String(length=10), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        sa.Table(
            "prices_history",
            metadata,
            sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
            sa.Column("product_id", sa.Uuid(as_uuid=True), nullable=False),
            sa.Column("price", sa.Numeric(precision=12, scale=2), nullable=False),
            sa.Column("quantity", sa.Numeric(precision=12, scale=3)),
            sa.Column("unit", sa.String(length=10)),
            sa.Column("price_basis", sa.String(length=10)),
            sa.Column("packaging", sa.String(length=64)),
            sa.Column("packaging_legacy", sa.String(length=64)),
            sa.Column("format_id", sa.Uuid(as_uuid=True)),
        )
        metadata.create_all(engine)
        return metadata

    def test_mapea_el_corpus_y_rechaza_lo_que_no_es_determinista(self) -> None:
        modulo = _cargar_modulo_migracion("0019_map_packaging_to_formats.py")

        engine = create_engine("sqlite://")
        metadata = self._schema(engine)
        producto = uuid.uuid4()

        # Una fila por línea de la tabla de mapeo del diseño, más los tres
        # rechazos. El último elemento es el envase esperado, o None.
        casos: list[tuple[str, str, str, str, str | None, str | None]] = [
            ("Paquete", "700", "10", "lb", "total", "paquete"),
            ("Saco", "17500", "25", "kg", "total", "saco"),
            ("A granel", "340", "1", "lb", None, "a granel"),
            ("Caja", "24800", "40", "lb", "total", "caja"),
            ("caja", "27250", "50", "lb", "total", "caja"),
            ("Saco de arroz De 110 lb", "31000", "110", "lb", "total", "saco"),
            ("Paquete de arroz 1kg", "770", "1", "kg", "total", "paquete"),
            ("Botella", "4000", "1", "l", "total", "botella"),
            ("botella", "2100", "1", "l", "total", "botella"),
            # `paca` no es envase (D3): trae 10 o 30 paquetes segun el anuncio
            # y el precio guardado ya es el del paquete.
            ("PACA que contiene 10 paquetes", "770", "1", "kg", "total", "paquete"),
            ("Paca que contiene 30 paquetes", "0.93", "1", "kg", "total", "paquete"),
            # `contenedor` sale del vocabulario; cada fila lleva debajo el
            # formato que el contenedor transporta (evidencia en proposal.md).
            ("Contenedor", "7810", "10", "kg", None, "caja"),
            ("Contenedor", "31.80", "40", "lb", "total", "caja"),
            ("Contenedor", "28.60", "25", "kg", "total", "saco"),
            ("contenedor", "4.45", "900", "ml", "total", "botella"),
            # Rechazo 1: un pedazo del anuncio guardado como envase.
            ("Arroz importado a 320 la libra", "320", "1", "lb", None, None),
            # Rechazos 2 y 3 (D5): `price == quantity` con basis 'unit' es el
            # artefacto de «Arroz guyanes a $340lb» — no hay lote de 340 lb.
            ("A granel", "340", "340", "lb", "unit", None),
            ("A granel", "340", "340", "lb", "unit", None),
        ]

        ids: list[uuid.UUID] = []
        with engine.connect() as connection:
            connection.execute(sa.insert(metadata.tables["products"]).values(id=producto))
            for packaging, price, quantity, unit, basis, _ in casos:
                fila_id = uuid.uuid4()
                ids.append(fila_id)
                connection.execute(
                    sa.insert(metadata.tables["prices_history"]).values(
                        id=fila_id,
                        product_id=producto,
                        price=Decimal(price),
                        quantity=Decimal(quantity),
                        unit=unit,
                        price_basis=basis,
                        packaging=packaging,
                        packaging_legacy=packaging,
                    )
                )
            connection.commit()

        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                modulo.upgrade()
            connection.commit()

            columnas = {c["name"] for c in inspect(connection).get_columns("prices_history")}
            assert "packaging" not in columnas  # dropeada al final
            assert "packaging_legacy" in columnas  # el respaldo sobrevive

            ph = metadata.tables["prices_history"]
            pf = metadata.tables["product_formats"]
            for fila_id, caso in zip(ids, casos, strict=True):
                _, _, quantity, unit, _, esperado = caso
                got = connection.execute(
                    sa.select(pf.c.packaging, pf.c.quantity, pf.c.unit)
                    .select_from(ph.outerjoin(pf, pf.c.id == ph.c.format_id))
                    .where(ph.c.id == fila_id)
                ).one()
                if esperado is None:
                    assert got[0] is None, f"{caso[0]!r} debia quedar sin formato"
                else:
                    assert got[0] == esperado, f"{caso[0]!r} -> {got[0]!r}"
                    assert Decimal(str(got[1])) == Decimal(quantity)
                    assert got[2] == unit

        engine.dispose()

    def test_no_duplica_formatos_entre_filas_del_mismo_triple(self) -> None:
        modulo = _cargar_modulo_migracion("0019_map_packaging_to_formats.py")

        engine = create_engine("sqlite://")
        metadata = self._schema(engine)
        producto = uuid.uuid4()

        with engine.connect() as connection:
            connection.execute(sa.insert(metadata.tables["products"]).values(id=producto))
            for _ in range(6):
                connection.execute(
                    sa.insert(metadata.tables["prices_history"]).values(
                        id=uuid.uuid4(),
                        product_id=producto,
                        price=Decimal("7810"),
                        quantity=Decimal("10"),
                        unit="kg",
                        packaging="Contenedor",
                        packaging_legacy="Contenedor",
                    )
                )
            connection.commit()

        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                modulo.upgrade()
            connection.commit()

            total = connection.execute(
                sa.text("select count(*) from product_formats")
            ).scalar_one()
            assert total == 1  # las 6 filas de Cotorro comparten (caja, 10, kg)

        engine.dispose()

    def test_no_toca_las_filas_que_ya_traen_formato(self) -> None:
        """Las curadas entre el commit 4 y este ya tienen `format_id`: el
        backfill las filtra y no las re-mapea (design.md D4a)."""
        modulo = _cargar_modulo_migracion("0019_map_packaging_to_formats.py")

        engine = create_engine("sqlite://")
        metadata = self._schema(engine)
        producto = uuid.uuid4()
        formato_previo = uuid.uuid4()
        fila = uuid.uuid4()

        with engine.connect() as connection:
            connection.execute(sa.insert(metadata.tables["products"]).values(id=producto))
            connection.execute(
                sa.insert(metadata.tables["product_formats"]).values(
                    id=formato_previo,
                    product_id=producto,
                    packaging="caja",
                    quantity=Decimal("40"),
                    unit="lb",
                    created_at=datetime(2026, 9, 4, tzinfo=UTC),
                )
            )
            connection.execute(
                sa.insert(metadata.tables["prices_history"]).values(
                    id=fila,
                    product_id=producto,
                    price=Decimal("100"),
                    quantity=Decimal("25"),
                    unit="kg",
                    packaging="Saco",  # contradice al formato ya asignado
                    packaging_legacy="Saco",
                    format_id=formato_previo,
                )
            )
            connection.commit()

        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                modulo.upgrade()
            connection.commit()

            total = connection.execute(
                sa.text("select count(*) from product_formats")
            ).scalar_one()
            assert total == 1  # no se creo `(saco, 25, kg)`

        engine.dispose()

    def test_downgrade_devuelve_el_texto_original_verbatim(self) -> None:
        """El mapeo es muchos-a-uno y con perdida: `Arroz importado a 320 la
        libra` no se reconstruye desde un formato. Por eso `0018` copio la
        columna a `packaging_legacy` (design.md D6)."""
        modulo = _cargar_modulo_migracion("0019_map_packaging_to_formats.py")

        engine = create_engine("sqlite://")
        metadata = self._schema(engine)
        producto = uuid.uuid4()
        fila = uuid.uuid4()
        texto = "Arroz importado a 320 la libra"

        with engine.connect() as connection:
            connection.execute(sa.insert(metadata.tables["products"]).values(id=producto))
            connection.execute(
                sa.insert(metadata.tables["prices_history"]).values(
                    id=fila,
                    product_id=producto,
                    price=Decimal("320"),
                    quantity=Decimal("1"),
                    unit="lb",
                    packaging=texto,
                    packaging_legacy=texto,
                )
            )
            connection.commit()

        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                modulo.upgrade()
            connection.commit()

        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                modulo.downgrade()
            connection.commit()

            columnas = {c["name"] for c in inspect(connection).get_columns("prices_history")}
            assert "packaging" in columnas
            ph = metadata.tables["prices_history"]
            recuperado = connection.execute(
                sa.select(ph.c.packaging).where(ph.c.id == fila)
            ).scalar_one()
            assert recuperado == texto

        engine.dispose()


class TestModeloYMigracionesNoDivergen:
    """Guardarraíl contra el error que produjo el 500 del 2026-09-04.

    `tests/conftest.py` arma el esquema con `Base.metadata.create_all`, o sea
    con lo que el modelo declara — nunca con lo que las migraciones dejaron.
    Por eso la suite entera pasó en verde con `PriceHistory.packaging` todavía
    declarada después de que `0019` dropeara la columna: en SQLite existía, en
    PostgreSQL no, y cualquier `SELECT` de la entidad completa reventaba.

    Este caso corre **la cadena real** contra un engine SQLite y compara el
    conjunto de columnas contra el del modelo. No valida tipos ni
    restricciones: valida que nadie agregue o quite una columna en un lado sin
    el otro, que es la clase de error que la suite no podía ver.
    """

    def test_las_columnas_de_prices_history_coinciden(self) -> None:
        from alembic.script import ScriptDirectory

        from saimc.models import PriceHistory

        raiz = Path(__file__).resolve().parent.parent
        script = ScriptDirectory(str(raiz / "alembic"))
        # Cadena completa en orden, de la base a head.
        revisiones = list(reversed(list(script.walk_revisions())))

        engine = create_engine("sqlite://")
        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                for rev in revisiones:
                    _cargar_modulo_migracion(
                        rev.path.rsplit("/", 1)[-1]
                    ).upgrade()
            connection.commit()
            migradas = {c["name"] for c in inspect(connection).get_columns("prices_history")}

        declaradas = {c.name for c in PriceHistory.__table__.columns}
        assert migradas == declaradas, (
            f"solo en las migraciones: {sorted(migradas - declaradas)} · "
            f"solo en el modelo: {sorted(declaradas - migradas)}"
        )
        engine.dispose()


class TestLlmProposalsReviewsMigration:
    """`0021_llm_proposals_reviews.py` — las dos tablas del slice 3a
    (`docs/spec-slice-3a.md`): la propuesta del modelo y la captura de la
    revisión. Aditiva: no toca ninguna tabla existente."""

    def _base(self) -> sa.engine.Engine:
        engine = create_engine("sqlite://")
        metadata = sa.MetaData()
        sa.Table(
            "raw_ingestions",
            metadata,
            sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        )
        metadata.create_all(engine)
        return engine

    def test_upgrade_crea_las_dos_tablas_y_downgrade_las_elimina(self) -> None:
        from saimc.models import LlmProposal, LlmReview

        modulo = _cargar_modulo_migracion("0021_llm_proposals_reviews.py")
        engine = self._base()

        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                modulo.upgrade()
            connection.commit()

            tablas = inspect(connection).get_table_names()
            assert {"llm_proposals", "llm_reviews"} <= set(tablas)
            # Mismas columnas que el modelo: `conftest` arma el esquema con el
            # modelo, así que una divergencia no la vería ningún otro test.
            for tabla, modelo in (("llm_proposals", LlmProposal), ("llm_reviews", LlmReview)):
                migradas = {c["name"] for c in inspect(connection).get_columns(tabla)}
                declaradas = {c.name for c in modelo.__table__.columns}
                assert migradas == declaradas, tabla

        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                modulo.downgrade()
            connection.commit()

            tablas = inspect(connection).get_table_names()
            assert "llm_proposals" not in tablas
            assert "llm_reviews" not in tablas
            assert "raw_ingestions" in tablas

        engine.dispose()
