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
from saimc.web.product_nav import (
    DEFAULT_WINDOW_DAYS,
    build_product_nav,
    build_province_nav,
    build_window_nav,
    product_slug,
    resolve_days,
    resolve_product,
    resolve_province,
)


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    def _get_db_override() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_db] = _get_db_override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


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
    _precio(session, product=arroz, province=habana, source=fuente, dia=date(2026, 8, 18))
    _precio(
        session,
        product=arroz,
        province=habana,
        source=fuente,
        dia=date(2026, 8, 21),
        price="320",
    )
    _precio(session, product=aceite, province=habana, source=fuente, dia=date(2026, 8, 20))
    _precio(session, product=azucar, province=habana, source=fuente, dia=date(2026, 8, 19))
    _precio(
        session,
        product=azucar,
        province=habana,
        source=fuente,
        dia=date(2026, 8, 22),
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
        # La barra de filtro está, con «Todos» marcado.
        assert 'class="ph-filter"' in texto
        assert 'class="is-selected" aria-current="true">Todos<' in texto

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
        assert 'class="is-selected" aria-current="true">Todos<' in response.text

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
        assert 'class="ph-filter"' in response.text

    def test_producto_con_un_solo_dia_no_dibuja_evolucion_pero_no_rompe(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_dos_productos(session)

        response = client.get("/?producto=aceite")

        assert response.status_code == 200
        # Aceite entra en la tabla por provincia (un día basta) pero no dibuja
        # serie (hacen falta 2 días distintos).
        assert "Aceite por formato y provincia" in response.text
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
        assert 'aria-current="true">Arroz<' in texto


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
            (date(2026, 8, 18), "300"),
            (date(2026, 8, 20), "340"),
            (date(2026, 8, 22), "320"),
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
            dia=date(2026, 8, 21),
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
        _precio(session, product=arroz, province=habana, source=fuente, dia=date(2026, 8, 18))
        _precio(
            session,
            product=arroz,
            province=habana,
            source=fuente,
            dia=date(2026, 8, 21),
            price="330",
        )
        _precio(
            session,
            product=arroz,
            province=santiago,
            source=fuente,
            dia=date(2026, 8, 20),
            price="410",
        )
        _precio(session, product=aceite, province=habana, source=fuente, dia=date(2026, 8, 19))
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
        assert 'aria-current="true">Arroz<' in texto
        tabla = texto.split("ph-provinces")[1].split("</table>")[0]
        assert "La Habana" in tabla
        assert "Santiago de Cuba" not in tabla

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
        _precio(session, product=arroz, province=habana, source=fuente, dia=date(2026, 8, 18))
        _precio(
            session, product=arroz, province=habana, source=fuente,
            dia=date(2026, 8, 21), price="330",
        )
        _precio(
            session, product=arroz, province=santiago, source=fuente,
            dia=date(2026, 8, 20), price="410",
        )
        _precio(session, product=aceite, province=habana, source=fuente, dia=date(2026, 8, 19))
        _precio(session, product=no_piloto, province=habana, source=fuente, dia=date(2026, 8, 19))
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
        self._seed(session)
        arroz = session.query(Product).filter_by(name="Arroz").one()
        habana = session.query(Province).filter_by(name="La Habana").one()
        fuente = _ingestion(session, hash_="h-csv-lejano")
        _precio(
            session, product=arroz, province=habana, source=fuente,
            dia=date.today() - timedelta(days=20), price="999",
        )
        session.commit()

        assert "999" in client.get("/datos.csv?dias=30").text
        assert "999" not in client.get("/datos.csv?dias=7").text

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

        # 15/08 queda fuera de una ventana de 7 días (hoy es 2026-08-26), pero
        # la línea de frescura igual lo muestra.
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
        _precio(session, product=arroz, province=habana, source=fuente, dia=date(2026, 8, 18))
        _precio(
            session, product=arroz, province=habana, source=fuente,
            dia=date(2026, 8, 21), price="330",
        )
        _precio(
            session, product=arroz, province=santiago, source=fuente,
            dia=date(2026, 8, 20), price="410",
        )
        _precio(session, product=aceite, province=habana, source=fuente, dia=date(2026, 8, 19))
        _precio(session, product=azucar, province=habana, source=fuente, dia=date(2026, 8, 19))
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

        assert "<title>SAIMC — Arroz — precios</title>" in enfocado
        assert "<title>SAIMC — Precios y cobertura</title>" in home
