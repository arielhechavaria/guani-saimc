"""Tabla por provincia de la ventana con pestañas (prototipo B, 2026-09-21):
por producto activo, una fila por provincia con su mediana, el cambio contra el
período anterior, la muestra y una sparkline de las medianas por período.

Valores esperados calculados a mano, antes de escribir `build_province_grids`.
HOY = domingo 30/08/2026; ventana de 30 días → períodos de 7 días, 5 casillas
contadas hacia atrás desde HOY. La casilla j cubre (HOY − (4−j)·7 − 6 .. HOY − (4−j)·7):

- j=2: 10/08–16/08   - j=3: 17/08–23/08   - j=4: 24/08–30/08

Arroz, La Habana: 10/08 → 280, 18/08 → 300, 27/08 → 320. Una mediana por
período: 280, 300, 320. Mediana de la ventana 300. Antes (julio): 250 y 250 →
250. Cambio 300/250 − 1 = +20 % → «+20%».
Arroz, Santiago: 27/08 → 400; sin anuncios antes → sin cambio.
Pollo, La Habana: 10/08 → 700 y 27/08 → 720; la casilla del medio queda vacía.

La sparkline mide 112 × 26 con 3 de margen; el eje x va de la primera a la
última casilla con dato (3 casillas: x = 3, 56, 109) y el eje y se normaliza
dentro de la propia fila: el mínimo abajo (y = 23), el máximo arriba (y = 3).
Arroz Habana: 280 → (3, 23), 300 → (56, 13), 320 → (109, 3).
"""

import uuid
from datetime import date
from decimal import Decimal

from saimc.cells import RetailObservation
from saimc.web.price_charts import build_median_steps, build_month_vs_month
from saimc.web.province_grid import build_product_grids, price_layer

HOY = date(2026, 8, 30)
PROVINCIAS = ["La Habana", "Santiago de Cuba", "Guantánamo"]


def _obs(product: str, province: str, mes: int, dia: int, precio: str) -> RetailObservation:
    return RetailObservation(
        product_name=product,
        province_name=province,
        currency="CUP",
        unit="lb",
        unconverted=False,
        day=date(2026, mes, dia),
        unit_price=Decimal(precio),
        evidence_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        evidence_is_public=False,
    )


ACTUAL = [
    _obs("Arroz", "La Habana", 8, 10, "280"),
    _obs("Arroz", "La Habana", 8, 18, "300"),
    _obs("Arroz", "La Habana", 8, 27, "320"),
    _obs("Arroz", "Santiago de Cuba", 8, 27, "400"),
    _obs("Pollo", "La Habana", 8, 10, "700"),
    _obs("Pollo", "La Habana", 8, 27, "720"),
]
ANTERIOR = [
    _obs("Arroz", "La Habana", 7, 10, "250"),
    _obs("Arroz", "La Habana", 7, 12, "250"),
]


def _grillas(active: list[str], provincias: list[str] | None = None):
    slopes = build_month_vs_month(ACTUAL, ANTERIOR, window_days=30, hoy=HOY)
    steps = build_median_steps(ACTUAL, window_days=30, hoy=HOY)
    return build_product_grids(active, slopes, steps, provincias or PROVINCIAS)


def _fila(grilla, provincia: str):
    return next(r for r in grilla.rows if r.province_name == provincia)


class TestFilas:
    def test_una_grilla_por_producto_activo_en_el_orden_recibido(self) -> None:
        assert [g.product_name for g in _grillas(["Pollo", "Arroz"])] == ["Pollo", "Arroz"]

    def test_sin_productos_activos_no_hay_grillas(self) -> None:
        assert _grillas([]) == ()

    def test_la_fila_lleva_mediana_muestra_y_cambio(self) -> None:
        habana = _fila(_grillas(["Arroz"])[0], "La Habana")

        assert habana.median_text == "300"
        assert habana.count == 3
        assert habana.change_text == "+20%"
        assert habana.has_data

    def test_sin_periodo_anterior_no_inventa_cambio(self) -> None:
        assert _fila(_grillas(["Arroz"])[0], "Santiago de Cuba").change_text == ""

    def test_la_unidad_se_escribe_como_se_lee_en_una_frase(self) -> None:
        """«CUP por libra», no «CUP por lb»."""
        assert _grillas(["Arroz"])[0].unit_noun == "libra"

    def test_la_moneda_y_la_unidad_salen_del_grafico(self) -> None:
        grilla = _grillas(["Arroz"])[0]

        assert (grilla.currency, grilla.unit) == ("CUP", "lb")

    def test_las_filas_van_de_mayor_a_menor_mediana(self) -> None:
        """Santiago (400) arriba de La Habana (300): es el orden de la
        columna «Mediana», el que el visitante puede invertir con un clic."""
        assert [r.province_name for r in _grillas(["Arroz"])[0].rows] == [
            "Santiago de Cuba",
            "La Habana",
        ]

    def test_las_provincias_sin_dato_no_son_filas_van_aparte_y_en_orden_alfabetico(self) -> None:
        """Ausencia de dato dicha como tal, nunca un cero (§9.0): se nombra
        en una línea al pie, no ocupa filas vacías de la tabla."""
        grilla = _grillas(["Arroz"], ["Santiago de Cuba", "La Habana", "Guantánamo", "Artemisa"])[0]

        assert grilla.sin_datos == ("Artemisa", "Guantánamo")

    def test_un_producto_sin_anuncios_lista_todas_las_provincias_como_sin_dato(self) -> None:
        grilla = _grillas(["Aceite"])[0]

        assert grilla.rows == ()
        assert grilla.sin_datos == ("Guantánamo", "La Habana", "Santiago de Cuba")
        assert grilla.currency == ""

    def test_el_periodo_de_la_sparkline_sale_del_grafico_de_periodos(self) -> None:
        """Con 30 días cada trazo es una semana; sin anuncios no hay período."""
        assert _grillas(["Arroz"])[0].period_name == "semana"
        assert _grillas(["Aceite"])[0].period_name == ""

    def test_el_titular_es_el_rango_de_medianas_y_no_una_cifra_nueva(self) -> None:
        """Sin «mediana nacional»: promediar medianas provinciales sería una
        estadística que la aplicación no calcula en ningún otro lado."""
        grilla = _grillas(["Arroz"])[0]

        assert (grilla.low_text, grilla.high_text) == ("300", "400")
        assert grilla.count == 4
        assert grilla.province_count == 2


class TestSparkline:
    def test_la_linea_une_las_medianas_por_periodo(self) -> None:
        spark = _fila(_grillas(["Arroz"])[0], "La Habana").spark

        assert spark is not None
        assert spark.line == "M3,23L56,13L109,3"

    def test_el_area_baja_hasta_la_base_y_cierra(self) -> None:
        spark = _fila(_grillas(["Arroz"])[0], "La Habana").spark

        assert spark is not None
        assert spark.area == "M3,23L56,13L109,3L109,23L3,23Z"

    def test_una_barra_por_periodo_con_dato(self) -> None:
        spark = _fila(_grillas(["Arroz"])[0], "La Habana").spark

        assert spark is not None
        assert spark.cols.count("M") == 3

    def test_el_punto_final_es_el_ultimo_periodo(self) -> None:
        spark = _fila(_grillas(["Arroz"])[0], "La Habana").spark

        assert spark is not None
        assert spark.end == (109.0, 3.0)

    def test_un_periodo_sin_anuncios_corta_la_linea_no_la_une(self) -> None:
        spark = _fila(_grillas(["Pollo"])[0], "La Habana").spark

        assert spark is not None
        assert spark.line == "M3,23M109,3"

    def test_un_solo_periodo_queda_a_media_altura_y_al_final_del_eje(self) -> None:
        """Santiago tiene solo la última casilla: un punto, sin pendiente que
        inventar (la fila se normaliza contra sí misma y no hay contra qué)."""
        spark = _fila(_grillas(["Arroz"])[0], "Santiago de Cuba").spark

        assert spark is not None
        assert spark.end == (109.0, 13.0)
        assert spark.line == "M109,13"


class TestCapaPrecioDelMapa:
    """El mapa lee un dato por provincia: para cada producto, el tinte y el
    texto de la mediana (el tooltip dice la cifra que el color resume)."""

    def test_la_mas_barata_va_en_0_y_la_mas_cara_en_1(self) -> None:
        capa = price_layer(_grillas(["Arroz"]))

        assert capa["la-habana"]["arroz"] == {"t": 0.0, "m": "300 CUP/lb"}
        assert capa["santiago-de-cuba"]["arroz"] == {"t": 1.0, "m": "400 CUP/lb"}

    def test_con_una_sola_provincia_no_hay_contra_que_comparar_y_queda_a_medio_tono(self) -> None:
        capa = price_layer(_grillas(["Pollo"]))

        assert capa == {"la-habana": {"pollo": {"t": 0.5, "m": "710 CUP/lb"}}}

    def test_cada_provincia_lleva_todos_los_productos_activos_que_tengan_dato(self) -> None:
        capa = price_layer(_grillas(["Arroz", "Pollo"]))

        assert set(capa["la-habana"]) == {"arroz", "pollo"}
        assert set(capa["santiago-de-cuba"]) == {"arroz"}

    def test_un_producto_sin_datos_no_aporta_nada(self) -> None:
        assert price_layer(_grillas(["Aceite"])) == {}

    def test_una_provincia_sin_dato_no_aparece(self) -> None:
        assert "guantanamo" not in price_layer(_grillas(["Arroz"]))
