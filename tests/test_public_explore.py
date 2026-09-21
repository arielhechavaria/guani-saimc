"""Vista «Explorar» (P2, 2026-09-21): el esqueleto del prototipo C aprobado —
panel izquierdo, pestañas por producto con la tabla en el centro y el detalle a
la derecha— servido por el servidor y legible sin JavaScript.

Lo que Python controla y estos casos protegen: que `?vista=explorar` exista y
sea la tercera opción de un selector que ya tenía dos, que cada producto abierto
sea una pestaña, que la tabla lleve las provincias con dato de mayor a menor
mediana, que el mapa traiga las 16 provincias y que el detalle de la derecha
diga lo de la provincia elegida. Las cifras las cubre `test_explore_view.py`;
acá se afirma lo que la plantilla hace con ellas. El aspecto se compara contra
el prototipo, a ojo y con capturas lado a lado.

Las fechas van relativas a hoy: con fechas duras el calendario pone rojo un test
que estaba bien.
"""

import re
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
from saimc.web.product_nav import VISTA_CHOICES, resolve_vista


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    def _get_db_override() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_db] = _get_db_override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def datos(session: Session) -> None:
    """Arroz en La Habana (300 de mediana, 2 anuncios) y Santiago (400, 1),
    Pollo en La Habana, Aceite sin ningún anuncio y Guantánamo sin nada."""
    arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True)
    pollo = Product(name="Pollo", category="carnes", unit="lb", aliases=[], is_pilot=True)
    aceite = Product(name="Aceite", category="grasas", unit="lb", aliases=[], is_pilot=True)
    habana = Province(code="hab", name="La Habana")
    santiago = Province(code="scu", name="Santiago de Cuba")
    guantanamo = Province(code="gtm", name="Guantánamo")
    session.add_all([arroz, pollo, aceite, habana, santiago, guantanamo])
    session.commit()
    origen = RawIngestion(
        source_type=SourceType.INBOX_TEXT,
        file_path="raw/e.dat",
        content_hash="hash-explorar",
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


def _html(client: TestClient, query: str) -> str:
    return str(client.get("/?vista=explorar" + query).text)


def _bloque(texto: str, inicio: str, fin: str) -> str:
    """El HTML entre dos marcas: cada columna de la vista es un bloque."""
    return texto.split(inicio)[1].split(fin)[0]


def _izquierda(texto: str) -> str:
    return _bloque(texto, 'class="ph-ex-left"', 'class="ph-ex-center"')


def _centro(texto: str) -> str:
    return _bloque(texto, 'class="ph-ex-center"', 'class="ph-ex-right"')


def _derecha(texto: str) -> str:
    return texto.split('class="ph-ex-right"')[1].split("</aside>")[0]


class TestVista:
    def test_explorar_es_la_tercera_vista(self) -> None:
        assert VISTA_CHOICES == ("mapa", "tablero", "explorar")
        assert resolve_vista("explorar") == "explorar"

    def test_una_vista_rara_sigue_cayendo_al_mapa(self) -> None:
        assert resolve_vista("dashboard") == "mapa"

    def test_la_ruta_responde_sin_credenciales(self, client: TestClient, datos: None) -> None:
        assert client.get("/?vista=explorar").status_code == 200

    def test_la_cookie_de_vista_tambien_abre_explorar(self, client: TestClient, datos: None) -> None:
        client.cookies.set("vista", "explorar")

        assert 'class="ph-ex"' in client.get("/").text

    def test_no_expone_la_herramienta_del_operador(self, client: TestClient, datos: None) -> None:
        texto = _html(client, "&productos=arroz")

        for enlace in ('href="/curation"', 'href="/control"', 'href="/catalog"'):
            assert enlace not in texto


class TestSelectorDeVistas:
    def test_el_panel_izquierdo_lleva_las_tres_vistas_con_explorar_marcada(
        self, client: TestClient, datos: None
    ) -> None:
        nav = _bloque(_izquierda(_html(client, "&productos=arroz")), '<nav class="ph-ex-views"', "</nav>")

        assert 'data-vista="mapa"' in nav
        assert 'data-vista="tablero"' in nav
        assert 'data-vista="explorar"' in nav
        assert re.search(r'data-vista="explorar"[^>]*aria-current="true"', nav)

    def test_cambiar_de_vista_conserva_los_productos_elegidos(self, client: TestClient, datos: None) -> None:
        nav = _bloque(
            _izquierda(_html(client, "&productos=arroz,pollo")), '<nav class="ph-ex-views"', "</nav>"
        )

        enlaces = re.findall(r"<a [^>]*>", nav)
        tablero = next(a for a in enlaces if 'data-vista="tablero"' in a)

        assert "productos=arroz,pollo" in tablero

    def test_el_mapa_ofrece_ir_a_explorar(self, client: TestClient, datos: None) -> None:
        assert 'data-vista="explorar"' in client.get("/?productos=arroz").text

    def test_el_tablero_ofrece_ir_a_explorar(self, client: TestClient, datos: None) -> None:
        assert 'data-vista="explorar"' in client.get("/?vista=tablero").text


class TestPanelIzquierdo:
    def test_lista_todos_los_productos_piloto_con_su_muestra(self, client: TestClient, datos: None) -> None:
        izq = _izquierda(_html(client, "&productos=arroz"))

        for nombre in ("Arroz", "Pollo", "Aceite"):
            assert f"<i>{nombre}</i>" in izq

    def test_los_productos_abiertos_estan_marcados_y_los_demas_no(
        self, client: TestClient, datos: None
    ) -> None:
        productos = _bloque(_izquierda(_html(client, "&productos=arroz")), "<h5>Productos</h5>", "</section>")

        assert productos.count('aria-pressed="true"') == 1
        assert productos.count('aria-pressed="false"') == 2

    def test_marcar_un_producto_lo_agrega_a_la_url_y_desmarcarlo_lo_saca(
        self, client: TestClient, datos: None
    ) -> None:
        productos = _bloque(_izquierda(_html(client, "&productos=arroz")), "<h5>Productos</h5>", "</section>")

        assert "productos=arroz,pollo&amp;" in productos
        assert "vista=explorar" in productos

    def test_las_pestanas_del_panel_son_filtros_y_columnas(self, client: TestClient, datos: None) -> None:
        izq = _izquierda(_html(client, "&productos=arroz"))

        assert 'role="tab"' in izq
        assert ">Filtros</button>" in izq
        assert ">Columnas</button>" in izq

    def test_los_filtros_son_los_del_producto_abierto(self, client: TestClient, datos: None) -> None:
        izq = _izquierda(_html(client, "&productos=arroz"))

        assert "Filtros de Arroz" in izq
        assert "Santiago de Cuba" in izq
        assert "Guantánamo" not in izq

    def test_trae_el_enlace_para_descargar_el_csv(self, client: TestClient, datos: None) -> None:
        assert 'href="/datos.csv?' in _izquierda(_html(client, "&productos=arroz"))


class TestPestanias:
    def test_sin_productos_no_hay_pestanias_y_se_invita_a_elegir(
        self, client: TestClient, datos: None
    ) -> None:
        centro = _centro(_html(client, ""))

        assert 'role="tab"' not in centro
        assert "Elegí un producto para empezar" in centro
        assert "productos=arroz" in centro

    def test_una_pestania_por_producto_abierto_con_su_muestra(self, client: TestClient, datos: None) -> None:
        centro = _centro(_html(client, "&productos=arroz,pollo"))

        assert centro.count('role="tab"') == 2
        assert ">Arroz<small>3</small>" in centro
        assert ">Pollo<small>1</small>" in centro

    def test_la_primera_esta_abierta(self, client: TestClient, datos: None) -> None:
        centro = _centro(_html(client, "&productos=arroz,pollo"))

        assert centro.count('aria-selected="true"') == 1
        abierta = next(a for a in re.findall(r"<a [^>]*aria-selected=\"true\"[^>]*>.*?</a>", centro))
        assert abierta.split(">", 1)[1].startswith("Arroz")
        assert "ph-ex-pane" in centro

    def test_tocar_otra_pestania_la_pone_primera(self, client: TestClient, datos: None) -> None:
        """Sin JavaScript, cambiar de pestaña es navegar con ese producto
        primero; P4 lo hace en el cliente."""
        centro = _centro(_html(client, "&productos=arroz,pollo"))

        assert re.search(r'href="/\?productos=pollo,arroz&amp;[^"]*vista=explorar"[^>]*>Pollo', centro)

    def test_la_x_quita_solo_ese_producto(self, client: TestClient, datos: None) -> None:
        centro = _centro(_html(client, "&productos=arroz,pollo"))

        assert re.search(r'href="/\?productos=pollo&amp;[^"]*"[^>]*aria-label="Cerrar Arroz"', centro)

    def test_el_mas_ofrece_los_productos_que_faltan(self, client: TestClient, datos: None) -> None:
        centro = _centro(_html(client, "&productos=arroz,pollo"))
        mas = _bloque(centro, '<details class="ph-ex-add"', "</details>")

        assert "Aceite" in mas
        assert "Arroz" not in mas

    def test_con_todos_abiertos_no_hay_mas(self, client: TestClient, datos: None) -> None:
        assert "ph-ex-add" not in _centro(_html(client, "&productos=arroz,pollo,aceite"))


class TestTabla:
    def test_las_provincias_con_dato_van_de_mayor_a_menor_mediana(
        self, client: TestClient, datos: None
    ) -> None:
        tabla = _bloque(_centro(_html(client, "&productos=arroz")), "<tbody", "</tbody>")

        assert re.findall(r'<tr class="ph-ex-row[^"]*"[^>]*data-slug="([^"]+)"', tabla) == [
            "santiago-de-cuba",
            "la-habana",
        ]

    def test_cada_fila_trae_mediana_cambio_tendencia_muestra_y_ultimo(
        self, client: TestClient, datos: None
    ) -> None:
        fila = _bloque(_centro(_html(client, "&productos=arroz")), 'data-slug="la-habana"', "</tr>")

        assert ">300<" in fila
        assert 'class="ph-ex-spark"' in fila
        assert ">2<" in fila
        assert "hace 5 d" in fila

    def test_la_cabecera_marca_la_columna_por_la_que_va_ordenada(
        self, client: TestClient, datos: None
    ) -> None:
        cabecera = _bloque(_centro(_html(client, "&productos=arroz")), "<thead", "</thead>")

        assert 'aria-sort="descending"' in cabecera
        assert ">Mediana<" in cabecera

    def test_el_titular_dice_provincias_y_anuncios_y_la_unidad(self, client: TestClient, datos: None) -> None:
        centro = _centro(_html(client, "&productos=arroz"))

        assert "2 provincias · 3 anuncios" in centro
        assert "CUP por libra" in centro

    def test_las_provincias_sin_anuncios_se_nombran_como_ausencia(
        self, client: TestClient, datos: None
    ) -> None:
        centro = _centro(_html(client, "&productos=arroz"))

        assert "Sin anuncios en la ventana" in centro
        assert "Guantánamo" in centro

    def test_un_producto_sin_anuncios_lo_dice_y_no_dibuja_una_tabla_vacia(
        self, client: TestClient, datos: None
    ) -> None:
        centro = _centro(_html(client, "&productos=aceite"))

        assert "Ningún anuncio de Aceite" in centro
        assert "<tbody" not in centro

    def test_el_cambio_no_lleva_flecha_ni_color(self, client: TestClient, datos: None) -> None:
        """§12.6: una baja es buena noticia para quien compra y mala para
        quien vende."""
        centro = _centro(_html(client, "&productos=arroz"))

        for señal in ("▲", "▼", "arrow_upward", "arrow_downward", "is-sube", "is-baja"):
            assert señal not in centro
        assert "sin flecha ni color" in centro


class TestPanelDerecho:
    def test_el_mapa_trae_las_dieciseis_provincias(self, client: TestClient, datos: None) -> None:
        der = _derecha(_html(client, "&productos=arroz"))

        assert len(re.findall(r'<path class="ph-ex-prov[^"]*"[^>]*data-slug="', der)) == 16

    def test_las_provincias_con_dato_llevan_su_tinte_y_las_demas_no(
        self, client: TestClient, datos: None
    ) -> None:
        der = _derecha(_html(client, "&productos=arroz"))

        assert re.search(r'data-slug="santiago-de-cuba"[^>]*style="--t: 1(\.0)?"', der)
        assert re.search(r'data-slug="la-habana"[^>]*style="--t: 0(\.0)?"', der)
        assert re.search(r'ph-ex-prov is-nodata"[^>]*data-slug="guantanamo"', der)

    def test_por_defecto_el_detalle_es_el_de_la_provincia_de_mayor_mediana(
        self, client: TestClient, datos: None
    ) -> None:
        der = _derecha(_html(client, "&productos=arroz"))

        assert ">Santiago de Cuba<" in der
        assert re.search(r'ph-ex-prov is-selected"[^>]*data-slug="santiago-de-cuba"', der)

    def test_la_provincia_de_la_url_manda(self, client: TestClient, datos: None) -> None:
        der = _derecha(_html(client, "&productos=arroz&provincia=la-habana"))

        assert re.search(r'ph-ex-prov is-selected"[^>]*data-slug="la-habana"', der)
        assert ">La Habana<" in der

    def test_el_detalle_dice_mediana_cambio_y_anuncios(self, client: TestClient, datos: None) -> None:
        der = _derecha(_html(client, "&productos=arroz&provincia=la-habana"))

        assert ">300<" in der
        assert "Cambio" in der
        assert "vs 30 días" in der

    def test_el_detalle_trae_el_grafico_semanal(self, client: TestClient, datos: None) -> None:
        der = _derecha(_html(client, "&productos=arroz&provincia=la-habana"))

        assert 'class="ph-ex-chart"' in der
        assert "13 semanas" in der

    def test_el_detalle_lista_los_anuncios_con_su_enlace(self, client: TestClient, datos: None) -> None:
        der = _derecha(_html(client, "&productos=arroz&provincia=la-habana"))

        assert "Anuncios detrás de la cifra" in der
        assert 'href="/anuncio/' in der
        assert ">ver anuncio<" in der

    def test_sin_producto_abierto_el_detalle_queda_en_blanco_pero_con_su_estructura(
        self, client: TestClient, datos: None
    ) -> None:
        der = _derecha(_html(client, ""))

        assert "Detalle" in der
        assert len(re.findall(r'<path class="ph-ex-prov', der)) == 16
        assert "ph-ex-prov is-selected" not in der
