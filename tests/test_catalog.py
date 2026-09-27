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
import re
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from urllib.parse import parse_qs, urlsplit

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
    Currency,
    GlossaryEntry,
    Platform,
    PriceHistory,
    ProcessingStatus,
    Product,
    ProductFormat,
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
    unit: str = "lb",
) -> Product:
    producto = Product(
        name=name,
        category="carnes",
        unit=unit,
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

    def test_corrige_la_unidad_de_un_producto_ya_existente(
        self, client: TestClient, session: Session
    ) -> None:
        """El alta no tiene edición: un producto cargado con la unidad
        equivocada (Picadillo entró como "unidad" con observaciones en lb)
        queda fuera de la vista minorista para siempre sin esto."""
        producto = _crear_producto(session, name="Picadillo", unit="unidad", is_pilot=True)

        response = client.post(
            f"/catalog/products/{producto.id}/unit",
            data={"unit": "lb"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        session.expire_all()
        assert session.get(Product, producto.id).unit == "lb"

    def test_unidad_no_canonica_se_rechaza_sin_tocar_el_producto(
        self, client: TestClient, session: Session
    ) -> None:
        """`/catalog` deja de editar `unit` como texto libre: un valor fuera de
        `saimc.units.UNITS` se rechaza con error, no se guarda ni se
        reemplaza por un default silencioso (proposal: `products.unit`)."""
        producto = _crear_producto(session, name="Picadillo", unit="lb", is_pilot=True)

        response = client.post(
            f"/catalog/products/{producto.id}/unit",
            data={"unit": "quintal"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert "error=" in response.headers["location"]
        session.expire_all()
        assert session.get(Product, producto.id).unit == "lb"  # intacto


def _crear_raw_para_precio(session: Session, *, hash_: str) -> RawIngestion:
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


class TestCatalogoFormatos:
    """Declaración y administración de formatos de venta por producto
    (design.md D7, con la excepción del borrado registrada en
    docs/decisions.md 2026-09-04). Editar un formato solo es seguro sin
    observaciones —no hay nada denormalizado que desalinear (D8)—; borrar es
    incondicional, y con historial las filas quedan sin formato en vez de
    bloquear el borrado.
    """

    def test_declarar_un_formato_lo_crea(
        self, client: TestClient, session: Session
    ) -> None:
        producto = _crear_producto(session, name="Pollo", is_pilot=True)

        response = client.post(
            f"/catalog/products/{producto.id}/formats",
            data={"packaging": "caja", "quantity": "40", "unit": "lb"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        formato = session.execute(select(ProductFormat)).scalars().one()
        assert (formato.product_id, formato.packaging, formato.quantity, formato.unit) == (
            producto.id,
            "caja",
            Decimal("40"),
            "lb",
        )

    def test_declarar_el_mismo_triple_no_duplica(
        self, client: TestClient, session: Session
    ) -> None:
        producto = _crear_producto(session, name="Pollo", is_pilot=True)
        for _ in range(2):
            client.post(
                f"/catalog/products/{producto.id}/formats",
                data={"packaging": "caja", "quantity": "40", "unit": "lb"},
                follow_redirects=False,
            )

        assert len(list(session.execute(select(ProductFormat)).scalars())) == 1

    def test_envase_fuera_del_vocabulario_se_rechaza(
        self, client: TestClient, session: Session
    ) -> None:
        producto = _crear_producto(session, name="Pollo", is_pilot=True)

        response = client.post(
            f"/catalog/products/{producto.id}/formats",
            data={"packaging": "contenedor", "quantity": "40", "unit": "lb"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert "error=" in response.headers["location"]
        assert session.execute(select(ProductFormat)).scalars().first() is None

    def test_borra_un_formato_sin_observaciones(
        self, client: TestClient, session: Session
    ) -> None:
        producto = _crear_producto(session, name="Pollo", is_pilot=True)
        formato = ProductFormat(
            product_id=producto.id, packaging="caja", quantity=Decimal("40"), unit="lb"
        )
        session.add(formato)
        session.commit()

        response = client.post(
            f"/catalog/formats/{formato.id}/delete", follow_redirects=False
        )

        assert response.status_code == 303
        assert session.get(ProductFormat, formato.id) is None

    def test_borra_un_formato_con_observaciones_las_deja_sin_formato(
        self, client: TestClient, session: Session
    ) -> None:
        producto = _crear_producto(session, name="Pollo", is_pilot=True)
        fuente = _crear_raw_para_precio(session, hash_="hash-formato-con-precio")
        formato = ProductFormat(
            product_id=producto.id, packaging="caja", quantity=Decimal("40"), unit="lb"
        )
        session.add(formato)
        session.flush()
        precio = PriceHistory(
            product_id=producto.id,
            price=Decimal("12400"),
            currency=Currency.CUP.value,
            format_id=formato.id,
            source_id=fuente.id,
        )
        session.add(precio)
        session.commit()

        response = client.post(
            f"/catalog/formats/{formato.id}/delete", follow_redirects=False
        )

        assert response.status_code == 303
        assert "error=" not in response.headers["location"]
        assert session.get(ProductFormat, formato.id) is None
        session.expire_all()
        assert session.get(PriceHistory, precio.id).format_id is None  # huérfana, no borrada

    def test_editar_un_formato_sin_observaciones_lo_corrige(
        self, client: TestClient, session: Session
    ) -> None:
        producto = _crear_producto(session, name="Pollo", is_pilot=True)
        formato = ProductFormat(
            product_id=producto.id, packaging="caja", quantity=Decimal("400"), unit="lb"
        )
        session.add(formato)
        session.commit()

        response = client.post(
            f"/catalog/formats/{formato.id}/edit",
            data={"packaging": "caja", "quantity": "40", "unit": "lb"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert "error=" not in response.headers["location"]
        session.expire_all()
        assert session.get(ProductFormat, formato.id).quantity == Decimal("40")

    def test_no_edita_un_formato_con_observaciones(
        self, client: TestClient, session: Session
    ) -> None:
        producto = _crear_producto(session, name="Pollo", is_pilot=True)
        fuente = _crear_raw_para_precio(session, hash_="hash-formato-no-editable")
        formato = ProductFormat(
            product_id=producto.id, packaging="caja", quantity=Decimal("40"), unit="lb"
        )
        session.add(formato)
        session.flush()
        session.add(
            PriceHistory(
                product_id=producto.id,
                price=Decimal("12400"),
                currency=Currency.CUP.value,
                format_id=formato.id,
                source_id=fuente.id,
            )
        )
        session.commit()

        response = client.post(
            f"/catalog/formats/{formato.id}/edit",
            data={"packaging": "caja", "quantity": "45", "unit": "lb"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert "error=" in response.headers["location"]
        session.expire_all()
        assert session.get(ProductFormat, formato.id).quantity == Decimal("40")  # intacto

    def test_editar_a_un_triple_que_ya_existe_se_rechaza(
        self, client: TestClient, session: Session
    ) -> None:
        producto = _crear_producto(session, name="Pollo", is_pilot=True)
        session.add(
            ProductFormat(
                product_id=producto.id, packaging="caja", quantity=Decimal("40"), unit="lb"
            )
        )
        editable = ProductFormat(
            product_id=producto.id, packaging="saco", quantity=Decimal("40"), unit="lb"
        )
        session.add(editable)
        session.commit()

        response = client.post(
            f"/catalog/formats/{editable.id}/edit",
            data={"packaging": "caja", "quantity": "40", "unit": "lb"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert "error=" in response.headers["location"]
        session.expire_all()
        assert session.get(ProductFormat, editable.id).packaging == "saco"  # intacto

    def test_la_lista_muestra_el_formato_y_su_conteo(
        self, client: TestClient, session: Session
    ) -> None:
        producto = _crear_producto(session, name="Pollo", is_pilot=True)
        fuente = _crear_raw_para_precio(session, hash_="hash-conteo")
        formato = ProductFormat(
            product_id=producto.id, packaging="caja", quantity=Decimal("40"), unit="lb"
        )
        session.add(formato)
        session.flush()
        session.add(
            PriceHistory(
                product_id=producto.id,
                price=Decimal("12400"),
                currency=Currency.CUP.value,
                format_id=formato.id,
                source_id=fuente.id,
            )
        )
        session.commit()

        response = client.get("/catalog")

        assert response.status_code == 200
        # La misma etiqueta que la Matriz y el select de la cola
        # (`saimc.formats.format_label`), no una copia con otro separador.
        assert "caja de 40 lb" in response.text

    def test_editar_formato_decimal_conserva_la_cantidad(
        self, client: TestClient, session: Session
    ) -> None:
        """El input de edición muestra 0.9, no 0.

        Sin observaciones el formato es editable, y el formulario venía
        truncando la cantidad con `| int`: abrir «botella de 0.9 l» y guardar
        sin tocar nada escribía 0, cambiando el formato en silencio.
        """
        producto = _crear_producto(session, name="Aceite", is_pilot=True, unit="l")
        session.add(
            ProductFormat(
                product_id=producto.id,
                packaging="botella",
                quantity=Decimal("0.9"),
                unit="l",
            )
        )
        session.commit()

        response = client.get("/catalog")

        assert response.status_code == 200
        assert 'name="quantity" value="0.9"' in response.text
        assert 'name="quantity" value="0"' not in response.text


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

    def test_alta_de_canal_con_nombre_repetido_no_falla(
        self, client: TestClient, session: Session
    ) -> None:
        """Desde la migración 0014 el nombre ya no es identidad (docs/decisions.md,
        2026-08-19) — el caso real fue "Ventas Mayoristas", canal y grupo a
        la vez, con `external_id` distinto cada uno."""
        client.post(
            "/catalog/channels",
            data={
                "name": "Ventas Mayoristas",
                "platform": Platform.WHATSAPP_CHANNEL.value,
                "external_id": "1@newsletter",
            },
            follow_redirects=False,
        )

        response = client.post(
            "/catalog/channels",
            data={
                "name": "Ventas Mayoristas",
                "platform": Platform.WHATSAPP_GROUP.value,
                "external_id": "1@g.us",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert "error=" not in response.headers["location"]
        canales = list(session.execute(select(Channel)).scalars())
        assert len(canales) == 2
        assert {c.name for c in canales} == {"Ventas Mayoristas"}

    def test_alta_de_canal_con_external_id_repetido_falla_sin_romper(
        self, client: TestClient, session: Session
    ) -> None:
        """`external_id` (migración 0011) sigue siendo la identidad real —
        acá sí hay que rechazar, pero con un error legible, no un 500."""
        client.post(
            "/catalog/channels",
            data={
                "name": "Tienda: The Boss",
                "platform": Platform.WHATSAPP_CHANNEL.value,
                "external_id": "1@newsletter",
            },
            follow_redirects=False,
        )

        response = client.post(
            "/catalog/channels",
            data={
                "name": "Otro nombre cualquiera",
                "platform": Platform.WHATSAPP_CHANNEL.value,
                "external_id": "1@newsletter",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert "error=" in response.headers["location"]
        canales = list(session.execute(select(Channel)).scalars())
        assert len(canales) == 1

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

    def test_alta_de_canal_con_provincia(self, client: TestClient, session: Session) -> None:
        provincia = Province(code="hab", name="La Habana")
        session.add(provincia)
        session.commit()

        response = client.post(
            "/catalog/channels",
            data={
                "name": "Groupej Morales",
                "platform": Platform.WHATSAPP_CHANNEL.value,
                "province_id": str(provincia.id),
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        canal = session.execute(select(Channel)).scalar_one()
        assert canal.province_id == provincia.id

    def test_alta_de_canal_sin_provincia_queda_nula(
        self, client: TestClient, session: Session
    ) -> None:
        client.post(
            "/catalog/channels",
            data={"name": "Canal sin provincia", "platform": Platform.OTHER.value},
            follow_redirects=False,
        )

        canal = session.execute(select(Channel)).scalar_one()
        assert canal.province_id is None

    def test_fija_provincia_de_un_canal_ya_existente(
        self, client: TestClient, session: Session
    ) -> None:
        """El caso real que motivó esto: Groupej Morales Surl ya estaba en la
        tabla antes de que esta columna existiera (docs/decisions.md,
        2026-08-17) — sin este endpoint, nunca podría cargársela."""
        provincia = Province(code="hab", name="La Habana")
        canal = Channel(name="Groupej Morales", platform=Platform.WHATSAPP_CHANNEL, is_active=True)
        session.add_all([provincia, canal])
        session.commit()

        response = client.post(
            f"/catalog/channels/{canal.id}/province",
            data={"province_id": str(provincia.id)},
            follow_redirects=False,
        )

        assert response.status_code == 303
        session.expire_all()
        assert session.get(Channel, canal.id).province_id == provincia.id

    def test_puede_volver_a_dejar_la_provincia_sin_fijar(
        self, client: TestClient, session: Session
    ) -> None:
        provincia = Province(code="hab", name="La Habana")
        canal = Channel(
            name="Groupej Morales",
            platform=Platform.WHATSAPP_CHANNEL,
            province_id=None,
            is_active=True,
        )
        session.add_all([provincia, canal])
        session.commit()
        canal.province_id = provincia.id
        session.commit()

        response = client.post(
            f"/catalog/channels/{canal.id}/province",
            data={"province_id": ""},
            follow_redirects=False,
        )

        assert response.status_code == 303
        session.expire_all()
        assert session.get(Channel, canal.id).province_id is None

    def test_alta_de_canal_con_direccion(self, client: TestClient, session: Session) -> None:
        response = client.post(
            "/catalog/channels",
            data={
                "name": "Groupej Morales",
                "platform": Platform.WHATSAPP_CHANNEL.value,
                "address": "Calle C % Avenida y 4ta #155, Cotorro",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        canal = session.execute(select(Channel)).scalar_one()
        assert canal.address == "Calle C % Avenida y 4ta #155, Cotorro"

    def test_alta_de_canal_sin_direccion_queda_nula(
        self, client: TestClient, session: Session
    ) -> None:
        client.post(
            "/catalog/channels",
            data={"name": "Canal sin dirección", "platform": Platform.OTHER.value},
            follow_redirects=False,
        )

        canal = session.execute(select(Channel)).scalar_one()
        assert canal.address is None

    def test_fija_direccion_de_un_canal_ya_existente(
        self, client: TestClient, session: Session
    ) -> None:
        """El caso real que lo motiva es el mismo que provincia: el canal ya
        estaba en la tabla antes de que esta columna existiera
        (docs/decisions.md, 2026-08-19) — la dirección sale de la
        descripción del grupo, que el operador lee después del alta."""
        canal = Channel(name="Groupej Morales", platform=Platform.WHATSAPP_CHANNEL, is_active=True)
        session.add(canal)
        session.commit()

        response = client.post(
            f"/catalog/channels/{canal.id}/address",
            data={"address": "Calle C % Avenida y 4ta #155, Cotorro"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        session.expire_all()
        assert session.get(Channel, canal.id).address == "Calle C % Avenida y 4ta #155, Cotorro"

    def test_puede_volver_a_dejar_la_direccion_sin_fijar(
        self, client: TestClient, session: Session
    ) -> None:
        canal = Channel(
            name="Groupej Morales",
            platform=Platform.WHATSAPP_CHANNEL,
            address="Calle C % Avenida y 4ta #155, Cotorro",
            is_active=True,
        )
        session.add(canal)
        session.commit()

        response = client.post(
            f"/catalog/channels/{canal.id}/address",
            data={"address": ""},
            follow_redirects=False,
        )

        assert response.status_code == 303
        session.expire_all()
        assert session.get(Channel, canal.id).address is None

    def test_edita_los_datos_de_un_canal_existente(
        self, client: TestClient, session: Session
    ) -> None:
        canal = Channel(name="Nombre con error", platform=Platform.OTHER, is_active=True)
        session.add(canal)
        session.commit()

        response = client.post(
            f"/catalog/channels/{canal.id}/edit",
            data={
                "name": "Ventas Mayoristas",
                "platform": Platform.WHATSAPP_GROUP.value,
                "url": "https://chat.whatsapp.com/abc123",
                "notes": "corregido",
                "external_id": "1@g.us",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert "error=" not in response.headers["location"]
        session.expire_all()
        editado = session.get(Channel, canal.id)
        assert editado.name == "Ventas Mayoristas"
        assert editado.platform == Platform.WHATSAPP_GROUP
        assert editado.url == "https://chat.whatsapp.com/abc123"
        assert editado.notes == "corregido"
        assert editado.external_id == "1@g.us"

    def test_editar_con_nombre_vacio_no_se_aplica(
        self, client: TestClient, session: Session
    ) -> None:
        canal = Channel(name="Groupej Morales", platform=Platform.WHATSAPP_CHANNEL, is_active=True)
        session.add(canal)
        session.commit()

        response = client.post(
            f"/catalog/channels/{canal.id}/edit",
            data={"name": "", "platform": Platform.OTHER.value},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert "error=" in response.headers["location"]
        session.expire_all()
        assert session.get(Channel, canal.id).name == "Groupej Morales"

    def test_editar_no_choca_con_su_propio_external_id(
        self, client: TestClient, session: Session
    ) -> None:
        """El pre-chequeo de duplicado excluye este mismo canal — de lo
        contrario, guardar sin tocar el Id. WAHA fallaría siempre."""
        canal = Channel(
            name="Groupej Morales",
            platform=Platform.WHATSAPP_CHANNEL,
            external_id="1@newsletter",
            is_active=True,
        )
        session.add(canal)
        session.commit()

        response = client.post(
            f"/catalog/channels/{canal.id}/edit",
            data={
                "name": "Groupej Morales",
                "platform": Platform.WHATSAPP_CHANNEL.value,
                "external_id": "1@newsletter",
                "notes": "sin cambios en el id",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert "error=" not in response.headers["location"]
        session.expire_all()
        assert session.get(Channel, canal.id).notes == "sin cambios en el id"

    def test_editar_con_external_id_de_otro_canal_falla_sin_romper(
        self, client: TestClient, session: Session
    ) -> None:
        otro = Channel(
            name="Tienda: The Boss",
            platform=Platform.WHATSAPP_CHANNEL,
            external_id="1@newsletter",
            is_active=True,
        )
        canal = Channel(name="Groupej Morales", platform=Platform.WHATSAPP_CHANNEL, is_active=True)
        session.add_all([otro, canal])
        session.commit()

        response = client.post(
            f"/catalog/channels/{canal.id}/edit",
            data={
                "name": "Groupej Morales",
                "platform": Platform.WHATSAPP_CHANNEL.value,
                "external_id": "1@newsletter",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert "error=" in response.headers["location"]
        session.expire_all()
        assert session.get(Channel, canal.id).external_id is None

    def test_elimina_un_canal_que_nunca_capturo_nada(
        self, client: TestClient, session: Session
    ) -> None:
        canal = Channel(name="Alta por error", platform=Platform.OTHER, is_active=True)
        session.add(canal)
        session.commit()
        canal_id = canal.id

        response = client.post(
            f"/catalog/channels/{canal_id}/delete", follow_redirects=False
        )

        assert response.status_code == 303
        assert "error=" not in response.headers["location"]
        session.expire_all()
        assert session.get(Channel, canal_id) is None

    def test_no_elimina_un_canal_con_historial_capturado(
        self, client: TestClient, session: Session
    ) -> None:
        """El mismo guardarraíl que `ondelete="RESTRICT"` en
        `raw_ingestions.channel_id` (migración 0015): con historial, la
        única vía es apagar (`toggle_channel`), nunca borrar."""
        canal = Channel(name="Groupej Morales", platform=Platform.WHATSAPP_CHANNEL, is_active=True)
        session.add(canal)
        session.commit()
        ingestion = _crear_raw_ingestion(session, hash_="con-historial", original_text="algo")
        ingestion.channel_id = canal.id
        session.commit()

        response = client.post(
            f"/catalog/channels/{canal.id}/delete", follow_redirects=False
        )

        assert response.status_code == 303
        assert "error=" in response.headers["location"]
        session.expire_all()
        assert session.get(Channel, canal.id) is not None


# --- búsqueda y filtro (sin paginación: 10 canales / 60 productos medidos en
# producción el 2026-08-21 no la justifican) ---------------------------------


class TestCatalogoBuscadorDeCanales:
    def test_busca_por_nombre_parcial_sin_importar_mayusculas(
        self, client: TestClient, session: Session
    ) -> None:
        session.add(Channel(name="Tienda El Boss", platform=Platform.WHATSAPP_CHANNEL))
        session.add(Channel(name="Mercado Central", platform=Platform.WHATSAPP_GROUP))
        session.commit()

        response = client.get("/catalog", params={"q_canal": "boss"})

        assert "Tienda El Boss" in response.text
        assert "Mercado Central" not in response.text

    def test_filtra_por_plataforma(self, client: TestClient, session: Session) -> None:
        session.add(Channel(name="Canal Uno", platform=Platform.WHATSAPP_CHANNEL))
        session.add(Channel(name="Grupo Uno", platform=Platform.WHATSAPP_GROUP))
        session.commit()

        response = client.get("/catalog", params={"platform": Platform.WHATSAPP_GROUP.value})

        assert "Grupo Uno" in response.text
        assert "Canal Uno" not in response.text

    def test_busqueda_y_plataforma_combinadas(self, client: TestClient, session: Session) -> None:
        session.add(Channel(name="Ventas Mayoristas", platform=Platform.WHATSAPP_CHANNEL))
        session.add(Channel(name="Ventas Mayoristas", platform=Platform.WHATSAPP_GROUP))
        session.commit()

        response = client.get(
            "/catalog",
            params={"q_canal": "Ventas", "platform": Platform.WHATSAPP_GROUP.value},
        )

        # El nombre aparece más de una vez por fila (texto visible + el
        # `confirm()` del botón borrar), así que se cuenta el texto entre
        # etiquetas, no la ocurrencia cruda del string en todo el HTML.
        assert response.text.count(">Ventas Mayoristas<") == 1

    def test_sin_coincidencias_muestra_mensaje_de_filtro_vacio(
        self, client: TestClient, session: Session
    ) -> None:
        session.add(Channel(name="Tienda El Boss", platform=Platform.WHATSAPP_CHANNEL))
        session.commit()

        response = client.get("/catalog", params={"q_canal": "no existe ninguno"})

        assert "Ningún canal coincide con el filtro" in response.text
        assert "Todavía no hay canales anotados" not in response.text

    def test_pilot_count_no_cambia_al_filtrar_canales(
        self, client: TestClient, session: Session
    ) -> None:
        """El stat «En el foco» es sobre productos, no sobre canales — no
        debe verse afectado por un filtro que solo toca la tabla de canales."""
        _crear_producto(session, name="Arroz", is_pilot=True)
        session.add(Channel(name="Tienda El Boss", platform=Platform.WHATSAPP_CHANNEL))
        session.commit()

        sin_filtro = client.get("/catalog")
        con_filtro = client.get("/catalog", params={"q_canal": "no existe"})

        patron = r'm3-stat-value is-accent">\s*(\d+)'
        assert re.search(patron, sin_filtro.text).group(1) == "1"
        assert re.search(patron, con_filtro.text).group(1) == "1"


class TestCatalogoBuscadorDeProductos:
    def test_busca_por_nombre_parcial(self, client: TestClient, session: Session) -> None:
        _crear_producto(session, name="Arroz", is_pilot=True)
        _crear_producto(session, name="Detergente", is_pilot=False)

        response = client.get("/catalog", params={"q_producto": "arr"})

        assert "Arroz" in response.text
        assert "Detergente" not in response.text

    def test_busqueda_se_combina_con_solo_el_foco(
        self, client: TestClient, session: Session
    ) -> None:
        _crear_producto(session, name="Arroz criollo", is_pilot=True)
        _crear_producto(session, name="Arroz importado", is_pilot=False)

        response = client.get("/catalog", params={"q_producto": "arroz", "focus": "1"})

        assert "Arroz criollo" in response.text
        assert "Arroz importado" not in response.text

    def test_sin_coincidencias_muestra_mensaje_de_busqueda_vacia(
        self, client: TestClient, session: Session
    ) -> None:
        _crear_producto(session, name="Arroz", is_pilot=True)

        response = client.get("/catalog", params={"q_producto": "no existe ninguno"})

        assert "Ningún producto coincide con la búsqueda" in response.text


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
        # setenv("", ...), no delenv: pydantic-settings lee `.env` del disco
        # como fuente propia — este repo ya tiene las claves reales cargadas
        # ahí para producción, así que borrar la variable de entorno no
        # alcanza para simular "sin configurar" (mismo bug ya corregido en
        # tests/test_whatsapp_webhook.py, 2026-08-17).
        monkeypatch.setenv("SAIMC_WAHA_BASE_URL", "")
        monkeypatch.setenv("SAIMC_WAHA_API_KEY", "")
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

    def test_mensaje_distingue_capturados_de_visibles_en_la_cola(
        self, client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """«N nuevos en la cola» daba a entender que se iban a ver ahí, pero
        el filtro de foco (curation.py) esconde lo que no menciona ningún
        producto piloto — de 174 pendientes reales solo 17 eran visibles
        (docs/decisions.md, 2026-08-21). El mensaje ahora separa las dos
        cuentas en vez de prometer visibilidad que el foco después no da."""
        _crear_producto(session, name="Arroz", is_pilot=True)
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
                text="Gasolina 3000 el galón",
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
        location = urlsplit(response.headers["location"])
        mensaje = parse_qs(location.query)["message"][0]
        assert "2 anuncios nuevos capturados" in mensaje
        assert "1 van a verse en la cola" in mensaje

    def test_backfill_guarda_channel_id_en_las_filas_importadas(
        self, client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Identidad real de origen (docs/decisions.md, 2026-08-19): el
        backfill ya conoce el canal que dispara la importación."""
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
        ]
        monkeypatch.setattr(
            "saimc.web.routers.catalog.fetch_channel_history", lambda *a, **k: mensajes
        )

        client.post(f"/catalog/channels/{canal.id}/backfill", follow_redirects=False)

        ingestiones = list(session.execute(select(RawIngestion)).scalars())
        assert len(ingestiones) == 1
        assert ingestiones[0].channel_id == canal.id

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


class TestChannelsProvinceMigration:
    """`0012_channels_province.py` — "el canal siempre manda" (docs/decisions.md,
    2026-08-17)."""

    def _tablas(self) -> tuple[sa.MetaData, sa.Table, sa.Table]:
        metadata = sa.MetaData()
        provincias = sa.Table(
            "provinces",
            metadata,
            sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
            sa.Column("name", sa.String(80), nullable=False),
        )
        canales = sa.Table(
            "channels",
            metadata,
            sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
            sa.Column("name", sa.String(120), nullable=False),
        )
        return metadata, provincias, canales

    def test_upgrade_agrega_la_columna_y_downgrade_la_elimina(self) -> None:
        modulo = _cargar_modulo_migracion("0012_channels_province.py")

        engine = create_engine("sqlite://")
        metadata, _, _ = self._tablas()
        metadata.create_all(engine)

        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                modulo.upgrade()
            connection.commit()
            assert "province_id" in {
                c["name"] for c in inspect(connection).get_columns("channels")
            }

        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                modulo.downgrade()
            connection.commit()
            columnas = {c["name"] for c in inspect(connection).get_columns("channels")}
            assert "province_id" not in columnas
            assert "name" in columnas

        engine.dispose()

    def test_province_id_es_nullable(self) -> None:
        """Un canal ya registrado antes de esta migración no tenía este dato
        — no puede volverse NOT NULL sin backfill, que no existe."""
        modulo = _cargar_modulo_migracion("0012_channels_province.py")

        engine = create_engine("sqlite://")
        metadata, provincias, canales = self._tablas()
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
                sa.Column("province_id", sa.Uuid(as_uuid=True), nullable=True),
            )
            connection.execute(
                sa.insert(tabla_con_columna).values(
                    id=uuid.uuid4(), name="Canal sin provincia", province_id=None
                )
            )
            connection.commit()

        engine.dispose()


class TestChannelsAddressMigration:
    """`0013_channels_address.py` — dirección del local, no de un anunciante
    individual (docs/decisions.md, 2026-08-19)."""

    def _tabla_channels(self) -> sa.MetaData:
        metadata = sa.MetaData()
        sa.Table(
            "channels",
            metadata,
            sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
            sa.Column("name", sa.String(120), nullable=False),
        )
        return metadata

    def test_upgrade_agrega_la_columna_y_downgrade_la_elimina(self) -> None:
        modulo = _cargar_modulo_migracion("0013_channels_address.py")

        engine = create_engine("sqlite://")
        self._tabla_channels().create_all(engine)

        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                modulo.upgrade()
            connection.commit()
            assert "address" in {c["name"] for c in inspect(connection).get_columns("channels")}

        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                modulo.downgrade()
            connection.commit()
            columnas = {c["name"] for c in inspect(connection).get_columns("channels")}
            assert "address" not in columnas
            assert "name" in columnas

        engine.dispose()

    def test_address_es_nullable(self) -> None:
        """Un canal ya registrado antes de esta migración no tenía este dato
        — no puede volverse NOT NULL sin backfill, que no existe."""
        modulo = _cargar_modulo_migracion("0013_channels_address.py")

        engine = create_engine("sqlite://")
        self._tabla_channels().create_all(engine)

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
                sa.Column("address", sa.Text(), nullable=True),
            )
            connection.execute(
                sa.insert(tabla_con_columna).values(
                    id=uuid.uuid4(), name="Canal sin dirección", address=None
                )
            )
            connection.commit()

        engine.dispose()


class TestChannelsNameIndexMigration:
    """`0014_channels_name_not_unique.py` — `external_id` (0011) es la
    identidad real; `name` es una etiqueta de WhatsApp que un canal y un
    grupo distintos pueden compartir (docs/decisions.md, 2026-08-19,
    "Ventas Mayoristas")."""

    def _tabla_channels_con_indice_unico(self, engine: sa.Engine) -> None:
        metadata = sa.MetaData()
        sa.Table(
            "channels",
            metadata,
            sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
            sa.Column("name", sa.String(120), nullable=False),
        )
        metadata.create_all(engine)
        with engine.begin() as connection:
            connection.execute(
                sa.text("CREATE UNIQUE INDEX idx_channels_name ON channels (name)")
            )

    def test_upgrade_permite_dos_canales_con_el_mismo_nombre(self) -> None:
        modulo = _cargar_modulo_migracion("0014_channels_name_not_unique.py")

        engine = create_engine("sqlite://")
        self._tabla_channels_con_indice_unico(engine)

        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                modulo.upgrade()
            connection.commit()

        tabla = sa.Table(
            "channels",
            sa.MetaData(),
            sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
            sa.Column("name", sa.String(120), nullable=False),
        )
        with engine.connect() as connection:
            connection.execute(
                sa.insert(tabla).values(id=uuid.uuid4(), name="Ventas Mayoristas")
            )
            connection.execute(
                sa.insert(tabla).values(id=uuid.uuid4(), name="Ventas Mayoristas")
            )
            connection.commit()
            filas = list(
                connection.execute(
                    sa.select(sa.func.count())
                    .select_from(tabla)
                    .where(tabla.c.name == "Ventas Mayoristas")
                )
            )
            assert filas[0][0] == 2

        engine.dispose()

    def test_indice_queda_no_unico_y_downgrade_lo_restaura(self) -> None:
        modulo = _cargar_modulo_migracion("0014_channels_name_not_unique.py")

        engine = create_engine("sqlite://")
        self._tabla_channels_con_indice_unico(engine)

        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                modulo.upgrade()
            connection.commit()
            indices = inspect(connection).get_indexes("channels")
            indice = next(i for i in indices if i["name"] == "idx_channels_name")
            assert not indice["unique"]

        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                modulo.downgrade()
            connection.commit()
            indices = inspect(connection).get_indexes("channels")
            indice = next(i for i in indices if i["name"] == "idx_channels_name")
            assert indice["unique"]

        engine.dispose()


class TestRawIngestionsChannelIdMigration:
    """`0015_raw_ingestions_channel_id.py` — `raw_ingestions.channel_id` es
    el FK real de origen (design D2), no `source_reference` suelto."""

    def _tablas(self) -> sa.MetaData:
        metadata = sa.MetaData()
        sa.Table(
            "channels",
            metadata,
            sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
            sa.Column("name", sa.String(120), nullable=False),
        )
        sa.Table(
            "raw_ingestions",
            metadata,
            sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
            sa.Column("source_type", sa.String(50), nullable=False),
        )
        return metadata

    def test_upgrade_agrega_la_columna_y_downgrade_la_elimina(self) -> None:
        modulo = _cargar_modulo_migracion("0015_raw_ingestions_channel_id.py")

        engine = create_engine("sqlite://")
        metadata = self._tablas()
        metadata.create_all(engine)

        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                modulo.upgrade()
            connection.commit()
            assert "channel_id" in {
                c["name"] for c in inspect(connection).get_columns("raw_ingestions")
            }

        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                modulo.downgrade()
            connection.commit()
            columnas = {c["name"] for c in inspect(connection).get_columns("raw_ingestions")}
            assert "channel_id" not in columnas
            assert "source_type" in columnas

        engine.dispose()

    def test_channel_id_es_nullable(self) -> None:
        """`raw_ingestions` es inmutable (`CLAUDE.md`): las filas ya
        existentes nunca reciben este dato de forma retroactiva."""
        modulo = _cargar_modulo_migracion("0015_raw_ingestions_channel_id.py")

        engine = create_engine("sqlite://")
        metadata = self._tablas()
        metadata.create_all(engine)

        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                modulo.upgrade()
            connection.commit()

            tabla_con_columna = sa.Table(
                "raw_ingestions",
                sa.MetaData(),
                sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
                sa.Column("source_type", sa.String(50), nullable=False),
                sa.Column("channel_id", sa.Uuid(as_uuid=True), nullable=True),
            )
            connection.execute(
                sa.insert(tabla_con_columna).values(
                    id=uuid.uuid4(), source_type="whatsapp_channel", channel_id=None
                )
            )
            connection.commit()

        engine.dispose()


class TestCatalogoGlosario:
    """CRUD del glosario del operador (slice 3c, `docs/spec-slice-3c.md`).

    Mismo patrón que `TestCatalogoFormatos`: alta, edición y borrado detrás
    de `require_operator`, redirect 303 con flash por querystring.
    """

    def test_la_pagina_muestra_la_entrada_activa_e_inactiva(
        self, client: TestClient, session: Session
    ) -> None:
        session.add(GlossaryEntry(term="4x10", definition="4 de 10 lb"))
        session.add(
            GlossaryEntry(term="6x5", definition="6 de 5 lb", active=False)
        )
        session.commit()

        response = client.get("/catalog")

        assert response.status_code == 200
        assert "4x10" in response.text
        assert "6x5" in response.text
        assert "desactivada" in response.text

    def test_alta_crea_una_entrada_activa(
        self, client: TestClient, session: Session
    ) -> None:
        response = client.post(
            "/catalog/glossary",
            data={"term": "4x10", "definition": "4 paquetes de 10 lb"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        entrada = session.execute(select(GlossaryEntry)).scalars().one()
        assert (entrada.term, entrada.definition, entrada.active) == (
            "4x10",
            "4 paquetes de 10 lb",
            True,
        )

    def test_alta_con_termino_vacio_se_rechaza(
        self, client: TestClient, session: Session
    ) -> None:
        response = client.post(
            "/catalog/glossary",
            data={"term": "   ", "definition": "4 paquetes de 10 lb"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert "error=" in response.headers["location"]
        assert session.execute(select(GlossaryEntry)).scalars().first() is None

    def test_alta_con_definicion_vacia_se_rechaza(
        self, client: TestClient, session: Session
    ) -> None:
        response = client.post(
            "/catalog/glossary",
            data={"term": "4x10", "definition": ""},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert "error=" in response.headers["location"]
        assert session.execute(select(GlossaryEntry)).scalars().first() is None

    def test_editar_una_entrada_la_corrige_en_el_lugar(
        self, client: TestClient, session: Session
    ) -> None:
        entrada = GlossaryEntry(term="4x10", definition="4 de 10 lb")
        session.add(entrada)
        session.commit()

        response = client.post(
            f"/catalog/glossary/{entrada.id}/edit",
            data={"term": "4x10", "definition": "4 paquetes de 10 lb, caja 40 lb"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert "error=" not in response.headers["location"]
        session.expire_all()
        assert len(list(session.execute(select(GlossaryEntry)).scalars())) == 1
        assert (
            session.get(GlossaryEntry, entrada.id).definition
            == "4 paquetes de 10 lb, caja 40 lb"
        )

    def test_desactivar_una_entrada_no_la_borra(
        self, client: TestClient, session: Session
    ) -> None:
        entrada = GlossaryEntry(term="4x10", definition="4 de 10 lb")
        session.add(entrada)
        session.commit()

        response = client.post(
            f"/catalog/glossary/{entrada.id}/toggle", follow_redirects=False
        )

        assert response.status_code == 303
        session.expire_all()
        assert session.get(GlossaryEntry, entrada.id).active is False

    def test_borrar_una_entrada_la_elimina(
        self, client: TestClient, session: Session
    ) -> None:
        entrada = GlossaryEntry(term="4x10", definition="4 de 10 lb")
        session.add(entrada)
        session.commit()

        response = client.post(
            f"/catalog/glossary/{entrada.id}/delete", follow_redirects=False
        )

        assert response.status_code == 303
        assert session.execute(select(GlossaryEntry)).scalars().first() is None
