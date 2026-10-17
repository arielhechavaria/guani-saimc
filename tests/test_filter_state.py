"""`FilterState`: el estado de filtros de la capa pública en un solo lugar.

Antes cada constructor de URL (`build_window_nav`, `build_province_nav`,
`build_layer_nav`, `_product_link`, `period_hidden`, `map_href`) recibía por su
cuenta producto, provincia, fechas, ventanas, capa y vista, y componía el query
a mano. Aquí se fija, con valores escritos a mano, qué URL sale de cada estado
y que provincia y fechas hacen el viaje `/precios` → `/producto/<slug>` → vuelta.

Fechas relativas a hoy en los tests de ruta (mismo motivo que `_hace` en
`test_public_filter.py`): la vista filtra contra `date.today()`.
"""

import re
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from html import unescape

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
    FilterState,
    build_layer_nav,
    build_product_nav,
    build_province_nav,
    build_window_nav,
)

DESDE, HASTA = date(2026, 8, 15), date(2026, 8, 25)
D, H = DESDE.isoformat(), HASTA.isoformat()


class TestUrl:
    def test_orden_fijo_y_fechas_siempre_explicitas(self) -> None:
        estado = FilterState(desde=DESDE, hasta=HASTA)

        assert estado.url() == f"/precios?desde={D}&hasta={H}"

    def test_todos_los_ejes_en_el_orden_producto_pestana_provincia_fechas_ver_capa_vista(
        self,
    ) -> None:
        estado = FilterState(
            desde=DESDE,
            hasta=HASTA,
            productos=("arroz", "pollo"),
            pestana="pollo",
            provincia="la-habana",
            ver=("cobertura", "precios"),
            capa="densidad",
            vista="explorar",
        )

        assert estado.url() == (
            f"/precios?productos=arroz,pollo&pestana=pollo&provincia=la-habana"
            f"&desde={D}&hasta={H}&ver=cobertura,precios&capa=densidad&vista=explorar"
        )

    def test_la_capa_por_defecto_no_ensucia_la_url_pero_la_vista_viaja_siempre(self) -> None:
        estado = FilterState(desde=DESDE, hasta=HASTA, vista="tablero")

        assert estado.url() == f"/precios?desde={D}&hasta={H}&vista=tablero"

    def test_productos_gana_sobre_el_alias_producto(self) -> None:
        estado = FilterState(desde=DESDE, hasta=HASTA, producto="aceite", productos=("arroz",))

        assert estado.url() == f"/precios?productos=arroz&desde={D}&hasta={H}"

    def test_el_alias_producto_se_emite_si_no_hay_productos(self) -> None:
        estado = FilterState(desde=DESDE, hasta=HASTA, producto="aceite")

        assert estado.url() == f"/precios?producto=aceite&desde={D}&hasta={H}"

    def test_path_distinto(self) -> None:
        estado = FilterState(desde=DESDE, hasta=HASTA, provincia="la-habana")

        assert estado.url("/datos.csv") == f"/datos.csv?provincia=la-habana&desde={D}&hasta={H}"


class TestConCambios:
    def test_with_devuelve_otro_estado_y_no_toca_el_original(self) -> None:
        base = FilterState(desde=DESDE, hasta=HASTA, provincia="la-habana")

        otro = base.with_(provincia="santiago-de-cuba")

        assert otro.provincia == "santiago-de-cuba"
        assert base.provincia == "la-habana"

    def test_es_inmutable(self) -> None:
        estado = FilterState(desde=DESDE, hasta=HASTA)

        with pytest.raises(AttributeError):
            estado.provincia = "x"  # type: ignore[misc]


class TestDesdeNombres:
    def test_convierte_nombres_a_slugs_una_sola_vez(self) -> None:
        estado = FilterState.from_names(
            DESDE,
            HASTA,
            products=["Azúcar blanca", "Arroz"],
            province="Santiago de Cuba",
            vista="tablero",
        )

        assert estado.productos == ("azucar-blanca", "arroz")
        assert estado.provincia == "santiago-de-cuba"

    def test_sin_provincia_ni_producto_quedan_vacios(self) -> None:
        estado = FilterState.from_names(DESDE, HASTA, province=None)

        assert estado.provincia == ""
        assert estado.productos == ()
        assert estado.producto == ""


class TestVistaLiviana:
    def test_light_url_lleva_provincia_y_fechas_y_nada_del_mapa(self) -> None:
        estado = FilterState(
            desde=DESDE,
            hasta=HASTA,
            productos=("arroz",),
            provincia="la-habana",
            ver=("precios",),
            capa="densidad",
            vista="mapa",
        )

        assert estado.light_url("arroz") == (
            f"/producto/arroz?provincia=la-habana&desde={D}&hasta={H}"
        )

    def test_map_url_vuelve_al_mapa_con_el_producto_provincia_y_fechas(self) -> None:
        """Ida y vuelta: lo que `light_url` lleva a `/producto/<slug>`, `map_url`
        lo trae de regreso a `/precios` sin perder nada."""
        estado = FilterState(desde=DESDE, hasta=HASTA, provincia="la-habana")

        assert estado.map_url("arroz") == (
            f"/precios?productos=arroz&provincia=la-habana&desde={D}&hasta={H}&vista=mapa"
        )


class TestCamposOcultos:
    """Lo que el formulario de fechas (`_selector_periodo.html`) reenvía."""

    def test_vista_productos_pestana_y_provincia_en_ese_orden(self) -> None:
        estado = FilterState(
            desde=DESDE,
            hasta=HASTA,
            productos=("arroz", "pollo"),
            pestana="pollo",
            provincia="la-habana",
            vista="explorar",
            ver=("precios",),
            capa="densidad",
        )

        assert estado.hidden_fields() == [
            ("vista", "explorar"),
            ("productos", "arroz,pollo"),
            ("pestana", "pollo"),
            ("provincia", "la-habana"),
        ]

    def test_los_ejes_vacios_viajan_vacios(self) -> None:
        estado = FilterState(desde=DESDE, hasta=HASTA, vista="tablero")

        assert estado.hidden_fields() == [
            ("vista", "tablero"),
            ("productos", ""),
            ("pestana", ""),
            ("provincia", ""),
        ]


class TestBarrasConEstado:
    """Las barras reciben el estado entero y solo cambian su propio eje."""

    ESTADO = FilterState(
        desde=DESDE,
        hasta=HASTA,
        productos=("arroz",),
        provincia="la-habana",
        ver=("cobertura",),
        capa="densidad",
        vista="tablero",
    )

    def test_provincia_cambia_solo_la_provincia(self) -> None:
        nav = build_province_nav(("La Habana", "Santiago de Cuba"), "La Habana", self.ESTADO)
        por_label = {c.label: c for c in nav}
        base = f"productos=arroz&desde={D}&hasta={H}&ver=cobertura&capa=densidad&vista=tablero"

        assert por_label["Todas"].query == f"/precios?{base}"
        assert por_label["Santiago de Cuba"].query == (
            f"/precios?productos=arroz&provincia=santiago-de-cuba&desde={D}&hasta={H}"
            "&ver=cobertura&capa=densidad&vista=tablero"
        )
        assert por_label["La Habana"].is_selected

    def test_ventana_cambia_solo_las_fechas(self) -> None:
        hoy = date(2026, 9, 14)
        nav = build_window_nav(self.ESTADO, hoy=hoy)
        siete = next(c for c in nav if c.days == 7)

        assert siete.query == (
            f"/precios?productos=arroz&provincia=la-habana&desde={(hoy - timedelta(days=7)).isoformat()}"
            f"&hasta={hoy.isoformat()}&ver=cobertura&capa=densidad&vista=tablero"
        )
        assert not any(c.is_selected for c in nav)

    def test_capa_cambia_la_capa_y_es_siempre_la_del_mapa(self) -> None:
        nav = build_layer_nav("densidad", self.ESTADO)
        por_slug = {c.slug: c for c in nav}

        assert por_slug["publicacion"].query == (
            f"/precios?productos=arroz&provincia=la-habana&desde={D}&hasta={H}"
            "&ver=cobertura&capa=publicacion&vista=mapa"
        )
        assert por_slug["densidad"].is_selected

    def test_chip_de_producto_conserva_provincia_y_fechas(self) -> None:
        nav = build_product_nav(("Arroz",), None, self.ESTADO)

        assert nav[1].query == (
            f"/precios?producto=arroz&provincia=la-habana&desde={D}&hasta={H}"
        )


# --- ruta: el bug de /precios?provincia=…&vista=tablero y el viaje a /producto ---


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    def _get_db_override() -> Iterator[Session]:
        yield session

    app.dependency_overrides[get_db] = _get_db_override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _hace(dias: int) -> date:
    return date.today() - timedelta(days=dias)


def _precio(
    session: Session, *, product: Product, province: Province, source: RawIngestion, dia: date,
    price: str,
) -> None:
    session.add(
        PriceHistory(
            provenance="human",
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


def _seed(session: Session) -> None:
    """Arroz con anuncios en La Habana (3) y Santiago (1); Pollo solo en La
    Habana (2). Con Santiago elegida, Pollo no tiene celda propia."""
    arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True)
    pollo = Product(name="Pollo", category="carnes", unit="lb", aliases=[], is_pilot=True)
    habana = Province(code="hab", name="La Habana")
    santiago = Province(code="scu", name="Santiago de Cuba")
    session.add_all([arroz, pollo, habana, santiago])
    session.commit()
    fuente = RawIngestion(
        source_type=SourceType.INBOX_TEXT,
        file_path="raw/h.dat",
        content_hash="h-filtros",
        content_type=ContentType.TEXT,
        processing_status=ProcessingStatus.CURATED,
        captured_at=datetime(2026, 8, 10, tzinfo=UTC),
    )
    session.add(fuente)
    session.flush()
    for dias, precio in ((6, "300"), (4, "310"), (2, "320")):
        _precio(session, product=arroz, province=habana, source=fuente, dia=_hace(dias), price=precio)
    _precio(session, product=arroz, province=santiago, source=fuente, dia=_hace(3), price="400")
    for dias, precio in ((5, "700"), (1, "710")):
        _precio(session, product=pollo, province=habana, source=fuente, dia=_hace(dias), price=precio)
    session.commit()


def _filas(html: str) -> list[tuple[str, str]]:
    """`(nombre del producto, html de la fila)` de la lista del tablero."""
    return [
        (m.group(1), m.group(0))
        for m in re.finditer(
            r'<a class="ph-fila".*?<span class="ph-fila-nombre">([^<]*)</span>.*?</a>',
            html,
            flags=re.S,
        )
    ]


class TestTableroConProvinciaEnfocada:
    """Hipótesis del operador sobre `/precios?provincia=santiago-de-cuba&vista=tablero`:
    la lista trae productos sin celda en la provincia enfocada y los ordena por
    el total de todas las provincias.

    Verificada: es lo que pasa. Pero no es un bug accidental: lo decidió S8
    (2026-09-19, «no se esconde el dato», descartada la opción de mostrar solo
    la provincia elegida) y el orden por muestra del producto entero, el
    2026-09-20. Este test lo deja fijado para que reabrirlo sea una decisión
    explícita en `docs/decisions.md` y no un arreglo de pasada."""

    def test_pollo_sin_celda_en_santiago_sigue_en_la_lista_y_el_orden_es_el_global(
        self, client: TestClient, session: Session
    ) -> None:
        _seed(session)
        r = client.get(
            f"/precios?provincia=santiago-de-cuba&desde={_hace(30).isoformat()}"
            f"&hasta={date.today().isoformat()}&vista=tablero"
        )

        assert r.status_code == 200
        filas = _filas(r.text)
        # Arroz: 4 anuncios en total (1 en Santiago); Pollo: 2, ninguno en Santiago.
        assert [nombre for nombre, _ in filas] == ["Arroz", "Pollo"]
        pollo = dict(filas)["Pollo"]
        assert "sin datos todavía" in pollo
        assert "La Habana" in pollo  # el dato de las otras provincias no se esconde


class TestViajeAProducto:
    """Provincia y fechas llegan a `/producto/<slug>` y vuelven."""

    def _desde_hasta(self) -> tuple[str, str]:
        return _hace(20).isoformat(), _hace(1).isoformat()

    def test_el_panel_del_mapa_lleva_provincia_y_fechas_a_la_vista_liviana(
        self, client: TestClient, session: Session
    ) -> None:
        _seed(session)
        desde, hasta = self._desde_hasta()
        r = client.get(
            f"/precios?productos=arroz&provincia=la-habana&desde={desde}&hasta={hasta}&vista=mapa"
        )

        hrefs = [unescape(h) for h in re.findall(r'href="(/producto/[^"]*)"', r.text)]
        assert f"/producto/arroz?provincia=la-habana&desde={desde}&hasta={hasta}" in hrefs

    def test_la_vista_liviana_vuelve_al_mapa_con_provincia_y_fechas(
        self, client: TestClient, session: Session
    ) -> None:
        _seed(session)
        desde, hasta = self._desde_hasta()
        r = client.get(f"/producto/arroz?provincia=la-habana&desde={desde}&hasta={hasta}")

        assert r.status_code == 200
        volver = unescape(re.search(r'<p class="ph-lite-mapa"><a href="([^"]+)"', r.text).group(1))
        assert volver == (
            f"/precios?productos=arroz&provincia=la-habana&desde={desde}&hasta={hasta}&vista=mapa"
        )
        # y esa URL de vuelta se sirve con los mismos filtros
        assert client.get(volver).status_code == 200

    def test_el_interruptor_mayorista_conserva_provincia_y_fechas(
        self, client: TestClient, session: Session
    ) -> None:
        _seed(session)
        desde, hasta = self._desde_hasta()
        r = client.get(f"/producto/arroz?provincia=la-habana&desde={desde}&hasta={hasta}")

        hrefs = [unescape(h) for h in re.findall(r'href="(/producto/[^"]*)"', r.text)]
        assert any(
            h.startswith(f"/producto/arroz?provincia=la-habana&desde={desde}&hasta={hasta}&capa=")
            for h in hrefs
        )
