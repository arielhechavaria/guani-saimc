"""Catálogo del foco: productos piloto y canales seguidos.

Implementa la decisión del 2026-08-15 (`docs/decisions.md`): la Fase 0 se
gobierna por densidad por celda, así que la curaduría extrae precio solo de
los productos del conjunto piloto, y el alta de productos deja de ocurrir
dentro del formulario de curaduría.

Los casos de restricción de extracción se escriben antes que el filtro, según
la regla de `CLAUDE.md`: la respuesta correcta se define independientemente de
correr el extractor.
"""

import importlib.util
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session

from saimc.config import get_settings
from saimc.ingestion.whatsapp_webhook import ParsedMessage
from saimc.models import (
    Channel,
    ContentType,
    Platform,
    ProcessingStatus,
    Product,
    Province,
    RawIngestion,
    SourceType,
)
from saimc.web.app import app
from saimc.web.auth import require_operator
from saimc.web.deps import get_db

# --- helpers ----------------------------------------------------------------


def _crear_producto(
    session: Session,
    *,
    name: str,
    is_pilot: bool,
    aliases: list[str] | None = None,
) -> Product:
    producto = Product(
        name=name,
        category="carnes",
        unit="lb",
        aliases=aliases or [],
        is_pilot=is_pilot,
    )
    session.add(producto)
    session.commit()
    return producto


def _crear_provincia(session: Session, *, code: str = "hab", name: str = "La Habana") -> Province:
    provincia = Province(code=code, name=name)
    session.add(provincia)
    session.commit()
    return provincia


def _crear_raw_ingestion(session: Session, *, hash_: str, original_text: str) -> RawIngestion:
    ingestion = RawIngestion(
        source_type=SourceType.INBOX_TEXT,
        file_path=f"raw/{hash_}.txt",
        content_hash=hash_,
        content_type=ContentType.TEXT,
        original_text=original_text,
        processing_status=ProcessingStatus.PENDING,
        captured_at=datetime.now(UTC),
    )
    session.add(ingestion)
    session.commit()
    return ingestion


@pytest.fixture
def client(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[TestClient]:
    def _get_db_override() -> Iterator[Session]:
        yield session

    inbox = tmp_path / "inbox"
    inbox.mkdir()
    monkeypatch.setenv("SAIMC_INBOX_DIR", str(inbox))
    monkeypatch.setenv("SAIMC_RAW_STORE_DIR", str(tmp_path / "data" / "raw"))
    get_settings.cache_clear()

    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[require_operator] = lambda: None
    yield TestClient(app)
    app.dependency_overrides.clear()


# --- restricción de la extracción al conjunto piloto ------------------------


class TestExtraccionRestringidaAPilotos:
    def test_producto_piloto_se_propone(self, client: TestClient, session: Session) -> None:
        """Caso de control: sin él, un filtro que rompa todo también pasaría."""
        producto = _crear_producto(
            session, name="Pollo", is_pilot=True, aliases=["pollo congelado"]
        )
        _crear_provincia(session)
        _crear_raw_ingestion(session, hash_="piloto-si", original_text="pollo a 500 habana")

        response = client.get("/curation")

        assert response.status_code == 200
        assert f'value="{producto.id}" selected' in response.text

    def test_producto_fuera_del_foco_no_se_propone(
        self, client: TestClient, session: Session
    ) -> None:
        """El anuncio nombra el producto y el catálogo lo tiene, pero no es
        piloto: no debe quedar preseleccionado en ninguna fila."""
        fuera = _crear_producto(
            session, name="Detergente", is_pilot=False, aliases=["detergente en polvo"]
        )
        _crear_provincia(session)
        _crear_raw_ingestion(
            session, hash_="piloto-no", original_text="detergente en polvo a 900 habana"
        )

        response = client.get("/curation")

        assert response.status_code == 200
        assert f'value="{fuera.id}" selected' not in response.text

    def test_producto_fuera_del_foco_no_aparece_en_el_selector(
        self, client: TestClient, session: Session
    ) -> None:
        """No alcanza con no proponerlo: si sigue en el `<select>`, el foco
        depende otra vez de la voluntad del operador."""
        _crear_producto(session, name="Pollo", is_pilot=True)
        fuera = _crear_producto(session, name="Detergente", is_pilot=False)
        _crear_provincia(session)
        _crear_raw_ingestion(session, hash_="selector", original_text="algo a 100 habana")

        response = client.get("/curation")

        assert response.status_code == 200
        assert str(fuera.id) not in response.text

    def test_el_formulario_ya_no_ofrece_alta_de_producto(
        self, client: TestClient, session: Session
    ) -> None:
        """El alta al vuelo es lo que produjo 55 productos de una sola
        observación y los duplicados («Arroz criollo» contra «Arroz»). El
        catálogo se administra en /catalog."""
        _crear_producto(session, name="Pollo", is_pilot=True)
        _crear_provincia(session)
        _crear_raw_ingestion(session, hash_="sin-alta", original_text="pollo a 500 habana")

        response = client.get("/curation")

        assert response.status_code == 200
        assert "new_product_name" not in response.text


# --- CRUD de productos piloto -----------------------------------------------


class TestCatalogoProductos:
    def test_lista_muestra_pilotos_y_no_pilotos(
        self, client: TestClient, session: Session
    ) -> None:
        _crear_producto(session, name="Arroz", is_pilot=True)
        _crear_producto(session, name="Detergente", is_pilot=False)

        response = client.get("/catalog")

        assert response.status_code == 200
        assert "Arroz" in response.text
        assert "Detergente" in response.text

    def test_promueve_un_producto_a_piloto(self, client: TestClient, session: Session) -> None:
        producto = _crear_producto(session, name="Azúcar blanca", is_pilot=False)

        response = client.post(
            f"/catalog/products/{producto.id}/pilot",
            data={"is_pilot": "on"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        session.expire_all()
        assert session.get(Product, producto.id).is_pilot is True

    def test_saca_un_producto_del_foco(self, client: TestClient, session: Session) -> None:
        producto = _crear_producto(session, name="Huevos", is_pilot=True)

        response = client.post(
            f"/catalog/products/{producto.id}/pilot", data={}, follow_redirects=False
        )

        assert response.status_code == 303
        session.expire_all()
        assert session.get(Product, producto.id).is_pilot is False

    def test_alta_de_producto_nuevo_nace_como_piloto(
        self, client: TestClient, session: Session
    ) -> None:
        """Dar de alta acá es una decisión explícita, a diferencia del alta al
        vuelo que se quitó de la curaduría: si lo agregás, lo querés medir."""
        response = client.post(
            "/catalog/products",
            data={"name": "Azúcar blanca", "category": "azucar", "unit": "lb"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        creado = session.execute(
            select(Product).where(Product.name == "Azúcar blanca")
        ).scalar_one()
        assert creado.is_pilot is True

    def test_alta_con_nombre_repetido_no_duplica(
        self, client: TestClient, session: Session
    ) -> None:
        """El duplicado es justamente lo que partió la densidad de «Azúcar
        blanca» contra «Azúcar blanco»."""
        _crear_producto(session, name="Arroz", is_pilot=True)

        response = client.post(
            "/catalog/products",
            data={"name": "arroz", "category": "granos", "unit": "lb"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        productos = session.execute(select(Product).where(Product.name.ilike("arroz"))).scalars()
        assert len(list(productos)) == 1


# --- CRUD de canales seguidos -----------------------------------------------


class TestCatalogoCanales:
    def test_alta_de_canal(self, client: TestClient, session: Session) -> None:
        response = client.post(
            "/catalog/channels",
            data={
                "name": "Tienda: The Boss",
                "platform": Platform.WHATSAPP_CHANNEL.value,
                "url": "https://whatsapp.com/channel/0029Vb6559K2UPBDJ9P8XW0s",
                "notes": "Santiago, minorista",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        canal = session.execute(select(Channel)).scalar_one()
        assert canal.name == "Tienda: The Boss"
        assert canal.platform == Platform.WHATSAPP_CHANNEL
        assert canal.is_active is True
        assert canal.external_id is None

    def test_alta_de_canal_con_external_id_para_automatizar(
        self, client: TestClient, session: Session
    ) -> None:
        """El `external_id` es lo que el webhook de WAHA usa para mapear un
        mensaje entrante a este canal (docs/decisions.md, 2026-08-15)."""
        response = client.post(
            "/catalog/channels",
            data={
                "name": "Groupej Morales",
                "platform": Platform.WHATSAPP_CHANNEL.value,
                "external_id": "1234567890@newsletter",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        canal = session.execute(select(Channel)).scalar_one()
        assert canal.external_id == "1234567890@newsletter"

    def test_external_id_en_blanco_se_guarda_como_nulo(
        self, client: TestClient, session: Session
    ) -> None:
        """Un canal manual, sin automatizar, no debe terminar con una cadena
        vacía en una columna que la migración 0011 declaró UNIQUE — dos
        canales manuales con `""` chocarían entre sí."""
        client.post(
            "/catalog/channels",
            data={"name": "Canal manual", "platform": Platform.OTHER.value, "external_id": ""},
            follow_redirects=False,
        )

        canal = session.execute(select(Channel)).scalar_one()
        assert canal.external_id is None

    def test_lista_muestra_los_canales(self, client: TestClient, session: Session) -> None:
        session.add(
            Channel(name="Groupej Morales", platform=Platform.WHATSAPP_CHANNEL, is_active=True)
        )
        session.commit()

        response = client.get("/catalog")

        assert response.status_code == 200
        assert "Groupej Morales" in response.text

    def test_apagar_un_canal_no_lo_borra(self, client: TestClient, session: Session) -> None:
        """Se apaga y no se borra: los registros crudos ya ingeridos siguen
        refiriéndose a él."""
        canal = Channel(name="Grupo viejo", platform=Platform.TELEGRAM_GROUP, is_active=True)
        session.add(canal)
        session.commit()

        response = client.post(
            f"/catalog/channels/{canal.id}/active", data={}, follow_redirects=False
        )

        assert response.status_code == 303
        session.expire_all()
        vigente = session.get(Channel, canal.id)
        assert vigente is not None
        assert vigente.is_active is False


# --- backfill de histórico ---------------------------------------------------


class TestCatalogoBackfillHistorico:
    """Disparador manual `/catalog/channels/{id}/backfill` (docs/decisions.md,
    2026-08-17). `fetch_channel_history` se mockea en el punto de import del
    router — el transporte HTTP en sí ya está cubierto por
    `tests/test_whatsapp_backfill.py`; acá se prueba lo que el router hace
    con el resultado."""

    def _canal_automatizado(self, session: Session, *, name: str = "Groupej") -> Channel:
        canal = Channel(
            name=name,
            platform=Platform.WHATSAPP_CHANNEL,
            external_id="1234567890@newsletter",
            is_active=True,
        )
        session.add(canal)
        session.commit()
        return canal

    def test_canal_sin_external_id_no_llama_a_waha(
        self, client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        canal = Channel(name="Manual", platform=Platform.OTHER, is_active=True)
        session.add(canal)
        session.commit()

        def _no_deberia_llamarse(*args: object, **kwargs: object) -> None:
            raise AssertionError("no debía consultar WAHA sin external_id")

        monkeypatch.setattr(
            "saimc.web.routers.catalog.fetch_channel_history", _no_deberia_llamarse
        )

        response = client.post(
            f"/catalog/channels/{canal.id}/backfill", follow_redirects=False
        )

        assert response.status_code == 303
        assert "error=" in response.headers["location"]

    def test_sin_settings_de_waha_no_llama_al_transporte(
        self, client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        canal = self._canal_automatizado(session)
        monkeypatch.delenv("SAIMC_WAHA_BASE_URL", raising=False)
        monkeypatch.delenv("SAIMC_WAHA_API_KEY", raising=False)
        get_settings.cache_clear()

        def _no_deberia_llamarse(*args: object, **kwargs: object) -> None:
            raise AssertionError("no debía consultar WAHA sin configurar base_url/api_key")

        monkeypatch.setattr(
            "saimc.web.routers.catalog.fetch_channel_history", _no_deberia_llamarse
        )

        response = client.post(
            f"/catalog/channels/{canal.id}/backfill", follow_redirects=False
        )

        assert response.status_code == 303
        assert "error=" in response.headers["location"]

    def test_waha_no_responde_muestra_error_sin_crear_nada(
        self, client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        canal = self._canal_automatizado(session)
        monkeypatch.setenv("SAIMC_WAHA_BASE_URL", "http://127.0.0.1:3000")
        monkeypatch.setenv("SAIMC_WAHA_API_KEY", "clave")
        get_settings.cache_clear()
        monkeypatch.setattr(
            "saimc.web.routers.catalog.fetch_channel_history", lambda *a, **k: None
        )

        response = client.post(
            f"/catalog/channels/{canal.id}/backfill", follow_redirects=False
        )

        assert response.status_code == 303
        assert "error=" in response.headers["location"]
        assert session.execute(select(RawIngestion)).first() is None

    def test_historico_vacio_no_es_error(
        self, client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Consultó bien y WAHA no tenía nada con texto — distinto de fallar
        la consulta (ver test anterior): mensaje informativo, no de error."""
        canal = self._canal_automatizado(session)
        monkeypatch.setenv("SAIMC_WAHA_BASE_URL", "http://127.0.0.1:3000")
        monkeypatch.setenv("SAIMC_WAHA_API_KEY", "clave")
        get_settings.cache_clear()
        monkeypatch.setattr(
            "saimc.web.routers.catalog.fetch_channel_history", lambda *a, **k: []
        )

        response = client.post(
            f"/catalog/channels/{canal.id}/backfill", follow_redirects=False
        )

        assert response.status_code == 303
        assert "error=" not in response.headers["location"]
        assert "message=" in response.headers["location"]

    def test_importa_los_mensajes_a_la_cola_de_curaduria(
        self, client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        canal = self._canal_automatizado(session)
        monkeypatch.setenv("SAIMC_WAHA_BASE_URL", "http://127.0.0.1:3000")
        monkeypatch.setenv("SAIMC_WAHA_API_KEY", "clave")
        get_settings.cache_clear()

        mensajes = [
            ParsedMessage(
                chat_id="1234567890@newsletter",
                text="Arroz grano dorado 100 lb 43.5 o 30885",
                timestamp=datetime(2026, 8, 10, 12, 0, tzinfo=UTC),
                has_media=False,
            ),
            ParsedMessage(
                chat_id="1234567890@newsletter",
                text="Carapacho de pollo 10kg 11 o 7810",
                timestamp=datetime(2026, 8, 12, 9, 30, tzinfo=UTC),
                has_media=False,
            ),
        ]
        monkeypatch.setattr(
            "saimc.web.routers.catalog.fetch_channel_history", lambda *a, **k: mensajes
        )

        response = client.post(
            f"/catalog/channels/{canal.id}/backfill", follow_redirects=False
        )

        assert response.status_code == 303
        assert "error=" not in response.headers["location"]
        ingestiones = list(session.execute(select(RawIngestion)).scalars())
        assert len(ingestiones) == 2
        assert {i.source_type for i in ingestiones} == {SourceType.WHATSAPP_CHANNEL}
        assert {i.source_reference for i in ingestiones} == {canal.name}
        assert {i.processing_status for i in ingestiones} == {ProcessingStatus.PENDING}
        # Cada mensaje conserva SU propia fecha real, no la del momento de la
        # importación — a diferencia de agrupar todos los paths en un único
        # ingest_inbox, que les pondría a todos el mismo captured_at.
        # (SQLite no guarda zona horaria: se compara en naive, PostgreSQL
        # real sí la conserva vía TIMESTAMPTZ — decisions.md, 2026-08-06.)
        fechas = {i.captured_at.replace(tzinfo=None) for i in ingestiones}
        assert fechas == {m.timestamp.replace(tzinfo=None) for m in mensajes}

    def test_correrlo_dos_veces_no_duplica(
        self, client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """El mismo canal reposteando el mismo aviso varios días (caso real
        medido contra Groupej Morales Surl, 2026-08-17) no debe inflar la
        cola cada vez que se corre el backfill."""
        canal = self._canal_automatizado(session)
        monkeypatch.setenv("SAIMC_WAHA_BASE_URL", "http://127.0.0.1:3000")
        monkeypatch.setenv("SAIMC_WAHA_API_KEY", "clave")
        get_settings.cache_clear()

        mensajes = [
            ParsedMessage(
                chat_id="1234567890@newsletter",
                text="Arroz grano dorado 100 lb 43.5 o 30885",
                timestamp=datetime(2026, 8, 10, 12, 0, tzinfo=UTC),
                has_media=False,
            )
        ]
        monkeypatch.setattr(
            "saimc.web.routers.catalog.fetch_channel_history", lambda *a, **k: mensajes
        )

        client.post(f"/catalog/channels/{canal.id}/backfill", follow_redirects=False)
        segunda = client.post(
            f"/catalog/channels/{canal.id}/backfill", follow_redirects=False
        )

        assert segunda.status_code == 303
        assert len(list(session.execute(select(RawIngestion)).scalars())) == 1


def _cargar_modulo_migracion(nombre: str) -> ModuleType:
    """Carga un archivo de migración como módulo, sin `alembic upgrade`.

    Mismo patrón que `tests/test_processing_layer.py`.
    """
    ruta = Path(__file__).resolve().parent.parent / "alembic" / "versions" / nombre
    spec = importlib.util.spec_from_file_location(nombre.removesuffix(".py"), ruta)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _tabla_products() -> tuple[sa.MetaData, sa.Table]:
    metadata = sa.MetaData()
    tabla = sa.Table(
        "products",
        metadata,
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
    )
    return metadata, tabla


class TestIsPilotMigration:
    """`0009_products_is_pilot.py` contra un engine SQLite real."""

    def test_upgrade_agrega_la_columna_y_downgrade_la_elimina(self) -> None:
        modulo = _cargar_modulo_migracion("0009_products_is_pilot.py")

        engine = create_engine("sqlite://")
        metadata, tabla = _tabla_products()
        metadata.create_all(engine)

        with engine.connect() as connection:
            connection.execute(sa.insert(tabla).values(id=uuid.uuid4(), name="Arroz"))
            connection.commit()

        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                modulo.upgrade()
            connection.commit()
            assert "is_pilot" in {c["name"] for c in inspect(connection).get_columns("products")}

        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                modulo.downgrade()
            connection.commit()
            columnas = {c["name"] for c in inspect(connection).get_columns("products")}
            assert "is_pilot" not in columnas
            assert "name" in columnas  # el downgrade no se llevó otra columna

        engine.dispose()

    def test_upgrade_enciende_los_pilotos_y_deja_apagado_el_resto(self) -> None:
        """Encender de más devolvería a la curaduría productos que nadie
        eligió medir, que es justo lo que este slice cierra."""
        modulo = _cargar_modulo_migracion("0009_products_is_pilot.py")

        engine = create_engine("sqlite://")
        metadata, tabla = _tabla_products()
        metadata.create_all(engine)

        with engine.connect() as connection:
            for nombre in ("Arroz", "Aceite", "Azúcar blanca", "Pollo", "Sazon Dubai", "Leche"):
                connection.execute(sa.insert(tabla).values(id=uuid.uuid4(), name=nombre))
            connection.commit()

        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                modulo.upgrade()
            connection.commit()
            encendidos = {
                nombre
                for (nombre,) in connection.execute(
                    sa.text("select name from products where is_pilot")
                )
            }

        # «Leche» está en spec.md §9 y no tiene una sola observación: la
        # migración sigue la decisión del operador, no la lista del documento.
        assert encendidos == {"Arroz", "Aceite", "Azúcar blanca", "Pollo"}
        engine.dispose()


class TestChannelsExternalIdMigration:
    """`0011_channels_external_id.py` contra un engine SQLite real."""

    def _tabla_channels(self) -> tuple[sa.MetaData, sa.Table]:
        metadata = sa.MetaData()
        tabla = sa.Table(
            "channels",
            metadata,
            sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
            sa.Column("name", sa.String(120), nullable=False),
        )
        return metadata, tabla

    def test_upgrade_agrega_la_columna_y_downgrade_la_elimina(self) -> None:
        modulo = _cargar_modulo_migracion("0011_channels_external_id.py")

        engine = create_engine("sqlite://")
        metadata, tabla = self._tabla_channels()
        metadata.create_all(engine)

        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                modulo.upgrade()
            connection.commit()
            assert "external_id" in {
                c["name"] for c in inspect(connection).get_columns("channels")
            }

        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                modulo.downgrade()
            connection.commit()
            columnas = {c["name"] for c in inspect(connection).get_columns("channels")}
            assert "external_id" not in columnas
            assert "name" in columnas

        engine.dispose()

    def test_dos_external_id_nulos_conviven_pero_valores_repetidos_no(self) -> None:
        """El guardarraíl real de esta migración: dos canales sin automatizar
        no chocan, pero dos apuntando al mismo remitente de WAHA sí."""
        modulo = _cargar_modulo_migracion("0011_channels_external_id.py")

        engine = create_engine("sqlite://")
        metadata, tabla = self._tabla_channels()
        metadata.create_all(engine)

        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                modulo.upgrade()
            connection.commit()

            tabla_con_columna = sa.Table(
                "channels",
                sa.MetaData(),
                sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
                sa.Column("name", sa.String(120), nullable=False),
                sa.Column("external_id", sa.String(120), nullable=True),
            )
            connection.execute(
                sa.insert(tabla_con_columna).values(
                    id=uuid.uuid4(), name="A", external_id=None
                )
            )
            connection.execute(
                sa.insert(tabla_con_columna).values(
                    id=uuid.uuid4(), name="B", external_id=None
                )
            )
            connection.commit()

            connection.execute(
                sa.insert(tabla_con_columna).values(
                    id=uuid.uuid4(), name="C", external_id="1@newsletter"
                )
            )
            connection.commit()

            with pytest.raises(Exception):  # noqa: B017 — IntegrityError de cualquier dialecto
                connection.execute(
                    sa.insert(tabla_con_columna).values(
                        id=uuid.uuid4(), name="D", external_id="1@newsletter"
                    )
                )
                connection.commit()

        engine.dispose()


class TestChannelsMigration:
    """`0010_channels.py` — tabla nueva, sin `ALTER`."""

    def test_upgrade_crea_la_tabla_y_downgrade_la_elimina(self) -> None:
        modulo = _cargar_modulo_migracion("0010_channels.py")

        engine = create_engine("sqlite://")

        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                modulo.upgrade()
            connection.commit()
            assert "channels" in inspect(connection).get_table_names()
            columnas = {c["name"] for c in inspect(connection).get_columns("channels")}
            assert {"id", "name", "platform", "url", "notes", "is_active"} <= columnas

        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                modulo.downgrade()
            connection.commit()
            assert "channels" not in inspect(connection).get_table_names()

        engine.dispose()
