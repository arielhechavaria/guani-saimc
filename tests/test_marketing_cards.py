"""`saimc.marketing.cards` — contexto de las tarjetas para redes.

Sin base: reciben `ProductCard`/`SlopeChart`/`RetailObservation` armadas a
mano, los mismos tipos que producen `card_view.py`/`price_charts.py`/
`cells.py` sobre datos reales.
"""

from datetime import date
from decimal import Decimal

from saimc.cells import RetailObservation
from saimc.marketing.cards import (
    build_como_se_movio,
    build_dato_del_dia,
    build_de_donde_sale,
)
from saimc.web.card_view import ProductCard
from saimc.web.price_charts import SlopeChart, SlopeLine


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


# --- «Cómo se movió» --------------------------------------------------------


def _linea(
    province: str, *, before: str | None, now: str | None, before_n: int = 5, now_n: int = 5
) -> SlopeLine:
    return SlopeLine(
        province_name=province,
        slot=0,
        before_median=Decimal(before) if before is not None else None,
        now_median=Decimal(now) if now is not None else None,
        before_count=before_n,
        now_count=now_n,
        y_before=None,
        y_now=None,
        label_y_before=None,
        label_y_now=None,
    )


def test_como_se_movio_trae_el_cambio_por_provincia() -> None:
    chart = SlopeChart(
        product_name="Arroz", currency="CUP", unit="lb",
        lines=(
            _linea("La Habana", before="300", now="330"),
            _linea("Santiago de Cuba", before="400", now="380"),
        ),
        before_label="01 – 07 sep", now_label="08 – 14 sep",
    )

    tarjeta = build_como_se_movio(
        [chart], product_name="Arroz", province_names=PROVINCES
    )

    assert tarjeta is not None
    habana, santiago = tarjeta.rows
    assert habana.change_text == "+10%"
    assert santiago.change_text == "−5%"
    assert tarjeta.before_label == "01 – 07 sep"


def test_como_se_movio_sin_producto_da_none() -> None:
    chart = SlopeChart(
        product_name="Pollo", currency="CUP", unit="lb",
        lines=(_linea("La Habana", before="300", now="330"),),
        before_label="a", now_label="b",
    )

    assert build_como_se_movio([chart], product_name="Arroz", province_names=PROVINCES) is None


def test_como_se_movio_provincia_sin_los_dos_periodos_queda_sin_linea() -> None:
    chart = SlopeChart(
        product_name="Arroz", currency="CUP", unit="lb",
        lines=(
            _linea("La Habana", before="300", now="330"),
            _linea("Santiago de Cuba", before=None, now="380"),
        ),
        before_label="a", now_label="b",
    )

    tarjeta = build_como_se_movio([chart], product_name="Arroz", province_names=PROVINCES)

    assert tarjeta is not None
    _, santiago = tarjeta.rows
    assert santiago.has_line is False
    assert santiago.change_text == ""


def test_como_se_movio_el_punto_mas_caro_queda_mas_arriba_en_el_grafico() -> None:
    chart = SlopeChart(
        product_name="Arroz", currency="CUP", unit="lb",
        lines=(
            _linea("La Habana", before="300", now="300"),
            _linea("Santiago de Cuba", before="600", now="600"),
        ),
        before_label="a", now_label="b",
    )

    tarjeta = build_como_se_movio([chart], product_name="Arroz", province_names=PROVINCES)

    assert tarjeta is not None
    habana, santiago = tarjeta.rows
    # Y crece hacia abajo en SVG: el precio más alto tiene que dar un Y menor.
    assert santiago.y_before < habana.y_before


# --- «De dónde sale este precio» -------------------------------------------


def _obs(day_iso: str, price: str, *, product="Arroz", province="La Habana") -> RetailObservation:
    import uuid
    from datetime import date as _date

    y, m, d = (int(x) for x in day_iso.split("-"))
    return RetailObservation(
        product_name=product,
        province_name=province,
        currency="CUP",
        unit="lb",
        unconverted=False,
        day=_date(y, m, d),
        unit_price=Decimal(price),
        evidence_id=uuid.uuid4(),
        evidence_is_public=False,
    )


def test_de_donde_sale_trae_el_rango_el_conteo_y_los_mas_recientes() -> None:
    observaciones = [
        _obs("2026-09-05", "300"),
        _obs("2026-09-07", "247"),
        _obs("2026-09-09", "255"),
        _obs("2026-09-10", "260"),
        _obs("2026-09-11", "309"),
    ]

    tarjeta = build_de_donde_sale(
        observaciones, product_name="Arroz", province_name="La Habana", max_rows=4
    )

    assert tarjeta is not None
    assert tarjeta.count == 5
    assert tarjeta.range_text == "247 – 309"
    assert [r.day_text for r in tarjeta.rows] == ["11/09", "10/09", "09/09", "07/09"]
    assert tarjeta.rows[0].price_text == "309"


def test_de_donde_sale_sin_observaciones_da_none() -> None:
    assert build_de_donde_sale([], product_name="Arroz", province_name="La Habana") is None


def test_de_donde_sale_no_mezcla_otra_provincia_ni_otro_producto() -> None:
    observaciones = [
        _obs("2026-09-05", "300", province="La Habana"),
        _obs("2026-09-05", "999", province="Santiago de Cuba"),
        _obs("2026-09-05", "888", product="Pollo"),
    ]

    tarjeta = build_de_donde_sale(
        observaciones, product_name="Arroz", province_name="La Habana"
    )

    assert tarjeta is not None
    assert tarjeta.count == 1
    assert tarjeta.range_text == "300"
