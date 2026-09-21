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
    ProductFormat,
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
    DEFAULT_VISTA,
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
    resolve_vista,
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


def _ventana_default() -> tuple[date, date]:
    """El rango de calendario que la vista arma sin `?desde=`/`?hasta=`/
    `?dias=` en el query string: últimos 30 días desde hoy (2026-09-14, date
    picker). Mismo motivo que `_hace`: fijar una fecha absoluta rompería
    solo cuando el reloj se corre."""
    hoy = date.today()
    return hoy - timedelta(days=30), hoy


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
    format_id: object | None = None,
    currency: str = Currency.CUP.value,
) -> None:
    session.add(
        PriceHistory(
            product_id=product.id,
            province_id=province.id,
            price=Decimal(price),
            currency=currency,
            source_id=source.id,
            unit="lb",
            sale_format=SaleFormat.RETAIL.value,
            quantity=Decimal("1"),
            published_date=dia,
            format_id=format_id,
        )
    )


def _formato(
    session: Session, *, product: Product, packaging: str, quantity: str, unit: str
) -> ProductFormat:
    formato = ProductFormat(
        product_id=product.id, packaging=packaging, quantity=Decimal(quantity), unit=unit
    )
    session.add(formato)
    session.flush()
    return formato


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
    """`build_product_nav` (y `_product_link` por debajo) no está enganchada
    a ningún lado de la app en producción — código huérfano desde el
    2026-09-14 (ver docs/decisions.md, «El chip se queda en el mapa»). Se
    sigue probando igual: sigue siendo parte de la superficie pública del
    módulo, y romperla en silencio sin un test que avise sería peor que no
    usarla."""

    # Rango de 10 días a propósito, no de 30: así el par nunca coincide con
    # el default (últimos 30 días hasta hoy) sin importar qué día real sea
    # "hoy" cuando corra el test — mismo motivo que `_hace`.
    DESDE, HASTA = date(2026, 8, 15), date(2026, 8, 25)

    def test_todos_va_primero_y_activo_sin_seleccion(self) -> None:
        nav = build_product_nav(("Arroz", "Aceite"), None, self.DESDE, self.HASTA)

        assert nav[0].label == "Todos"
        assert nav[0].slug == ""
        assert nav[0].query == f"/?desde={self.DESDE.isoformat()}&hasta={self.HASTA.isoformat()}"
        assert nav[0].is_selected
        assert all(not c.is_selected for c in nav[1:])

    def test_marca_el_producto_elegido(self) -> None:
        nav = build_product_nav(("Arroz", "Aceite"), "Aceite", self.DESDE, self.HASTA)
        por_label = {c.label: c for c in nav}

        assert not por_label["Todos"].is_selected
        assert por_label["Aceite"].is_selected
        assert not por_label["Arroz"].is_selected

    def test_chip_de_producto_usa_la_url_limpia_cuando_coincide_con_el_default(
        self,
    ) -> None:
        """Sin provincia y con el rango por defecto (últimos 30 días hasta
        hoy —`_product_link` compara contra `date.today()` cuando no se le
        pasa `hoy`—), el chip apunta a `/producto/<slug>` en vez de
        `/?producto=<slug>`: más limpio para compartir. Fecha relativa a
        hoy y no absoluta, mismo motivo que `_hace`."""
        desde, hasta = _ventana_default()
        nav = build_product_nav(("Arroz", "Aceite"), None, desde, hasta)
        por_label = {c.label: c for c in nav}

        assert por_label["Arroz"].query == "/producto/arroz"
        assert por_label["Aceite"].query == "/producto/aceite"
        assert por_label["Todos"].query == f"/?desde={desde.isoformat()}&hasta={hasta.isoformat()}"

    def test_con_provincia_el_chip_vuelve_al_query_string(self) -> None:
        con_provincia = build_product_nav(
            ("Arroz",), None, self.DESDE, self.HASTA, selected_province="La Habana"
        )

        assert con_provincia[1].query == (
            f"/?producto=arroz&provincia=la-habana&desde={self.DESDE.isoformat()}"
            f"&hasta={self.HASTA.isoformat()}"
        )

    def test_la_ventana_no_default_viaja_en_el_query_del_producto(self) -> None:
        nav = build_product_nav(("Arroz",), "Arroz", self.DESDE, self.HASTA)
        por_label = {c.label: c for c in nav}

        assert por_label["Todos"].query == f"/?desde={self.DESDE.isoformat()}&hasta={self.HASTA.isoformat()}"
        assert por_label["Arroz"].query == (
            f"/?producto=arroz&desde={self.DESDE.isoformat()}&hasta={self.HASTA.isoformat()}"
        )


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
    DESDE, HASTA = date(2026, 8, 15), date(2026, 9, 14)

    def _chips(self, active: list[str], **kwargs: object) -> list:
        kwargs.setdefault("selected_desde", self.DESDE)
        kwargs.setdefault("selected_hasta", self.HASTA)
        return build_product_chips(self.PILOT, active, **kwargs)  # type: ignore[arg-type]

    def test_un_chip_por_piloto_en_orden_de_catalogo(self) -> None:
        chips = self._chips([])

        assert [c.label for c in chips] == ["Arroz", "Aceite", "Pollo"]
        assert all(not c.is_active for c in chips)

    def test_marca_activos_los_del_conjunto(self) -> None:
        chips = self._chips(["Aceite"])
        por_label = {c.label: c for c in chips}

        assert por_label["Aceite"].is_active
        assert not por_label["Arroz"].is_active

    def test_togglear_un_apagado_lo_agrega_al_final(self) -> None:
        chips = self._chips(["Arroz"])
        por_label = {c.label: c for c in chips}

        # Prender Aceite → productos=arroz,aceite (orden de activación).
        assert por_label["Aceite"].query == (
            f"/?productos=arroz,aceite&desde={self.DESDE.isoformat()}&hasta={self.HASTA.isoformat()}"
        )

    def test_un_chip_prendido_no_se_apaga_al_clickearlo(self) -> None:
        # Clic en un chip ya activo no lo quita del conjunto: cerrar un
        # producto es cosa de la × de su panel, no del riel de chips (antes
        # sí lo apagaba, y era fácil cerrar un panel sin querer).
        chips = self._chips(["Arroz", "Aceite"])
        por_label = {c.label: c for c in chips}

        assert por_label["Arroz"].query == (
            f"/?productos=arroz,aceite&desde={self.DESDE.isoformat()}&hasta={self.HASTA.isoformat()}"
        )
        chips_uno = self._chips(["Arroz"])
        assert {c.label: c for c in chips_uno}["Arroz"].query == (
            f"/?productos=arroz&desde={self.DESDE.isoformat()}&hasta={self.HASTA.isoformat()}"
        )

    def test_con_toggle_true_un_chip_prendido_si_se_apaga(self) -> None:
        """Tablero (2026-09-16): no hay panel flotante con su propia × ahí,
        así que `toggle=True` invierte el comportamiento de arriba."""
        chips = self._chips(["Arroz", "Aceite"], toggle=True)
        por_label = {c.label: c for c in chips}

        assert por_label["Arroz"].query == (
            f"/?productos=aceite&desde={self.DESDE.isoformat()}&hasta={self.HASTA.isoformat()}"
        )

    def test_preserva_provincia_y_rango_en_el_toggle(self) -> None:
        chips = self._chips(["Arroz"], selected_province="La Habana")
        por_label = {c.label: c for c in chips}

        assert por_label["Aceite"].query == (
            f"/?productos=arroz,aceite&provincia=la-habana&desde={self.DESDE.isoformat()}"
            f"&hasta={self.HASTA.isoformat()}"
        )

    def test_el_chip_trae_el_icono_del_producto_o_ninguno(self) -> None:
        chips = build_product_chips(
            ("Arroz", "Malanga"), [], selected_desde=self.DESDE, selected_hasta=self.HASTA
        )
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

    def test_el_chip_siempre_se_queda_en_el_mapa(
        self, client: TestClient, session: Session
    ) -> None:
        """El chip nunca salta a la vista liviana de `/producto/<slug>»
        (2026-09-14, ajuste del operador: «no me gusta que vaya directo a
        esa vista») — siempre `?productos=`, siempre con `hx-get`."""
        _seed_dos_productos(session)
        desde, hasta = _ventana_default()

        texto = client.get("/").text

        assert (
            f'<a class="ph-chip-toggle is-icon" href="/?productos=arroz&amp;desde={desde.isoformat()}'
            f'&amp;hasta={hasta.isoformat()}" hx-get="/?productos=arroz&amp;desde={desde.isoformat()}'
            f'&amp;hasta={hasta.isoformat()}" data-product="arroz" aria-pressed="false">'
        ) in texto

    def test_el_panel_flotante_enlaza_a_la_vista_simple_sin_hx_get(
        self, client: TestClient, session: Session
    ) -> None:
        """La vista liviana se alcanza DESDE el panel flotante, no desde el
        riel de chips: `light_query` en el panel, sin `hx-get` porque esa
        vista no tiene `#ph-live` que un swap parcial pueda encontrar."""
        _seed_dos_productos(session)
        desde, hasta = _ventana_default()

        texto = client.get("/?productos=arroz").text

        panel = texto.split('id="ph-panel-arroz"')[1].split("</section>")[0]
        assert (
            f'<a class="ph-fpanel-detail" href="/producto/arroz?desde={desde.isoformat()}'
            f'&amp;hasta={hasta.isoformat()}">Vista simple para compartir</a>'
        ) in panel
        assert "hx-get=\"/producto/arroz" not in panel

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
        desde, hasta = _ventana_default()

        texto = client.get("/?productos=arroz,aceite").text

        assert (
            f'href="/datos.csv?productos=arroz,aceite&amp;desde={desde.isoformat()}'
            f'&amp;hasta={hasta.isoformat()}"'
        ) in texto

    def test_datos_csv_respeta_productos(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_dos_productos(session)

        texto = client.get("/datos.csv?productos=arroz,aceite").text

        assert "Arroz" in texto
        assert "Aceite" in texto
        assert "Azúcar blanca" not in texto


class TestOverflowDeChips:
    """Rediseño horizontal (2026-09-14, pedido del operador — «los
    productos son 5 hoy, el día que haya más se pone un ícono para abrir
    el modal con los otros elementos»): el riel se autolimita a los
    primeros `VISIBLE_CHIP_COUNT` (5) productos piloto; el resto se pliega
    detrás de un disparador `<details>`, sin JS propio, mismo patrón que
    el cluster de la insignia del mapa."""

    def _seed(self, session: Session, nombres: list[str]) -> None:
        for nombre in nombres:
            session.add(
                Product(name=nombre, category="varios", unit="lb", aliases=[], is_pilot=True)
            )
        session.commit()

    def test_con_cinco_o_menos_no_aparece_el_disparador(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session, ["Arroz", "Aceite", "Azúcar", "Pollo", "Picadillo"])

        texto = client.get("/").text

        assert 'class="ph-chip-overflow"' not in texto

    def test_con_mas_de_cinco_el_disparador_muestra_el_resto(
        self, client: TestClient, session: Session
    ) -> None:
        """`pilot_product_names` ordena alfabético: con 7 productos, los 5
        primeros van al riel visible y los últimos 2 al panel."""
        self._seed(
            session,
            ["Aceite", "Arroz", "Azúcar", "Boniato", "Calabaza", "Frijol", "Malanga"],
        )

        texto = client.get("/").text

        assert 'class="ph-chip-overflow"' in texto
        assert 'aria-label="Ver 2 productos más"' in texto
        rail = texto.split('class="ph-chip-rail"')[1].split("</nav>")[0]
        visible, panel = rail.split('class="ph-filter-popover"')

        for nombre in ("Aceite", "Arroz", "Azúcar", "Boniato", "Calabaza"):
            assert nombre in visible
        for nombre in ("Frijol", "Malanga"):
            assert nombre not in visible
            assert nombre in panel

    def test_un_solo_producto_de_mas_usa_singular(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(
            session, ["Aceite", "Arroz", "Azúcar", "Boniato", "Calabaza", "Frijol"]
        )

        texto = client.get("/").text

        assert 'aria-label="Ver 1 producto más"' in texto


class TestDatePickerSiempreVisible:
    """Date picker de rango de calendario (2026-09-14, pedido del operador:
    «que siempre se pueda ver el rango en uso y que se pueda cambiar»).
    Vive en `.ph-compass-filters` —la misma isla del riel de chips y el
    selector de provincia—, no escondido dentro de la ventana «Precios»."""

    def test_los_campos_de_fecha_muestran_el_rango_en_uso(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_dos_productos(session)
        desde = date(2026, 8, 1)
        hasta = date(2026, 8, 20)

        texto = client.get(f"/?desde={desde.isoformat()}&hasta={hasta.isoformat()}").text

        bloque = texto.split('<form class="ph-window-form"')[1].split("</form>")[0]
        assert f'<input type="date" name="desde" value="{desde.isoformat()}">' in bloque
        assert f'<input type="date" name="hasta" value="{hasta.isoformat()}">' in bloque

    def test_el_rango_por_defecto_tambien_se_ve_precargado(
        self, client: TestClient, session: Session
    ) -> None:
        """Sin ningún filtro puesto, el picker igual muestra el rango que
        está en uso —los últimos 30 días—, no queda vacío."""
        _seed_dos_productos(session)
        desde, hasta = _ventana_default()

        texto = client.get("/").text

        bloque = texto.split('<form class="ph-window-form"')[1].split("</form>")[0]
        assert f'<input type="date" name="desde" value="{desde.isoformat()}">' in bloque
        assert f'<input type="date" name="hasta" value="{hasta.isoformat()}">' in bloque

    def test_el_formulario_preserva_productos_provincia_y_ventanas(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_dos_productos(session)

        texto = client.get(
            "/?productos=arroz&provincia=la-habana&ver=cobertura&capa=densidad"
        ).text

        bloque = texto.split('<form class="ph-window-form"')[1].split("</form>")[0]
        assert '<input type="hidden" name="productos" value="arroz">' in bloque
        assert '<input type="hidden" name="provincia" value="la-habana">' in bloque
        assert '<input type="hidden" name="ver" value="cobertura">' in bloque
        assert '<input type="hidden" name="capa" value="densidad">' in bloque

    def test_los_atajos_de_siempre_siguen_ahi_junto_al_picker(
        self, client: TestClient, session: Session
    ) -> None:
        """7/30/90 días no desaparecen: quedan como acceso rápido junto al
        picker, no reemplazados por él."""
        _seed_dos_productos(session)

        texto = client.get("/").text

        assert 'class="ph-window-shortcuts"' in texto
        # Por prefijo y no por el atributo entero: la lista de clases del
        # atajo creció con `ph-tap-44` (2026-09-17) y una comparación exacta
        # se rompe cada vez que se le suma una clase, sin que cambie nada de
        # lo que este caso quiere proteger.
        assert texto.count('class="ph-window-shortcut ') == 3

    def test_el_atajo_del_rango_activo_queda_marcado(
        self, client: TestClient, session: Session
    ) -> None:
        """Sin filtro, el rango efectivo son los 30 días de siempre: el
        atajo «30 días» —y solo ese— queda marcado."""
        _seed_dos_productos(session)

        texto = client.get("/").text

        bloque = texto.split('class="ph-window-shortcuts"')[1].split("</div>")[0]
        assert "ph-window-shortcut" in bloque
        assert bloque.count("is-selected") == 1
        assert bloque.split("is-selected")[1].split("</a>")[0].endswith(">30 días")


class TestTendenciaEnLaTarjeta:
    def test_la_tarjeta_muestra_el_cambio_vs_el_periodo_anterior(
        self, client: TestClient, session: Session
    ) -> None:
        """`.ph-card` contesta "¿sube o baja?" (spec.md §6.7) sin abrir el
        panel flotante — mismo `Headline.change_text` que ya usaba el panel,
        reusado en la tarjeta simple (2026-09-14), y el ícono ilustrado como
        ancla visual en vez de solo el nombre."""
        arroz = Product(
            name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True
        )
        habana = Province(code="hab", name="La Habana")
        session.add_all([arroz, habana])
        session.commit()
        fuente = _ingestion(session, hash_="h-tendencia")
        _precio(session, product=arroz, province=habana, source=fuente, dia=_hace(10), price="330")
        _precio(session, product=arroz, province=habana, source=fuente, dia=_hace(40), price="300")
        session.commit()

        texto = client.get("/").text
        tarjeta = texto.split('class="ph-surface ph-card"')[1].split("</li>")[0]

        assert '<div class="ph-cifra-cambio ph-card-trend">+10%' in tarjeta
        assert "vs. los 30 días anteriores" in tarjeta
        assert 'class="ph-fpanel-illustration"' in tarjeta

    def test_sin_periodo_anterior_la_tarjeta_no_muestra_cambio(
        self, client: TestClient, session: Session
    ) -> None:
        arroz = Product(
            name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True
        )
        habana = Province(code="hab", name="La Habana")
        session.add_all([arroz, habana])
        session.commit()
        fuente = _ingestion(session, hash_="h-sin-tendencia")
        _precio(session, product=arroz, province=habana, source=fuente, dia=_hace(5))
        session.commit()

        texto = client.get("/").text
        tarjeta = texto.split('class="ph-surface ph-card"')[1].split("</li>")[0]

        assert "ph-card-trend" not in tarjeta


class TestFormatoEnElPanel:
    def test_dos_formatos_en_la_misma_provincia_dan_dos_cifras(
        self, client: TestClient, session: Session
    ) -> None:
        """2026-09-14: el panel flotante ya no funde botella y garrafa del
        mismo producto en una sola cifra — mismo eje de formato que ya
        distinguía `/control` (`cells.py`) y la tabla enfocada de
        `#precios`."""
        aceite = Product(
            name="Aceite", category="grasas", unit="lb", aliases=[], is_pilot=True
        )
        habana = Province(code="hab", name="La Habana")
        session.add_all([aceite, habana])
        session.commit()
        botella = _formato(session, product=aceite, packaging="botella", quantity="1", unit="L")
        pomo = _formato(session, product=aceite, packaging="pomo", quantity="5", unit="L")
        fuente = _ingestion(session, hash_="h-formato")
        _precio(session, product=aceite, province=habana, source=fuente, dia=_hace(5), format_id=botella.id)
        _precio(session, product=aceite, province=habana, source=fuente, dia=_hace(4), format_id=botella.id)
        _precio(session, product=aceite, province=habana, source=fuente, dia=_hace(3), format_id=pomo.id)
        session.commit()

        texto = client.get("/?producto=aceite").text
        panel = texto.split('id="ph-panel-aceite"')[1].split('<a class="ph-fpanel-detail"')[0]

        assert panel.count('class="ph-cifra"') == 2
        assert "La Habana" in panel
        assert '<span class="ph-chip">Botella de 1 L</span>' in panel
        assert '<span class="ph-chip">Pomo de 5 L</span>' in panel

    def test_solo_el_primer_bloque_arranca_abierto(
        self, client: TestClient, session: Session
    ) -> None:
        """El operador lo pidió al ver el panel crecer con la separación
        por formato: con 2+ bloques, solo el de más muestra (que ya viene
        primero) arranca abierto; el resto colapsa a su `<summary>`."""
        aceite = Product(
            name="Aceite", category="grasas", unit="lb", aliases=[], is_pilot=True
        )
        habana = Province(code="hab", name="La Habana")
        session.add_all([aceite, habana])
        session.commit()
        botella = _formato(session, product=aceite, packaging="botella", quantity="1", unit="L")
        pomo = _formato(session, product=aceite, packaging="pomo", quantity="5", unit="L")
        fuente = _ingestion(session, hash_="h-abierto")
        # Botella: 2 anuncios (más muestra, va primero). Pomo: 1.
        _precio(session, product=aceite, province=habana, source=fuente, dia=_hace(5), format_id=botella.id)
        _precio(session, product=aceite, province=habana, source=fuente, dia=_hace(4), format_id=botella.id)
        _precio(session, product=aceite, province=habana, source=fuente, dia=_hace(3), format_id=pomo.id)
        session.commit()

        texto = client.get("/?producto=aceite").text
        panel = texto.split('id="ph-panel-aceite"')[1].split('<a class="ph-fpanel-detail"')[0]
        bloques = panel.split('<details class="ph-cifra"')[1:]

        assert len(bloques) == 2
        assert bloques[0].startswith(" open")
        assert "Botella de 1 L" in bloques[0]
        assert not bloques[1].startswith(" open")
        assert "Pomo de 5 L" in bloques[1]

    def test_sin_formato_declarado_sigue_una_sola_cifra(
        self, client: TestClient, session: Session
    ) -> None:
        arroz = Product(
            name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True
        )
        habana = Province(code="hab", name="La Habana")
        session.add_all([arroz, habana])
        session.commit()
        fuente = _ingestion(session, hash_="h-sin-formato")
        _precio(session, product=arroz, province=habana, source=fuente, dia=_hace(5))
        _precio(session, product=arroz, province=habana, source=fuente, dia=_hace(4))
        session.commit()

        texto = client.get("/?producto=arroz").text
        panel = texto.split('id="ph-panel-arroz"')[1].split('<a class="ph-fpanel-detail"')[0]

        assert panel.count('class="ph-cifra"') == 1
        assert "ph-chip" not in panel.split('class="ph-cifra-prov"')[1].split("</summary>")[0]


class TestAnunciosEnElPanel:
    def test_el_bloque_enlaza_a_sus_anuncios(
        self, client: TestClient, session: Session
    ) -> None:
        """Recorrido hacia atrás hasta el anuncio (§6.5), 2026-09-14, pedido
        comparando con el enlace "ver anuncios" que ya tiene `/control`
        sobre cada número de la matriz."""
        arroz = Product(
            name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True
        )
        habana = Province(code="hab", name="La Habana")
        session.add_all([arroz, habana])
        session.commit()
        fuente = _ingestion(session, hash_="h-anuncios")
        _precio(session, product=arroz, province=habana, source=fuente, dia=_hace(5))
        session.commit()

        texto = client.get("/?producto=arroz").text
        panel = texto.split('id="ph-panel-arroz"')[1].split('<a class="ph-fpanel-detail"')[0]

        assert "1 anuncio detrás de esta cifra" in panel
        assert '<a href="/anuncio/' in panel

    def test_cada_bloque_solo_enlaza_sus_propios_anuncios(
        self, client: TestClient, session: Session
    ) -> None:
        """Dos provincias, un anuncio cada una: el bloque de Santiago no
        debe traer el anuncio de La Habana ni viceversa."""
        arroz = Product(
            name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True
        )
        habana = Province(code="hab", name="La Habana")
        santiago = Province(code="scu", name="Santiago de Cuba")
        session.add_all([arroz, habana, santiago])
        session.commit()
        fuente = _ingestion(session, hash_="h-anuncios-dos")
        _precio(session, product=arroz, province=habana, source=fuente, dia=_hace(5))
        _precio(session, product=arroz, province=santiago, source=fuente, dia=_hace(4))
        session.commit()

        texto = client.get("/?producto=arroz").text
        panel = texto.split('id="ph-panel-arroz"')[1].split('<a class="ph-fpanel-detail"')[0]
        bloques = panel.split('<details class="ph-cifra"')[1:]

        assert len(bloques) == 2
        assert all("1 anuncio detrás de esta cifra" in b for b in bloques)


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
        desde, hasta = _ventana_default()

        texto = client.get("/?productos=arroz,aceite").text

        for slug in ("arroz", "aceite"):
            assert f'class="ph-fpanel" data-win="{slug.capitalize()}" data-product="{slug}"' in texto
            assert (
                f'hx-get="/?productos=arroz,aceite&amp;desde={desde.isoformat()}'
                f'&amp;hasta={hasta.isoformat()}" data-product="{slug}"'
            ) in texto

    def test_el_boton_de_cerrar_quita_ese_producto(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_dos_productos(session)
        desde, hasta = _ventana_default()

        texto = client.get("/?productos=arroz,aceite").text

        # El × de Arroz apunta a la vista sin Arroz (queda Aceite), por `href`
        # (fallback) y por `hx-get` (swap parcial).
        assert (
            f'href="/?productos=aceite&amp;desde={desde.isoformat()}&amp;hasta={hasta.isoformat()}" '
            f'hx-get="/?productos=aceite&amp;desde={desde.isoformat()}&amp;hasta={hasta.isoformat()}"'
        ) in texto

    def test_carga_htmx_y_arma_el_bloque_vivo(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_dos_productos(session)
        desde, hasta = _ventana_default()

        texto = client.get("/?productos=arroz").text

        assert '/static/htmx.min.js?v=' in texto
        assert 'id="ph-live"' in texto
        # Los chips hacen `hx-get` además del `href` de fallback.
        assert (
            f'hx-get="/?productos=arroz,aceite&amp;desde={desde.isoformat()}&amp;hasta={hasta.isoformat()}"'
        ) in texto

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
        desde, hasta = _ventana_default()

        texto = client.get("/").text

        assert (
            f'href="/?provincia=la-habana&amp;desde={desde.isoformat()}&amp;hasta={hasta.isoformat()}" '
            f'hx-get="/?provincia=la-habana&amp;desde={desde.isoformat()}&amp;hasta={hasta.isoformat()}"'
        ) in texto

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
        # Santiago se atenúa; La Habana, la elegida, no. Se cuentan las filas
        # y las atenuadas por separado: desde el 2026-09-20 cada fila lleva
        # además la clase de color de su provincia, y fijar la cadena entera
        # hacía fallar el test por un cambio de tinta que no es lo que mira.
        filas = re.findall(r'<g class="ph-compara-fila([^"]*)"', precios)
        assert len(filas) == 2
        assert sum("is-atenuada" in f for f in filas) == 1
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
    DESDE, HASTA = date(2026, 8, 15), date(2026, 8, 25)

    def test_un_item_por_ventana_en_el_orden_del_menu(self) -> None:
        menu = build_view_menu([], selected_desde=self.DESDE, selected_hasta=self.HASTA)

        assert [v.key for v in menu] == ["cobertura", "precios", "evolucion"]
        assert all(not v.is_open for v in menu)

    def test_abrir_agrega_al_final_y_cerrar_quita(self) -> None:
        por_key = {
            v.key: v
            for v in build_view_menu(["precios"], selected_desde=self.DESDE, selected_hasta=self.HASTA)
        }

        assert por_key["precios"].is_open
        assert por_key["precios"].query == f"/?desde={self.DESDE.isoformat()}&hasta={self.HASTA.isoformat()}"
        assert por_key["cobertura"].query == (
            f"/?desde={self.DESDE.isoformat()}&hasta={self.HASTA.isoformat()}&ver=precios,cobertura"
        )

    def test_preserva_productos_provincia_y_rango(self) -> None:
        menu = build_view_menu(
            [],
            active_products=["Arroz"],
            selected_province="La Habana",
            selected_desde=self.DESDE,
            selected_hasta=self.HASTA,
        )

        assert menu[1].query == (
            f"/?productos=arroz&provincia=la-habana&desde={self.DESDE.isoformat()}"
            f"&hasta={self.HASTA.isoformat()}&ver=precios"
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
        desde, hasta = _ventana_default()

        texto = client.get("/?ver=precios").text

        assert (
            f'class="ph-menu-item is-open" href="/?desde={desde.isoformat()}&amp;hasta={hasta.isoformat()}" '
            f'hx-get="/?desde={desde.isoformat()}&amp;hasta={hasta.isoformat()}" aria-pressed="true"'
        ) in texto

    def test_prender_un_chip_no_cierra_las_ventanas(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_dos_productos(session)
        desde, hasta = _ventana_default()

        texto = client.get("/?ver=precios").text

        assert (
            f'hx-get="/?productos=arroz&amp;desde={desde.isoformat()}&amp;hasta={hasta.isoformat()}'
            f'&amp;ver=precios"'
        ) in texto

    def test_elegir_provincia_no_cierra_las_ventanas(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_dos_productos(session)
        desde, hasta = _ventana_default()

        texto = client.get("/?ver=cobertura").text

        assert (
            f'hx-get="/?provincia=la-habana&amp;desde={desde.isoformat()}&amp;hasta={hasta.isoformat()}'
            f'&amp;ver=cobertura"'
        ) in texto

    def test_el_filtro_de_provincia_preserva_chips_y_ventanas(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_dos_productos(session)
        desde, hasta = _ventana_default()

        texto = client.get("/?productos=arroz,aceite&ver=precios").text

        assert (
            f'hx-get="/?productos=arroz,aceite&amp;provincia=la-habana&amp;desde={desde.isoformat()}'
            f'&amp;hasta={hasta.isoformat()}&amp;ver=precios"'
            in texto
        )

    def test_ver_detalle_del_panel_abre_precios_sin_cerrar_otras(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_dos_productos(session)
        desde, hasta = _ventana_default()

        texto = client.get("/?productos=arroz&ver=cobertura").text

        assert (
            f'class="ph-fpanel-detail" href="/?productos=arroz&amp;desde={desde.isoformat()}'
            f'&amp;hasta={hasta.isoformat()}&amp;ver=cobertura,precios"'
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
    DESDE, HASTA = date(2026, 8, 15), date(2026, 8, 25)

    def test_todas_va_primero_y_activo_sin_seleccion(self) -> None:
        nav = build_province_nav(
            ("La Habana", "Santiago de Cuba"), None, selected_desde=self.DESDE, selected_hasta=self.HASTA
        )

        assert nav[0].label == "Todas"
        assert nav[0].slug == ""
        assert nav[0].is_selected
        assert all(not c.is_selected for c in nav[1:])

    def test_preserva_producto_y_rango_en_el_query(self) -> None:
        nav = build_province_nav(
            ("La Habana",),
            "La Habana",
            selected_product="Arroz",
            selected_desde=self.DESDE,
            selected_hasta=self.HASTA,
        )
        por_label = {c.label: c for c in nav}

        assert por_label["Todas"].query == (
            f"/?producto=arroz&desde={self.DESDE.isoformat()}&hasta={self.HASTA.isoformat()}"
        )
        assert por_label["La Habana"].query == (
            f"/?producto=arroz&provincia=la-habana&desde={self.DESDE.isoformat()}"
            f"&hasta={self.HASTA.isoformat()}"
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


class TestResolveVista:
    def test_acepta_mapa_y_tablero(self) -> None:
        assert resolve_vista("mapa") == "mapa"
        assert resolve_vista("tablero") == "tablero"

    def test_cae_al_default_ante_basura(self) -> None:
        assert resolve_vista(None) == DEFAULT_VISTA
        assert resolve_vista("") == DEFAULT_VISTA
        assert resolve_vista("dashboard") == DEFAULT_VISTA


class TestBuildLayerNav:
    DESDE, HASTA = date(2026, 8, 15), date(2026, 8, 25)

    def test_dos_capas_marcando_la_activa(self) -> None:
        elegidas = build_layer_nav("densidad", selected_desde=self.DESDE, selected_hasta=self.HASTA)
        por_slug = {c.slug: c for c in elegidas}

        assert len(elegidas) == 2
        assert por_slug["densidad"].is_selected
        assert not por_slug["publicacion"].is_selected

    def test_el_enlace_preserva_productos_provincia_rango_y_ventanas(self) -> None:
        elegidas = build_layer_nav(
            "publicacion",
            active_products=["Arroz"],
            selected_province="La Habana",
            selected_desde=self.DESDE,
            selected_hasta=self.HASTA,
            ver=["cobertura"],
        )
        por_slug = {c.slug: c for c in elegidas}

        assert por_slug["densidad"].query == (
            f"/?productos=arroz&provincia=la-habana&desde={self.DESDE.isoformat()}"
            f"&hasta={self.HASTA.isoformat()}&ver=cobertura&capa=densidad"
        )
        # La propia capa activa no ensucia la URL con `capa=publicacion`.
        assert "capa=" not in por_slug["publicacion"].query


class TestBuildWindowNav:
    """Atajos rápidos del date picker (2026-09-14): 7/30/90 días, expresados
    como el rango de calendario que representan (`hoy - N` a `hoy`)."""

    HOY = date(2026, 9, 14)

    def test_tres_opciones_con_la_activa_marcada(self) -> None:
        desde_7 = self.HOY - timedelta(days=7)
        nav = build_window_nav(desde_7, self.HOY, hoy=self.HOY)
        por_dias = {c.days: c for c in nav}

        assert set(por_dias) == {7, 30, 90}
        assert por_dias[7].is_selected
        assert not por_dias[30].is_selected

    def test_un_rango_personalizado_no_marca_ningun_atajo(self) -> None:
        """Un rango que no coincide EXACTO con ninguno de los tres presets
        —ni en largo ni en que `hasta` sea hoy— no marca ninguno: es la
        señal correcta, no es ninguno de los atajos."""
        nav = build_window_nav(date(2026, 8, 1), date(2026, 8, 10), hoy=self.HOY)

        assert not any(c.is_selected for c in nav)

    def test_preserva_el_producto_elegido_en_el_query(self) -> None:
        desde_30 = self.HOY - timedelta(days=30)
        nav = build_window_nav(desde_30, self.HOY, selected_name="Arroz", hoy=self.HOY)
        por_dias = {c.days: c for c in nav}

        assert por_dias[30].query == (
            f"/?producto=arroz&desde={desde_30.isoformat()}&hasta={self.HOY.isoformat()}"
        )
        desde_7 = self.HOY - timedelta(days=7)
        assert por_dias[7].query == (
            f"/?producto=arroz&desde={desde_7.isoformat()}&hasta={self.HOY.isoformat()}"
        )

    def test_preserva_la_vista_tablero_en_el_query(self) -> None:
        """Cambiar la ventana de días en el tablero no puede devolver al
        mapa (2026-09-16)."""
        desde_30 = self.HOY - timedelta(days=30)
        nav = build_window_nav(desde_30, self.HOY, vista="tablero", hoy=self.HOY)

        assert all("vista=tablero" in c.query for c in nav)


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
        """El <form> del selector no puede resetear producto/rango/ventanas/capa
        al cambiar de provincia — mismo principio que ya sostiene
        `NavChoice.query` para los enlaces."""
        self._seed(session)
        hoy = date.today()
        desde = hoy - timedelta(days=90)

        texto = client.get(
            "/?producto=arroz&dias=90&ver=cobertura&capa=densidad"
        ).text
        bloque = texto.split('<form class="ph-province-form"')[1].split("</form>")[0]

        assert '<input type="hidden" name="productos" value="arroz">' in bloque
        assert f'<input type="hidden" name="desde" value="{desde.isoformat()}">' in bloque
        assert f'<input type="hidden" name="hasta" value="{hoy.isoformat()}">' in bloque
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
        """`?dias=` sigue funcionando como alias de compatibilidad
        (`resolve_window`) y se resuelve al rango de calendario real antes
        de armar el enlace del CSV."""
        self._seed(session)
        hoy = date.today()
        desde = hoy - timedelta(days=7)

        texto = client.get("/?producto=arroz&dias=7").text

        # Jinja escapa el `&` del atributo href.
        assert (
            f'href="/datos.csv?producto=arroz&amp;desde={desde.isoformat()}&amp;hasta={hoy.isoformat()}"'
            in texto
        )


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
    """`GET /producto/<slug>`: URL dedicada por producto.

    Hasta el 2026-09-14 era alias exacto de `/?producto=<slug>`; desde esa
    fecha es la vista LIVIANA (reporte de mejoras no consideradas, idea #5)
    — sin mapa, sin Leaflet, sin compás ni dock. `/?producto=<slug>` sigue
    siendo la vista completa e interactiva."""

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

        assert ">La Habana<" in texto
        assert ">Santiago de Cuba<" in texto
        # Un solo panel por construcción: no hay forma de que se cuele el
        # nombre de otro producto piloto.
        assert "Aceite" not in texto

    def test_trae_su_propio_date_picker_precargado(
        self, client: TestClient, session: Session
    ) -> None:
        """La vista liviana también tiene el date picker (2026-09-14) — sin
        `hx-get`, un submit normal alcanza porque no hay nada parcial que
        actualizar."""
        self._seed(session)
        desde, hasta = _ventana_default()

        texto = client.get("/producto/arroz").text

        assert 'class="ph-lite-window" method="get"' in texto
        assert f'<input type="date" name="desde" value="{desde.isoformat()}">' in texto
        assert f'<input type="date" name="hasta" value="{hasta.isoformat()}">' in texto

    def test_el_date_picker_respeta_un_rango_explicito(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)
        desde, hasta = date(2026, 8, 1), date(2026, 8, 20)

        texto = client.get(f"/producto/arroz?desde={desde.isoformat()}&hasta={hasta.isoformat()}").text

        assert f'<input type="date" name="desde" value="{desde.isoformat()}">' in texto
        assert f'<input type="date" name="hasta" value="{hasta.isoformat()}">' in texto

    def test_resuelve_slug_con_tilde(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)

        r = client.get("/producto/azucar-blanca")

        assert r.status_code == 200
        assert "<h1" in r.text and ">Azúcar blanca<" in r.text

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

        assert ">La Habana<" in texto
        assert "Santiago de Cuba" not in texto

    def test_ver_y_capa_ya_no_significan_nada_pero_no_rompen(
        self, client: TestClient, session: Session
    ) -> None:
        """Un enlace viejo con `?ver=`/`?capa=` (parámetros de la vista con
        mapa) no tiene por qué dar error acá: simplemente se ignoran."""
        self._seed(session)

        r = client.get("/producto/arroz?ver=precios&capa=densidad")

        assert r.status_code == 200

    def test_ya_no_es_alias_la_ruta_limpia_no_trae_el_mapa(
        self, client: TestClient, session: Session
    ) -> None:
        """2026-09-14: `/producto/<slug>` deja de ser un alias exacto de
        `/?producto=<slug>` — pasa a ser la vista liviana (reporte de
        mejoras no consideradas, idea #5). La ruta con `?producto=` sigue
        cargando el mapa Leaflet completo; la ruta limpia no."""
        self._seed(session)

        limpia = client.get("/producto/arroz").text
        con_mapa = client.get("/?producto=arroz").text

        assert "leaflet.css" not in limpia
        assert 'id="ph-leaflet-map"' not in limpia
        assert "leaflet.css" in con_mapa
        assert 'id="ph-leaflet-map"' in con_mapa

        # El mismo dato de fondo sigue ahí, solo que sin el mapa alrededor.
        assert ">La Habana<" in limpia
        assert "La Habana" in con_mapa

    def test_la_ruta_limpia_enlaza_de_vuelta_al_mapa_interactivo(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)
        desde, hasta = _ventana_default()

        texto = client.get("/producto/arroz").text

        assert (
            f'href="/?productos=arroz&amp;desde={desde.isoformat()}&amp;hasta={hasta.isoformat()}"'
        ) in texto
        assert "Ver Arroz en el mapa interactivo" in texto

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


class TestOpenGraph:
    """Vista previa al compartir en WhatsApp/Telegram: sin og:title/
    og:description un link pegado no muestra nada. `og:image` queda afuera
    a propósito — no hay ningún asset de marca en el repo."""

    def test_home_sin_producto_usa_la_descripcion_generica(
        self, client: TestClient
    ) -> None:
        texto = client.get("/").text

        assert 'property="og:title" content="Guani — Precios y cobertura"' in texto
        assert (
            'property="og:description" content="Precios del mercado informal cubano, '
            'verificados anuncio por anuncio, provincia por provincia."' in texto
        )
        assert 'name="description" content="Precios del mercado informal cubano' in texto

    def test_producto_enfocado_menciona_el_producto_en_la_descripcion(
        self, client: TestClient, session: Session
    ) -> None:
        TestRutaProductoLimpia()._seed(session)

        texto = client.get("/producto/arroz").text

        assert 'property="og:title" content="Guani — Arroz — precios"' in texto
        assert (
            'property="og:description" content="Precios de Arroz en el mercado '
            'informal cubano, verificados anuncio por anuncio."' in texto
        )


class TestAtribucionTRMI:
    """spec.md §5, «Posicionamiento frente a elTOQUE»: «Toda serie derivada
    que use la TRMI como base de conversión debe atribuirla visiblemente».
    Vinculante (spec-map.md). Vive en `_public_shell.html`, así que llega
    a toda la capa pública sin repetirse por vista."""

    def test_el_pie_atribuye_la_trmi_a_eltoque(self, client: TestClient) -> None:
        texto = client.get("/").text

        assert "Tasa Representativa del Mercado" in texto
        assert 'href="https://eltoque.com/"' in texto


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
        assert 'class="ph-card-variant"' in texto
        # `.ph-card-variant` no tenía NINGUNA regla (2026-09-12): el `<div>`
        # con la variante renderizaba de bloque a full width, pegado al
        # titular, sin el tratamiento secundario que ya usa `.ph-card-median`.
        # Vive en `saimc-public.css` (2026-09-16, `.ph-card` se movió del
        # `<style>` de esta página a la hoja compartida), no inline: se
        # busca ahí, no en el HTML de `/`.
        hoja = client.get("/static/saimc-public.css").text
        assert re.search(r"\.ph-card-variant\b[^{]*\{[^}]*\}", hoja)

    def test_la_tarjeta_publica_declara_estilo_para_el_formato(
        self, client: TestClient
    ) -> None:
        """Mismo hallazgo que la variante, para `.ph-card-format` — el `<div>`
        del formato declarado («Bolsa 1kg») que se muestra entre el titular y
        el rango de precio."""
        hoja = client.get("/static/saimc-public.css").text

        assert re.search(r"\.ph-card-format\b[^{]*\{[^}]*\}", hoja)


class TestVistaTableroEnLaRuta:
    """`?vista=tablero`: una tabla plana con todos los datos, sin mapa.

    Hasta el 2026-09-17 convivían dos presentaciones detrás de un toggle
    (tarjetas agrupadas con índice de salto, y esta tabla). El operador pidió
    sacar las tarjetas del tablero (2026-09-18): la tabla queda como la única
    forma de verlo — no hay `?presentacion=` que resolver ni toggle que
    mostrar."""

    def _seed(self, session: Session) -> None:
        """Arroz en Habana (2 días) y Santiago (1); Aceite solo en Habana."""
        arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True)
        aceite = Product(name="Aceite", category="grasas", unit="lb", aliases=[], is_pilot=True)
        habana = Province(code="hab", name="La Habana")
        santiago = Province(code="scu", name="Santiago de Cuba")
        session.add_all([arroz, aceite, habana, santiago])
        session.commit()
        fuente = _ingestion(session, hash_="h-vista")
        _precio(session, product=arroz, province=habana, source=fuente, dia=_hace(6))
        _precio(session, product=arroz, province=habana, source=fuente, dia=_hace(3), price="330")
        _precio(session, product=arroz, province=santiago, source=fuente, dia=_hace(4), price="410")
        _precio(session, product=aceite, province=habana, source=fuente, dia=_hace(5))
        session.commit()

    def test_sin_vista_muestra_el_mapa(self, client: TestClient, session: Session) -> None:
        self._seed(session)

        texto = client.get("/").text

        assert 'id="ph-leaflet-map"' in texto
        assert 'class="ph-dash-table"' not in texto

    def test_vista_tablero_muestra_una_tarjeta_por_producto_sin_mapa(
        self, client: TestClient, session: Session
    ) -> None:
        """«Todos» es «Qué cuesta hoy»: una tarjeta por producto piloto, sin mapa
        y sin la tabla por formato, que vive en el detalle de cada producto."""
        self._seed(session)

        texto = client.get("/?vista=tablero").text

        assert 'id="ph-leaflet-map"' not in texto
        assert '<h1 class="ph-h1">Qué cuesta hoy</h1>' in texto
        assert '<span class="ph-tarjeta-nombre">Arroz</span>' in texto
        assert '<span class="ph-tarjeta-nombre">Aceite</span>' in texto
        assert "<table" not in texto
        assert texto.index("La Habana") < texto.index("Santiago de Cuba")

    def test_la_cookie_vista_decide_sin_query_string(
        self, client: TestClient, session: Session
    ) -> None:
        """Slice 3: sin `?vista=` en la URL, la cookie manda — el servidor
        elige la plantilla de entrada, sin redirect en el cliente."""
        self._seed(session)
        client.cookies.set("vista", "tablero")

        texto = client.get("/").text

        assert 'id="ph-leaflet-map"' not in texto
        assert 'class="ph-layout"' in texto

    def test_el_query_string_explicito_gana_a_la_cookie(
        self, client: TestClient, session: Session
    ) -> None:
        """Un link compartido con `?vista=mapa` tiene que abrir en mapa pase
        lo que pase, aunque la cookie diga tablero."""
        self._seed(session)
        client.cookies.set("vista", "tablero")

        texto = client.get("/?vista=mapa").text

        assert 'id="ph-leaflet-map"' in texto

    def test_una_cookie_invalida_cae_al_mapa(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)
        client.cookies.set("vista", "satelite")

        texto = client.get("/").text

        assert 'id="ph-leaflet-map"' in texto

    def test_los_enlaces_del_toggle_llevan_la_clase_y_el_dato_para_la_cookie(
        self, client: TestClient, session: Session
    ) -> None:
        """`_vista_cookie_script.html` cablea por `.js-vista-link` +
        `data-vista`, no por id — tiene que estar en los dos enlaces del
        tablero y en el del dock del mapa."""
        self._seed(session)

        tablero = client.get("/?vista=tablero").text
        assert re.search(r'class="js-vista-link[^"]*" data-vista="mapa"', tablero)
        assert re.search(r'class="js-vista-link[^"]*" data-vista="tablero"', tablero)

        mapa = client.get("/").text
        assert re.search(r'class="ph-menu-item js-vista-link" data-vista="tablero"', mapa)

    def test_vista_tablero_respeta_el_filtro_de_producto(
        self, client: TestClient, session: Session
    ) -> None:
        """La lista de productos muestra TODOS los piloto —incluido Aceite— para
        poder elegirlos; lo que respeta el filtro es el detalle: «Por envase»
        trae solo los envases del producto elegido."""
        self._seed(session)

        texto = client.get("/?vista=tablero&productos=arroz").text
        panel = texto[texto.index('class="ph-panel ph-envases"') :]
        panel = panel[: panel.index("</section>")]

        assert panel.count('class="ph-envases-prov ph-prov') == 2  # La Habana y Santiago
        assert "Aceite" not in panel

    def test_una_vista_invalida_cae_al_mapa(self, client: TestClient, session: Session) -> None:
        self._seed(session)

        respuesta = client.get("/?vista=satelite")

        assert respuesta.status_code == 200
        assert 'id="ph-leaflet-map"' in respuesta.text

    def test_el_toggle_preserva_filtro_y_ventana_en_los_dos_sentidos(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)

        texto = client.get("/?vista=tablero&productos=arroz&provincia=la-habana").text

        # El toggle Mapa/Tablero no ensucia su propio enlace con el default
        # (mismo criterio que `capa`). El enlace a «Cómo se movió» sí escribe
        # `vista=mapa` a propósito —para no caer en la
        # cookie que recuerda «tablero», ver `TestAccionesYCoberturaEnElTablero`—
        # así que la comparación se acota al enlace del toggle, no a toda la
        # página.
        toggle = re.search(r'<a class="js-vista-link[^>]*data-vista="mapa"[^>]*href="([^"]+)"', texto)
        assert toggle is not None
        assert "vista=mapa" not in toggle.group(1)
        assert 'href="/?productos=arroz&amp;provincia=la-habana' in texto
        assert 'aria-current="true">Tablero</a>' in texto

    def test_el_enlace_al_tablero_esta_en_el_dock_del_mapa_y_preserva_el_filtro(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)

        texto = client.get("/?productos=arroz").text

        assert re.search(r'href="/\?productos=arroz[^"]*vista=tablero[^"]*"', texto)

    def test_el_color_de_provincia_distingue_las_que_tienen_dato(
        self, client: TestClient, session: Session
    ) -> None:
        """Arreglo del 2026-09-18: antes el slot salía de `all_province_names`
        —el catálogo completo de 16, alfabético— y con datos reales solo en
        dos provincias (`La Habana` índice 8, `Santiago de Cuba` índice 14 en
        ese orden) las dos caían en el mismo neutral. Ahora sale de las
        provincias CON DATO en esta vista, así que dos provincias reales son
        dos slots reales, distintos entre sí. Desde S6 el color lo llevan los
        grupos de «Por envase» (antes, cada fila de la tabla)."""
        self._seed(session)

        texto = client.get("/?vista=tablero&productos=arroz").text

        assert 'class="ph-envases-prov ph-prov-0"' in texto
        assert 'class="ph-envases-prov ph-prov-1"' in texto
        # Nunca las dos en el neutral (el bug que se corrigió).
        assert texto.count('class="ph-envases-prov ph-prov-9"') == 0

    def test_la_navegacion_de_productos_trae_todo_el_catalogo_piloto(
        self, client: TestClient, session: Session
    ) -> None:
        """En el detalle, la barra lateral «Productos» lista a todos los piloto
        —no depende del filtro activo— con el elegido marcado."""
        self._seed(session)

        texto = client.get("/?vista=tablero&productos=arroz").text
        nav = texto[texto.index('<nav class="ph-nav') : texto.index("</nav>")]

        assert re.search(r'class="ph-nav-item is-active" href="[^"]+" aria-current="true">Arroz', nav)
        assert re.search(r'class="ph-nav-item" href="[^"]+">Aceite', nav)

    def test_un_producto_de_la_navegacion_reemplaza_la_seleccion_y_volver_la_vacia(
        self, client: TestClient, session: Session
    ) -> None:
        """A diferencia de los chips viejos (que sumaban a la multi-selección),
        un producto de la barra lateral reemplaza la selección entera; «‹ Todos
        los productos» la vacía (lista vacía = parámetro ausente)."""
        self._seed(session)

        texto = client.get("/?vista=tablero&productos=arroz").text
        aceite = re.search(r'<a class="ph-nav-item" href="([^"]+)">Aceite', texto)
        todos = re.search(r'<a class="ph-back" href="([^"]+)"', texto)

        assert aceite is not None and "productos=aceite&amp;" in aceite.group(1)
        assert "arroz" not in aceite.group(1)
        assert todos is not None and "productos=" not in todos.group(1)
        assert "vista=tablero" in todos.group(1)

    def test_la_tarjeta_muestra_la_mediana_de_cada_provincia(
        self, client: TestClient, session: Session
    ) -> None:
        """Arroz en Santiago (`_seed`): un solo anuncio de 410, así que la
        mediana es ese mismo precio; sin anuncios en el período anterior, no
        hay cambio que mostrar."""
        self._seed(session)

        texto = client.get("/?vista=tablero").text
        tarjeta = re.search(r'<span class="ph-tarjeta-nombre">Arroz</span>[\s\S]*?</a>', texto)

        assert tarjeta is not None
        assert "Santiago de Cuba" in tarjeta.group(0)
        assert '<div class="ph-fig">410 ' in tarjeta.group(0)
        assert "sin mes anterior" in tarjeta.group(0)

    def test_las_tarjetas_no_aparecen_en_el_mapa(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)

        assert 'class="ph-tarjeta' not in client.get("/").text

    def test_el_selector_sigue_visible_aunque_la_provincia_no_tenga_dato(
        self, client: TestClient, session: Session
    ) -> None:
        """Un filtro que deja una tarjeta vacía tiene que poder deshacerse desde
        la propia vista. Aceite no tiene dato en Santiago (ver `_seed`): su
        tarjeta lo dice y el selector sigue ahí para volver a «Todas»."""
        self._seed(session)

        texto = client.get("/?vista=tablero&provincia=santiago-de-cuba").text
        aceite = re.search(r'<span class="ph-tarjeta-nombre">Aceite</span>[\s\S]*?</a>', texto)

        assert aceite is not None and "sin datos todavía" in aceite.group(0)
        assert 'data-provincia=""' in texto
        assert 'class="ph-dash-chip ph-tap-44 is-active"' in texto  # Santiago sigue marcada

    def test_el_detalle_no_se_vacia_por_una_provincia_elegida(
        self, client: TestClient, session: Session
    ) -> None:
        """La provincia enfoca solo la lista de «Todos»: el detalle compara todas
        las provincias. Aceite no tiene dato en Santiago y el detalle sigue ahí,
        con Santiago en «sin datos todavía» y La Habana con su cifra."""
        self._seed(session)

        texto = client.get("/?vista=tablero&productos=aceite&provincia=santiago-de-cuba").text
        resumen = texto[texto.index('class="ph-panel ph-resumen"') :]
        resumen = resumen[: resumen.index("</section>")]

        assert "Sin anuncios verificados" not in texto
        assert "sin datos todavía" in resumen
        assert '<div class="ph-fig">' in resumen

    def test_el_selector_de_provincia_marca_la_activa(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)

        texto = client.get("/?vista=tablero&provincia=santiago-de-cuba").text
        filtros = texto[texto.index('id="mi-provincia-etiqueta"') :]

        assert re.search(
            r'class="ph-dash-chip ph-tap-44 is-active"[^>]*>[\s\S]*?Santiago de Cuba', filtros
        )
        assert re.search(r'class="ph-dash-chip ph-tap-44"[^>]*>[\s\S]*?Todas', filtros)

    def test_la_ventana_precios_del_mapa_tambien_trae_data_buscar(
        self, client: TestClient, session: Session
    ) -> None:
        """`_product_card_item.html` (el panel flotante del mapa) sigue
        trayendo el atributo aunque el tablero ya no use tarjetas."""
        self._seed(session)

        texto = client.get("/?ver=precios").text

        assert "data-buscar=" in texto

    def test_la_ventana_precios_del_mapa_tambien_trae_el_recorrido_hasta_el_anuncio(
        self, client: TestClient, session: Session
    ) -> None:
        """`_product_card_item.html` es compartida — el enlace hasta el
        anuncio sigue en el panel flotante «Precios» del mapa, que ya lo
        tenía disponible vía `card_ads`. El tablero (tabla) no lo repite
        inline; se llega desde ahí a `/producto/<slug>`, que sí lo trae."""
        self._seed(session)

        texto = client.get("/?ver=precios").text

        assert "ph-evo-anuncios" in texto
        assert re.search(r'href="/anuncio/[^"]+">ver anuncio</a>', texto)


class TestAccionesYCoberturaEnElTablero:
    """Lo que vivía en el menú del avatar del mapa, ya en el tablero.

    Hasta el 2026-09-18 el tablero tenía un `<details>` con el avatar que
    replicaba parte del compás (Cobertura, Cómo se movió, CSV, tema). Con la
    dirección C, Cobertura pasa al cuerpo de la página y las acciones quedan
    a la vista en la cabecera, sin menú que abrir."""

    def _seed(self, session: Session) -> None:
        arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True)
        habana = Province(code="hab", name="La Habana")
        session.add_all([arroz, habana])
        session.commit()
        fuente = _ingestion(session, hash_="h-acciones")
        _precio(session, product=arroz, province=habana, source=fuente, dia=_hace(3))
        session.commit()

    def test_el_avatar_sigue_en_la_cabecera(self, client: TestClient, session: Session) -> None:
        self._seed(session)

        texto = client.get("/?vista=tablero").text

        assert 'class="ph-top-brand"' in texto
        assert '<span class="ph-wordmark">Guani</span>' in texto
        assert 'viewBox="0 0 80 80"' in texto  # _guani_avatar.html
        assert "ph-actions-trigger" not in texto  # ya no es un menú

    def test_la_cobertura_esta_en_la_pagina_sin_ir_al_mapa(
        self, client: TestClient, session: Session
    ) -> None:
        """Un anuncio en La Habana: 1 provincia con dato de las que haya en el
        catálogo (una), ninguna publicada — y la nota de §9.0 que explica por
        qué. Es una viñeta de «Sobre los datos», no un panel aparte."""
        self._seed(session)

        texto = client.get("/?vista=tablero").text
        bloque = texto[texto.index('id="datos-titulo"') :]

        assert "ver=cobertura" not in texto
        assert "<strong>1 de 1 provincia</strong> tiene datos." in bloque
        assert "Ninguna está publicada todavía" in bloque
        assert "no cuando lo dice el calendario" in bloque

    def test_como_se_movio_ya_no_saca_al_mapa(
        self, client: TestClient, session: Session
    ) -> None:
        """Slice 2 (2026-09-18): «Cómo se movió» vive en el detalle del
        producto, dentro del tablero. El enlace provisorio a la ventana del
        mapa (`?ver=evolucion&vista=mapa`) se retira."""
        self._seed(session)

        texto = client.get("/?vista=tablero").text

        assert "ver=evolucion" not in texto

    def test_csv_descarga_directo_sin_pasar_por_el_mapa(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)

        texto = client.get("/?vista=tablero").text
        bloque = texto[texto.index('id="datos-titulo"') :]

        assert re.search(r'href="/datos\.csv\?desde=[^"]+"', bloque)

    def test_el_toggle_de_tema_esta_en_la_cabecera(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)

        texto = client.get("/?vista=tablero").text

        assert 'class="ph-dash-action ph-tap-44 theme-toggle"' in texto

    def test_las_acciones_no_aparecen_en_el_mapa(
        self, client: TestClient, session: Session
    ) -> None:
        """El compás del mapa ya tiene sus propias acciones."""
        self._seed(session)

        texto = client.get("/").text

        assert "ph-dash-actions" not in texto


class TestMiProvinciaEnElTablero:
    """S8 (2026-09-19): «¿En qué provincia estás?» arriba de la lista, y el
    tablero recuerda la elección. `?provincia=` explícito manda siempre; sin
    él, en el tablero, cae a la cookie `provincia` que escribe el clic en un
    chip. En el mapa la cookie no se lee: el mapa no cambia."""

    def _seed(self, session: Session) -> None:
        """Arroz en Habana (2 días) y Santiago (1); Aceite solo en Habana."""
        arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True)
        aceite = Product(name="Aceite", category="grasas", unit="lb", aliases=[], is_pilot=True)
        habana = Province(code="hab", name="La Habana")
        santiago = Province(code="scu", name="Santiago de Cuba")
        session.add_all([arroz, aceite, habana, santiago])
        session.commit()
        fuente = _ingestion(session, hash_="h-mi-provincia")
        _precio(session, product=arroz, province=habana, source=fuente, dia=_hace(6))
        _precio(session, product=arroz, province=habana, source=fuente, dia=_hace(3), price="330")
        _precio(session, product=arroz, province=santiago, source=fuente, dia=_hace(4), price="410")
        _precio(session, product=aceite, province=habana, source=fuente, dia=_hace(5))
        session.commit()

    def _lista(self, texto: str) -> str:
        return texto[texto.index('<div class="ph-tarjetas">') : texto.index('id="datos-titulo"')]

    def _columnas(self, texto: str) -> list[str]:
        """Los nombres de provincia de cada columna principal de las tarjetas."""
        return re.findall(r'<div class="ph-col-name"><span class="ph-dot"></span>([^<]+)', texto)

    def test_el_selector_esta_arriba_de_la_lista(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)

        texto = client.get("/?vista=tablero").text
        arriba = texto[texto.index('id="mi-provincia-etiqueta"') : texto.index('<div class="ph-tarjetas">')]

        assert "¿En qué provincia estás?" in arriba
        assert 'data-provincia="la-habana"' in arriba
        assert 'data-provincia="santiago-de-cuba"' in arriba
        assert 'data-provincia=""' in arriba  # «Todas»

    def test_el_encabezado_ya_no_repite_los_chips_de_provincia(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)

        texto = client.get("/?vista=tablero").text

        assert 'aria-label="Provincia"' not in texto
        assert texto.count('data-provincia="santiago-de-cuba"') == 1

    def test_con_provincia_la_lista_se_enfoca_y_las_otras_van_en_chico(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)

        lista = self._lista(client.get("/?vista=tablero&provincia=santiago-de-cuba").text)

        assert self._columnas(lista) == ["Santiago de Cuba", "Santiago de Cuba"]  # Aceite, Arroz
        assert 'class="ph-tarjeta is-foco"' in lista
        assert re.search(r'class="ph-otras">[\s\S]*?La Habana:', lista)

    def test_sin_provincia_no_hay_lineas_de_otras(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)

        lista = self._lista(client.get("/?vista=tablero").text)

        assert len(self._columnas(lista)) == 4  # dos tarjetas, dos provincias cada una
        assert "ph-otras" not in lista

    def test_la_cookie_provincia_enfoca_el_tablero_sin_query_string(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)
        client.cookies.set("provincia", "santiago-de-cuba")

        lista = self._lista(client.get("/?vista=tablero").text)

        assert self._columnas(lista) == ["Santiago de Cuba", "Santiago de Cuba"]
        assert "ph-otras" in lista

    def test_el_query_string_explicito_gana_a_la_cookie(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)
        client.cookies.set("provincia", "santiago-de-cuba")

        lista = self._lista(client.get("/?vista=tablero&provincia=la-habana").text)

        assert self._columnas(lista) == ["La Habana", "La Habana"]

    def test_una_cookie_invalida_no_vacia_ni_rompe_el_tablero(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)
        client.cookies.set("provincia", "narnia")

        r = client.get("/?vista=tablero")

        assert r.status_code == 200
        assert len(self._columnas(self._lista(r.text))) == 4

    def test_la_cookie_no_toca_el_mapa(self, client: TestClient, session: Session) -> None:
        self._seed(session)
        sin_cookie = client.get("/?vista=mapa").text
        client.cookies.set("provincia", "santiago-de-cuba")

        con_cookie = client.get("/?vista=mapa").text

        assert con_cookie == sin_cookie

    def test_el_clic_en_un_chip_escribe_o_borra_la_cookie(
        self, client: TestClient, session: Session
    ) -> None:
        """El servidor solo lee la cookie; la escribe el clic, como la de
        `vista`: recordar lo que el usuario eligió, no lo que abrió por un
        enlace ajeno."""
        self._seed(session)

        texto = client.get("/?vista=tablero").text

        assert "a[data-provincia]" in texto
        assert 'document.cookie = "provincia="' in texto
        assert "max-age=0" in texto


class TestSobreLosDatosEnElTablero:
    """«Sobre los datos» (prototipo E): cinco viñetas —cuántos anuncios, hasta
    cuándo, cuántas provincias, qué es la cifra y qué es la base— y la descarga.
    El rótulo §9.0 «muestra en formación» es la excepción: va bajo el título,
    pegado a las cifras, no en la barra lateral."""

    def _seed(self, session: Session) -> None:
        arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True)
        habana = Province(code="hab", name="La Habana")
        session.add_all([arroz, habana])
        session.commit()
        fuente = _ingestion(session, hash_="h-sobre-datos")
        _precio(session, product=arroz, province=habana, source=fuente, dia=_hace(3))
        session.commit()

    def _bloque(self, texto: str) -> str:
        inicio = texto.index('id="datos-titulo"')
        return texto[inicio : texto.index("</section>", inicio)]

    def test_el_bloque_reune_cobertura_lectura_y_descarga(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)

        texto = client.get("/?vista=tablero").text
        bloque = self._bloque(texto)

        assert ">Sobre los datos</h2>" in bloque
        assert 'id="cobertura-titulo"' not in texto
        assert "<strong>1 de 1 provincia</strong> tiene datos." in bloque
        assert "<strong>mediana</strong>" in bloque
        assert "«(base N)»" in bloque
        assert "verificados a mano uno por uno" in bloque
        assert re.search(r'href="/datos\.csv\?desde=[^"]+"', bloque)

    def test_la_descarga_esta_en_la_cabecera_y_en_el_bloque(
        self, client: TestClient, session: Session
    ) -> None:
        """Como en el prototipo E: el ícono de descarga en la cabecera y el
        enlace con texto en «Sobre los datos»."""
        self._seed(session)

        texto = client.get("/?vista=tablero").text
        inicio = texto.index('<header class="ph-top">')
        cabecera = texto[inicio : texto.index("</header>", inicio)]

        assert 'class="ph-top-csv" href="/datos.csv?' in cabecera
        assert 'class="ph-dash-action ph-tap-44 theme-toggle"' in cabecera
        assert "/datos.csv?" in self._bloque(texto)
        assert texto.count("/datos.csv?") == 2

    def test_la_guia_de_lectura_vive_solo_en_sobre_los_datos(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)

        texto = client.get("/?vista=tablero").text

        assert texto.count("«(base N)»") == 1
        assert "«(base N)»" in self._bloque(texto)

    def test_el_rotulo_de_muestra_en_formacion_queda_junto_a_las_cifras(
        self, client: TestClient, session: Session
    ) -> None:
        """§9.0: el rótulo acompaña a la cifra. Va entre el título y las
        tarjetas, no dentro de «Sobre los datos»."""
        self._seed(session)

        texto = client.get("/?vista=tablero").text
        rotulo = texto.index("<strong>muestra en formación</strong>")

        assert texto.count("<strong>muestra en formación</strong>") == 1
        assert texto.index('<h1 class="ph-h1">') < rotulo < texto.index('<div class="ph-tarjetas">')

    def test_la_frescura_esta_en_el_chip_y_en_una_vineta(
        self, client: TestClient, session: Session
    ) -> None:
        """El prototipo E la muestra dos veces: el chip de la barra lateral y
        la viñeta «Datos hasta el…: hace N días no entran anuncios nuevos»."""
        self._seed(session)

        texto = client.get("/?vista=tablero").text

        assert texto.count("Datos verificados hasta el") == 1  # el chip
        assert 'class="ph-fresh"' in texto
        assert "no entran anuncios nuevos" in self._bloque(texto)

    def test_el_mapa_no_cambia(self, client: TestClient, session: Session) -> None:
        self._seed(session)

        texto = client.get("/").text

        assert "Sobre los datos" not in texto
        assert 'id="datos-titulo"' not in texto
        assert 'class="ph-fresh"' not in texto


class TestDetalleDeProductoEnElTablero:
    """Detalle del prototipo E: con un producto elegido, el tablero muestra la
    tarjeta resumen, «Cómo se movió», «Cuánto varía», «Por envase» y los
    anuncios detrás —con los mismos parciales de gráficos que las ventanas
    del mapa—. Sin producto elegido no hay detalle: las tarjetas ya resumen a
    todos.

    Valores esperados, a mano, con la ventana por defecto (30 días):
    Arroz en La Habana, período anterior 300 y 300 → mediana 300; ventana
    actual 300 y 330 → mediana 315. Santiago: 410, sin período anterior."""

    def _seed(self, session: Session) -> None:
        arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True)
        aceite = Product(name="Aceite", category="grasas", unit="lb", aliases=[], is_pilot=True)
        habana = Province(code="hab", name="La Habana")
        santiago = Province(code="scu", name="Santiago de Cuba")
        session.add_all([arroz, aceite, habana, santiago])
        session.commit()
        fuente = _ingestion(session, hash_="h-detalle")
        _precio(session, product=arroz, province=habana, source=fuente, dia=_hace(45))
        _precio(session, product=arroz, province=habana, source=fuente, dia=_hace(40))
        _precio(session, product=arroz, province=habana, source=fuente, dia=_hace(12))
        _precio(session, product=arroz, province=habana, source=fuente, dia=_hace(3), price="330")
        _precio(session, product=arroz, province=santiago, source=fuente, dia=_hace(4), price="410")
        _precio(session, product=aceite, province=habana, source=fuente, dia=_hace(5))
        session.commit()

    def _detalle(self, texto: str) -> str:
        return texto[texto.index('<main class="ph-main">') : texto.index("</main>")]

    def test_sin_producto_elegido_no_hay_detalle(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)

        assert 'class="ph-panel ph-resumen"' not in client.get("/?vista=tablero").text

    def test_con_un_producto_elegido_aparecen_como_se_movio_y_cuanto_varia(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)

        detalle = self._detalle(client.get("/?vista=tablero&productos=arroz").text)

        assert 'class="ph-pasos"' in detalle  # «Cómo se movió»
        assert 'class="ph-compara"' in detalle  # «Cuánto varía»
        assert ">Cómo se movió</h2>" in detalle and ">Cuánto varía</h2>" in detalle
        # El prototipo E no trae estos dos: el cambio ya está en el resumen.
        assert 'class="ph-pendiente"' not in detalle
        assert 'class="ph-calendario"' not in detalle

    def test_el_resumen_trae_las_medianas_el_cambio_y_quien_es_mas_barata(
        self, client: TestClient, session: Session
    ) -> None:
        """La Habana: 315 contra 300 antes (+5%, base 2); Santiago 410. La
        Habana es la más barata."""
        self._seed(session)

        detalle = self._detalle(client.get("/?vista=tablero&productos=arroz").text)
        resumen = detalle[detalle.index('class="ph-panel ph-resumen"') :]
        resumen = resumen[: resumen.index("</section>")]

        assert '<div class="ph-fig">315 ' in resumen
        assert '<div class="ph-fig">410 ' in resumen
        assert "+5% (base 2)" in resumen
        assert "<strong>La Habana</strong> es la más barata: 315 contra 410 en Santiago de Cuba." in resumen

    def test_el_detalle_es_solo_del_producto_elegido(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)

        detalle = self._detalle(client.get("/?vista=tablero&productos=arroz").text)

        assert "Aceite" not in detalle

    def test_los_anuncios_del_detalle_se_pueden_recorrer(
        self, client: TestClient, session: Session
    ) -> None:
        """La procedencia es el diferencial (§6.5): los anuncios que sostienen
        los gráficos, uno por renglón. Cuatro de arroz en la ventana: dos en
        La Habana dentro de los 30 días (hace 12 y hace 3), uno en Santiago;
        los de hace 40 y 45 son del período anterior y no entran."""
        self._seed(session)

        detalle = self._detalle(client.get("/?vista=tablero&productos=arroz").text)

        assert 'class="ph-panel ph-anuncios"' in detalle
        assert "Los 3 anuncios de la ventana" in detalle

    def test_con_dos_productos_de_una_url_vieja_no_hay_detalle(
        self, client: TestClient, session: Session
    ) -> None:
        """El detalle es de UN producto; `?productos=a,b` (enlace heredado)
        muestra las tarjetas de todos, sin detalle."""
        self._seed(session)

        texto = client.get("/?vista=tablero&productos=arroz,aceite").text

        assert 'class="ph-panel ph-resumen"' not in texto
        assert '<h1 class="ph-h1">Qué cuesta hoy</h1>' in texto

    def test_el_detalle_compara_todas_las_provincias_aunque_haya_una_elegida(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)

        detalle = self._detalle(
            client.get("/?vista=tablero&productos=arroz&provincia=santiago-de-cuba").text
        )

        assert "<strong>La Habana</strong> es la más barata" in detalle
        assert detalle.count('class="ph-envases-prov-name"') == 2
        # «Cómo se movió» y «Cuánto varía» traen la leyenda de las dos
        # provincias: con el detalle filtrado a una sola no habría leyenda.
        assert detalle.count('class="ph-leyenda"') == 2

    def test_la_navegacion_de_productos_solo_esta_en_el_detalle(
        self, client: TestClient, session: Session
    ) -> None:
        """La clase `has-nav` de la grilla y el propio menú tienen que ir
        juntos: es esa clase la que le reserva su fila en la barra lateral, y
        si una aparece sin la otra el menú pierde su lugar o queda una fila
        vacía que duplica el aire."""
        self._seed(session)

        todos = client.get("/?vista=tablero").text
        detalle = client.get("/?vista=tablero&productos=arroz").text

        assert '<nav class="ph-nav' not in todos
        assert '<div class="ph-layout">' in todos
        assert '<nav class="ph-nav' in detalle
        assert '<div class="ph-layout has-nav">' in detalle


class TestGraficosComoElPrototipo:
    """Alineación con el prototipo E (2026-09-20), en los parciales que el
    tablero comparte con el mapa.

    El último anuncio de la semilla es de hace 3 días y la ventana termina
    hoy: la nota al pie tiene que decir el día del anuncio, no el de la
    ventana."""

    def _seed(self, session: Session) -> None:
        """La misma semilla del detalle: estos gráficos son los de esa vista."""
        TestDetalleDeProductoEnElTablero()._seed(session)

    def _detalle(self, texto: str) -> str:
        return TestDetalleDeProductoEnElTablero()._detalle(texto)

    def test_la_nota_dice_el_ultimo_dia_con_dato_y_no_el_fin_de_la_ventana(
        self, client: TestClient, session: Session
    ) -> None:
        """El error que esto arregla: decía «llega hasta el <hoy>» con el
        último anuncio doce días atrás, prometiendo dato que no existe."""
        self._seed(session)

        detalle = self._detalle(client.get("/?vista=tablero&productos=arroz").text)
        nota = detalle[detalle.index("La última semana llega hasta el") :]

        assert _hace(3).strftime("%d/%m") in nota[:60]
        assert _hace(0).strftime("%d/%m") not in nota[:60]

    def test_el_paso_ya_no_rotula_el_final_de_cada_linea(
        self, client: TestClient, session: Session
    ) -> None:
        """Las etiquetas se pisaban cuando dos líneas terminaban cerca. Quién
        es cada tinta lo dice la leyenda, que por eso deja de ser opcional."""
        self._seed(session)

        detalle = self._detalle(client.get("/?vista=tablero&productos=arroz").text)

        assert "ph-pasos-etq" not in detalle
        assert 'class="ph-leyenda"' in detalle

    def test_las_fechas_son_las_de_los_periodos_y_no_las_de_la_ventana(
        self, client: TestClient, session: Session
    ) -> None:
        """Antes se escribían dos fechas: el principio y el fin de la ventana,
        que no marcan ningún punto del dibujo. Ahora va una debajo de cada
        período con dato, en el inicio de ese período.

        La semilla deja dos semanas con anuncios —la de hace 12 días y la de
        hace 3 y 4—, que empiezan hace 13 y hace 6 días."""
        self._seed(session)

        detalle = self._detalle(client.get("/?vista=tablero&productos=arroz").text)
        pasos = detalle[detalle.index('class="ph-pasos"') : detalle.index("</svg>")]
        fechas = re.findall(r'text-anchor="middle">([^<]+)</text>', pasos)

        assert fechas == [_hace(13).strftime("%d/%m"), _hace(6).strftime("%d/%m")]
        # Las de la ventana, que es lo que se escribía antes, ya no están.
        assert _hace(30).strftime("%d/%m") not in fechas
        assert _hace(0).strftime("%d/%m") not in fechas

    def test_a_la_derecha_de_cada_provincia_va_la_mediana_y_no_los_anuncios(
        self, client: TestClient, session: Session
    ) -> None:
        """Prototipo E: la cifra que se viene a buscar es la mediana. Cuántos
        anuncios la sostienen sigue en el `title` de la fila."""
        self._seed(session)

        detalle = self._detalle(client.get("/?vista=tablero&productos=arroz").text)
        compara = detalle[detalle.index('class="ph-compara"') :]
        compara = compara[: compara.index("</svg>")]

        assert "ph-compara-mediana-txt" in compara
        assert "anuncios</text>" not in compara
        assert ">410<" in compara  # la mediana de Santiago, escrita
        assert "410 CUP/lb" in compara  # y en el title, con su unidad

    def test_los_dos_graficos_pintan_cada_provincia_del_mismo_color(
        self, client: TestClient, session: Session
    ) -> None:
        """La leyenda es una sola para los dos dibujos, así que la tinta de
        una provincia tiene que ser la misma en los dos. Hasta el 2026-09-20
        «Cuánto varía» era todo de un color y la leyenda no explicaba nada."""
        self._seed(session)

        detalle = self._detalle(client.get("/?vista=tablero&productos=arroz").text)

        def tintas(marca: str, clase: str, corte: str) -> dict[str, str]:
            """Provincia → clase de color, leyendo el primer `title` de cada
            grupo: el de pasos trae uno por punto y el de comparación uno por
            fila, así que se toma el primero de cada `<g>`."""
            desde = detalle.index(marca)
            svg = detalle[desde : detalle.index("</svg>", desde)]
            fuera = {}
            for slot, cuerpo in re.findall(clase + r"(.*?)</g>", svg, re.S):
                nombre = re.search(r"<title>([^" + corte + r"]+)" + corte, cuerpo)
                assert nombre is not None, cuerpo[:200]
                fuera[nombre.group(1)] = slot
            return fuera

        en_pasos = tintas('class="ph-pasos"', r'<g class="ph-prov-(\d)">', ",")
        en_compara = tintas(
            'class="ph-compara"', r'<g class="ph-compara-fila ph-prov-(\d)[^"]*">', ":"
        )

        assert set(en_pasos) == {"La Habana", "Santiago de Cuba"}
        assert en_pasos == en_compara

    def test_la_leyenda_de_cuanto_varia_va_debajo_del_dibujo(
        self, client: TestClient, session: Session
    ) -> None:
        """En «Cómo se movió» la leyenda va arriba y en «Cuánto varía» abajo,
        como el prototipo: ahí cada fila ya trae su nombre a la izquierda, así
        que la leyenda es un recordatorio del color, no la identificación."""
        self._seed(session)

        detalle = self._detalle(client.get("/?vista=tablero&productos=arroz").text)
        varia = detalle[detalle.index(">Cuánto varía</h2>") :]
        movio = detalle[detalle.index(">Cómo se movió</h2>") : detalle.index(">Cuánto varía</h2>")]

        assert varia.index('class="ph-compara"') < varia.index('class="ph-leyenda"')
        assert movio.index('class="ph-leyenda"') < movio.index('class="ph-pasos"')


class TestCambioUnicoEnElTablero:
    """El cambio de la lista de productos y el de las tarjetas del mapa son el
    mismo número (2026-09-18, slice 3); la tabla del tablero ya no lo repite
    (2026-09-19, S5).

    Antes la tabla mostraba el cambio del último formato de la provincia:
    arroz en La Habana decía −12% en todas las filas, el de «saco de 50 kg»
    con un solo anuncio, cuando la lista decía −3%.

    Valores esperados, a mano (ventana de 30 días; el período anterior son
    los 30 días previos). Arroz en La Habana, dos formatos de 1 lb:
    - paquete: antes 300, 300, 300; ahora 330, 330, 330 → +10% él solo.
    - saco (un anuncio): antes 320; ahora 290 → −9% él solo.
    Todo el producto: antes 300, 300, 300, 320 → mediana 300; ahora 330, 330,
    330, 290 → mediana 330 → +10%. Ese es el número de todas las filas."""

    def _seed(self, session: Session) -> None:
        arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True)
        habana = Province(code="hab", name="La Habana")
        session.add_all([arroz, habana])
        session.commit()
        paquete = _formato(session, product=arroz, packaging="paquete", quantity="1", unit="lb")
        saco = _formato(session, product=arroz, packaging="saco", quantity="1", unit="lb")
        fuente = _ingestion(session, hash_="h-cambio")
        for dia, precio in ((45, "300"), (42, "300"), (40, "300"), (12, "330"), (8, "330"), (3, "330")):
            _precio(session, product=arroz, province=habana, source=fuente, dia=_hace(dia),
                    price=precio, format_id=paquete.id)
        _precio(session, product=arroz, province=habana, source=fuente, dia=_hace(44),
                price="320", format_id=saco.id)
        _precio(session, product=arroz, province=habana, source=fuente, dia=_hace(5),
                price="290", format_id=saco.id)
        session.commit()

    def test_por_envase_no_repite_el_cambio_de_cada_formato(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)

        texto = client.get("/?vista=tablero&productos=arroz").text
        panel = texto[texto.index('class="ph-panel ph-envases"') :]
        panel = panel[: panel.index("</section>")]

        assert panel.count("<tr") == 3  # el encabezado y un renglón por formato
        # El cambio vive en la lista de productos; «Por envase» es solo precios
        # (2026-09-19), así que ni el del producto ni el de un formato.
        assert "<th>Cambio</th>" not in texto
        assert "+10%" not in panel and "−9%" not in panel

    def test_la_tabla_y_la_lista_dicen_lo_mismo(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)

        texto = client.get("/?vista=tablero").text
        fila = re.search(r'<span class="ph-tarjeta-nombre">Arroz</span>[\s\S]*?</a>', texto)

        assert fila is not None
        assert '<div class="ph-col-change">+10% (base 4)</div>' in fila.group(0)

    def test_la_tarjeta_del_mapa_dice_lo_mismo(
        self, client: TestClient, session: Session
    ) -> None:
        """`attach_trends` alimenta también las tarjetas de la ventana
        «Precios» del mapa: el arreglo llega a las dos vistas."""
        self._seed(session)

        texto = client.get("/?ver=precios").text

        assert "+10%" in texto
        assert "−9%" not in texto


class TestUnaSolaMedianaEnElTablero:
    """La lista de productos junta todos los formatos de la provincia (slice 4,
    2026-09-19). La tira «dónde está más barato», que antes elegía «el formato
    mejor respaldado» y daba 530 donde la lista daba 463, salió del tablero en
    S5; esto protege que la única cifra que queda no vuelva a elegir un formato.

    Valores esperados, a mano. Arroz en La Habana, dos formatos de 1 lb, todos
    dentro de los últimos 30 días:
    - paquete: 300, 300 (2 anuncios)
    - saco: 400, 420, 440 (3 anuncios, el mejor respaldado)
    Todos juntos: 300, 300, 400, 420, 440 → mediana 400, 5 anuncios.
    (Elegir el saco, que era lo que hacía la tira, daba 420 y 3 anuncios.)"""

    def _seed(self, session: Session) -> None:
        arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True)
        habana = Province(code="hab", name="La Habana")
        session.add_all([arroz, habana])
        session.commit()
        paquete = _formato(session, product=arroz, packaging="paquete", quantity="1", unit="lb")
        saco = _formato(session, product=arroz, packaging="saco", quantity="1", unit="lb")
        fuente = _ingestion(session, hash_="h-mediana")
        for dia, precio, formato in (
            (20, "300", paquete), (15, "300", paquete),
            (10, "400", saco), (6, "420", saco), (2, "440", saco),
        ):
            _precio(session, product=arroz, province=habana, source=fuente, dia=_hace(dia),
                    price=precio, format_id=formato.id)
        session.commit()

    def test_la_lista_de_productos_dice_lo_mismo(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)

        texto = client.get("/?vista=tablero").text
        fila = re.search(r'<span class="ph-tarjeta-nombre">Arroz</span>[\s\S]*?</a>', texto)

        assert fila is not None
        assert '<div class="ph-fig">400 ' in fila.group(0)



class TestListaAbsorbeLaTira:
    """S5 (2026-09-19): la lista de productos muestra lo que antes mostraban la
    cinta y las tiras —quién es más barata y cuántos anuncios sostienen cada
    cifra—, y esas dos piezas salen del tablero.

    Valores esperados, a mano. Arroz de 1 lb en dos provincias, todo dentro de
    los últimos 30 días: La Habana 300, 300 → mediana 300, 2 anuncios;
    Santiago 400, 410 → mediana 405, 2 anuncios. La Habana es la más barata."""

    def _seed(self, session: Session) -> None:
        arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True)
        habana = Province(code="hab", name="La Habana")
        santiago = Province(code="scu", name="Santiago de Cuba")
        session.add_all([arroz, habana, santiago])
        session.commit()
        fuente = _ingestion(session, hash_="h-absorbe")
        anuncios = ((9, "300", habana), (5, "300", habana), (8, "400", santiago), (3, "410", santiago))
        for dia, precio, prov in anuncios:
            _precio(session, product=arroz, province=prov, source=fuente, dia=_hace(dia), price=precio)
        session.commit()

    def _fila(self, session: Session, client: TestClient) -> str:
        texto = client.get("/?vista=tablero").text
        fila = re.search(r'<span class="ph-tarjeta-nombre">Arroz</span>[\s\S]*?</a>', texto)
        assert fila is not None
        return str(fila.group(0))

    def test_la_celda_de_la_provincia_mas_barata_lleva_la_etiqueta(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)

        fila = self._fila(session, client)

        assert fila.count("más barata") == 1
        assert fila.index("más barata") < fila.index('<div class="ph-fig">405 ')

    def test_cada_celda_dice_cuantos_anuncios_la_sostienen(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)

        fila = self._fila(session, client)

        assert fila.count('<div class="ph-col-n">2 anuncios</div>') == 2

    def test_el_cambio_muestra_la_base_contra_la_que_se_compara(
        self, client: TestClient, session: Session
    ) -> None:
        """Sin período anterior no hay cambio: dice «sin mes anterior», sin base."""
        self._seed(session)

        fila = self._fila(session, client)

        assert fila.count('<div class="ph-col-change">sin mes anterior</div>') == 2

    def test_con_periodo_anterior_el_cambio_lleva_su_base(
        self, client: TestClient, session: Session
    ) -> None:
        """Arroz en La Habana: antes 250, 250 (mes anterior); ahora 300, 300.
        300/250 − 1 = +20%, contra 2 anuncios: «+20% (base 2)». Sin la base,
        un cambio contra un solo anuncio se lee igual que uno contra cien."""
        arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True)
        habana = Province(code="hab", name="La Habana")
        session.add_all([arroz, habana])
        session.commit()
        fuente = _ingestion(session, hash_="h-base")
        for dia, precio in ((45, "250"), (40, "250"), (9, "300"), (5, "300")):
            _precio(session, product=arroz, province=habana, source=fuente, dia=_hace(dia), price=precio)
        session.commit()

        fila = self._fila(session, client)

        assert '<div class="ph-col-change">+20% (base 2)</div>' in fila

    def test_ya_no_hay_cinta_ni_tiras_ni_busqueda_ni_columna_de_cambio(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)

        texto = client.get("/?vista=tablero").text

        for resto in ("ph-strips", "ph-strip-cell", "ph-ticker-item", "tablero-q", "<th>Cambio</th>"):
            assert resto not in texto, resto


class TestPorEnvaseEnElDetalle:
    """S6 (2026-09-19): la tabla por formato deja «Todos» y pasa al detalle del
    producto como «Por envase», agrupada por provincia sin repetirla en cada
    fila. Los envases de la nota §9.0 («muestra en formación») siguen a la
    vista: la nota se muda a la lista de productos.

    Valores esperados, a mano. Arroz, envases de 1 lb (el precio del envase es
    el precio por libra), todo dentro de los últimos 30 días:
    - La Habana: paquete 300, 320 (2 anuncios: rango 300–320, mediana 310);
      saco 400 (1 anuncio: precio 400, sin mediana, «—»).
    - Santiago de Cuba: paquete 410 (1 anuncio).
    Con más de 3 envases en una provincia se ven los 3 de más muestra y el
    resto queda en un desplegable."""

    def _seed(self, session: Session) -> None:
        arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True)
        habana = Province(code="hab", name="La Habana")
        santiago = Province(code="scu", name="Santiago de Cuba")
        session.add_all([arroz, habana, santiago])
        session.commit()
        paquete = _formato(session, product=arroz, packaging="paquete", quantity="1", unit="lb")
        saco = _formato(session, product=arroz, packaging="saco", quantity="1", unit="lb")
        fuente = _ingestion(session, hash_="h-envases")
        for dia, precio, prov, formato in (
            (9, "300", habana, paquete), (5, "320", habana, paquete),
            (7, "400", habana, saco), (3, "410", santiago, paquete),
        ):
            _precio(session, product=arroz, province=prov, source=fuente, dia=_hace(dia),
                    price=precio, format_id=formato.id)
        session.commit()

    def _por_envase(self, client: TestClient) -> str:
        texto = str(client.get("/?vista=tablero&productos=arroz").text)
        inicio = texto.index('class="ph-panel ph-envases"')
        return texto[inicio : texto.index("</section>", inicio)]

    def test_en_todos_ya_no_hay_tabla_por_formato(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)

        texto = client.get("/?vista=tablero").text

        assert 'class="ph-panel ph-envases"' not in texto
        assert "<table" not in texto

    def test_con_un_producto_hay_un_grupo_por_provincia(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)

        panel = self._por_envase(client)

        assert panel.count('class="ph-envases-prov-name"') == 2
        assert panel.index("La Habana") < panel.index("Santiago de Cuba")

    def test_la_provincia_no_se_repite_en_cada_fila(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)

        panel = self._por_envase(client)

        assert panel.count("La Habana") == 1  # el encabezado del grupo, no cada fila

    def test_cada_fila_dice_envase_precio_mediana_y_anuncios(
        self, client: TestClient, session: Session
    ) -> None:
        self._seed(session)

        panel = self._por_envase(client)
        habana = panel[: panel.index("Santiago de Cuba")]

        # paquete: 2 anuncios, primero por tener más muestra; rango 300–320, mediana 310.
        assert habana.index("Paquete de 1 lb") < habana.index("Saco de 1 lb")
        assert re.search(r"Paquete de 1 lb[\s\S]*?>300–320<[\s\S]*?>310<[\s\S]*?>2<", habana)
        # saco: una sola observación → un precio y «—» de mediana.
        assert re.search(r"Saco de 1 lb[\s\S]*?>400<[\s\S]*?>—<[\s\S]*?>1<", habana)

    def test_la_provincia_elegida_no_recorta_los_envases(
        self, client: TestClient, session: Session
    ) -> None:
        """La provincia elegida enfoca solo la lista de «Todos»: el detalle
        compara todas, así que «Por envase» trae los dos grupos."""
        self._seed(session)

        texto = client.get("/?vista=tablero&productos=arroz&provincia=santiago-de-cuba").text
        panel = texto[texto.index('class="ph-panel ph-envases"') :]
        panel = panel[: panel.index("</section>")]

        assert panel.count('class="ph-envases-prov-name"') == 2
        assert "Santiago de Cuba" in panel and "La Habana" in panel

    def test_con_mas_de_cuatro_envases_el_resto_va_en_un_desplegable(
        self, client: TestClient, session: Session
    ) -> None:
        """La Habana con 5 envases de 1 lb y muestras 5, 4, 3, 2 y 1: se ven
        los cuatro mayores —los que muestra el prototipo— y el quinto (pomo,
        1 anuncio) queda adentro."""
        arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True)
        habana = Province(code="hab", name="La Habana")
        session.add_all([arroz, habana])
        session.commit()
        fuente = _ingestion(session, hash_="h-envases-5")
        for envase, anuncios in (
            ("paquete", 5), ("saco", 4), ("caja", 3), ("botella", 2), ("pomo", 1)
        ):
            formato = _formato(session, product=arroz, packaging=envase, quantity="1", unit="lb")
            for i in range(anuncios):
                _precio(session, product=arroz, province=habana, source=fuente,
                        dia=_hace(2 + i), price="300", format_id=formato.id)
        session.commit()

        panel = self._por_envase(client)
        visible, _, oculto = panel.partition('<details class="ph-envases-mas">')

        assert "Ver los 5 envases de La Habana" in oculto
        assert all(
            e in visible
            for e in ("Paquete de 1 lb", "Saco de 1 lb", "Caja de 1 lb", "Botella de 1 lb")
        )
        assert "Pomo de 1 lb" not in visible and "Pomo de 1 lb" in oculto

    def test_el_precio_no_repite_la_misma_cifra_dos_veces(
        self, client: TestClient, session: Session
    ) -> None:
        """Dos anuncios del mismo envase al mismo precio no son un rango:
        «300–300» inventaría una variación que no se observó."""
        arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True)
        habana = Province(code="hab", name="La Habana")
        session.add_all([arroz, habana])
        session.commit()
        paquete = _formato(session, product=arroz, packaging="paquete", quantity="1", unit="lb")
        fuente = _ingestion(session, hash_="h-envases-plano")
        for dia in (6, 4):
            _precio(session, product=arroz, province=habana, source=fuente, dia=_hace(dia),
                    price="300", format_id=paquete.id)
        session.commit()

        panel = self._por_envase(client)

        assert "300–300" not in panel
        assert re.search(r"Paquete de 1 lb[\s\S]*?>300<[\s\S]*?>300<[\s\S]*?>2<", panel)

    def test_la_moneda_va_en_el_encabezado_y_no_en_cada_fila(
        self, client: TestClient, session: Session
    ) -> None:
        """Prototipo E: el panel es de un producto y su moneda es una sola, así
        que se dice una vez en el subtítulo y en la columna."""
        self._seed(session)

        panel = self._por_envase(client)

        precios = re.findall(r'<td class="mono precio">([^<]*)</td>', panel)

        assert "Lo que se paga por el envase, en CUP (no por libra)" in panel
        assert "<th>Precio del envase (CUP)</th>" in panel
        assert precios and not any("CUP" in c for c in precios)

    def test_con_exactamente_cuatro_envases_no_hay_desplegable(
        self, client: TestClient, session: Session
    ) -> None:
        """El borde del corte: con cuatro entran los cuatro y el `<details>`
        no se emite. Con cinco ya hay desplegable, y eso lo prueba el test de
        arriba; acá se fija el lado de abajo para que mover el corte otra vez
        no pase inadvertido."""
        arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True)
        habana = Province(code="hab", name="La Habana")
        session.add_all([arroz, habana])
        session.commit()
        fuente = _ingestion(session, hash_="h-envases-justo-4")
        for envase, anuncios in (("paquete", 4), ("saco", 3), ("caja", 2), ("botella", 1)):
            formato = _formato(session, product=arroz, packaging=envase, quantity="1", unit="lb")
            for i in range(anuncios):
                _precio(session, product=arroz, province=habana, source=fuente,
                        dia=_hace(2 + i), price="300", format_id=formato.id)
        session.commit()

        panel = self._por_envase(client)

        assert "ph-envases-mas" not in panel
        assert "envases de La Habana" not in panel
        assert all(
            e in panel
            for e in ("Paquete de 1 lb", "Saco de 1 lb", "Caja de 1 lb", "Botella de 1 lb")
        )

    def test_el_panel_nunca_mezcla_dos_monedas(
        self, client: TestClient, session: Session
    ) -> None:
        """Por qué la moneda puede vivir en el encabezado y no en cada fila.

        `build_cards` descarta las filas `unconverted` —moneda extranjera sin
        tasa resuelta— y `_resolve_row` funde en CUP todo lo que sí se pudo
        convertir, así que toda tarjeta que llega al panel es CUP. Acá se
        siembra un anuncio en USD sin tasa junto a los de CUP: no aparece, y
        el encabezado sigue diciendo una sola moneda. Si algún día una fila en
        otra moneda llegara al panel, este test cae y hay que devolverle la
        moneda a la fila.
        """
        arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True)
        habana = Province(code="hab", name="La Habana")
        session.add_all([arroz, habana])
        session.commit()
        paquete = _formato(session, product=arroz, packaging="paquete", quantity="1", unit="lb")
        saco = _formato(session, product=arroz, packaging="saco", quantity="1", unit="lb")
        fuente = _ingestion(session, hash_="h-envases-dos-monedas")
        for dia in (9, 7, 5):
            _precio(session, product=arroz, province=habana, source=fuente, dia=_hace(dia),
                    price="300", format_id=paquete.id)
        _precio(session, product=arroz, province=habana, source=fuente, dia=_hace(4),
                price="2", format_id=saco.id, currency=Currency.USD.value)
        session.commit()

        panel = self._por_envase(client)

        assert panel.count("<th>Precio del envase (") == 1
        assert "<th>Precio del envase (CUP)</th>" in panel
        assert "USD" not in panel
        assert "Paquete de 1 lb" in panel and "Saco de 1 lb" not in panel

    def test_dos_filas_del_mismo_envase_se_distinguen_por_su_variante(
        self, client: TestClient, session: Session
    ) -> None:
        """Con datos reales, «saco de 100 lb» aparecía dos veces en La Habana:
        eran dos variantes (una genérica y una «Criollo») que la tabla vieja no
        mostraba, así que parecían filas repetidas. Cada fila dice cuál es."""
        arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True)
        habana = Province(code="hab", name="La Habana")
        session.add_all([arroz, habana])
        session.commit()
        criollo = ProductVariant(
            product_id=arroz.id, kind="Criollo", brand=None, origin=None,
            kind_key="criollo", brand_key=None, origin_key=None,
        )
        session.add(criollo)
        session.commit()
        fuente = _ingestion(session, hash_="h-envases-variante")
        for dia, precio, variante in ((6, "300", None), (4, "350", criollo)):
            session.add(PriceHistory(
                product_id=arroz.id, province_id=habana.id, price=Decimal(precio),
                currency=Currency.CUP.value, source_id=fuente.id, unit="lb",
                sale_format=SaleFormat.RETAIL.value, quantity=Decimal("1"),
                published_date=_hace(dia), variant_id=variante.id if variante else None,
            ))
        session.commit()

        panel = self._por_envase(client)

        assert panel.count("Sin envase declarado") == 2
        assert panel.count('class="ph-envases-variante"') == 1
        assert '<span class="ph-envases-variante">Criollo</span>' in panel

    def test_la_nota_de_muestra_en_formacion_sigue_a_la_vista_en_todos(
        self, client: TestClient, session: Session
    ) -> None:
        """§9.0: la cifra va rotulada «muestra en formación» mientras N siga sin
        calibrar. El rótulo va una vez por vista, bajo el título."""
        self._seed(session)

        for url in ("/?vista=tablero", "/?vista=tablero&productos=arroz"):
            texto = client.get(url).text

            nota = "<strong>muestra en formación</strong>"
            assert texto.count(nota) == 1, url
            assert texto.index(nota) > texto.index('<h1 class="ph-h1">')
