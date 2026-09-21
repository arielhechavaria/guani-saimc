"""Ventana con pestañas por producto y capa «precio» del mapa (prototipo B,
2026-09-21).

Lo que Python controla y estos casos protegen: que elegir un producto abra una
ventana con una pestaña por producto y una fila por provincia con dato, que la
provincia sin dato se diga como ausencia y no como fila vacía (§9.0), que el
mapa reciba el tinte de cada provincia en `#ph-map-data` y que la capa por
defecto pase a ser «precio» solo cuando hay un producto abierto. El color final
de cada polígono lo pone `estiloDeProvincia()` en JS, que pytest no ejecuta.

Las fechas van relativas a hoy (lección de `test_public_map.py`): con fechas
duras, el calendario pone rojo un test que estaba bien.
"""

import json
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from saimc.models import (
    ContentType,
    Currency,
    PriceHistory,
    Product,
    Province,
    RawIngestion,
    SaleFormat,
    SourceType,
)
from saimc.models.raw_ingestion import ProcessingStatus
from saimc.web.app import app
from saimc.web.deps import get_db


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    def _get_db_override() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_db] = _get_db_override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _map_data(texto: str) -> dict[str, dict]:
    bloque = texto.split('id="ph-map-data">')[1].split("</script>")[0]
    return {fila["slug"]: fila for fila in json.loads(bloque)}


def _ventana(texto: str) -> str:
    """El HTML de la ventana con pestañas, sin el resto de la página."""
    return texto.split('class="ph-fpanel ph-tabbed"')[1].split("</section>")[0]


@pytest.fixture
def datos(session: Session) -> None:
    """Arroz y Pollo en La Habana; Arroz también en Santiago; Guantánamo sin
    ningún anuncio."""
    arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True)
    pollo = Product(name="Pollo", category="carnes", unit="lb", aliases=[], is_pilot=True)
    habana = Province(code="hab", name="La Habana")
    santiago = Province(code="scu", name="Santiago de Cuba")
    guantanamo = Province(code="gtm", name="Guantánamo")
    session.add_all([arroz, pollo, habana, santiago, guantanamo])
    session.commit()
    origen = RawIngestion(
        source_type=SourceType.INBOX_TEXT,
        file_path="raw/h.dat",
        content_hash="hash-precio",
        content_type=ContentType.TEXT,
        processing_status=ProcessingStatus.CURATED,
        captured_at=datetime(2026, 8, 10, tzinfo=UTC),
    )
    session.add(origen)
    session.flush()
    hoy = date.today()

    def precio(producto: Product, provincia: Province, hace: int, valor: str) -> None:
        session.add(
            PriceHistory(
                product_id=producto.id,
                province_id=provincia.id,
                price=Decimal(valor),
                currency=Currency.CUP.value,
                source_id=origen.id,
                unit="lb",
                sale_format=SaleFormat.RETAIL.value,
                quantity=Decimal("1"),
                published_date=hoy - timedelta(days=hace),
            )
        )

    precio(arroz, habana, 12, "280")
    precio(arroz, habana, 5, "320")
    precio(arroz, santiago, 3, "400")
    precio(pollo, habana, 4, "700")
    session.commit()


class TestCapaPorDefecto:
    def test_con_un_producto_el_mapa_se_pinta_por_precio(self, client: TestClient, datos: None) -> None:
        assert 'data-capa="precio"' in client.get("/?productos=arroz").text

    def test_sin_productos_no_hay_de_que_pintar_y_queda_en_publicacion(
        self, client: TestClient, datos: None
    ) -> None:
        assert 'data-capa="publicacion"' in client.get("/").text

    def test_quien_pide_publicacion_la_conserva_aunque_haya_producto(
        self, client: TestClient, datos: None
    ) -> None:
        assert 'data-capa="publicacion"' in client.get("/?productos=arroz&capa=publicacion").text

    def test_el_selector_de_capas_ofrece_precio_solo_con_producto(
        self, client: TestClient, datos: None
    ) -> None:
        con = client.get("/?productos=arroz").text
        sin = client.get("/").text

        assert ">Precio</a>" in con
        assert ">Precio</a>" not in sin

    def test_la_leyenda_de_precio_dice_que_es_relativa_y_no_que_una_sea_buena(
        self, client: TestClient, datos: None
    ) -> None:
        texto = client.get("/?productos=arroz").text

        assert "Más barata" in texto
        assert "entre las provincias con dato" in texto


class TestDatosDelMapa:
    def test_cada_provincia_con_dato_lleva_su_tinte_y_su_cifra(self, client: TestClient, datos: None) -> None:
        filas = _map_data(client.get("/?productos=arroz").text)

        assert filas["la-habana"]["precio"]["arroz"] == {"t": 0.0, "m": "300 CUP/lb"}
        assert filas["santiago-de-cuba"]["precio"]["arroz"] == {"t": 1.0, "m": "400 CUP/lb"}

    def test_la_provincia_sin_dato_no_trae_tinte(self, client: TestClient, datos: None) -> None:
        filas = _map_data(client.get("/?productos=arroz").text)

        assert filas["guantanamo"]["precio"] == {}

    def test_lleva_cada_producto_activo(self, client: TestClient, datos: None) -> None:
        filas = _map_data(client.get("/?productos=arroz,pollo").text)

        assert set(filas["la-habana"]["precio"]) == {"arroz", "pollo"}

    def test_sin_productos_no_hay_tintes(self, client: TestClient, datos: None) -> None:
        assert all(f["precio"] == {} for f in _map_data(client.get("/").text).values())


class TestVentanaConPestanias:
    def test_sin_productos_no_hay_ventana(self, client: TestClient, datos: None) -> None:
        assert 'class="ph-fpanel ph-tabbed"' not in client.get("/").text

    def test_una_pestania_por_producto_activo(self, client: TestClient, datos: None) -> None:
        ventana = _ventana(client.get("/?productos=arroz,pollo").text)

        assert ventana.count('role="tab"') == 2
        assert 'data-product="arroz"' in ventana
        assert 'data-product="pollo"' in ventana

    def test_la_primera_pestania_arranca_abierta_y_las_demas_ocultas(
        self, client: TestClient, datos: None
    ) -> None:
        ventana = _ventana(client.get("/?productos=arroz,pollo").text)

        assert ventana.count('aria-selected="true"') == 1
        assert ventana.count('role="tabpanel"') == 2
        assert ventana.count(" hidden>") == 1

    def test_la_tabla_lista_las_provincias_con_dato_con_su_mediana(
        self, client: TestClient, datos: None
    ) -> None:
        ventana = _ventana(client.get("/?productos=arroz").text)

        assert "Santiago de Cuba" in ventana
        assert "La Habana" in ventana
        assert ">400<" in ventana
        assert ">300<" in ventana

    def test_la_provincia_sin_dato_se_nombra_al_pie_y_no_es_una_fila(
        self, client: TestClient, datos: None
    ) -> None:
        ventana = _ventana(client.get("/?productos=arroz").text)

        assert "Sin anuncios en la ventana" in ventana
        assert "Guantánamo" in ventana
        assert ventana.count('data-slug="guantanamo"') == 0

    def test_cada_fila_trae_los_tres_dibujos_de_la_sparkline(self, client: TestClient, datos: None) -> None:
        ventana = _ventana(client.get("/?productos=arroz").text)

        assert 'class="ph-sp-line"' in ventana
        assert 'class="ph-sp-area"' in ventana
        assert 'class="ph-sp-cols"' in ventana

    def test_el_cambio_no_lleva_flecha_ni_color(self, client: TestClient, datos: None) -> None:
        """§12.6: una baja es buena noticia para quien compra y mala para
        quien vende."""
        ventana = _ventana(client.get("/?productos=arroz").text)

        for señal in ("▲", "▼", "arrow_upward", "arrow_downward", "is-sube", "is-baja"):
            assert señal not in ventana

    def test_cada_pestania_tiene_su_x_que_quita_solo_ese_producto(
        self, client: TestClient, datos: None
    ) -> None:
        ventana = _ventana(client.get("/?productos=arroz,pollo").text)

        assert 'href="/?productos=pollo&' in ventana
        assert 'href="/?productos=arroz&' in ventana

    def test_el_nombre_de_la_fila_lleva_a_esa_provincia(self, client: TestClient, datos: None) -> None:
        ventana = _ventana(client.get("/?productos=arroz").text)

        assert "provincia=santiago-de-cuba" in ventana

    def test_el_detalle_anterior_del_producto_sigue_en_la_pestania(
        self, client: TestClient, datos: None
    ) -> None:
        """Nada de lo que el panel decía se pierde: pasa a un desplegable."""
        ventana = _ventana(client.get("/?productos=arroz").text)

        assert "ph-cifra" in ventana
        assert "Vista simple para compartir" in ventana

    def test_el_titular_es_el_rango_entre_provincias_y_no_una_mediana_nacional(
        self, client: TestClient, datos: None
    ) -> None:
        ventana = _ventana(client.get("/?productos=arroz").text)

        assert "nacional" not in ventana.lower()
        assert ">300<" in ventana
        assert ">400<" in ventana
