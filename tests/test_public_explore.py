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
    ProductFormat,
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


@pytest.fixture
def arbol(session: Session, datos: None) -> None:
    """`datos` más un formato: en La Habana, Arroz en «Saco de 100 lb» con seis
    anuncios (hace 1 a 6 días) al lado de los dos de la libra. Son más de los
    cuatro que la tabla enseña por formato."""
    arroz = session.query(Product).filter_by(name="Arroz").one()
    habana = session.query(Province).filter_by(code="hab").one()
    origen = session.query(RawIngestion).one()
    saco = ProductFormat(product_id=arroz.id, packaging="saco", quantity=Decimal("100"), unit="lb")
    session.add(saco)
    session.flush()
    for hace in range(1, 7):
        session.add(
            PriceHistory(
                product_id=arroz.id,
                province_id=habana.id,
                price=Decimal("3000"),
                currency=Currency.CUP.value,
                source_id=origen.id,
                unit="lb",
                sale_format=SaleFormat.RETAIL.value,
                quantity=Decimal("1"),
                format_id=saco.id,
                published_date=date.today() - timedelta(days=hace),
            )
        )
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


def _grupo(texto: str, clave: str) -> str:
    """El grupo de una provincia: su fila, las de sus formatos y las de los
    anuncios, hasta el cierre de su `<tbody>`."""
    return _bloque(texto, f'<tbody class="ph-ex-grp" data-key="{clave}"', "</tbody>")


def _filas(grupo: str) -> list[str]:
    return re.findall(r"<tr [^>]*>", grupo)


def _derecha(texto: str) -> str:
    return texto.split('class="ph-ex-right"')[1].split("</aside>")[0]


class TestVista:
    def test_explorar_es_la_tercera_vista(self) -> None:
        assert VISTA_CHOICES == ("mapa", "tablero", "explorar")
        assert resolve_vista("explorar") == "explorar"

    def test_una_vista_rara_cae_al_tablero(self) -> None:
        assert resolve_vista("dashboard") == "tablero"

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

    def test_tocar_otra_pestania_no_reordena_solo_cambia_la_activa(
        self, client: TestClient, datos: None
    ) -> None:
        """P4: la lista de pestañas queda en el orden en que se abrieron; sin
        JavaScript, tocar una navega con `?pestana=` y `?productos=` intacto."""
        centro = _centro(_html(client, "&productos=arroz,pollo"))

        assert re.search(r'href="/\?productos=arroz,pollo&amp;pestana=pollo&amp;[^"]*"[^>]*>Pollo', centro)
        assert "productos=pollo,arroz" not in centro

    def test_elegir_pestana_explicita_marca_esa_como_activa(self, client: TestClient, datos: None) -> None:
        centro = _centro(_html(client, "&productos=arroz,pollo&pestana=pollo"))

        assert centro.count('aria-selected="true"') == 1
        abierta = next(a for a in re.findall(r"<a [^>]*aria-selected=\"true\"[^>]*>.*?</a>", centro))
        assert abierta.split(">", 1)[1].startswith("Pollo")

    def test_la_x_quita_solo_ese_producto(self, client: TestClient, datos: None) -> None:
        centro = _centro(_html(client, "&productos=arroz,pollo"))

        assert re.search(r'href="/\?productos=pollo&amp;[^"]*"[^>]*aria-label="Cerrar Arroz"', centro)

    def test_cerrar_la_activa_activa_a_su_vecina_de_verdad_no_solo_en_la_url(
        self, client: TestClient, datos: None
    ) -> None:
        """Sigue el enlace de cerrar Arroz (la activa por defecto) y confirma
        que el servidor responde con Pollo ya activa."""
        centro = _centro(_html(client, "&productos=arroz,pollo"))
        cerrar = re.search(r'href="([^"]+)"[^>]*aria-label="Cerrar Arroz"', centro)
        assert cerrar is not None

        despues = _centro(client.get(cerrar.group(1).replace("&amp;", "&")).text)

        assert "productos=pollo" in despues
        (abierta,) = re.findall(r"<a [^>]*aria-selected=\"true\"[^>]*>.*?</a>", despues)
        assert abierta.split(">", 1)[1].startswith("Pollo")

    def test_el_mas_ofrece_los_productos_que_faltan(self, client: TestClient, datos: None) -> None:
        centro = _centro(_html(client, "&productos=arroz,pollo"))
        mas = _bloque(centro, '<details class="ph-ex-add"', "</details>")

        assert "Aceite" in mas
        assert "Arroz" not in mas

    def test_con_todos_abiertos_no_hay_mas(self, client: TestClient, datos: None) -> None:
        assert "ph-ex-add" not in _centro(_html(client, "&productos=arroz,pollo,aceite"))


class TestHTMX:
    """P4 (2026-09-25): abrir, cerrar y cambiar de pestaña navegan por htmx,
    reusando `#ph-live` —el mismo mecanismo que ya usa `public_home.html`—
    en vez de una recarga completa. Sin JavaScript siguen siendo el `href`
    de toda la vida; acá solo se verifica que el cableado esté."""

    def test_el_contenedor_raiz_es_la_zona_viva_de_htmx(self, client: TestClient, datos: None) -> None:
        texto = _html(client, "&productos=arroz")

        assert 'id="ph-live"' in texto
        assert 'hx-target="#ph-live"' in texto
        assert 'hx-select="#ph-live"' in texto
        assert 'hx-swap="outerHTML"' in texto
        assert 'hx-push-url="true"' in texto
        assert 'hx-indicator="#ph-loading"' in texto
        assert 'id="ph-loading"' in texto

    def test_carga_htmx(self, client: TestClient, datos: None) -> None:
        assert "/static/htmx.min.js" in _html(client, "")

    def test_marcar_un_producto_lleva_hx_get_igual_al_href(self, client: TestClient, datos: None) -> None:
        izq = _izquierda(_html(client, "&productos=arroz"))
        opt = re.search(
            r'<a class="ph-ex-opt[^"]*" href="([^"]+)" hx-get="([^"]+)"[^>]*><span class="ph-ex-box"'
            r"[^>]*></span><i>Pollo</i>",
            izq,
        )

        assert opt is not None
        assert opt.group(1) == opt.group(2)

    def test_las_pestanias_y_su_x_llevan_hx_get_igual_al_href(self, client: TestClient, datos: None) -> None:
        centro = _centro(_html(client, "&productos=arroz,pollo"))

        assert re.search(r'class="ph-ex-tab-btn"[^>]*href="([^"]+)" hx-get="\1"', centro)
        assert re.search(r'class="ph-ex-tab-x" href="([^"]+)" hx-get="\1"', centro)

    def test_el_mas_lleva_hx_get(self, client: TestClient, datos: None) -> None:
        mas = _bloque(
            _centro(_html(client, "&productos=arroz,pollo")), '<details class="ph-ex-add"', "</details>"
        )

        assert re.search(r'class="ph-ex-opt" href="([^"]+)" hx-get="\1"', mas)

    def test_las_pastillas_del_estado_vacio_llevan_hx_get(self, client: TestClient, datos: None) -> None:
        vacio = _centro(_html(client, ""))

        assert re.search(r'class="ph-ex-pill" href="([^"]+)" hx-get="\1"', vacio)

    def test_el_cambio_de_vista_no_lleva_hx_get(self, client: TestClient, datos: None) -> None:
        """Fuera de alcance: el selector Mapa/Tablero/Explorar queda como
        estaba en P2, con navegación completa."""
        assert not re.search(r"js-vista-link[^>]*hx-get", _html(client, "&productos=arroz"))

    def test_la_fila_de_provincia_y_el_mapa_llevan_hx_get_igual_al_href(
        self, client: TestClient, datos: None
    ) -> None:
        """P6: elegir una provincia, desde la fila o desde el mapa, ya no
        recarga la página entera."""
        texto = _html(client, "&productos=arroz")

        assert re.search(r'class="ph-ex-nmt" href="([^"]+)" hx-get="\1"', texto)
        assert re.search(r'class="ph-ex-provlink" href="([^"]+)" hx-get="\1"', texto)


class TestTabla:
    def test_las_provincias_con_dato_van_de_mayor_a_menor_mediana(
        self, client: TestClient, datos: None
    ) -> None:
        centro = _centro(_html(client, "&productos=arroz"))

        assert re.findall(r'<tbody class="ph-ex-grp" data-key="([^"]+)"', centro) == [
            "santiago-de-cuba",
            "la-habana",
        ]

    def test_cada_fila_trae_mediana_cambio_tendencia_muestra_y_ultimo(
        self, client: TestClient, datos: None
    ) -> None:
        fila = _grupo(_centro(_html(client, "&productos=arroz")), "la-habana").split("</tr>")[0]

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


class TestArbol:
    """P3: cada provincia es un grupo que se abre en sus formatos y estos en
    sus anuncios (prototipo C: filas lv0 / lv1 / lv2)."""

    def test_una_provincia_es_un_grupo_con_su_fila_y_las_de_sus_formatos(
        self, client: TestClient, arbol: None
    ) -> None:
        habana = _filas(_grupo(_centro(_html(client, "&productos=arroz")), "la-habana"))

        assert sum('class="ph-ex-row lv0' in f for f in habana) == 1
        assert sum('class="ph-ex-row lv1' in f for f in habana) == 2
        assert [re.search(r'data-fmt="([^"]+)"', f).group(1) for f in habana if "lv1" in f] == [  # type: ignore[union-attr]
            "la-habana|saco-de-100-lb",
            "la-habana|libra",
        ]

    def test_la_provincia_con_mas_anuncios_llega_elegida_y_abierta(
        self, client: TestClient, arbol: None
    ) -> None:
        centro = _centro(_html(client, "&productos=arroz"))
        habana = _grupo(centro, "la-habana")
        santiago = _grupo(centro, "santiago-de-cuba")

        assert "is-sel" in _filas(habana)[0]
        assert re.search(r'ph-ex-chev"[^>]*aria-expanded="true"', habana)
        assert not any("hidden" in f for f in _filas(habana) if "lv1" in f)
        assert "is-sel" not in _filas(santiago)[0]
        assert re.search(r'ph-ex-chev"[^>]*aria-expanded="false"', santiago)
        assert all("hidden" in f for f in _filas(santiago) if "lv1" in f)
        assert ">La Habana<" in _derecha(_html(client, "&productos=arroz"))

    def test_los_formatos_llegan_cerrados_con_sus_anuncios_ocultos(
        self, client: TestClient, arbol: None
    ) -> None:
        habana = _grupo(_centro(_html(client, "&productos=arroz")), "la-habana")

        formatos = [f for f in _filas(habana) if "lv1" in f]
        anuncios = [f for f in _filas(habana) if "lv2" in f]
        assert len(formatos) == 2
        assert re.findall(r'ph-ex-chev"[^>]*aria-expanded="(\w+)"', habana) == ["true", "false", "false"]
        assert len(anuncios) == 6 + 1 + 2
        assert all("hidden" in f for f in anuncios)

    def test_cada_formato_enseña_cuatro_anuncios_y_el_resto_queda_tras_ver_los_n(
        self, client: TestClient, arbol: None
    ) -> None:
        habana = _grupo(_centro(_html(client, "&productos=arroz")), "la-habana")
        saco = [f for f in _filas(habana) if 'data-of="la-habana|saco-de-100-lb"' in f]
        libra = [f for f in _filas(habana) if 'data-of="la-habana|libra"' in f]

        assert len(saco) == 6 + 1
        assert sum("data-extra" in f for f in saco) == 2
        assert sum("more" in f for f in saco) == 1
        assert "ver los 6 anuncios →" in habana
        assert len(libra) == 2
        assert not any("more" in f or "data-extra" in f for f in libra)
        assert "ver los 2 anuncios" not in habana

    def test_cada_anuncio_lleva_fecha_precio_y_enlace_a_su_evidencia(
        self, client: TestClient, arbol: None
    ) -> None:
        habana = _grupo(_centro(_html(client, "&productos=arroz")), "la-habana")
        dia = (date.today() - timedelta(days=5)).strftime("%d/%m")
        fila = _bloque(habana, 'data-of="la-habana|libra"', "</tr>")
        libra = habana.split('data-of="la-habana|libra"')[1:]

        assert any(dia in f.split("</tr>")[0] and ">320<" in f.split("</tr>")[0] for f in libra)
        assert 'href="/anuncio/' in fila
        assert ">ver anuncio<" in fila

    def test_la_provincia_cuenta_sus_formatos_y_el_formato_va_en_su_pastilla(
        self, client: TestClient, arbol: None
    ) -> None:
        centro = _centro(_html(client, "&productos=arroz"))
        habana = _grupo(centro, "la-habana")

        assert ">2 formatos<" in habana
        assert ">1 formato" not in _grupo(centro, "santiago-de-cuba")
        assert 'class="ph-ex-nmt fmt">Saco de 100 lb<' in habana
        assert 'class="ph-ex-nmt fmt">Libra<' in habana

    def test_solo_las_cabeceras_que_ordenan_son_botones(self, client: TestClient, datos: None) -> None:
        cabecera = _bloque(_centro(_html(client, "&productos=arroz")), "<thead", "</thead>")

        assert re.findall(r'data-sort="(\w+)"', cabecera) == ["name", "med", "chg", "n", "last"]
        assert re.search(r'data-sort="med"[^>]*data-s="desc"', cabecera)
        for rotulo in ("Rango", "Tendencia", "Mediana por semana"):
            assert f">{rotulo}</th>" in cabecera

    def test_cada_grupo_lleva_lo_que_el_cliente_necesita_para_ordenar(
        self, client: TestClient, datos: None
    ) -> None:
        habana = re.search(
            r'<tbody class="ph-ex-grp"[^>]*data-key="la-habana"[^>]*>', _html(client, "&productos=arroz")
        )

        assert habana is not None
        for atributo, valor in (("name", "La Habana"), ("med", "300"), ("n", "2"), ("last", "5")):
            assert f'data-{atributo}="{valor}"' in habana.group(0)
        assert 'data-chg="' in habana.group(0)

    def test_rango_y_semanas_llegan_dibujados_pero_ocultos(self, client: TestClient, datos: None) -> None:
        centro = _centro(_html(client, "&productos=arroz"))
        fila = _grupo(centro, "la-habana").split("</tr>")[0]

        assert 'data-hide="rango sem"' in centro
        assert 'class="ph-ex-rng"' in fila
        assert 'title="280–320 CUP/libra"' in fila
        assert len(re.findall(r'class="ph-ex-wk', fila)) == 8

    def test_el_calor_semanal_pone_la_mediana_de_cada_semana_con_dato(
        self, client: TestClient, datos: None
    ) -> None:
        """Hace 12 días cae en la penúltima semana (11) y hace 5 en la última
        (12): de las ocho que se ven, solo esas dos llevan cifra."""
        fila = _grupo(_centro(_html(client, "&productos=arroz")), "la-habana").split("</tr>")[0]

        assert re.findall(r'class="ph-ex-wk[^"]*"[^>]*>([^<]*)<', fila) == [
            "",
            "",
            "",
            "",
            "",
            "",
            "280",
            "320",
        ]
        assert len(re.findall(r"--t: ", fila)) == 2

    def test_el_panel_de_columnas_gobierna_las_siete_de_la_tabla(
        self, client: TestClient, datos: None
    ) -> None:
        cols = _bloque(_izquierda(_html(client, "&productos=arroz")), 'id="ph-ex-l-cols"', "</div>")

        assert re.findall(r'data-col="(\w+)"( checked)?', cols) == [
            ("med", " checked"),
            ("chg", " checked"),
            ("rango", ""),
            ("tend", " checked"),
            ("sem", ""),
            ("n", " checked"),
            ("last", " checked"),
        ]


class TestFiltros:
    """P5 (2026-09-25): filtrar es enteramente del cliente, sobre las filas
    que ya trajo el servidor — acá se verifica el contrato del que depende
    ese JavaScript (atributos, nombres, topes), no la interacción en sí, que
    pytest no ejecuta (se comprueba a mano en Chromium, como el resto de la
    vista)."""

    def test_cada_formato_lleva_su_nombre_suelto_para_filtrar_y_buscar(
        self, client: TestClient, arbol: None
    ) -> None:
        habana = _grupo(_centro(_html(client, "&productos=arroz")), "la-habana")

        assert 'data-fname="Saco de 100 lb"' in habana
        assert 'data-fname="Libra"' in habana

    def test_las_casillas_de_formato_llevan_su_nombre_como_valor(
        self, client: TestClient, arbol: None
    ) -> None:
        izq = _izquierda(_html(client, "&productos=arroz"))
        formatos = _bloque(izq, "<h5>Formato</h5>", "</section>")

        assert 'name="formato" value="Saco de 100 lb"' in formatos
        assert 'name="formato" value="Libra"' in formatos

    def test_las_casillas_de_provincia_llevan_el_slug_como_valor(
        self, client: TestClient, datos: None
    ) -> None:
        izq = _izquierda(_html(client, "&productos=arroz"))
        provincias = _bloque(izq, "· provincia</h5>", "</section>")

        assert 'name="provincia" value="la-habana"' in provincias
        assert 'name="provincia" value="santiago-de-cuba"' in provincias

    def test_los_steppers_llevan_paso_y_tope_del_prototipo(self, client: TestClient, datos: None) -> None:
        izq = _izquierda(_html(client, "&productos=arroz"))

        anuncios = re.search(r'<div class="ph-ex-step"[^>]*data-stepper="nmin"[^>]*>', izq)
        cambio = re.search(r'<div class="ph-ex-step"[^>]*data-stepper="dmin"[^>]*>', izq)
        assert anuncios and 'data-max="60"' in anuncios.group(0) and 'data-paso="2"' in anuncios.group(0)
        assert cambio and 'data-max="30"' in cambio.group(0) and 'data-paso="1"' in cambio.group(0)
        assert izq.count('data-dir="-1"') == 2
        assert izq.count('data-dir="1"') == 2

    def test_la_busqueda_tiene_la_clase_que_el_js_engancha(self, client: TestClient, datos: None) -> None:
        centro = _centro(_html(client, "&productos=arroz"))

        assert '<input type="search" class="ph-ex-q"' in centro

    def test_los_contenedores_de_chips_y_sin_resultados_arrancan_vacios_y_ocultos(
        self, client: TestClient, datos: None
    ) -> None:
        centro = _centro(_html(client, "&productos=arroz"))

        assert re.search(r'<div class="ph-ex-chips" id="ph-ex-chips" hidden></div>', centro)
        assert re.search(r'<p class="ph-ex-nomatch" id="ph-ex-nomatch" hidden>', centro)
        assert "Limpiar filtros" in centro

    def test_incluso_sin_formato_explicito_el_producto_tiene_una_casilla(
        self, client: TestClient, datos: None
    ) -> None:
        """La fixture base no crea un `ProductFormat`; el anuncio sin
        formato todavía muestra su presentación por defecto (`Libra`)."""
        formatos = _bloque(_izquierda(_html(client, "&productos=arroz")), "<h5>Formato</h5>", "</section>")

        assert 'name="formato" value="Libra"' in formatos


class TestMovil:
    """P7 (2026-09-25, fiel a M2): sin panel izquierdo, «+» y «Filtros» abren
    una hoja inferior y las columnas se eligen con tres presets en vez de
    siete casillas. Los controles se repiten (mismos macros que el panel de
    escritorio) para que también funcionen con CSS deshabilitado; el CSS que
    decide cuál se ve en cada ancho no lo ejecuta pytest, se comprobó a mano
    en Chromium a 390 px."""

    def test_la_hoja_de_productos_trae_todos_con_su_estado(self, client: TestClient, datos: None) -> None:
        centro = _centro(_html(client, "&productos=arroz"))
        hoja = _bloque(centro, 'class="ph-ex-msheet ph-ex-msheet-prod"', "</details>")

        assert hoja.count("ph-ex-opt") >= 3
        assert 'is-on" href' in hoja  # Arroz, ya abierto

    def test_la_hoja_de_filtros_trae_provincia_formato_y_steppers(
        self, client: TestClient, datos: None
    ) -> None:
        centro = _centro(_html(client, "&productos=arroz"))
        hoja = _bloque(centro, 'class="ph-ex-msheet ph-ex-msheet-filt"', "</details>")

        assert 'name="provincia"' in hoja
        assert 'name="formato"' in hoja
        assert 'data-stepper="nmin"' in hoja
        assert 'data-stepper="dmin"' in hoja
        assert "data-clear-all" in hoja
        assert "data-close-sheet" in hoja

    def test_los_steppers_del_escritorio_y_la_hoja_comparten_clave(
        self, client: TestClient, datos: None
    ) -> None:
        """Dos copias, la misma clave: el JS las mantiene sincronizadas
        (`ponerStepper` escribe en las dos)."""
        texto = _html(client, "&productos=arroz")

        assert texto.count('data-stepper="nmin"') == 2
        assert texto.count('data-stepper="dmin"') == 2

    def test_los_tres_presets_de_columnas_estan_y_precio_es_el_activo(
        self, client: TestClient, datos: None
    ) -> None:
        centro = _centro(_html(client, "&productos=arroz"))
        patron = r'<button type="button" class="ph-ex-preset( is-on)?" data-preset="(\w+)">'
        presets = re.findall(patron, centro)

        assert [(clave, bool(on)) for on, clave in presets] == [
            ("precio", True),
            ("semanas", False),
            ("todo", False),
        ]

    def test_la_cuenta_de_la_hoja_de_filtros_arranca_con_las_provincias_con_dato(
        self, client: TestClient, datos: None
    ) -> None:
        centro = _centro(_html(client, "&productos=arroz"))

        assert '<span id="ph-ex-sheetcount">2</span>' in centro  # La Habana y Santiago

    def test_sin_pestanias_abiertas_no_hay_hoja_de_filtros_ni_presets(
        self, client: TestClient, datos: None
    ) -> None:
        centro = _centro(_html(client, ""))

        assert "ph-ex-msheet-filt" not in centro
        assert "ph-ex-colpreset" not in centro


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

    def test_por_defecto_el_detalle_es_el_de_la_provincia_con_mas_anuncios(
        self, client: TestClient, datos: None
    ) -> None:
        """La Habana tiene 2 anuncios y Santiago 1: gana la muestra, no la
        mediana más alta (así elige el prototipo)."""
        der = _derecha(_html(client, "&productos=arroz"))

        assert ">La Habana<" in der
        assert re.search(r'ph-ex-prov is-selected"[^>]*data-slug="la-habana"', der)

    def test_la_provincia_de_la_url_manda(self, client: TestClient, datos: None) -> None:
        der = _derecha(_html(client, "&productos=arroz&provincia=la-habana"))

        assert re.search(r'ph-ex-prov is-selected"[^>]*data-slug="la-habana"', der)
        assert ">La Habana<" in der

    def test_las_provincias_con_dato_van_envueltas_en_un_enlace_por_htmx(
        self, client: TestClient, datos: None
    ) -> None:
        """P6: elegir una provincia desde el mapa no recarga la página."""
        der = _derecha(_html(client, "&productos=arroz"))

        envueltas = re.findall(r'<a class="ph-ex-provlink" href="([^"]+)" hx-get="([^"]+)"[^>]*>', der)
        assert len(envueltas) == 2  # La Habana y Santiago de Cuba, las únicas con dato
        assert all(href == hx for href, hx in envueltas)

    def test_las_provincias_sin_dato_no_llevan_enlace(self, client: TestClient, datos: None) -> None:
        der = _derecha(_html(client, "&productos=arroz"))

        guantanamo = re.search(r'.{80}data-slug="guantanamo"', der)
        assert guantanamo is not None
        assert "ph-ex-provlink" not in guantanamo.group(0)

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


class TestCintilloEnLasTresVistas:
    """Cintillo de precios del prototipo aprobado (2026-09-26): la misma franja
    bajo la cabecera de las tres vistas, con la mediana entre las provincias
    con dato. Cada enlace lleva a la vista en la que se está, para que tocar un
    producto no cambie de presentación."""

    def _cinta(self, texto: str) -> str:
        inicio = texto.index('class="ph-cintillo"')
        return texto[inicio : texto.index("<!-- /ph-cintillo -->", inicio)]

    @pytest.mark.parametrize("vista", ["tablero", "mapa", "explorar"])
    def test_las_tres_vistas_traen_el_cintillo(
        self, client: TestClient, datos: None, vista: str
    ) -> None:
        texto = client.get(f"/?vista={vista}").text

        assert 'class="ph-cintillo"' in texto
        assert "Mediana · provincias con dato" in texto

    @pytest.mark.parametrize(
        ("vista", "fragmento"),
        [
            ("tablero", "vista=tablero"),
            ("mapa", "vista=mapa"),
            ("explorar", "vista=explorar"),
        ],
    )
    def test_cada_enlace_se_queda_en_su_vista(
        self, client: TestClient, datos: None, vista: str, fragmento: str
    ) -> None:
        cinta = self._cinta(client.get(f"/?vista={vista}").text)
        enlaces = re.findall(r'class="ph-cintillo-item" href="([^"]+)"', cinta)

        # Arroz y Pollo (Aceite no tiene anuncios), cada uno en el grupo y en su copia.
        assert len(enlaces) == 4
        assert all(fragmento in href.replace("&amp;", "&") for href in enlaces)
        assert any("productos=arroz" in href for href in enlaces)

    def test_en_explorar_el_enlace_abre_la_pestana_del_producto(
        self, client: TestClient, datos: None
    ) -> None:
        cinta = self._cinta(client.get("/?vista=explorar").text)

        assert "pestana=arroz" in cinta

    def test_la_cinta_lleva_una_copia_oculta_para_el_bucle(
        self, client: TestClient, datos: None
    ) -> None:
        """El desplazamiento continuo necesita dos grupos iguales. La copia es
        decorativa: `aria-hidden` y sus enlaces fuera del orden de tabulación,
        para que un lector de pantalla no lea cada producto dos veces."""
        cinta = self._cinta(client.get("/?vista=tablero").text)

        assert cinta.count('<ul class="ph-cintillo-grupo">') == 1
        assert cinta.count('<ul class="ph-cintillo-grupo" aria-hidden="true">') == 1
        assert cinta.count('tabindex="-1"') == 2

    def test_muestra_la_mediana_y_el_cambio_sin_flecha(self, client: TestClient, datos: None) -> None:
        cinta = self._cinta(client.get("/?vista=tablero").text)

        assert '<span class="ph-cintillo-cifra">350</span>' in cinta  # (300 + 400) / 2
        assert "▲" not in cinta and "▼" not in cinta

    def test_sin_datos_no_hay_cintillo(self, client: TestClient) -> None:
        for vista in ("tablero", "mapa", "explorar"):
            assert 'class="ph-cintillo"' not in client.get(f"/?vista={vista}").text, vista


class TestBarraDeVistasMovil:
    """Barra inferior de vistas (prototipo móvil aprobado, 2026-09-26): en el
    teléfono el selector Mapa / Tablero / Explorar pasa abajo, al alcance del
    pulgar. Es el mismo en las tres vistas; el CSS la muestra solo en ≤720 px."""

    def _barra(self, texto: str) -> str:
        inicio = texto.index('<nav class="ph-barra-vistas"')
        return texto[inicio : texto.index("</nav>", inicio)]

    @pytest.mark.parametrize("vista", ["tablero", "mapa", "explorar"])
    def test_las_tres_vistas_traen_la_barra_con_las_tres_entradas(
        self, client: TestClient, datos: None, vista: str
    ) -> None:
        barra = self._barra(client.get(f"/?vista={vista}").text)

        for destino in ("mapa", "tablero", "explorar"):
            assert re.search(
                rf'<a class="ph-barra-item js-vista-link" data-vista="{destino}" href="[^"]*vista={destino}"',
                barra,
            ), destino

    @pytest.mark.parametrize("vista", ["tablero", "mapa", "explorar"])
    def test_solo_la_vista_actual_esta_marcada(
        self, client: TestClient, datos: None, vista: str
    ) -> None:
        barra = self._barra(client.get(f"/?vista={vista}").text)

        assert barra.count('aria-current="true"') == 1
        assert re.search(rf'data-vista="{vista}"[^>]*aria-current="true"', barra)

    def test_la_barra_se_ve_aun_sin_datos(self, client: TestClient) -> None:
        """A diferencia del cintillo, la barra es navegación: sin datos también
        hace falta poder cambiar de vista."""
        for vista in ("tablero", "mapa", "explorar"):
            assert '<nav class="ph-barra-vistas"' in client.get(f"/?vista={vista}").text, vista


class TestCoberturaEnElTablero:
    """Mosaico de cobertura (prototipo aprobado, 2026-09-26): las 16 provincias
    como casillas, llenas las que tienen dato y punteadas el resto, con un
    enlace al mapa. Solo en «Todos»: el detalle habla de un producto."""

    def _panel(self, texto: str) -> str:
        inicio = texto.index('class="ph-panel ph-cobertura"')
        return texto[inicio : texto.index("</section>", inicio)]

    def test_dibuja_una_casilla_por_provincia_del_catalogo(
        self, client: TestClient, datos: None
    ) -> None:
        """El fixture trae tres provincias (La Habana, Santiago y Guantánamo);
        el catálogo real trae las 16. Una casilla por cada una que exista."""
        panel = self._panel(client.get("/?vista=tablero").text)

        assert panel.count('class="ph-tesela ') == 3

    def test_las_provincias_con_dato_van_llenas_y_las_demas_punteadas(
        self, client: TestClient, datos: None
    ) -> None:
        panel = self._panel(client.get("/?vista=tablero").text)

        assert re.search(r'data-estado="en-ingesta"[^>]*title="La Habana: ', panel)
        assert re.search(r'data-estado="en-ingesta"[^>]*title="Santiago de Cuba: ', panel)
        assert re.search(r'data-estado="sin-datos"[^>]*title="Guantánamo: Sin datos todavía"', panel)
        assert panel.count('data-estado="en-ingesta"') == 2

    def test_cada_casilla_lleva_su_lugar_en_la_rejilla(
        self, client: TestClient, datos: None
    ) -> None:
        panel = self._panel(client.get("/?vista=tablero").text)

        # La Habana: columna 3, fila 1; Santiago de Cuba: columna 11, fila 2.
        assert re.search(r'style="grid-column: 3; grid-row: 1;[^"]*" title="La Habana', panel)
        assert re.search(r'style="grid-column: 11; grid-row: 2;[^"]*" title="Santiago de Cuba', panel)

    def test_ofrece_el_puente_al_mapa(self, client: TestClient, datos: None) -> None:
        panel = self._panel(client.get("/?vista=tablero").text)

        assert re.search(r'<a class="ph-cobertura-mapa ph-tap-44" href="[^"]*vista=mapa"', panel)

    def test_no_repite_la_cuenta_que_ya_da_sobre_los_datos(
        self, client: TestClient, datos: None
    ) -> None:
        """La viñeta «N de 16 provincias tienen datos» es de «Sobre los datos»
        (§9.0); el mosaico la dibuja, no la vuelve a escribir."""
        texto = client.get("/?vista=tablero").text

        assert texto.count("tienen datos") == 1

    def test_el_detalle_no_lleva_el_mosaico(self, client: TestClient, datos: None) -> None:
        texto = client.get("/?vista=tablero&productos=arroz").text

        assert 'class="ph-panel ph-cobertura"' not in texto


class TestSelectorDePeriodoEnElTablero:
    """Selector de período del tablero (2026-09-26): el Mapa deja elegir 7 / 30 /
    90 días o un rango de fechas, y el tablero no. Mismos atajos y mismo rango
    de calendario, con `vista=tablero` en cada enlace. Está en «Todos» y en el
    detalle; el mapa conserva el suyo."""

    def _periodo(self, texto: str) -> str:
        inicio = texto.index('class="ph-mi-prov ph-periodo"')
        return texto[inicio : texto.index("<!-- /ph-periodo -->", inicio)]

    @pytest.mark.parametrize("url", ["/?vista=tablero", "/?vista=tablero&productos=arroz"])
    def test_esta_en_todos_y_en_el_detalle(self, client: TestClient, datos: None, url: str) -> None:
        assert 'class="ph-mi-prov ph-periodo"' in client.get(url).text

    def test_ofrece_los_tres_atajos_en_su_vista(self, client: TestClient, datos: None) -> None:
        periodo = self._periodo(client.get("/?vista=tablero").text)
        atajos = re.findall(r'<a class="ph-dash-chip ph-tap-44[^"]*" href="([^"]+)"[^>]*>(\d+ días)</a>', periodo)

        assert [dias for _, dias in atajos] == ["7 días", "30 días", "90 días"]
        assert all("vista=tablero" in href and "desde=" in href and "hasta=" in href for href, _ in atajos)

    def test_sin_elegir_nada_esta_marcado_el_de_30_dias(self, client: TestClient, datos: None) -> None:
        periodo = self._periodo(client.get("/?vista=tablero").text)

        marcados = re.findall(r'aria-current="true">(\d+ días)</a>', periodo)

        assert marcados == ["30 días"]

    def test_un_rango_propio_no_marca_ningun_atajo_y_enciende_las_fechas(
        self, client: TestClient, datos: None
    ) -> None:
        periodo = self._periodo(client.get("/?vista=tablero&desde=2026-09-01&hasta=2026-09-10").text)

        assert 'aria-current="true"' not in periodo
        assert re.search(r'<summary class="ph-dash-chip ph-tap-44 is-active"', periodo)
        assert 'name="desde" value="2026-09-01"' in periodo
        assert 'name="hasta" value="2026-09-10"' in periodo

    def test_el_formulario_conserva_vista_provincia_y_producto(
        self, client: TestClient, datos: None
    ) -> None:
        periodo = self._periodo(
            client.get("/?vista=tablero&productos=arroz&provincia=la-habana").text
        )

        assert '<input type="hidden" name="vista" value="tablero">' in periodo
        assert '<input type="hidden" name="provincia" value="la-habana">' in periodo
        assert '<input type="hidden" name="productos" value="arroz">' in periodo

    def test_el_mapa_no_lo_estrena(self, client: TestClient, datos: None) -> None:
        assert 'class="ph-mi-prov ph-periodo"' not in client.get("/?vista=mapa").text

    def test_elegir_un_rango_desde_el_formulario_abre_el_tablero_con_ese_rango(
        self, client: TestClient, datos: None
    ) -> None:
        """Ida y vuelta: lo que manda el formulario (GET con `vista=tablero`) es
        lo que el servidor devuelve en el aviso y en los campos."""
        texto = client.get("/?vista=tablero&desde=2026-09-01&hasta=2026-09-10").text

        assert "Mediana de los últimos 9 días" in texto
