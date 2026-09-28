"""`saimc.marketing.cards` — contexto de la tarjeta «Dato del día».

Sin base: `build_dato_del_dia` recibe `ProductCard` ya armadas a mano, el
mismo tipo que produce `card_view.build_cards` sobre datos reales.
"""

from datetime import date
from decimal import Decimal

from saimc.marketing.cards import build_dato_del_dia
from saimc.web.card_view import ProductCard


def _card(province: str, *, minimum, median, maximum, count: int, product="Arroz") -> ProductCard:
    return ProductCard(
        product_name=product,
        province_name=province,
        currency="CUP",
        unit="lb",
        count=count,
        minimum=Decimal(minimum),
        median=Decimal(median),
        maximum=Decimal(maximum),
    )


PROVINCES = ("La Habana", "Santiago de Cuba")
SEMANA = (date(2026, 9, 22), date(2026, 9, 28))


def test_una_fila_por_provincia_con_su_rango_y_muestra() -> None:
    cards = [
        _card("La Habana", minimum=300, median=320, maximum=350, count=20),
        _card("Santiago de Cuba", minimum=310, median=330, maximum=360, count=15),
    ]

    tarjeta = build_dato_del_dia(
        cards, product_name="Arroz", province_names=PROVINCES,
        week_from=SEMANA[0], week_to=SEMANA[1],
    )

    habana, santiago = tarjeta.rows
    assert habana.has_data and habana.range_text == "300 – 350"
    assert habana.median_text == "320" and habana.count == 20
    assert santiago.has_data and santiago.range_text == "310 – 360"
    assert tarjeta.unit_noun == "libra"
    assert tarjeta.currency == "CUP"
    assert tarjeta.week_from == "22/09" and tarjeta.week_to == "28/09"


def test_la_provincia_de_mediana_mas_baja_queda_marcada_como_mas_barata() -> None:
    cards = [
        _card("La Habana", minimum=300, median=320, maximum=350, count=20),
        _card("Santiago de Cuba", minimum=310, median=400, maximum=420, count=15),
    ]

    tarjeta = build_dato_del_dia(
        cards, product_name="Arroz", province_names=PROVINCES,
        week_from=SEMANA[0], week_to=SEMANA[1],
    )

    habana, santiago = tarjeta.rows
    assert habana.is_cheapest is True
    assert santiago.is_cheapest is False


def test_una_sola_provincia_con_dato_no_marca_mas_barata() -> None:
    cards = [_card("La Habana", minimum=300, median=320, maximum=350, count=20)]

    tarjeta = build_dato_del_dia(
        cards, product_name="Arroz", province_names=PROVINCES,
        week_from=SEMANA[0], week_to=SEMANA[1],
    )

    habana, santiago = tarjeta.rows
    assert habana.has_data and habana.is_cheapest is False
    assert santiago.has_data is False
    assert santiago.range_text == ""


def test_un_solo_precio_observado_no_muestra_rango_con_guion() -> None:
    cards = [_card("La Habana", minimum=300, median=300, maximum=300, count=1)]

    tarjeta = build_dato_del_dia(
        cards, product_name="Arroz", province_names=PROVINCES,
        week_from=SEMANA[0], week_to=SEMANA[1],
    )

    assert tarjeta.rows[0].range_text == "300"


def test_solo_toma_la_ProductCard_de_mas_muestra_por_provincia() -> None:
    # `build_cards` ya ordena por conteo descendente: acá se simula esa
    # entrada ya ordenada, con dos formatos de Arroz en La Habana.
    cards = [
        _card("La Habana", minimum=300, median=320, maximum=350, count=20),
        _card("La Habana", minimum=900, median=950, maximum=1000, count=2),
    ]

    tarjeta = build_dato_del_dia(
        cards, product_name="Arroz", province_names=("La Habana",),
        week_from=SEMANA[0], week_to=SEMANA[1],
    )

    assert tarjeta.rows[0].count == 20
    assert tarjeta.rows[0].range_text == "300 – 350"


def test_otro_producto_en_la_lista_no_se_mezcla() -> None:
    cards = [
        _card("La Habana", minimum=300, median=320, maximum=350, count=20, product="Arroz"),
        _card("La Habana", minimum=500, median=520, maximum=550, count=30, product="Pollo"),
    ]

    tarjeta = build_dato_del_dia(
        cards, product_name="Arroz", province_names=("La Habana",),
        week_from=SEMANA[0], week_to=SEMANA[1],
    )

    assert tarjeta.rows[0].range_text == "300 – 350"


def test_sin_ninguna_provincia_con_dato_no_revienta() -> None:
    tarjeta = build_dato_del_dia(
        [], product_name="Arroz", province_names=PROVINCES,
        week_from=SEMANA[0], week_to=SEMANA[1],
    )

    assert all(not r.has_data for r in tarjeta.rows)
    assert tarjeta.unit_noun == ""


def test_la_barra_ubica_el_rango_mas_barato_mas_a_la_izquierda() -> None:
    cards = [
        _card("La Habana", minimum=300, median=320, maximum=350, count=20),
        _card("Santiago de Cuba", minimum=400, median=420, maximum=450, count=15),
    ]

    tarjeta = build_dato_del_dia(
        cards, product_name="Arroz", province_names=PROVINCES,
        week_from=SEMANA[0], week_to=SEMANA[1],
    )

    habana, santiago = tarjeta.rows
    assert habana.bar_left_pct == 0.0
    assert santiago.bar_left_pct > habana.bar_left_pct
    assert santiago.bar_left_pct + santiago.bar_width_pct == 100.0


def test_el_marcador_de_mediana_cae_dentro_del_rango_de_su_propia_barra() -> None:
    cards = [_card("La Habana", minimum=300, median=320, maximum=350, count=20)]

    tarjeta = build_dato_del_dia(
        cards, product_name="Arroz", province_names=("La Habana",),
        week_from=SEMANA[0], week_to=SEMANA[1],
    )

    fila = tarjeta.rows[0]
    # Escala 0-100 = 300-350 (única provincia con dato): la mediana 320 cae al 40%.
    assert fila.median_pct == 40.0
    assert fila.bar_left_pct <= fila.median_pct <= fila.bar_left_pct + fila.bar_width_pct
