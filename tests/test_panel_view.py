"""`panel_view.build_product_panels`: agrega por producto para los paneles
flotantes de la home pública (slice 4 del rediseño)."""

from decimal import Decimal

from saimc.web.card_view import ProductCard
from saimc.web.panel_view import build_product_panels


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

        panels = build_product_panels(["Aceite", "Arroz"], cards, [])

        assert [p.name for p in panels] == ["Aceite", "Arroz"]

    def test_agrega_rango_conteo_y_provincias(self) -> None:
        cards = [
            _card("Arroz", "La Habana", count=10, minimum="90", maximum="130"),
            _card("Arroz", "Santiago de Cuba", count=4, minimum="110", maximum="160"),
        ]

        (panel,) = build_product_panels(["Arroz"], cards, [])

        assert panel.count == 14
        assert panel.provinces == 2
        assert panel.minimum == Decimal("90")
        assert panel.maximum == Decimal("160")
        assert not panel.is_single_value

    def test_producto_activo_sin_tarjetas_entra_vacio(self) -> None:
        (panel,) = build_product_panels(["Picadillo"], [], [])

        assert panel.name == "Picadillo"
        assert not panel.has_data
        assert panel.count == 0

    def test_una_sola_observacion_no_finge_rango(self) -> None:
        cards = [_card("Aceite", "La Habana", count=1, minimum="1200", maximum="1200")]

        (panel,) = build_product_panels(["Aceite"], cards, [])

        assert panel.is_single_value

    def test_close_query_quita_solo_ese_producto(self) -> None:
        cards = [_card("Arroz", "La Habana"), _card("Aceite", "La Habana")]

        panels = build_product_panels(["Arroz", "Aceite"], cards, [])
        por_nombre = {p.name: p for p in panels}

        # Cerrar Arroz deja a Aceite; cerrar el único deja la URL limpia.
        assert por_nombre["Arroz"].close_query == "/?productos=aceite"
        (solo,) = build_product_panels(["Arroz"], [_card("Arroz", "La Habana")], [])
        assert solo.close_query == "/"

    def test_close_query_preserva_provincia_y_ventana(self) -> None:
        cards = [_card("Arroz", "La Habana"), _card("Aceite", "La Habana")]

        panels = build_product_panels(
            ["Arroz", "Aceite"],
            cards,
            [],
            selected_province="La Habana",
            selected_days=7,
        )
        por_nombre = {p.name: p for p in panels}

        assert por_nombre["Arroz"].close_query == (
            "/?productos=aceite&provincia=la-habana&dias=7"
        )
