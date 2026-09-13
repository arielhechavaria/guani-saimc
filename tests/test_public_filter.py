"""Filtros de la vista pública: `?producto=`, `?provincia=` y `?dias=`.

Lo que estos casos protegen:

- El slug es estable y resuelve de vuelta al nombre, tildes y espacios
  incluidos («azucar-blanca» -> «Azúcar blanca», «la-habana» -> «La Habana»).
- Elegir un producto o una provincia acota las tarjetas y las series, y nada
  más de la página (el mapa de cobertura no depende de los filtros).
- Los tres ejes se combinan y cada enlace preserva los otros dos.
- Un valor que no existe no da 404: una vista pública muestra todo ante un
  query string raro.
- Un filtro sin dato suficiente no rompe la página: muestra el aviso y deja
  las barras para cambiar de filtro.
"""

import csv
import io
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
    ProductVariant,
    Province,
    RawIngestion,
    SaleFormat,
    SourceType,
)
from saimc.models.raw_ingestion import ProcessingStatus
from saimc.web.app import app
from saimc.web.deps import get_db
from saimc.web.product_nav import (
    DEFAULT_LAYER,
    DEFAULT_WINDOW_DAYS,
    build_layer_nav,
    build_product_chips,
    build_product_nav,
    build_province_nav,
    build_view_menu,
    build_window_nav,
    product_slug,
    resolve_days,
    resolve_layer,
    resolve_product,
    resolve_products,
    resolve_province,
    resolve_views,
)


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    def _get_db_override() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_db] = _get_db_override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _hace(dias: int) -> date:
    """Un día relativo a hoy, para sembrar dentro de la ventana que se filtra.

    Los seeds de este archivo usaban fechas absolutas de agosto de 2026. Eso
    funciona hasta que el reloj se corre: la vista filtra por `?dias=N` contra
    `date.today()`, así que un anuncio fijo en el calendario sale de la ventana
    solo, y el test empieza a fallar sin que nadie haya tocado el código. Pasó
    el 2026-09-04 con los dos casos que piden `dias=7`.

    La excepción deliberada es `TestFrescuraDelDato`, que sí usa fechas
    absolutas: ahí lo que se afirma es el string formateado en pantalla y el
    punto de la prueba es justamente que esa línea NO depende de la ventana.
    """
    return date.today() - timedelta(days=dias)


def _ingestion(session: Session, *, hash_: str) -> RawIngestion:
    row = RawIngestion(
        source_type=SourceType.INBOX_TEXT,
        file_path=f"raw/{hash_}.dat",
        content_hash=hash_,
        content_type=ContentType.TEXT,
        processing_status=ProcessingStatus.CURATED,
        captured_at=datetime(2026, 8, 10, tzinfo=UTC),
    )
    session.add(row)
    session.flush()
    return row


def _precio(
    session: Session,
    *,
    product: Product,
    province: Province,
    source: RawIngestion,
    dia: date,
    price: str = "300",
) -> None:
    session.add(
        PriceHistory(
            product_id=product.id,
            province_id=province.id,
            price=Decimal(price),
            currency=Currency.CUP.value,
            source_id=source.id,
            unit="lb",
            sale_format=SaleFormat.RETAIL.value,
            quantity=Decimal("1"),
            published_date=dia,
        )
    )


def _seed_dos_productos(session: Session) -> None:
    """Arroz con evolución (dos días) y Aceite con un solo día, ambos piloto."""
    arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True)
    aceite = Product(name="Aceite", category="grasas", unit="lb", aliases=[], is_pilot=True)
    azucar = Product(
        name="Azúcar blanca", category="dulces", unit="lb", aliases=[], is_pilot=True
    )
    habana = Province(code="hab", name="La Habana")
    session.add_all([arroz, aceite, azucar, habana])
    session.commit()

    fuente = _ingestion(session, hash_="h-arroz")
    _precio(session, product=arroz, province=habana, source=fuente, dia=_hace(6))
    _precio(
        session,
        product=arroz,
        province=habana,
        source=fuente,
        dia=_hace(3),
        price="320",
    )
    _precio(session, product=aceite, province=habana, source=fuente, dia=_hace(4))
    _precio(session, product=azucar, province=habana, source=fuente, dia=_hace(5))
    _precio(
        session,
        product=azucar,
        province=habana,
        source=fuente,
        dia=_hace(2),
        price="115",
    )
    session.commit()


# --- product_nav (sin base) --------------------------------------------------


class TestProductSlug:
    def test_quita_tildes_y_espacios(self) -> None:
        assert product_slug("Azúcar blanca") == "azucar-blanca"

    def test_producto_de_una_palabra(self) -> None:
        assert product_slug("Arroz") == "arroz"

    def test_es_ida_y_vuelta_con_resolve(self) -> None:
        nombres = ("Arroz", "Azúcar blanca", "Picadillo")
        for nombre in nombres:
            assert resolve_product(nombres, product_slug(nombre)) == nombre


class TestResolveProduct:
    def test_slug_vacio_no_selecciona(self) -> None:
        assert resolve_product(("Arroz",), "") is None
        assert resolve_product(("Arroz",), None) is None

    def test_slug_desconocido_no_selecciona(self) -> None:
        assert resolve_product(("Arroz",), "leche") is None


class TestBuildProductNav:
    def test_todos_va_primero_y_activo_sin_seleccion(self) -> None:
        nav = build_product_nav(("Arroz", "Aceite"), None)

        assert nav[0].label == "Todos"
        assert nav[0].slug == ""
        assert nav[0].query == "/"
        assert nav[0].is_selected
        assert all(not c.is_selected for c in nav[1:])

    def test_marca_el_producto_elegido(self) -> None:
        nav = build_product_nav(("Arroz", "Aceite"), "Aceite")
        por_label = {c.label: c for c in nav}

        assert not por_label["Todos"].is_selected
        assert por_label["Aceite"].is_selected
        assert not por_label["Arroz"].is_selected

    def test_chip_de_producto_usa_la_url_limpia_cuando_no_hay_otro_filtro(
        self,
    ) -> None:
        """Sin provincia ni ventana no-default, el chip apunta a `/producto/<slug>`
        en vez de `/?producto=<slug>`: más limpio para compartir."""
        nav = build_product_nav(("Arroz", "Aceite"), None)
        por_label = {c.label: c for c in nav}

        assert por_label["Arroz"].query == "/producto/arroz"
        assert por_label["Aceite"].query == "/producto/aceite"
        assert por_label["Todos"].query == "/"

    def test_con_otro_filtro_activo_el_chip_vuelve_al_query_string(self) -> None:
        con_ventana = build_product_nav(("Arroz",), None, selected_days=7)
        con_provincia = build_product_nav(
            ("Arroz",), None, selected_province="La Habana"
        )

        assert con_ventana[1].query == "/?producto=arroz&dias=7"
        assert con_provincia[1].query == "/?producto=arroz&provincia=la-habana"

    def test_la_ventana_no_default_viaja_en_el_query_del_producto(self) -> None:
        nav = build_product_nav(("Arroz",), "Arroz", selected_days=7)
        por_label = {c.label: c for c in nav}

        assert por_label["Todos"].query == "/?dias=7"
        assert por_label["Arroz"].query == "/?producto=arroz&dias=7"


class TestResolveProducts:
    PILOT = ("Arroz", "Aceite", "Azúcar blanca")

    def test_lista_vacia_ante_none_o_cadena_vacia(self) -> None:
        assert resolve_products(self.PILOT, None) == []
        assert resolve_products(self.PILOT, "") == []

    def test_resuelve_en_orden_y_sin_repetir(self) -> None:
        assert resolve_products(self.PILOT, "aceite,arroz,aceite") == ["Aceite", "Arroz"]

    def test_ignora_los_slugs_que_no_resuelven(self) -> None:
        assert resolve_products(self.PILOT, "arroz,inventado,azucar-blanca") == [
            "Arroz",
            "Azúcar blanca",
        ]

    def test_tolera_espacios_alrededor_de_las_comas(self) -> None:
        assert resolve_products(self.PILOT, " arroz , aceite ") == ["Arroz", "Aceite"]


class TestBuildProductChips:
    PILOT = ("Arroz", "Aceite", "Pollo")

    def test_un_chip_por_piloto_en_orden_de_catalogo(self) -> None:
        chips = build_product_chips(self.PILOT, [])

        assert [c.label for c in chips] == ["Arroz", "Aceite", "Pollo"]
        assert all(not c.is_active for c in chips)

    def test_marca_activos_los_del_conjunto(self) -> None:
        chips = build_product_chips(self.PILOT, ["Aceite"])
        por_label = {c.label: c for c in chips}

        assert por_label["Aceite"].is_active
        assert not por_label["Arroz"].is_active

    def test_togglear_un_apagado_lo_agrega_al_final(self) -> None:
        chips = build_product_chips(self.PILOT, ["Arroz"])
        por_label = {c.label: c for c in chips}

        # Prender Aceite → productos=arroz,aceite (orden de activación).
        assert por_label["Aceite"].query == "/?productos=arroz,aceite"

    def test_un_chip_prendido_no_se_apaga_al_clickearlo(self) -> None:
        # Clic en un chip ya activo no lo quita del conjunto: cerrar un
        # producto es cosa de la × de su panel, no del riel de chips (antes
        # sí lo apagaba, y era fácil cerrar un panel sin querer).
        chips = build_product_chips(self.PILOT, ["Arroz", "Aceite"])
        por_label = {c.label: c for c in chips}

        assert por_label["Arroz"].query == "/?productos=arroz,aceite"
        chips_uno = build_product_chips(self.PILOT, ["Arroz"])
        assert {c.label: c for c in chips_uno}["Arroz"].query == "/?productos=arroz"

    def test_preserva_provincia_y_ventana_en_el_toggle(self) -> None:
        chips = build_product_chips(
            self.PILOT, ["Arroz"], selected_province="La Habana", selected_days=7
        )
        por_label = {c.label: c for c in chips}

        assert por_label["Aceite"].query == (
            "/?productos=arroz,aceite&provincia=la-habana&dias=7"
        )

    def test_el_chip_trae_el_icono_del_producto_o_ninguno(self) -> None:
        chips = build_product_chips(("Arroz", "Malanga"), [])
        por_label = {c.label: c for c in chips}

        assert por_label["Arroz"].icon == "rice_bowl"
        # Sin ícono asignado, el chip sigue mostrando el nombre.
        assert por_label["Malanga"].icon is None


class TestChipsMultiSeleccionEnLaRuta:
    def test_el_chip_con_icono_muestra_solo_el_icono_y_nombra_al_pasar(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_dos_productos(session)

        texto = client.get("/").text

        # Ligadura de Material Symbols: el texto es el nombre del ícono.
        assert '<span class="m3-icon ph-chip-icon" aria-hidden="true">rice_bowl</span>' in texto
        # El nombre va visible debajo del ícono (y es el nombre accesible).
        assert '<span class="ph-chip-name">Arroz</span>' in texto

    def test_productos_acota_a_la_union_de_los_activos(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_dos_productos(session)

        texto = client.get("/?productos=arroz,aceite").text

        # Dos activos → grilla de tarjetas (no la tabla de un solo producto),
        # acotada a esos dos: Azúcar no aparece como tarjeta.
        assert 'class="ph-cards"' in texto
        assert "ph-provinces" not in texto
        assert '<span class="ph-card-product">Arroz</span>' in texto
        assert '<span class="ph-card-product">Aceite</span>' in texto
        assert '<span class="ph-card-product">Azúcar blanca</span>' not in texto

    def test_los_chips_reflejan_el_conjunto_activo(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_dos_productos(session)

        texto = client.get("/?productos=arroz,aceite").text

        assert 'aria-pressed="true"><span class="m3-icon ph-chip-icon" aria-hidden="true">rice_bowl</span><span class="ph-chip-name">Arroz</span></a>' in texto
        assert 'aria-pressed="true"><span class="m3-icon ph-chip-icon" aria-hidden="true">water_drop</span><span class="ph-chip-name">Aceite</span></a>' in texto
        assert 'aria-pressed="false"><span class="m3-icon ph-chip-icon" aria-hidden="true">grain</span><span class="ph-chip-name">Azúcar blanca</span></a>' in texto

    def test_un_solo_producto_via_productos_da_la_tabla_enfocada(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_dos_productos(session)

        texto = client.get("/?productos=arroz").text

        # Exactamente uno → misma vista enfocada que `?producto=arroz`.
        assert "Arroz por formato y provincia" in texto

    def test_el_csv_de_la_pagina_lleva_los_productos_activos(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_dos_productos(session)

        texto = client.get("/?productos=arroz,aceite").text

        assert 'href="/datos.csv?productos=arroz,aceite"' in texto

    def test_datos_csv_respeta_productos(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_dos_productos(session)

        texto = client.get("/datos.csv?productos=arroz,aceite").text

        assert "Arroz" in texto
        assert "Aceite" in texto
        assert "Azúcar blanca" not in texto


class TestPanelesFlotantes:
    def test_un_panel_por_producto_activo(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_dos_productos(session)

        texto = client.get("/?productos=arroz,aceite").text

        assert texto.count('class="ph-fpanel"') == 2
        assert '<span class="ph-fpanel-name">Arroz</span>' in texto
        assert '<span class="ph-fpanel-name">Aceite</span>' in texto

    def test_sin_productos_no_hay_pila_de_paneles(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_dos_productos(session)

        texto = client.get("/").text

        assert 'class="ph-panel-stack"' not in texto

    def test_el_panel_y_su_chip_comparten_data_product(
        self, client: TestClient, session: Session
    ) -> None:
        """Contrato del cierre optimista (2026-09-11): al tocar la × de un
        panel, el JS lo esconde y apaga su chip antes de que vuelva la
        respuesta, y los empareja por `data-product`. Si un lado pierde el
        atributo, el JS no falla —simplemente deja de apagar el chip, en
        silencio—, que es el modo de romperse que
        `TestClasesDelJavascriptExisten` ya documenta para las clases."""
        _seed_dos_productos(session)

        texto = client.get("/?productos=arroz,aceite").text

        for slug in ("arroz", "aceite"):
            assert f'class="ph-fpanel" data-win="{slug.capitalize()}" data-product="{slug}"' in texto
            assert f'hx-get="/?productos=arroz,aceite" data-product="{slug}"' in texto

    def test_el_boton_de_cerrar_quita_ese_producto(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_dos_productos(session)

        texto = client.get("/?productos=arroz,aceite").text

        # El × de Arroz apunta a la vista sin Arroz (queda Aceite), por `href`
        # (fallback) y por `hx-get` (swap parcial).
        assert 'href="/?productos=aceite" hx-get="/?productos=aceite"' in texto

    def test_carga_htmx_y_arma_el_bloque_vivo(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_dos_productos(session)

        texto = client.get("/?productos=arroz").text

        assert '/static/htmx.min.js?v=' in texto
        assert 'id="ph-live"' in texto
        # Los chips hacen `hx-get` además del `href` de fallback.
        assert 'hx-get="/?productos=arroz,aceite"' in texto

    def test_anuncio_no_carga_htmx(
        self, client: TestClient, session: Session
    ) -> None:
        """La Transparency Layer sigue siendo HTML plano: htmx es solo de `/`."""
        _seed_dos_productos(session)
        ingestion = RawIngestion(
            source_type=SourceType.INBOX_TEXT,
            file_path="raw/txt.dat",
            content_hash="hash-anuncio-sin-htmx",
            content_type=ContentType.TEXT,
            processing_status=ProcessingStatus.CURATED,
            original_text="Aceite a 1200",
            captured_at=datetime(2026, 8, 10, tzinfo=UTC),
        )
        session.add(ingestion)
        session.commit()

        texto = client.get(f"/anuncio/{ingestion.id}").text

        assert "htmx.min.js" not in texto


class TestEnlacesDeProvinciaEnElMapa:
    """Hasta el 2026-09-11 esta clase probaba también la hoja de detalle de
    provincia (modal a pantalla completa); se sacó del todo el 2026-09-12
    —tapaba el mapa, que ya se filtra y hace zoom solo con elegir la
    provincia—. Lo que queda es lo que no dependía de ella: que el mapa
    enlace a la vista filtrada, y los chips de producto."""

    def _seed(self, session: Session) -> None:
        """Arroz y Aceite con dato en La Habana; Malanga piloto pero sin filas."""
        arroz = Product(
            name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True
        )
        aceite = Product(
            name="Aceite", category="grasas", unit="lb", aliases=[], is_pilot=True
        )
        malanga = Product(
            name="Malanga", category="viandas", unit="lb", aliases=[], is_pilot=True
        )
        habana = Province(code="hab", name="La Habana")
        session.add_all([arroz, aceite, malanga, habana])
        session.commit()
        fuente = _ingestion(session, hash_="h-hoja")
        _precio(session, product=arroz, province=habana, source=fuente, dia=_hace(6))
        _precio(
            session, product=arroz, province=habana, source=fuente,
            dia=_hace(3), price="330",
        )
        _precio(session, product=aceite, province=habana, source=fuente, dia=_hace(4))
        session.commit()

    def test_las_provincias_del_mapa_son_enlaces(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)

        texto = client.get("/").text

        assert 'href="/?provincia=la-habana" hx-get="/?provincia=la-habana"' in texto

    def test_elegir_provincia_ya_no_abre_ninguna_hoja(
        self, client: TestClient, session: Session
    ) -> None:
        """La hoja de detalle (modal a pantalla completa) se sacó del todo
        el 2026-09-12: tapaba el mapa, que ya se zoomea y filtra solo con
        elegir la provincia."""
        self._seed(session)

        texto = client.get("/?provincia=la-habana").text

        assert "ph-sheet" not in texto

    def test_un_piloto_sin_icono_sigue_mostrando_su_nombre(
        self, client: TestClient, session: Session
    ) -> None:
        """Malanga es piloto y no está en `PRODUCT_ICONS`: su chip no queda
        vacío, muestra el nombre como antes."""
        self._seed(session)

        texto = client.get("/").text

        assert 'aria-pressed="false">Malanga</a>' in texto


class TestProvinciasLadoALadoEnLaRuta:
    """Slice 2 de los gráficos: «¿dónde está más caro?» en la ventana Precios,
    con la provincia elegida (si hay una) resaltada sobre las demás."""

    def _seed(self, session: Session) -> None:
        """Arroz más caro en Santiago (400–420) que en La Habana (300–340)."""
        arroz = Product(
            name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True
        )
        habana = Province(code="hab", name="La Habana")
        santiago = Province(code="scu", name="Santiago de Cuba")
        session.add_all([arroz, habana, santiago])
        session.commit()
        fuente = _ingestion(session, hash_="h-lado")
        for dias, provincia, precio in (
            (6, habana, "300"),
            (3, habana, "340"),
            (5, santiago, "400"),
            (2, santiago, "420"),
        ):
            _precio(session, product=arroz, province=provincia, source=fuente, dia=_hace(dias), price=precio)
        session.commit()

    def test_la_ventana_precios_compara_las_provincias_la_mas_cara_arriba(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)

        texto = client.get("/?ver=precios").text
        precios = texto.split('id="precios"')[1].split("</section>")[0]

        assert 'class="ph-compara"' in precios
        assert precios.index("Santiago de Cuba") < precios.index("La Habana")

    def test_con_provincia_elegida_la_ventana_precios_la_resalta(
        self, client: TestClient, session: Session
    ) -> None:
        """Hasta el 2026-09-11 esto se veía en la hoja de detalle; la hoja se
        sacó del todo el 2026-09-12 y la ventana «Precios» pasó a ser el
        único lugar donde se ve esta comparación, con o sin provincia
        elegida — antes, con provincia elegida, mostraba solo una nota
        apuntando a la hoja en vez del gráfico."""
        self._seed(session)

        texto = client.get("/?ver=precios&provincia=la-habana").text
        precios = texto.split('id="precios"')[1].split("</section>")[0]

        assert 'class="ph-compara"' in precios
        # Santiago se atenúa; La Habana, la elegida, no.
        assert precios.count('class="ph-compara-fila is-atenuada"') == 1
        assert "La Habana se destaca sobre las demás." in precios

    def test_con_chip_prendido_la_ventana_precios_no_repite_el_panel(
        self, client: TestClient, session: Session
    ) -> None:
        # El panel flotante de Arroz (a la derecha) ya desglosa por
        # provincia; repetirlo en Precios era el mismo dato dos veces.
        self._seed(session)

        texto = client.get("/?ver=precios&productos=arroz").text
        precios = texto.split('id="precios"')[1].split("</section>")[0]

        assert 'class="ph-compara"' not in precios


class TestComoSeMovioEnLaRuta:
    """Slice 3 de los gráficos: la ventana «Cómo se movió» muestra, por
    producto, mes contra mes, las provincias en el tiempo y el calendario."""

    def _seed(self, session: Session) -> None:
        """Arroz en dos semanas distintas por provincia, y un anuncio de La
        Habana en el período anterior (40 días atrás, con la ventana de 30)."""
        arroz = Product(
            name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True
        )
        habana = Province(code="hab", name="La Habana")
        santiago = Province(code="scu", name="Santiago de Cuba")
        session.add_all([arroz, habana, santiago])
        session.commit()
        fuente = _ingestion(session, hash_="h-evo")
        for dias, provincia, precio in (
            (40, habana, "280"),
            (10, habana, "300"),
            (3, habana, "340"),
            (9, santiago, "400"),
            (2, santiago, "420"),
        ):
            _precio(session, product=arroz, province=provincia, source=fuente, dia=_hace(dias), price=precio)
        session.commit()

    @staticmethod
    def _ventana(texto: str) -> str:
        return texto.split('id="evolucion"')[1].split('data-win-grip="e"')[0]

    def test_dibuja_los_tres_graficos_con_leyenda(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)

        evo = self._ventana(client.get("/?ver=evolucion&dias=30").text)

        assert 'class="ph-pendiente"' in evo
        assert 'class="ph-pasos"' in evo
        assert 'class="ph-calendario"' in evo
        leyenda = evo.split('class="ph-evo-leyenda"')[1].split("</ul>")[0]
        assert "La Habana" in leyenda
        assert "Santiago de Cuba" in leyenda

    def test_mes_contra_mes_dice_el_cambio_con_su_muestra(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)

        evo = self._ventana(client.get("/?ver=evolucion&dias=30").text)

        # La Habana: antes 280 (1 anuncio), ahora mediana de 300 y 340 = 320
        # (2 anuncios) → 320 / 280 = 1,143 → +14%.
        assert "320 (2) +14%" in evo

    def test_con_una_provincia_elegida_solo_esa(
        self, client: TestClient, session: Session
    ) -> None:
        # Elegir una provincia dice «solo me interesa esta» (2026-09-11): el
        # hueco del zoom era justo este, la única ventana que seguía
        # mostrando las 16 aunque hubiera una elegida.
        self._seed(session)

        evo = self._ventana(client.get("/?ver=evolucion&dias=30&provincia=la-habana").text)

        assert "Arroz" in evo
        assert "Santiago de Cuba" not in evo
        assert "Ventana de 30 días · La Habana" in evo


class TestResolveViews:
    def test_vacio_ante_none_o_cadena_vacia(self) -> None:
        assert resolve_views(None) == []
        assert resolve_views("") == []

    def test_en_orden_de_apertura_sin_repetir_ni_desconocidas(self) -> None:
        assert resolve_views("precios, cobertura,precios,inventada") == [
            "precios",
            "cobertura",
        ]


class TestBuildViewMenu:
    def test_un_item_por_ventana_en_el_orden_del_menu(self) -> None:
        menu = build_view_menu([])

        assert [v.key for v in menu] == ["cobertura", "precios", "evolucion"]
        assert all(not v.is_open for v in menu)

    def test_abrir_agrega_al_final_y_cerrar_quita(self) -> None:
        por_key = {v.key: v for v in build_view_menu(["precios"])}

        assert por_key["precios"].is_open
        assert por_key["precios"].query == "/"
        assert por_key["cobertura"].query == "/?ver=precios,cobertura"

    def test_preserva_productos_provincia_y_dias(self) -> None:
        menu = build_view_menu(
            [],
            active_products=["Arroz"],
            selected_province="La Habana",
            selected_days=7,
        )

        assert menu[1].query == (
            "/?productos=arroz&provincia=la-habana&dias=7&ver=precios"
        )


class TestVentanasDelMenu:
    """La página es el mapa: cobertura, precios y evolución son ventanas
    flotantes que se abren desde el menú del header, varias a la vez."""

    def test_sin_ver_todas_las_ventanas_estan_cerradas(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_dos_productos(session)

        texto = client.get("/").text

        for key in ("cobertura", "precios", "evolucion"):
            assert f'id="{key}" aria-labelledby="ph-win-{key}-t" hidden>' in texto

    def test_ver_abre_varias_a_la_vez(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_dos_productos(session)

        texto = client.get("/?ver=precios,evolucion").text

        assert 'id="precios" aria-labelledby="ph-win-precios-t">' in texto
        assert 'id="evolucion" aria-labelledby="ph-win-evolucion-t">' in texto
        assert 'id="cobertura" aria-labelledby="ph-win-cobertura-t" hidden>' in texto

    def test_el_menu_marca_las_abiertas(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_dos_productos(session)

        texto = client.get("/?ver=precios").text

        assert 'class="ph-menu-item is-open" href="/" hx-get="/" aria-pressed="true"' in texto

    def test_prender_un_chip_no_cierra_las_ventanas(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_dos_productos(session)

        texto = client.get("/?ver=precios").text

        assert 'hx-get="/?productos=arroz&amp;ver=precios"' in texto

    def test_elegir_provincia_no_cierra_las_ventanas(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_dos_productos(session)

        texto = client.get("/?ver=cobertura").text

        assert 'hx-get="/?provincia=la-habana&amp;ver=cobertura"' in texto

    def test_el_filtro_de_provincia_preserva_chips_y_ventanas(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_dos_productos(session)

        texto = client.get("/?productos=arroz,aceite&ver=precios").text

        assert (
            'hx-get="/?productos=arroz,aceite&amp;provincia=la-habana&amp;ver=precios"'
            in texto
        )

    def test_ver_detalle_del_panel_abre_precios_sin_cerrar_otras(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_dos_productos(session)

        texto = client.get("/?productos=arroz&ver=cobertura").text

        assert (
            'class="ph-fpanel-detail" href="/?productos=arroz&amp;ver=cobertura,precios"'
            in texto
        )


class TestVentanasArrastrables:
    """Ventanas del menú y paneles de producto comparten forma: barra de título
    arrastrable y botones de minimizar y expandir. El comportamiento es JS
    (mejora progresiva); acá se afirma la marca que el script necesita."""

    def test_los_paneles_arrancan_expandidos(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_dos_productos(session)

        texto = client.get("/?productos=arroz,aceite").text

        assert texto.count('<section class="ph-fpanel" data-win=') == 2
        # Nada sale minimizado ni expandido del servidor: el estado lo aplica
        # el script en el navegador.
        assert 'class="ph-fpanel is-' not in texto
        assert 'class="ph-win is-' not in texto

    def test_la_ventana_y_su_item_de_menu_se_emparejan_por_aria_controls(
        self, client: TestClient, session: Session
    ) -> None:
        """Contrato del cierre optimista (2026-09-11): al tocar la × de una
        ventana, el JS la esconde y apaga su ítem del menú antes de que
        vuelva la respuesta, y los encuentra por el `aria-controls` del ítem
        apuntando al `id` de la ventana. Ya existían los dos —no se agregó
        marca nueva—, pero ahora hay código que depende de que sigan
        coincidiendo."""
        _seed_dos_productos(session)

        texto = client.get("/?ver=cobertura").text

        assert 'aria-controls="cobertura"' in texto
        assert 'id="cobertura"' in texto

    def test_cada_ventana_tiene_barra_y_controles(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_dos_productos(session)

        texto = client.get("/?productos=arroz&ver=precios").text

        # 3 ventanas del menú + 1 panel de producto.
        barras = texto.count('<header class="ph-win-head" data-win-handle>') + texto.count(
            '<header class="ph-fpanel-head" data-win-handle>'
        )
        assert barras == 4
        assert texto.count('data-win-min aria-pressed="false"') == 4
        assert texto.count('data-win-max aria-pressed="false"') == 4
        assert 'data-win="Precios" id="precios"' in texto
        assert 'id="ph-panel-arroz"' in texto

    def test_cada_ventana_tiene_agarres_para_redimensionar(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_dos_productos(session)

        texto = client.get("/?productos=arroz&ver=precios").text

        # 3 ventanas del menú + 1 panel: borde derecho, inferior y esquina.
        for direccion in ("e", "s", "se"):
            assert texto.count(f'data-win-grip="{direccion}"') == 4

    def test_el_script_de_ventanas_viaja_en_la_pagina(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_dos_productos(session)

        texto = client.get("/").text

        assert 'classList.add("ph-js")' in texto
        # Va en el bloque de scripts, después de htmx y fuera de `#ph-live`:
        # htmx no lo re-ejecuta en cada swap.
        assert texto.index('classList.add("ph-js")') > texto.index("htmx.min.js")


class TestResolveProvince:
    def test_resuelve_por_slug_con_espacios(self) -> None:
        nombres = ("La Habana", "Santiago de Cuba")
        assert resolve_province(nombres, "la-habana") == "La Habana"
        assert resolve_province(nombres, "santiago-de-cuba") == "Santiago de Cuba"

    def test_slug_vacio_o_desconocido_no_selecciona(self) -> None:
        assert resolve_province(("La Habana",), "") is None
        assert resolve_province(("La Habana",), None) is None
        assert resolve_province(("La Habana",), "matanzas") is None


class TestBuildProvinceNav:
    def test_todas_va_primero_y_activo_sin_seleccion(self) -> None:
        nav = build_province_nav(("La Habana", "Santiago de Cuba"), None)

        assert nav[0].label == "Todas"
        assert nav[0].slug == ""
        assert nav[0].is_selected
        assert all(not c.is_selected for c in nav[1:])

    def test_preserva_producto_y_ventana_en_el_query(self) -> None:
        nav = build_province_nav(
            ("La Habana",), "La Habana", selected_product="Arroz", selected_days=7
        )
        por_label = {c.label: c for c in nav}

        assert por_label["Todas"].query == "/?producto=arroz&dias=7"
        assert (
            por_label["La Habana"].query
            == "/?producto=arroz&provincia=la-habana&dias=7"
        )


class TestResolveDays:
    def test_acepta_las_tres_ventanas(self) -> None:
        assert resolve_days("7") == 7
        assert resolve_days("30") == 30
        assert resolve_days("90") == 90
        assert resolve_days(7) == 7

    def test_cae_al_default_ante_basura(self) -> None:
        assert resolve_days(None) == DEFAULT_WINDOW_DAYS
        assert resolve_days("abc") == DEFAULT_WINDOW_DAYS
        assert resolve_days("31") == DEFAULT_WINDOW_DAYS
        assert resolve_days("-7") == DEFAULT_WINDOW_DAYS


class TestResolveLayer:
    def test_acepta_las_dos_capas(self) -> None:
        assert resolve_layer("publicacion") == "publicacion"
        assert resolve_layer("densidad") == "densidad"

    def test_cae_al_default_ante_basura(self) -> None:
        assert resolve_layer(None) == DEFAULT_LAYER
        assert resolve_layer("") == DEFAULT_LAYER
        assert resolve_layer("precio") == DEFAULT_LAYER


class TestBuildLayerNav:
    def test_dos_capas_marcando_la_activa(self) -> None:
        elegidas = build_layer_nav("densidad")
        por_slug = {c.slug: c for c in elegidas}

        assert len(elegidas) == 2
        assert por_slug["densidad"].is_selected
        assert not por_slug["publicacion"].is_selected

    def test_el_enlace_preserva_productos_provincia_dias_y_ventanas(self) -> None:
        elegidas = build_layer_nav(
            "publicacion",
            active_products=["Arroz"],
            selected_province="La Habana",
            selected_days=7,
            ver=["cobertura"],
        )
        por_slug = {c.slug: c for c in elegidas}

        assert por_slug["densidad"].query == (
            "/?productos=arroz&provincia=la-habana&dias=7&ver=cobertura&capa=densidad"
        )
        # La propia capa activa no ensucia la URL con `capa=publicacion`.
        assert "capa=" not in por_slug["publicacion"].query


class TestBuildWindowNav:
    def test_tres_opciones_con_la_activa_marcada(self) -> None:
        nav = build_window_nav(7)
        por_dias = {c.days: c for c in nav}

        assert set(por_dias) == {7, 30, 90}
        assert por_dias[7].is_selected
        assert not por_dias[30].is_selected

    def test_preserva_el_producto_elegido_en_el_query(self) -> None:
        nav = build_window_nav(30, selected_name="Arroz")
        por_dias = {c.days: c for c in nav}

        # 30 es el default: no ensucia la URL con `dias=30`.
        assert por_dias[30].query == "/?producto=arroz"
        assert por_dias[7].query == "/?producto=arroz&dias=7"


# --- ruta pública ----------------------------------------------------------


class TestFiltroEnLaRuta:
    def test_sin_filtro_aparecen_los_productos_con_dato(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_dos_productos(session)

        texto = client.get("/").text

        assert "Arroz" in texto
        assert "Aceite" in texto
        # El riel de chips de producto está, sin ninguno prendido: `?productos=`
        # vacío = se muestra todo.
        assert 'class="ph-chip-rail"' in texto
        assert 'aria-pressed="false"><span class="m3-icon ph-chip-icon" aria-hidden="true">rice_bowl</span><span class="ph-chip-name">Arroz</span></a>' in texto
        assert 'aria-pressed="true"' not in texto

    def test_elegir_un_producto_acota_la_vista(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_dos_productos(session)

        texto = client.get("/?producto=arroz").text

        # Producto enfocado: tabla por provincia (ver TestTablaPorProvincia).
        # La barra de filtro nombra todos los productos; los datos mostrados son
        # solo del elegido.
        tabla = texto.split("ph-provinces")[1].split("</table>")[0]
        assert "Arroz por formato y provincia" in texto
        assert "Aceite" not in tabla

    def test_resuelve_un_nombre_con_tilde(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_dos_productos(session)

        texto = client.get("/?producto=azucar-blanca").text

        assert "Azúcar blanca por formato y provincia" in texto
        tabla = texto.split("ph-provinces")[1].split("</table>")[0]
        assert "Arroz" not in tabla

    def test_producto_inexistente_no_da_404(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_dos_productos(session)

        response = client.get("/?producto=leche-en-polvo")

        assert response.status_code == 200
        # Slug raro → ningún producto activo, todos los chips apagados.
        assert 'class="ph-chip-rail"' in response.text
        assert 'aria-pressed="true"' not in response.text

    def test_el_mapa_de_cobertura_no_depende_del_filtro(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_dos_productos(session)

        texto = client.get("/?producto=arroz").text

        assert "La Habana" in texto
        assert "Observaciones por provincia" in texto

    def test_producto_sin_dato_en_la_ventana_avisa_y_no_rompe(
        self, client: TestClient, session: Session
    ) -> None:
        """Picadillo es piloto pero no tiene ninguna fila: la página responde
        200, avisa, y deja la barra para cambiar de producto."""
        _seed_dos_productos(session)
        session.add(
            Product(
                name="Picadillo",
                category="carnes",
                unit="lb",
                aliases=[],
                is_pilot=True,
            )
        )
        session.commit()

        response = client.get("/?producto=picadillo")

        assert response.status_code == 200
        assert "No hay anuncios de Picadillo" in response.text
        # El riel de chips sigue para poder cambiar de producto.
        assert 'class="ph-chip-rail"' in response.text

    def test_producto_con_un_solo_dia_no_dibuja_evolucion_pero_no_rompe(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_dos_productos(session)

        response = client.get("/?producto=aceite")

        assert response.status_code == 200
        # Aceite entra en la tabla por provincia (un día basta) y tiene su cifra
        # en el panel, pero no dibuja evolución (hacen falta 2 días distintos):
        # la ventana «Cómo se movió» lo dice.
        assert "Aceite por formato y provincia" in response.text
        assert '<span class="ph-cifra-valor">300</span>' in response.text
        assert "No hay suficientes días" in response.text


class TestVentanaEnLaRuta:
    def _seed_lejano_y_cercano(self, session: Session) -> None:
        """Arroz con un anuncio a 10 días y otro a 3 días de hoy."""
        arroz = Product(
            name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True
        )
        habana = Province(code="hab", name="La Habana")
        session.add_all([arroz, habana])
        session.commit()
        fuente = _ingestion(session, hash_="h-ventana")
        hoy = date.today()
        _precio(
            session,
            product=arroz,
            province=habana,
            source=fuente,
            dia=hoy - timedelta(days=10),
            price="300",
        )
        _precio(
            session,
            product=arroz,
            province=habana,
            source=fuente,
            dia=hoy - timedelta(days=3),
            price="360",
        )
        session.commit()

    def test_dias_7_recorta_la_muestra(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed_lejano_y_cercano(session)

        texto_30 = client.get("/?producto=arroz&dias=30").text
        texto_7 = client.get("/?producto=arroz&dias=7").text

        assert "Ventana de 30 días" in texto_30
        assert "Ventana de 7 días" in texto_7
        # A 30 días entran los dos (rango 300–360); a 7 solo el de 360.
        assert "300" in texto_30
        assert "300" not in texto_7.split('id="precios"')[1].split("</section>")[0]

    def test_dias_invalido_cae_al_default(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed_lejano_y_cercano(session)

        response = client.get("/?dias=999")

        assert response.status_code == 200
        assert "Ventana de 30 días" in response.text

    def test_ventana_y_producto_se_combinan(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed_lejano_y_cercano(session)

        texto = client.get("/?dias=90&producto=arroz").text

        assert "Ventana de 90 días" in texto
        assert 'aria-current="true">90 días<' in texto
        assert 'aria-pressed="true"><span class="m3-icon ph-chip-icon" aria-hidden="true">rice_bowl</span><span class="ph-chip-name">Arroz</span></a>' in texto


class TestTablaPorProvincia:
    def _seed_arroz_dos_provincias(self, session: Session) -> None:
        """Arroz en La Habana (3 anuncios, hay rango) y Santiago (1, dato suelto)."""
        arroz = Product(
            name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True
        )
        habana = Province(code="hab", name="La Habana")
        santiago = Province(code="scu", name="Santiago de Cuba")
        session.add_all([arroz, habana, santiago])
        session.commit()
        fuente = _ingestion(session, hash_="h-tabla")
        for dia, precio in (
            (_hace(6), "300"),
            (_hace(4), "340"),
            (_hace(2), "320"),
        ):
            _precio(
                session,
                product=arroz,
                province=habana,
                source=fuente,
                dia=dia,
                price=precio,
            )
        _precio(
            session,
            product=arroz,
            province=santiago,
            source=fuente,
            dia=_hace(3),
            price="500",
        )
        session.commit()

    def test_producto_enfocado_muestra_tabla_por_provincia(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed_arroz_dos_provincias(session)

        texto = client.get("/?producto=arroz").text

        assert "ph-provinces" in texto
        tabla = texto.split("ph-provinces")[1].split("</table>")[0]
        assert "La Habana" in tabla
        assert "Santiago de Cuba" in tabla

    def test_la_provincia_con_una_sola_observacion_no_finge_rango(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed_arroz_dos_provincias(session)

        texto = client.get("/?producto=arroz").text
        tabla = texto.split("ph-provinces")[1].split("</table>")[0]
        fila_santiago = tabla.split("Santiago de Cuba")[1].split("</tr>")[0]

        # n=1: mínimo y máximo van como «—», solo la mediana lleva el valor.
        assert fila_santiago.count("—") == 2
        assert "500" in fila_santiago

    def test_la_tabla_ordena_por_cantidad_de_anuncios(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed_arroz_dos_provincias(session)

        tabla = (
            client.get("/?producto=arroz")
            .text.split("ph-provinces")[1]
            .split("</table>")[0]
        )

        assert tabla.index("La Habana") < tabla.index("Santiago de Cuba")

    def test_sin_producto_enfocado_no_hay_tabla_por_provincia(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed_arroz_dos_provincias(session)

        texto = client.get("/").text

        assert "ph-provinces" not in texto
        assert 'class="ph-cards"' in texto


class TestFiltroPorProvinciaEnLaRuta:
    def _seed(self, session: Session) -> None:
        """Arroz en Habana (2 días) y Santiago (1); Aceite solo en Habana."""
        arroz = Product(
            name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True
        )
        aceite = Product(
            name="Aceite", category="grasas", unit="lb", aliases=[], is_pilot=True
        )
        habana = Province(code="hab", name="La Habana")
        santiago = Province(code="scu", name="Santiago de Cuba")
        session.add_all([arroz, aceite, habana, santiago])
        session.commit()
        fuente = _ingestion(session, hash_="h-prov")
        _precio(session, product=arroz, province=habana, source=fuente, dia=_hace(6))
        _precio(
            session,
            product=arroz,
            province=habana,
            source=fuente,
            dia=_hace(3),
            price="330",
        )
        _precio(
            session,
            product=arroz,
            province=santiago,
            source=fuente,
            dia=_hace(4),
            price="410",
        )
        _precio(session, product=aceite, province=habana, source=fuente, dia=_hace(5))
        session.commit()

    def test_elegir_provincia_acota_las_tarjetas_a_esa_provincia(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)

        texto = client.get("/?provincia=santiago-de-cuba").text

        # Santiago solo tiene Arroz; Aceite (solo Habana) no aparece.
        assert '<span class="ph-card-product">Arroz</span>' in texto
        assert '<span class="ph-card-product">Aceite</span>' not in texto
        assert 'aria-current="true">Santiago de Cuba<' in texto

    def test_provincia_y_producto_se_combinan(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)

        texto = client.get("/?provincia=la-habana&producto=arroz").text

        assert 'aria-current="true">La Habana<' in texto
        assert 'aria-pressed="true"><span class="m3-icon ph-chip-icon" aria-hidden="true">rice_bowl</span><span class="ph-chip-name">Arroz</span></a>' in texto
        tabla = texto.split("ph-provinces")[1].split("</table>")[0]
        assert "La Habana" in tabla
        assert "Santiago de Cuba" not in tabla

    def test_provincia_y_producto_acota_el_panel_a_esa_provincia(
        self, client: TestClient, session: Session
    ) -> None:
        """El panel de producto (ícono + mediana + rango + conteo) no lista
        las 16 provincias cuando hay una elegida: solo la enfocada, mismo
        criterio que la tabla y «Cómo se movió» (2026-09-13). El header pasa
        a un ícono ilustrado propio en vez del glifo de Material Symbols."""
        self._seed(session)

        texto = client.get("/?provincia=la-habana&producto=arroz").text
        panel = texto.split('class="ph-fpanel"')[1].split("</section>")[0]

        assert panel.count('class="ph-cifra"') == 1
        assert 'aria-label="Arroz en La Habana"' in panel
        assert "Santiago de Cuba" not in panel
        assert 'class="ph-fpanel-illustration"' in panel
        assert "ph-fpanel-icon" not in panel

    def _insignia(self, texto: str, slug: str) -> str:
        return texto.split(f'id="ph-map-badge-{slug}"')[1].split("</template>")[0]

    def test_provincia_con_un_producto_activo_pone_insignia_en_el_mapa(
        self, client: TestClient, session: Session
    ) -> None:
        """La insignia (ícono + cifra) sobre la provincia se arma con una
        provincia elegida y un producto activo con dato ahí —
        `onEachFeature` la clona con `L.divIcon` (2026-09-13)."""
        self._seed(session)

        texto = client.get("/?provincia=la-habana&producto=arroz").text

        assert 'id="ph-map-badge-la-habana"' in texto
        assert "2 anuncios" in self._insignia(texto, "la-habana")

    def test_sin_provincia_elegida_hay_una_insignia_por_provincia_con_dato(
        self, client: TestClient, session: Session
    ) -> None:
        """Corrección 2026-09-13: la primera versión solo armaba la insignia
        con una `selected_province` elegida — el operador reportó "si no
        tengo ninguna provincia seleccionada y selecciono algún producto
        entonces no se ven". Mirando el país entero tiene que haber una
        insignia por cada provincia con dato, no ninguna."""
        self._seed(session)

        texto = client.get("/?producto=arroz").text

        assert "2 anuncios" in self._insignia(texto, "la-habana")
        assert "1 anuncio" in self._insignia(texto, "santiago-de-cuba")
        # Cada insignia es de su propia provincia, no una combinada.
        assert "Santiago" not in self._insignia(texto, "la-habana")

    def test_provincia_elegida_sin_producto_no_hay_insignia(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)

        texto = client.get("/?provincia=la-habana").text

        assert 'id="ph-map-badge-' not in texto

    def test_con_dos_productos_activos_colapsa_a_cluster(
        self, client: TestClient, session: Session
    ) -> None:
        """La primera versión ocultaba la insignia entera con 2+ productos
        activos para no superponerlas; el operador la vio "desaparecer" al
        prender un segundo chip sin querer. La segunda apilaba sin tope —el
        operador señaló que hoy son 5 productos piloto pero mañana pueden
        ser 200—. Ahora colapsa a un cluster (círculo con la cantidad,
        `<details>`/`<summary>` nativos) que se abre al tocarlo."""
        self._seed(session)

        insignia = self._insignia(
            client.get("/?provincia=la-habana&productos=arroz,aceite").text,
            "la-habana",
        )

        assert 'class="ph-map-badge-root ph-map-badge-cluster"' in insignia
        assert '<summary class="ph-map-badge-count"' in insignia and ">2<" in insignia
        assert insignia.count('class="ph-map-badge"') == 2

    def test_con_un_producto_activo_no_colapsa_a_cluster(
        self, client: TestClient, session: Session
    ) -> None:
        """Con un solo producto no hay nada que clusterizar: se pinta la
        insignia sola, sin el círculo de conteo."""
        self._seed(session)

        insignia = self._insignia(
            client.get("/?provincia=la-habana&producto=arroz").text, "la-habana"
        )

        assert "ph-map-badge-cluster" not in insignia

    def test_producto_activo_sin_dato_en_la_provincia_no_apila_insignia_vacia(
        self, client: TestClient, session: Session
    ) -> None:
        """Aceite no tiene dato en Santiago (solo Habana): con Santiago
        elegida, Arroz sí apila su insignia pero Aceite no deja una vacía."""
        self._seed(session)

        texto = client.get("/?provincia=santiago-de-cuba&productos=arroz,aceite").text

        assert self._insignia(texto, "santiago-de-cuba").count('class="ph-map-badge"') == 1

    def test_provincia_inexistente_no_da_404(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)

        response = client.get("/?provincia=matanzas")

        assert response.status_code == 200
        assert 'aria-current="true">Todas<' in response.text

    def test_provincia_sin_dato_piloto_avisa(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)
        session.add(Province(code="mat", name="Matanzas"))
        session.commit()

        response = client.get("/?provincia=matanzas")

        # Matanzas existe pero no tiene fila piloto: 200 y aviso.
        assert response.status_code == 200
        assert "No hay anuncios" in response.text
        assert "Matanzas" in response.text

    def test_la_barra_de_provincia_esta_con_todas_marcado(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)

        texto = client.get("/").text

        assert 'aria-current="true">Todas<' in texto

    def test_el_select_de_provincia_lista_todas_las_que_tienen_dato(
        self, client: TestClient, session: Session
    ) -> None:
        """Selector visible siempre junto al riel de chips (2026-09-11), no
        solo adentro de la ventana «Precios» — mismo `province_nav`."""
        self._seed(session)

        texto = client.get("/").text

        assert '<select name="provincia" class="ph-province-select"' in texto
        assert '<option value="" selected>Todas</option>' in texto
        assert "La Habana</option>" in texto
        assert "Santiago de Cuba</option>" in texto

    def test_elegir_provincia_marca_la_opcion_en_el_select(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)

        texto = client.get("/?provincia=santiago-de-cuba").text

        assert '<option value="santiago-de-cuba" selected>Santiago de Cuba</option>' in texto
        assert '<option value="" selected>' not in texto

    def test_el_chip_de_provincia_solo_aparece_con_una_elegida(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)

        sin_elegir = client.get("/").text
        con_elegida = client.get("/?provincia=la-habana").text

        assert '<span class="ph-province-chip">' not in sin_elegir
        assert '<span class="ph-province-chip">' in con_elegida
        cuerpo = con_elegida.split('<span class="ph-province-chip">')[1].split("</span>")[0]
        assert "La Habana" in cuerpo

    def test_el_select_preserva_los_otros_ejes_en_campos_ocultos(
        self, client: TestClient, session: Session
    ) -> None:
        """El <form> del selector no puede resetear producto/días/ventanas/capa
        al cambiar de provincia — mismo principio que ya sostiene
        `NavChoice.query` para los enlaces."""
        self._seed(session)

        texto = client.get(
            "/?producto=arroz&dias=90&ver=cobertura&capa=densidad"
        ).text
        bloque = texto.split('<form class="ph-province-form"')[1].split("</form>")[0]

        assert '<input type="hidden" name="productos" value="arroz">' in bloque
        assert '<input type="hidden" name="dias" value="90">' in bloque
        assert '<input type="hidden" name="ver" value="cobertura">' in bloque
        assert '<input type="hidden" name="capa" value="densidad">' in bloque


class TestDescargaCsv:
    """`GET /datos.csv`: la evidencia detrás de la vista, en CSV, con los mismos
    tres filtros que `/`. No expone nada que la página no muestre ya."""

    def _seed(self, session: Session) -> None:
        arroz = Product(
            name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True
        )
        aceite = Product(
            name="Aceite", category="grasas", unit="lb", aliases=[], is_pilot=True
        )
        no_piloto = Product(
            name="Malanga", category="viandas", unit="lb", aliases=[], is_pilot=False
        )
        habana = Province(code="hab", name="La Habana")
        santiago = Province(code="scu", name="Santiago de Cuba")
        session.add_all([arroz, aceite, no_piloto, habana, santiago])
        session.commit()
        fuente = _ingestion(session, hash_="h-csv")
        _precio(session, product=arroz, province=habana, source=fuente, dia=_hace(6))
        _precio(
            session, product=arroz, province=habana, source=fuente,
            dia=_hace(3), price="330",
        )
        _precio(
            session, product=arroz, province=santiago, source=fuente,
            dia=_hace(4), price="410",
        )
        _precio(session, product=aceite, province=habana, source=fuente, dia=_hace(5))
        _precio(session, product=no_piloto, province=habana, source=fuente, dia=_hace(5))
        session.commit()

    def test_devuelve_csv_como_adjunto(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)

        r = client.get("/datos.csv")

        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/csv")
        assert "attachment" in r.headers["content-disposition"]

    def test_cabecera_y_una_fila_por_observacion_piloto(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)

        lineas = client.get("/datos.csv").text.strip().splitlines()

        assert lineas[0] == (
            "producto,provincia,dia,precio_por_unidad,moneda,unidad,anuncio"
        )
        # 4 observaciones piloto (Malanga no cuenta).
        assert len(lineas) == 1 + 4

    def test_respeta_el_filtro_de_producto(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)

        texto = client.get("/datos.csv?producto=arroz").text

        assert "Aceite" not in texto
        assert texto.count("Arroz") == 3

    def test_respeta_el_filtro_de_provincia(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)

        texto = client.get("/datos.csv?provincia=santiago-de-cuba").text

        assert "La Habana" not in texto
        assert "Santiago de Cuba" in texto

    def test_respeta_la_ventana(
        self, client: TestClient, session: Session
    ) -> None:
        # Precios contra la columna `precio_por_unidad`, no contra el texto
        # crudo del CSV: la última columna (`anuncio`) es un UUID al azar
        # (PriceHistory.id) y buscar "999" en todo el texto es un falso
        # positivo latente cada vez que ese UUID —de esta fila o de cualquier
        # otra sembrada por `_seed`— contiene esos tres dígitos seguidos.
        self._seed(session)
        arroz = session.query(Product).filter_by(name="Arroz").one()
        habana = session.query(Province).filter_by(name="La Habana").one()
        fuente = _ingestion(session, hash_="h-csv-lejano")
        _precio(
            session, product=arroz, province=habana, source=fuente,
            dia=date.today() - timedelta(days=20), price="999",
        )
        session.commit()

        def precios(dias: int) -> list[str]:
            texto = client.get(f"/datos.csv?dias={dias}").text
            filas = csv.DictReader(io.StringIO(texto))
            return [fila["precio_por_unidad"] for fila in filas]

        assert "999" in precios(30)
        assert "999" not in precios(7)

    def test_excluye_los_no_piloto(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)

        assert "Malanga" not in client.get("/datos.csv").text

    def test_no_trae_columnas_personales(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)

        texto = client.get("/datos.csv").text.lower()

        for termino in ("channel", "source_reference", "telefono", "phone", "remitente"):
            assert termino not in texto

    def test_la_pagina_enlaza_al_csv_con_los_filtros_activos(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)

        texto = client.get("/?producto=arroz&dias=7").text

        # Jinja escapa el `&` del atributo href.
        assert 'href="/datos.csv?producto=arroz&amp;dias=7"' in texto


class TestFrescuraDelDato:
    """Línea de «datos verificados hasta el …» en la home: señal de antigüedad,
    no veredicto (§12.6). Independiente de los filtros: si el anuncio más
    reciente es viejo, hay que verlo aunque la ventana elegida lo deje afuera.
    """

    def test_toma_el_dia_del_anuncio_piloto_mas_reciente(
        self, client: TestClient, session: Session
    ) -> None:
        arroz = Product(
            name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True
        )
        no_piloto = Product(
            name="Malanga", category="viandas", unit="lb", aliases=[], is_pilot=False
        )
        habana = Province(code="hab", name="La Habana")
        session.add_all([arroz, no_piloto, habana])
        session.commit()
        fuente = _ingestion(session, hash_="h-fresh")
        _precio(session, product=arroz, province=habana, source=fuente, dia=date(2026, 8, 12))
        _precio(session, product=arroz, province=habana, source=fuente, dia=date(2026, 8, 15))
        # Un no-piloto más nuevo no cuenta.
        _precio(
            session, product=no_piloto, province=habana, source=fuente,
            dia=date(2026, 8, 25),
        )
        session.commit()

        texto = client.get("/").text

        assert "Datos verificados hasta el 15/08/2026" in texto

    def test_es_independiente_de_la_ventana(
        self, client: TestClient, session: Session
    ) -> None:
        arroz = Product(
            name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True
        )
        habana = Province(code="hab", name="La Habana")
        session.add_all([arroz, habana])
        session.commit()
        fuente = _ingestion(session, hash_="h-fresh2")
        _precio(session, product=arroz, province=habana, source=fuente, dia=date(2026, 8, 15))
        session.commit()

        # Fecha absoluta a propósito, y vieja a propósito: queda fuera de
        # cualquier ventana razonable, y la línea de frescura igual la muestra.
        # Es lo único de este archivo que NO usa `_hace()`.
        texto = client.get("/?dias=7").text

        assert "Datos verificados hasta el 15/08/2026" in texto

    def test_sin_dato_no_muestra_la_linea(
        self, client: TestClient, session: Session
    ) -> None:
        texto = client.get("/").text

        assert "Datos verificados hasta" not in texto


class TestRutaProductoLimpia:
    """`GET /producto/<slug>`: URL dedicada por producto, misma vista que
    `/?producto=<slug>`. El query string sigue siendo alias válido."""

    def _seed(self, session: Session) -> None:
        arroz = Product(
            name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True
        )
        aceite = Product(
            name="Aceite", category="grasas", unit="lb", aliases=[], is_pilot=True
        )
        azucar = Product(
            name="Azúcar blanca", category="dulces", unit="lb", aliases=[], is_pilot=True
        )
        habana = Province(code="hab", name="La Habana")
        santiago = Province(code="scu", name="Santiago de Cuba")
        session.add_all([arroz, aceite, azucar, habana, santiago])
        session.commit()
        fuente = _ingestion(session, hash_="h-ruta")
        _precio(session, product=arroz, province=habana, source=fuente, dia=_hace(6))
        _precio(
            session, product=arroz, province=habana, source=fuente,
            dia=_hace(3), price="330",
        )
        _precio(
            session, product=arroz, province=santiago, source=fuente,
            dia=_hace(4), price="410",
        )
        _precio(session, product=aceite, province=habana, source=fuente, dia=_hace(5))
        _precio(session, product=azucar, province=habana, source=fuente, dia=_hace(5))
        session.commit()

    def test_muestra_la_vista_enfocada_en_el_producto(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)

        texto = client.get("/producto/arroz").text

        assert "Arroz por formato y provincia" in texto
        tabla = texto.split("ph-provinces")[1].split("</table>")[0]
        assert "Aceite" not in tabla

    def test_resuelve_slug_con_tilde(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)

        r = client.get("/producto/azucar-blanca")

        assert r.status_code == 200
        assert "Azúcar blanca por formato y provincia" in r.text

    def test_slug_inexistente_da_404(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)

        assert client.get("/producto/inventado").status_code == 404

    def test_acepta_provincia_y_dias_como_query(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)

        texto = client.get("/producto/arroz?provincia=la-habana&dias=7").text

        assert "Ventana de 7 días" in texto
        assert 'aria-current="true">La Habana<' in texto
        tabla = texto.split("ph-provinces")[1].split("</table>")[0]
        assert "Santiago de Cuba" not in tabla

    def test_el_query_string_sigue_siendo_alias(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)

        limpia = client.get("/producto/arroz").text
        alias = client.get("/?producto=arroz").text

        # Misma sección de precios (misma tabla por provincia).
        assert "Arroz por formato y provincia" in limpia
        assert "Arroz por formato y provincia" in alias

    def test_el_titulo_refleja_el_producto_enfocado(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)

        enfocado = client.get("/producto/arroz").text
        home = client.get("/").text

        assert "<title>Guani — Arroz — precios</title>" in enfocado
        assert "<title>Guani — Precios y cobertura</title>" in home

    def test_el_kicker_muestra_guani_y_saimc_juntos(
        self, client: TestClient
    ) -> None:
        """Branding (2026-09-11): «Guani» como nombre cercano/avatar, «SAIMC»
        como nombre técnico al lado — ninguno reemplaza al otro."""
        texto = client.get("/").text

        assert "Guani · SAIMC · Fase 0" in texto
        assert 'aria-label="Guani"' in texto  # el avatar del header

    def test_el_indicador_de_carga_usa_el_avatar_y_vive_fuera_de_ph_live(
        self, client: TestClient
    ) -> None:
        """El avatar dobla de indicador de carga (2026-09-11): htmx le agrega
        `htmx-request` a `#ph-loading` en cada `hx-get` de `#ph-live` — tiene
        que estar FUERA de ese bloque, si no el `outerHTML` swap lo borra a
        mitad de la animación en vez de apagarlo prolijo."""
        texto = client.get("/").text

        assert 'hx-indicator="#ph-loading"' in texto
        assert 'class="ph-loading htmx-indicator"' in texto
        assert texto.count('aria-label="Guani"') == 2  # header + indicador

        # `{# /#ph-live #}` no sobrevive al render (es un comentario Jinja);
        # se ancla en el pie de la leyenda del mapa, que es el último markup
        # real de `#ph-live` — el indicador tiene que venir después. El
        # crédito «Natural Earth» servía de ancla hasta el 2026-09-12, pero
        # pasó al `attribution` de Leaflet: ahora se imprime en el JS del
        # final y queda DESPUÉS del indicador sin que eso signifique nada.
        pie_de_la_leyenda = texto.index('class="ph-canvas-count"')
        indicador = texto.index('id="ph-loading"')
        assert pie_de_la_leyenda < indicador  # fuera del bloque que htmx reemplaza


class TestVarianteEnTarjetaPublica:
    """La variante declarada es su propia serie pública, distinguible de la
    genérica (public-price-range: "wherever product series are shown — the
    control matrix and the public range"; el hueco que sdd-tasks encontró en
    el diseño de este cambio)."""

    def test_la_tarjeta_publica_muestra_la_etiqueta_de_variante(
        self, client: TestClient, session: Session
    ) -> None:
        aceite = Product(
            name="Aceite", category="grasas", unit="lb", aliases=[], is_pilot=True
        )
        habana = Province(code="hab", name="La Habana")
        session.add_all([aceite, habana])
        session.commit()
        variante = ProductVariant(
            product_id=aceite.id,
            kind="girasol",
            brand="Vitalín",
            origin="importado",
            kind_key="girasol",
            brand_key="vitalin",
            origin_key="importado",
        )
        session.add(variante)
        session.commit()
        origen = _ingestion(session, hash_="hash-variante-publica")
        session.add(
            PriceHistory(
                product_id=aceite.id,
                province_id=habana.id,
                price=Decimal("450"),
                currency=Currency.CUP.value,
                source_id=origen.id,
                unit="lb",
                sale_format=SaleFormat.RETAIL.value,
                quantity=Decimal("1"),
                published_date=_hace(1),
                variant_id=variante.id,
            )
        )
        session.commit()

        texto = client.get("/").text

        assert "girasol Vitalín importado" in texto
        # `.ph-card-variant` no tenía NINGUNA regla (2026-09-12): el `<div>`
        # con la variante renderizaba de bloque a full width, pegado al
        # titular, sin el tratamiento secundario que ya usa `.ph-card-median`.
        assert 'class="ph-card-variant"' in texto
        assert re.search(r"\.ph-card-variant\b[^{]*\{[^}]*\}", texto)

    def test_la_tarjeta_publica_declara_estilo_para_el_formato(
        self, client: TestClient
    ) -> None:
        """Mismo hallazgo que la variante, para `.ph-card-format` — el `<div>`
        del formato declarado («Bolsa 1kg») que se muestra entre el titular y
        el rango de precio."""
        texto = client.get("/").text

        assert re.search(r"\.ph-card-format\b[^{]*\{[^}]*\}", texto)
