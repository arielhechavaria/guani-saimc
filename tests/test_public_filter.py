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
from datetime import UTC, date, datetime
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
from saimc.web.product_nav import build_product_nav, product_slug, resolve_product


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

    def test_elegir_un_producto_acota_las_tarjetas(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_dos_productos(session)

        texto = client.get("/?producto=arroz").text

        # La barra de filtro nombra todos los productos; lo que se acota son
        # las tarjetas. Se mira el marcado de tarjeta, no el nombre suelto.
        assert '<span class="ph-card-product">Arroz</span>' in texto
        assert '<span class="ph-card-product">Aceite</span>' not in texto

    def test_resuelve_un_nombre_con_tilde(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_dos_productos(session)

        texto = client.get("/?producto=azucar-blanca").text

        assert '<span class="ph-card-product">Azúcar blanca</span>' in texto
        assert '<span class="ph-card-product">Arroz</span>' not in texto

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
        # Aceite tiene una tarjeta (un día basta) pero no serie (hacen falta 2).
        assert '<span class="ph-card-product">Aceite</span>' in response.text
        assert "No hay suficientes días" in response.text
