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
