"""Tarjetas de producto: qué se publica y con qué rótulo.

Los valores esperados están calculados a mano acá, antes e independientemente
de correr la vista.

Lo que estos casos fijan es la lectura de §9.0: se puede publicar el precio,
pero como **rango** y nunca como valor puntual, siempre con el tamaño de muestra
a la vista y rotulado «muestra en formación» mientras N siga sin calibrar.
"""

from datetime import date
from decimal import Decimal

from saimc.cells import RetailRange
from saimc.web.card_view import (
    SAMPLE_FORMING,
    attach_trends,
    build_cards,
    format_price,
    group_cards_by_province,
    province_color_slots,
)
from saimc.web.price_charts import Headline


def _rango(
    *,
    product: str = "Arroz",
    province: str | None = "La Habana",
    count: int = 5,
    minimum: str = "300",
    median: str = "310",
    maximum: str = "320",
    unconverted: bool = False,
    format_packaging: str | None = None,
    format_quantity: str | None = None,
    format_unit: str | None = None,
    variant_kind: str | None = None,
    variant_brand: str | None = None,
    variant_origin: str | None = None,
) -> RetailRange:
    return RetailRange(
        product_name=product,
        province_name=province,
        currency="CUP",
        unit="lb",
        format_packaging=format_packaging,
        format_quantity=Decimal(format_quantity) if format_quantity is not None else None,
        format_unit=format_unit,
        count=count,
        median=Decimal(median),
        minimum=Decimal(minimum),
        maximum=Decimal(maximum),
        unconverted=unconverted,
        variant_kind=variant_kind,
        variant_brand=variant_brand,
        variant_origin=variant_origin,
    )


class TestFormatPrice:
    def test_redondea_a_entero_por_encima_de_cien(self) -> None:
        assert format_price(Decimal("806.0606060606060606")) == "806"
        assert format_price(Decimal("312.389065219")) == "312"

    def test_usa_punto_como_separador_de_miles(self) -> None:
        assert format_price(Decimal("30885.00")) == "30.885"
        assert format_price(Decimal("1264260.00")) == "1.264.260"

    def test_conserva_decimales_por_debajo_de_cien(self) -> None:
        """Redondear 4,45 a entero lo volvería 4 y perdería el 10% del valor."""
        assert format_price(Decimal("4.45")) == "4,45"
        assert format_price(Decimal("0.5")) == "0,50"


class TestBuildCards:
    def test_traslada_el_rango_y_la_muestra(self) -> None:
        (card,) = build_cards([_rango(count=17, minimum="277.5", median="312.39", maximum="340")])

        assert card.product_name == "Arroz"
        assert card.province_name == "La Habana"
        assert card.count == 17
        assert card.minimum_text == "278"
        assert card.median_text == "312"
        assert card.maximum_text == "340"

    def test_toda_tarjeta_va_rotulada_muestra_en_formacion(self) -> None:
        """No hay estado alternativo todavía: N está sin calibrar y §9.0 dice
        que las Fases 0 y 1 SON su calibración. Un estado condicional exigiría
        el umbral que §12.6 prohíbe inventar."""
        cards = build_cards([_rango(count=2), _rango(product="Pollo", count=40)])

        assert {c.state for c in cards} == {SAMPLE_FORMING}
        assert SAMPLE_FORMING == "muestra en formación"

    def test_descarta_las_filas_sin_provincia(self) -> None:
        """La tarjeta responde «cuánto cuesta acá» y una fila sin provincia no
        contesta eso. Sigue contando en la matriz del operador."""
        assert build_cards([_rango(province=None)]) == []

    def test_descarta_las_filas_sin_conversion_resuelta(self) -> None:
        """Mostrarla junto a las de CUP afirmaría una equivalencia que nadie
        calculó — es lo que la bandera `unconverted` existe para evitar."""
        assert build_cards([_rango(unconverted=True)]) == []

    def test_ordena_por_tamaño_de_muestra(self) -> None:
        """Lo mejor respaldado primero. No es ranking de precio ni de
        importancia: es cuánta evidencia hay detrás."""
        cards = build_cards(
            [
                _rango(product="Aceite", count=3),
                _rango(product="Arroz", count=17),
                _rango(product="Pollo", count=8),
            ]
        )

        assert [c.product_name for c in cards] == ["Arroz", "Pollo", "Aceite"]

    def test_una_sola_observacion_no_es_un_rango(self) -> None:
        """Con n=1 mínimo, mediana y máximo son el mismo número. Presentarlos
        como rango sugeriría una variación que no se observó."""
        (card,) = build_cards([_rango(count=1, minimum="400", median="400", maximum="400")])

        assert card.is_single_observation

    def test_con_varias_observaciones_si_hay_rango(self) -> None:
        (card,) = build_cards([_rango(count=2)])

        assert not card.is_single_observation


class TestTarjetaConFormato:
    """La pregunta del hogar es por el formato: el titular publicado es el
    precio del formato, con el precio por unidad base al lado para comparar.
    """

    def test_la_tarjeta_lleva_la_etiqueta_y_el_precio_del_formato(self) -> None:
        (card,) = build_cards(
            [
                _rango(
                    median="302.73",
                    minimum="290",
                    maximum="315",
                    format_packaging="saco",
                    format_quantity="25",
                    format_unit="kg",
                )
            ]
        )

        assert card.format_label == "saco de 25 kg"
        # 302,73 CUP/lb x (25 kg -> 55,1156 lb) = 16.685 CUP el saco.
        assert card.format_median_text == "16.685"
        # El precio por unidad base sigue disponible como comparador.
        assert card.median_text == "303"

    def test_sin_formato_no_hay_etiqueta_ni_precio_de_formato(self) -> None:
        (card,) = build_cards([_rango()])

        assert card.format_label is None
        assert card.format_median_text is None


class TestTarjetaConVariante:
    """La variante declarada es su propia serie pública, distinguible de la
    genérica (public-price-range: "wherever product series are shown — the
    control matrix and the public range")."""

    def test_la_tarjeta_lleva_la_etiqueta_de_la_variante(self) -> None:
        (card,) = build_cards(
            [_rango(variant_kind="girasol", variant_brand="Vitalín", variant_origin="importado")]
        )

        assert card.variant_label == "girasol Vitalín importado"

    def test_sin_variante_declarada_no_hay_etiqueta(self) -> None:
        (card,) = build_cards([_rango()])

        assert card.variant_label is None

    def test_dos_variantes_del_mismo_producto_y_provincia_dan_dos_tarjetas_distintas(
        self,
    ) -> None:
        cards = build_cards(
            [
                _rango(
                    variant_kind="girasol", variant_brand="Vitalín", variant_origin="importado"
                ),
                _rango(
                    variant_kind="girasol", variant_brand="Magdalena", variant_origin="importado"
                ),
            ]
        )

        assert len(cards) == 2
        assert {c.variant_label for c in cards} == {
            "girasol Vitalín importado",
            "girasol Magdalena importado",
        }


def _cifra(
    *,
    product: str = "Arroz",
    province: str = "La Habana",
    median: str = "330",
    previous_median: str | None = "300",
) -> Headline:
    return Headline(
        product_name=product,
        province_name=province,
        currency="CUP",
        unit="lb",
        count=5,
        outliers=0,
        median=Decimal(median),
        minimum=Decimal(median),
        maximum=Decimal(median),
        previous_median=Decimal(previous_median) if previous_median is not None else None,
        previous_count=5,
        last_day=date(2026, 9, 1),
    )


class TestAttachTrends:
    """`attach_trends`: la tarjeta simple contesta «¿sube o baja?» (spec.md
    §6.7) con el mismo número sin color que ya usa el panel flotante —nunca
    un semáforo, eso sigue bloqueado por §12.6."""

    def test_pega_el_cambio_de_la_cifra_que_coincide(self) -> None:
        (card,) = attach_trends(
            build_cards([_rango(product="Arroz", province="La Habana")]),
            [_cifra(product="Arroz", province="La Habana", median="330", previous_median="300")],
        )

        assert card.change_text == "+10%"

    def test_sin_cifra_coincidente_el_cambio_queda_vacio(self) -> None:
        (card,) = attach_trends(
            build_cards([_rango(product="Arroz", province="La Habana")]),
            [_cifra(product="Aceite", province="La Habana")],
        )

        assert card.change_text == ""

    def test_no_mezcla_el_cambio_de_otra_provincia(self) -> None:
        """Arroz tiene cifra en Santiago pero la tarjeta es de La Habana: no
        hay que pegarle un cambio que no le corresponde."""
        (card,) = attach_trends(
            build_cards([_rango(product="Arroz", province="La Habana")]),
            [_cifra(product="Arroz", province="Santiago de Cuba")],
        )

        assert card.change_text == ""

    def test_sin_periodo_anterior_no_hay_cambio_que_mostrar(self) -> None:
        (card,) = attach_trends(
            build_cards([_rango(product="Arroz", province="La Habana")]),
            [_cifra(product="Arroz", province="La Habana", previous_median=None)],
        )

        assert card.change_text == ""


class TestIconoDeTarjeta:
    """Ancla visual de la tarjeta (2026-09-14): la misma ilustración/glifo
    que ya usa el panel flotante, no un ícono nuevo."""

    def test_producto_piloto_tiene_ilustracion(self) -> None:
        (card,) = build_cards([_rango(product="Aceite")])

        assert card.has_illustration

    def test_producto_sin_entrada_no_tiene_ilustracion(self) -> None:
        (card,) = build_cards([_rango(product="Café")])

        assert not card.has_illustration


class TestAgrupadoPorProvincia:
    """Para el modo Tablero (2026-09-16): mismas tarjetas, agrupadas."""

    def test_agrupa_en_el_orden_de_province_names(self) -> None:
        cards = build_cards(
            [
                _rango(product="Arroz", province="Santiago de Cuba"),
                _rango(product="Arroz", province="La Habana"),
                _rango(product="Aceite", province="La Habana"),
            ]
        )

        agrupado = group_cards_by_province(cards, ["La Habana", "Santiago de Cuba"])

        assert [nombre for nombre, _ in agrupado] == ["La Habana", "Santiago de Cuba"]
        productos_habana = {c.product_name for c in dict(agrupado)["La Habana"]}
        assert productos_habana == {"Arroz", "Aceite"}
        productos_santiago = {c.product_name for c in dict(agrupado)["Santiago de Cuba"]}
        assert productos_santiago == {"Arroz"}

    def test_una_provincia_sin_ninguna_tarjeta_no_entra(self) -> None:
        cards = build_cards([_rango(product="Arroz", province="La Habana")])

        agrupado = group_cards_by_province(cards, ["La Habana", "Santiago de Cuba"])

        assert [nombre for nombre, _ in agrupado] == ["La Habana"]

    def test_sin_tarjetas_no_hay_grupos(self) -> None:
        assert group_cards_by_province([], ["La Habana", "Santiago de Cuba"]) == []


class TestSlotsDeColorPorProvincia:
    """Identidad de color por posición en el catálogo (Dirección D, 2026-09-16)."""

    def test_las_tres_primeras_tienen_slot_propio(self) -> None:
        slots = province_color_slots(["La Habana", "Santiago de Cuba", "Matanzas"])

        assert slots == {"La Habana": 0, "Santiago de Cuba": 1, "Matanzas": 2}

    def test_de_la_cuarta_en_adelante_cae_en_el_neutral(self) -> None:
        slots = province_color_slots(
            ["La Habana", "Santiago de Cuba", "Matanzas", "Holguín", "Camagüey"]
        )

        assert slots["Holguín"] == 9
        assert slots["Camagüey"] == 9

    def test_lista_vacia_no_produce_slots(self) -> None:
        assert province_color_slots([]) == {}
