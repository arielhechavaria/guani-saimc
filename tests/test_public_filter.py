"""Filtro por producto en la vista pública (`/?producto=<slug>`).

Lo que estos casos protegen:

- El slug es estable y resuelve de vuelta al nombre, tildes incluidas
  («azucar-blanca» -> «Azúcar blanca»).
- Elegir un producto acota las tarjetas y las series a ese producto, y nada
  más de la página (el mapa de cobertura no depende del producto elegido).
- Un `producto` que no existe no da 404: una vista pública muestra todo ante
  un query string raro.
- Elegir un producto sin dato suficiente no rompe la página: muestra el aviso
  y deja la barra de filtro para cambiar de producto.
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
    build_window_nav,
    product_slug,
    resolve_days,
    resolve_product,
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
        assert por_label["Aceite"].query == "/?producto=aceite"
        assert not por_label["Arroz"].is_selected

    def test_la_ventana_no_default_viaja_en_el_query_del_producto(self) -> None:
        nav = build_product_nav(("Arroz",), "Arroz", selected_days=7)
        por_label = {c.label: c for c in nav}

        assert por_label["Todos"].query == "/?dias=7"
        assert por_label["Arroz"].query == "/?producto=arroz&dias=7"


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
        assert "Arroz por provincia" in texto
        assert "Aceite" not in tabla

    def test_resuelve_un_nombre_con_tilde(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_dos_productos(session)

        texto = client.get("/?producto=azucar-blanca").text

        assert "Azúcar blanca por provincia" in texto
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
        assert "Aceite por provincia" in response.text
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
