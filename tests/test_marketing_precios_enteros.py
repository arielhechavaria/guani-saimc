"""Los precios de las tarjetas de marketing y del texto del post van en enteros.

Es solo presentación (`cards.format_price_whole`): ninguna medición cambia, y
el sitio público sigue con `card_view.format_price` (dos decimales por debajo de
100). Las respuestas esperadas están escritas a mano antes que el código.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest

from saimc.cells import RetailObservation
from saimc.marketing.caption import build_caption
from saimc.marketing.cards import (
    DatoDelDiaCard,
    build_como_se_movio,
    build_dato_del_dia,
    build_de_donde_sale,
    format_price_whole,
)
from saimc.marketing.context import BY_SLUG
from saimc.web.card_view import ProductCard, format_price
from saimc.web.price_charts import SlopeChart, SlopeLine


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [
        ("45.50", "46"),  # mitad hacia arriba, no al par
        ("46.50", "47"),
        ("99.5", "100"),
        ("12.49", "12"),
        ("2529", "2.529"),
        ("2300000", "2.300.000"),
        ("100", "100"),
        ("0.5", "1"),  # redondea a 1: no a 0
        ("0.4", "0,40"),  # guarda: un precio chico nunca se muestra «0»
        ("0.04", "0,04"),
        ("0.49", "0,49"),
        ("0", "0"),  # un cero es cero
    ],
)
def test_el_precio_se_redondea_a_entero_con_puntos_de_miles(valor: str, esperado: str) -> None:
    assert format_price_whole(Decimal(valor)) == esperado


def test_el_sitio_publico_conserva_sus_decimales() -> None:
    """`format_price` no se toca: lo usan series, gráficos y ticker."""
    assert format_price(Decimal("45.50")) == "45,50"


def _producto(provincia: str, minimo: str, mediana: str, maximo: str, n: int) -> ProductCard:
    return ProductCard(
        product_name="Arroz", province_name=provincia, currency="CUP", unit="lb", count=n,
        minimum=Decimal(minimo), median=Decimal(mediana), maximum=Decimal(maximo),
    )


def _dato(*cards: ProductCard) -> DatoDelDiaCard:
    return build_dato_del_dia(
        cards, product_name="Arroz", province_names=[c.province_name for c in cards],
        week_from=date(2026, 10, 12), week_to=date(2026, 10, 18),
    )


def test_dato_del_dia_muestra_rango_y_mediana_en_enteros() -> None:
    fila = _dato(_producto("La Habana", "45.50", "99.5", "2529.4", 3)).rows[0]

    assert fila.range_text == "46 – 2.529"
    assert fila.median_text == "100"


def test_dos_precios_distintos_que_redondean_igual_son_una_sola_cifra() -> None:
    fila = _dato(_producto("La Habana", "45.2", "45.3", "45.4", 3)).rows[0]

    assert fila.range_text == "45"  # no «45 – 45»


def test_con_una_sola_observacion_sigue_siendo_una_cifra() -> None:
    fila = _dato(_producto("La Habana", "45.50", "45.50", "45.50", 1)).rows[0]

    assert fila.range_text == "46" and fila.median_text == "46"


def test_dato_del_dia_con_un_precio_chico_conserva_los_decimales() -> None:
    fila = _dato(_producto("La Habana", "0.4", "0.4", "0.4", 2)).rows[0]

    assert fila.range_text == "0,40"


def _obs(dia: int, precio: str) -> RetailObservation:
    return RetailObservation(
        product_name="Arroz", province_name="La Habana", day=date(2026, 10, dia),
        unit_price=Decimal(precio), unit="lb", currency="CUP", unconverted=False,
        evidence_id=uuid.uuid4(), evidence_is_public=False,
    )


def test_de_donde_sale_muestra_rango_y_cada_anuncio_en_enteros() -> None:
    card = build_de_donde_sale(
        [_obs(15, "45.50"), _obs(16, "99.5"), _obs(17, "2529.4")],
        product_name="Arroz", province_name="La Habana",
    )

    assert card is not None
    assert card.range_text == "46 – 2.529"
    assert [r.price_text for r in card.rows] == ["2.529", "100", "46"]


def test_de_donde_sale_con_precios_que_redondean_igual_es_una_cifra() -> None:
    card = build_de_donde_sale(
        [_obs(15, "45.2"), _obs(16, "45.4")], product_name="Arroz", province_name="La Habana"
    )

    assert card is not None and card.range_text == "45"


def test_como_se_movio_redondea_los_precios_y_no_toca_el_porcentaje() -> None:
    linea = SlopeLine(
        province_name="La Habana", slot=0, before_median=Decimal("45.50"),
        now_median=Decimal("2529.4"), before_count=3, now_count=4,
        y_before=1.0, y_now=2.0, label_y_before=1.0, label_y_now=2.0,
    )
    chart = SlopeChart(
        product_name="Arroz", currency="CUP", unit="lb", lines=(linea,),
        before_label="12/10", now_label="18/10",
    )

    card = build_como_se_movio([chart], product_name="Arroz", province_names=["La Habana"])

    assert card is not None
    fila = card.rows[0]
    assert (fila.before_text, fila.now_text) == ("46", "2.529")
    assert fila.change_text == linea.change_text  # el porcentaje sale de price_charts, intacto
    assert fila.change_text.endswith("%")


def test_el_texto_del_post_usa_los_mismos_enteros() -> None:
    contexto = {"card": _dato(_producto("La Habana", "45.50", "99.5", "2529.4", 3))}

    texto = build_caption(BY_SLUG["dato-del-dia"], contexto)

    assert "46 – 2.529 CUP/lb (mediana 100, 3 anuncios)" in texto
    assert "45,50" not in texto
