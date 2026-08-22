"""Tarjetas de producto: qué se publica y con qué rótulo.

Los valores esperados están calculados a mano acá, antes e independientemente
de correr la vista.

Lo que estos casos fijan es la lectura de §9.0: se puede publicar el precio,
pero como **rango** y nunca como valor puntual, siempre con el tamaño de muestra
a la vista y rotulado «muestra en formación» mientras N siga sin calibrar.
"""

from decimal import Decimal

from saimc.cells import RetailRange
from saimc.web.card_view import SAMPLE_FORMING, build_cards, format_price


def _rango(
    *,
    product: str = "Arroz",
    province: str | None = "La Habana",
    count: int = 5,
    minimum: str = "300",
    median: str = "310",
    maximum: str = "320",
    unconverted: bool = False,
) -> RetailRange:
    return RetailRange(
        product_name=product,
        province_name=province,
        currency="CUP",
        unit="lb",
        count=count,
        median=Decimal(median),
        minimum=Decimal(minimum),
        maximum=Decimal(maximum),
        unconverted=unconverted,
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
