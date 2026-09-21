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
from html.parser import HTMLParser

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
    return texto.split('class="ph-side ph-tabbed"')[1].split("</aside>")[0]


class _Ancestros(HTMLParser):
    """Las clases de cada ancestro del primer elemento con la clase pedida."""

    VACIOS = frozenset({"br", "hr", "img", "input", "meta", "link", "path", "circle", "rect", "line", "use"})

    def __init__(self, clase: str) -> None:
        super().__init__()
        self.clase = clase
        self.pila: list[str] = []
        self.resultado: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list) -> None:
        clases = dict(attrs).get("class") or ""
        if self.resultado is None and self.clase in clases.split():
            self.resultado = [c for c in self.pila if c]
        if tag not in self.VACIOS:
            self.pila.append(clases)

    def handle_endtag(self, tag: str) -> None:
        if tag not in self.VACIOS and self.pila:
            self.pila.pop()


def _clases_de_los_ancestros(texto: str, clase: str) -> list[str]:
    """Solo desde `.ph-canvas` hacia adentro, para que el resultado no dependa
    del shell de la página."""
    analizador = _Ancestros(clase)
    analizador.feed(texto)
    ancestros = analizador.resultado or []
    lienzo = "ph-canvas has-side"
    return ancestros[ancestros.index(lienzo) :] if lienzo in ancestros else ancestros


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
        assert 'class="ph-side ph-tabbed"' not in client.get("/").text

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


class TestPanelLateralAnclado:
    """Tras el prototipo B el operador pidió dejar de flotar la ventana
    (2026-09-21): es una columna anclada a la derecha, a altura completa, que
    encoge el mapa; en angosto pasa a hoja inferior. Nada de arrastrar."""

    def test_con_producto_el_lienzo_reserva_la_columna(self, client: TestClient, datos: None) -> None:
        assert 'class="ph-canvas has-side"' in client.get("/?productos=arroz").text

    def test_sin_producto_el_mapa_ocupa_todo_el_ancho(self, client: TestClient, datos: None) -> None:
        assert 'class="ph-canvas has-side"' not in client.get("/").text

    def test_no_es_una_ventana_arrastrable(self, client: TestClient, datos: None) -> None:
        """El script de arrastre engancha todo `[data-win]`; el panel anclado
        no lo lleva, ni agarradores de tamaño, ni minimizar/expandir."""
        panel = _ventana(client.get("/?productos=arroz").text)

        for marca in ("data-win", "data-win-handle", "data-win-grip", "data-win-min", "data-win-max"):
            assert marca not in panel

    def test_se_puede_plegar_y_hay_con_que_volver_a_abrirlo(self, client: TestClient, datos: None) -> None:
        texto = client.get("/?productos=arroz").text

        # Plegar (en el panel) y reabrir (en el borde, visible solo plegado).
        assert 'class="ph-side-toggle" data-side-toggle aria-expanded="true"' in texto
        assert 'class="ph-side-open" data-side-toggle aria-expanded="false"' in texto

    def test_en_angosto_la_hoja_se_expande_con_un_boton_y_no_solo_arrastrando(
        self, client: TestClient, datos: None
    ) -> None:
        """Material Design 3: la manija de una hoja necesita una alternativa
        a arrastrar."""
        panel = _ventana(client.get("/?productos=arroz").text)

        assert "data-side-expand" in panel
        assert ">Ver todas<" in panel

    def test_el_panel_no_vive_en_el_muelle_de_ventanas(self, client: TestClient, datos: None) -> None:
        """El muelle sigue siendo de las ventanas del menú, que flotan: el
        panel es hijo directo del lienzo, no del muelle."""
        assert _clases_de_los_ancestros(client.get("/?productos=arroz").text, "ph-side") == [
            "ph-canvas has-side"
        ]

    def test_con_provincia_elegida_su_detalle_llega_abierto(self, client: TestClient, datos: None) -> None:
        panel = _ventana(client.get("/?productos=arroz&provincia=la-habana").text)

        assert '<details class="ph-pane-mas" open>' in panel

    def test_sin_provincia_elegida_el_detalle_llega_cerrado(self, client: TestClient, datos: None) -> None:
        assert '<details class="ph-pane-mas">' in _ventana(client.get("/?productos=arroz").text)
