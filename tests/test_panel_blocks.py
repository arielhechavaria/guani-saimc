"""`panel_view.attach_blocks`: pega a cada panel de producto su cifra y su
rango por período, un bloque por provincia."""

import uuid
from datetime import date
from decimal import Decimal

from saimc.cells import RetailObservation
from saimc.web.card_view import ProductCard
from saimc.web.panel_view import attach_blocks, build_product_panels, group_blocks_by_province
from saimc.web.price_charts import build_cell_ads, build_headlines, build_range_series

HOY = date(2026, 8, 30)
_EVIDENCE_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


def _obs(
    product: str,
    province: str,
    dia: int,
    precio: str,
    *,
    format_packaging: str | None = None,
    format_quantity: str | None = None,
    format_unit: str | None = None,
    evidence_id: uuid.UUID = _EVIDENCE_ID,
    evidence_is_public: bool = False,
) -> RetailObservation:
    return RetailObservation(
        product_name=product,
        province_name=province,
        currency="CUP",
        unit="lb",
        unconverted=False,
        day=date(2026, 8, dia),
        unit_price=Decimal(precio),
        evidence_id=evidence_id,
        evidence_is_public=evidence_is_public,
        format_packaging=format_packaging,
        format_quantity=Decimal(format_quantity) if format_quantity is not None else None,
        format_unit=format_unit,
    )


def _obs_aceite_dos_formatos() -> list[RetailObservation]:
    """Aceite en La Habana, dos formatos: dos anuncios en botella de 1 L y
    uno en garrafa de 5 L — reusado por los tests de formato de abajo."""
    return [
        _obs(
            "Aceite", "La Habana", 30, "300",
            format_packaging="botella", format_quantity="1", format_unit="L",
        ),
        _obs(
            "Aceite", "La Habana", 29, "310",
            format_packaging="botella", format_quantity="1", format_unit="L",
        ),
        _obs(
            "Aceite", "La Habana", 28, "1400",
            format_packaging="garrafa", format_quantity="5", format_unit="L",
        ),
    ]


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

    def test_ads_by_cell_pega_los_anuncios_de_esa_celda(self) -> None:
        """Recorrido hacia atrás (§6.5): cada bloque trae solo SUS anuncios,
        sin mezclar la otra provincia."""
        id_habana = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
        id_santiago = uuid.UUID("00000000-0000-0000-0000-0000000000a2")
        obs = [
            _obs("Arroz", "La Habana", 30, "300", evidence_id=id_habana),
            _obs("Arroz", "Santiago de Cuba", 28, "320", evidence_id=id_santiago),
        ]
        cards = [_card("Arroz", "La Habana"), _card("Arroz", "Santiago de Cuba")]
        panels = build_product_panels(["Arroz"], cards, [])

        (panel,) = attach_blocks(
            panels,
            build_headlines(obs, []),
            build_range_series(obs, window_days=30, hoy=HOY),
            build_cell_ads(obs),
        )

        por_provincia = {b.headline.province_name: b for b in panel.blocks}
        assert [a.evidence_id for a in por_provincia["La Habana"].ads] == [id_habana]
        assert [a.evidence_id for a in por_provincia["Santiago de Cuba"].ads] == [id_santiago]

    def test_sin_ads_by_cell_los_bloques_quedan_sin_anuncios(self) -> None:
        obs = [_obs("Arroz", "La Habana", 30, "300")]
        panels = build_product_panels(["Arroz"], [_card("Arroz", "La Habana")], [])

        (panel,) = attach_blocks(
            panels, build_headlines(obs, []), build_range_series(obs, window_days=30, hoy=HOY)
        )

        assert panel.blocks[0].ads == ()

    def test_dos_formatos_en_la_misma_provincia_dan_dos_bloques(self) -> None:
        """2026-09-14: antes botella y garrafa se fundían en un solo bloque
        de La Habana; ahora cada formato trae el suyo, sin mezclar su cifra
        ni su rango con el del otro."""
        obs = _obs_aceite_dos_formatos()
        panels = build_product_panels(["Aceite"], [_card("Aceite", "La Habana")], [])

        (panel,) = attach_blocks(
            panels,
            build_headlines(obs, []),
            build_range_series(obs, window_days=30, hoy=HOY),
        )

        assert len(panel.blocks) == 2
        por_formato = {b.headline.format_label: b for b in panel.blocks}
        botella, garrafa = por_formato["botella de 1 L"], por_formato["garrafa de 5 L"]
        assert botella.headline.count == 2
        assert garrafa.headline.count == 1
        # El rango de cada bloque es el de SU formato, no el fundido.
        assert botella.ranges is not None and botella.ranges.format_label == "botella de 1 L"
        assert garrafa.ranges is not None and garrafa.ranges.format_label == "garrafa de 5 L"


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

    def test_varios_formatos_del_mismo_producto_no_inflan_la_insignia(self) -> None:
        """La insignia del mapa es una lectura rápida, no desglosa por
        formato (eso es del panel, 2026-09-14): con botella y garrafa de
        Aceite en La Habana, se queda con una sola entrada — la de más
        anuncios, que es la que `panel.blocks` ya trae primero."""
        obs = _obs_aceite_dos_formatos()
        panels = attach_blocks(
            build_product_panels(["Aceite"], [_card("Aceite", "La Habana")], []),
            build_headlines(obs, []),
            build_range_series(obs, window_days=30, hoy=HOY),
        )

        agrupado = group_blocks_by_province(panels)

        assert len(agrupado["la-habana"]) == 1
        _, bloque = agrupado["la-habana"][0]
        assert bloque.headline.format_label == "botella de 1 L"
