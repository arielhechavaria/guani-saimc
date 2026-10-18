"""Texto del post (`saimc.marketing.caption`).

Los textos esperados están escritos a mano, antes del código: la respuesta
correcta no sale de correr `build_caption`.
"""

import re
import uuid
from datetime import date
from decimal import Decimal

from saimc.cells import RetailObservation
from saimc.marketing.caption import CAPTION_MAX, HASHTAGS, HASHTAGS_MAX, build_caption
from saimc.marketing.cards import (
    build_como_se_movio,
    build_dato_del_dia,
    build_de_donde_sale,
)
from saimc.marketing.context import BY_SLUG
from saimc.web.card_view import ProductCard
from saimc.web.price_charts import SlopeChart, SlopeLine

PROVINCIAS = ("La Habana", "Santiago de Cuba")
ETIQUETAS = "#guani #preciosencuba #cuba #mercadocubano"
FUENTE = "Fuente: canales de WhatsApp de emprendedores cubanos. Muestra en formación."


def _card(provincia: str, *, minimo: str, mediana: str, maximo: str, n: int) -> ProductCard:
    return ProductCard(
        product_name="Arroz", province_name=provincia, currency="CUP", unit="lb", count=n,
        minimum=Decimal(minimo), median=Decimal(mediana), maximum=Decimal(maximo),
    )


def test_los_hashtags_fijos_respetan_el_tope_y_empiezan_con_numeral() -> None:
    assert 0 < len(HASHTAGS) <= HASHTAGS_MAX
    assert all(re.fullmatch(r"#\w+", h) for h in HASHTAGS)
    assert " ".join(HASHTAGS) == ETIQUETAS


def test_dato_del_dia() -> None:
    dato = build_dato_del_dia(
        [
            _card("La Habana", minimo="300", mediana="320", maximo="350", n=3),
            _card("Santiago de Cuba", minimo="400", mediana="400", maximo="400", n=1),
        ],
        product_name="Arroz", province_names=PROVINCIAS,
        week_from=date(2026, 9, 22), week_to=date(2026, 9, 28),
    )

    texto = build_caption(BY_SLUG["dato-del-dia"], {"card": dato})

    assert texto == (
        "Arroz: precio por libra, semana del 22/09 al 28/09.\n"
        "\n"
        "La Habana: 300 – 350 CUP/lb (mediana 320, 3 anuncios)\n"
        "Santiago de Cuba: 400 CUP/lb (mediana 400, 1 anuncio)\n"
        "\n"
        "Mediana más baja de la semana: La Habana.\n"
        "\n"
        f"{FUENTE}\n"
        "\n"
        f"{ETIQUETAS}"
    )


def test_dato_del_dia_con_una_provincia_sin_datos_no_inventa_nada() -> None:
    dato = build_dato_del_dia(
        [_card("La Habana", minimo="300", mediana="320", maximo="350", n=3)],
        product_name="Arroz", province_names=PROVINCIAS,
        week_from=date(2026, 9, 22), week_to=date(2026, 9, 28),
    )

    texto = build_caption(BY_SLUG["dato-del-dia"], {"card": dato})

    assert "Santiago de Cuba: sin datos esta semana" in texto
    assert "Mediana más baja" not in texto


def _linea(provincia: str, antes: str | None, ahora: str | None) -> SlopeLine:
    return SlopeLine(
        province_name=provincia, slot=0,
        before_median=Decimal(antes) if antes else None,
        now_median=Decimal(ahora) if ahora else None,
        before_count=5, now_count=5,
        y_before=None, y_now=None, label_y_before=None, label_y_now=None,
    )


def test_como_se_movio() -> None:
    grafico = SlopeChart(
        product_name="Arroz", currency="CUP", unit="lb",
        lines=(_linea("La Habana", "300", "330"), _linea("Santiago de Cuba", None, "380")),
        before_label="01 – 07 sep", now_label="08 – 14 sep",
    )
    movio = build_como_se_movio([grafico], product_name="Arroz", province_names=PROVINCIAS)
    assert movio is not None

    texto = build_caption(BY_SLUG["como-se-movio"], {"card": movio})

    assert texto == (
        "Cómo se movió el Arroz (precio por libra): 01 – 07 sep frente a 08 – 14 sep.\n"
        "\n"
        "La Habana: 300 → 330 CUP/lb (+10%)\n"
        "Santiago de Cuba: sin comparación esta semana\n"
        "\n"
        f"{FUENTE}\n"
        "\n"
        f"{ETIQUETAS}"
    )


def _obs(dia: date, precio: str) -> RetailObservation:
    return RetailObservation(
        product_name="Arroz", province_name="La Habana", currency="CUP", unit="lb",
        unconverted=False, day=dia, unit_price=Decimal(precio),
        evidence_id=uuid.uuid4(), evidence_is_public=False,
    )


def test_de_donde_sale_lista_fecha_y_precio_nada_mas() -> None:
    card = build_de_donde_sale(
        [
            _obs(date(2026, 9, 5), "300"),
            _obs(date(2026, 9, 7), "247"),
            _obs(date(2026, 9, 9), "255"),
            _obs(date(2026, 9, 10), "260"),
            _obs(date(2026, 9, 11), "309"),
        ],
        product_name="Arroz", province_name="La Habana",
    )
    assert card is not None

    texto = build_caption(BY_SLUG["de-donde-sale"], {"card": card})

    assert texto == (
        "De dónde sale el precio del Arroz en La Habana: 247 – 309 CUP/lb.\n"
        "\n"
        "5 anuncios detrás de esta cifra. Los más recientes:\n"
        "- 11/09: 309 CUP/lb\n"
        "- 10/09: 260 CUP/lb\n"
        "- 09/09: 255 CUP/lb\n"
        "- 07/09: 247 CUP/lb\n"
        "\n"
        "La imagen original de cada anuncio no se publica.\n"
        "\n"
        f"{ETIQUETAS}"
    )


def test_una_tarjeta_sin_datos_lleva_un_texto_fijo_y_los_hashtags() -> None:
    texto = build_caption(BY_SLUG["carrusel-cierre"], {})

    assert texto.endswith(ETIQUETAS)
    assert texto.startswith("Guani: ")


def test_un_texto_larguisimo_se_recorta_sin_perder_los_hashtags() -> None:
    filas = [
        _card(f"Provincia con un nombre muy largo número {i}", minimo="1", mediana="2", maximo="3", n=2)
        for i in range(200)
    ]
    nombres = tuple(f.province_name for f in filas)
    dato = build_dato_del_dia(
        filas, product_name="Arroz", province_names=nombres,
        week_from=date(2026, 9, 22), week_to=date(2026, 9, 28),
    )

    texto = build_caption(BY_SLUG["dato-del-dia"], {"card": dato})

    assert len(texto) <= CAPTION_MAX
    assert texto.endswith(ETIQUETAS)
    assert texto.count("#") <= HASHTAGS_MAX
