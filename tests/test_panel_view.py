"""`panel_view.build_product_panels`: agrega por producto para los paneles
flotantes de la home pública (slice 4 del rediseño)."""

from collections.abc import Sequence
from datetime import date, timedelta
from decimal import Decimal

from saimc.web.card_view import ProductCard
from saimc.web.panel_view import build_product_panels
from saimc.web.product_nav import DEFAULT_LAYER

HOY = date(2026, 9, 14)
DESDE = HOY - timedelta(days=30)


def _panels(
    active_products: list[str],
    cards: list[ProductCard],
    series: list,
    *,
    selected_province: str = "",
    selected_desde: date = DESDE,
    selected_hasta: date = HOY,
    ver: Sequence[str] = (),
    capa: str = DEFAULT_LAYER,
) -> list:
    """`build_product_panels` con el rango de calendario de siempre en estos
    tests (`DESDE`-`HOY`) ya puesto — la mayoría de estos casos no ejercita
    el date picker en sí, solo necesita un rango válido para armar el panel."""
    return build_product_panels(
        active_products,
        cards,
        series,
        selected_province=selected_province,
        selected_desde=selected_desde,
        selected_hasta=selected_hasta,
        ver=ver,
        capa=capa,
    )


def _card(
    product: str,
    province: str,
    *,
    count: int = 3,
    minimum: str = "100",
    maximum: str = "140",
    currency: str = "CUP",
    unit: str = "lb",
) -> ProductCard:
    return ProductCard(
        product_name=product,
        province_name=province,
        currency=currency,
        unit=unit,
        count=count,
        minimum=Decimal(minimum),
        median=Decimal(minimum),
        maximum=Decimal(maximum),
    )


class TestBuildProductPanels:
    def test_un_panel_por_producto_activo_en_orden(self) -> None:
        cards = [_card("Arroz", "La Habana"), _card("Aceite", "Camagüey")]

        panels = _panels(["Aceite", "Arroz"], cards, [])

        assert [p.name for p in panels] == ["Aceite", "Arroz"]

    def test_agrega_rango_conteo_y_provincias(self) -> None:
        cards = [
            _card("Arroz", "La Habana", count=10, minimum="90", maximum="130"),
            _card("Arroz", "Santiago de Cuba", count=4, minimum="110", maximum="160"),
        ]

        (panel,) = _panels(["Arroz"], cards, [])

        assert panel.count == 14
        assert panel.provinces == 2
        assert panel.minimum == Decimal("90")
        assert panel.maximum == Decimal("160")
        assert not panel.is_single_value

    def test_producto_activo_sin_tarjetas_entra_vacio(self) -> None:
        (panel,) = _panels(["Picadillo"], [], [])

        assert panel.name == "Picadillo"
        assert not panel.has_data
        assert panel.count == 0

    def test_una_sola_observacion_no_finge_rango(self) -> None:
        cards = [_card("Aceite", "La Habana", count=1, minimum="1200", maximum="1200")]

        (panel,) = _panels(["Aceite"], cards, [])

        assert panel.is_single_value

    def test_close_query_quita_solo_ese_producto(self) -> None:
        cards = [_card("Arroz", "La Habana"), _card("Aceite", "La Habana")]

        panels = _panels(["Arroz", "Aceite"], cards, [])
        por_nombre = {p.name: p for p in panels}

        # Cerrar Arroz deja a Aceite; cerrar el único deja la URL limpia.
        assert por_nombre["Arroz"].close_query == (
            f"/?productos=aceite&desde={DESDE.isoformat()}&hasta={HOY.isoformat()}"
        )
        (solo,) = _panels(["Arroz"], [_card("Arroz", "La Habana")], [])
        assert solo.close_query == f"/?desde={DESDE.isoformat()}&hasta={HOY.isoformat()}"

    def test_producto_piloto_tiene_ilustracion(self) -> None:
        (panel,) = _panels(["Aceite"], [_card("Aceite", "La Habana")], [])

        assert panel.has_illustration

    def test_producto_sin_entrada_no_tiene_ilustracion(self) -> None:
        (panel,) = _panels(["Café"], [], [])

        assert not panel.has_illustration

    def test_close_query_preserva_provincia_y_ventana(self) -> None:
        cards = [_card("Arroz", "La Habana"), _card("Aceite", "La Habana")]
        desde, hasta = date(2026, 8, 1), date(2026, 8, 7)

        panels = _panels(
            ["Arroz", "Aceite"],
            cards,
            [],
            selected_province="La Habana",
            selected_desde=desde,
            selected_hasta=hasta,
        )
        por_nombre = {p.name: p for p in panels}

        assert por_nombre["Arroz"].close_query == (
            "/?productos=aceite&provincia=la-habana&desde=2026-08-01&hasta=2026-08-07"
        )

    def test_light_query_incluye_el_rango_exacto(self) -> None:
        """La puerta de salida hacia la vista liviana (2026-09-14): el panel
        se queda en el mapa por defecto, `light_query` es cómo se sale de
        ahí a propósito. El rango de fechas viaja SIEMPRE explícito en la
        URL —a diferencia de `?dias=`, que se omitía en el default—: es un
        link para compartir, y el destinatario tiene que ver el mismo
        período que compartieron, no "los últimos 30 días desde que YO lo
        abra"."""
        (panel,) = _panels(
            ["Arroz"], [_card("Arroz", "La Habana")], [], selected_desde=DESDE, selected_hasta=HOY
        )

        assert panel.light_query == f"/producto/arroz?desde={DESDE.isoformat()}&hasta={HOY.isoformat()}"

    def test_light_query_preserva_provincia_y_ventana(self) -> None:
        (panel,) = _panels(
            ["Arroz"],
            [_card("Arroz", "La Habana")],
            [],
            selected_province="La Habana",
            selected_desde=date(2026, 8, 1),
            selected_hasta=date(2026, 8, 7),
        )

        assert panel.light_query == "/producto/arroz?provincia=la-habana&desde=2026-08-01&hasta=2026-08-07"

    def test_light_query_no_lleva_ver_ni_capa(self) -> None:
        """La vista liviana no tiene ventanas ni capas de mapa: preservarlas
        ahí no significaría nada."""
        (panel,) = _panels(
            ["Arroz"],
            [_card("Arroz", "La Habana")],
            [],
            ver=["precios"],
            capa="densidad",
        )

        assert panel.light_query == f"/producto/arroz?desde={DESDE.isoformat()}&hasta={HOY.isoformat()}"
