"""`panel_view.attach_blocks`: pega a cada panel de producto su cifra y su
rango por período, un bloque por provincia."""

import uuid
from datetime import date
from decimal import Decimal

from saimc.cells import RetailObservation
from saimc.web.card_view import ProductCard
from saimc.web.panel_view import attach_blocks, build_product_panels, group_blocks_by_province
from saimc.web.price_charts import build_headlines, build_range_series

HOY = date(2026, 8, 30)


def _obs(product: str, province: str, dia: int, precio: str) -> RetailObservation:
    return RetailObservation(
        product_name=product,
        province_name=province,
        currency="CUP",
        unit="lb",
        unconverted=False,
        day=date(2026, 8, dia),
        unit_price=Decimal(precio),
        evidence_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        evidence_is_public=False,
    )


def _card(product: str, province: str) -> ProductCard:
    return ProductCard(
        product_name=product,
        province_name=province,
        currency="CUP",
        unit="lb",
        count=1,
        minimum=Decimal("300"),
        median=Decimal("300"),
        maximum=Decimal("300"),
    )


class TestAttachBlocks:
    def test_empareja_cifra_y_rango_por_provincia(self) -> None:
        obs = [
            _obs("Arroz", "La Habana", 30, "300"),
            _obs("Arroz", "La Habana", 29, "310"),
            _obs("Arroz", "Santiago de Cuba", 28, "320"),
            _obs("Aceite", "La Habana", 30, "3400"),
        ]
        panels = build_product_panels(["Arroz"], [_card("Arroz", "La Habana")], [])

        (panel,) = attach_blocks(
            panels,
            build_headlines(obs, []),
            build_range_series(obs, window_days=30, hoy=HOY),
        )

        # Solo las provincias de Arroz, la de más anuncios primero; el Aceite
        # no se cuela en el panel de Arroz.
        assert [b.headline.province_name for b in panel.blocks] == [
            "La Habana",
            "Santiago de Cuba",
        ]
        assert all(b.ranges is not None for b in panel.blocks)
        assert all(b.ranges.province_name == b.headline.province_name for b in panel.blocks)

    def test_sin_cifras_el_panel_queda_sin_bloques(self) -> None:
        (panel,) = attach_blocks(build_product_panels(["Arroz"], [], []), [], [])

        assert panel.blocks == ()


class TestGroupBlocksByProvince:
    """`group_blocks_by_province`: la insignia del mapa agrupa por provincia
    en vez de por producto, sin depender de si hay una sola elegida —hace
    falta también mirando el país entero (docs/decisions.md, 2026-09-13)."""

    def test_agrupa_por_slug_de_provincia(self) -> None:
        obs = [
            _obs("Arroz", "La Habana", 30, "300"),
            _obs("Arroz", "Santiago de Cuba", 28, "320"),
        ]
        cards = [_card("Arroz", "La Habana"), _card("Arroz", "Santiago de Cuba")]
        panels = attach_blocks(
            build_product_panels(["Arroz"], cards, []),
            build_headlines(obs, []),
            build_range_series(obs, window_days=30, hoy=HOY),
        )

        agrupado = group_blocks_by_province(panels)

        assert set(agrupado) == {"la-habana", "santiago-de-cuba"}
        assert [p.headline.province_name for _, p in agrupado["la-habana"]] == ["La Habana"]

    def test_dos_productos_en_la_misma_provincia_se_apilan(self) -> None:
        obs = [
            _obs("Arroz", "La Habana", 30, "300"),
            _obs("Aceite", "La Habana", 29, "3400"),
        ]
        panels = attach_blocks(
            build_product_panels(
                ["Arroz", "Aceite"],
                [_card("Arroz", "La Habana"), _card("Aceite", "La Habana")],
                [],
            ),
            build_headlines(obs, []),
            build_range_series(obs, window_days=30, hoy=HOY),
        )

        agrupado = group_blocks_by_province(panels)

        assert len(agrupado["la-habana"]) == 2
        assert {p.name for p, _ in agrupado["la-habana"]} == {"Arroz", "Aceite"}

    def test_producto_sin_bloques_no_agrega_nada(self) -> None:
        panels = attach_blocks(build_product_panels(["Picadillo"], [], []), [], [])

        assert group_blocks_by_province(panels) == {}
