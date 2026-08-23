"""Gráfico de evolución: qué se dibuja y, sobre todo, qué NO se dibuja.

Los valores esperados están calculados a mano acá, antes e independientemente
de correr la vista.

La decisión que estos casos protegen es que **no hay línea entre los puntos**.
Con dato en 14 de 30 días —y en 3 de 30 para Aceite—, unir observaciones
separadas por días dibuja precios que nadie observó. El mismo criterio por el
que §12.6 prohíbe inventar umbrales aplica a inventar observaciones.
"""

from datetime import date
from decimal import Decimal

from saimc.cells import RetailObservation
from saimc.web.series_view import (
    HEIGHT,
    MIN_DAYS,
    PAD_TOP,
    WIDTH,
    build_series,
)

HOY = date(2026, 8, 30)
VENTANA = 30


def _obs(
    dia: int,
    precio: str,
    *,
    product: str = "Arroz",
    province: str | None = "La Habana",
    unconverted: bool = False,
) -> RetailObservation:
    return RetailObservation(
        product_name=product,
        province_name=province,
        currency="CUP",
        unit="lb",
        unconverted=unconverted,
        day=date(2026, 8, dia),
        unit_price=Decimal(precio),
    )


class TestBuildSeries:
    def test_una_celda_con_dos_dias_se_grafica(self) -> None:
        (serie,) = build_series(
            [_obs(10, "300"), _obs(20, "320")], window_days=VENTANA, hoy=HOY
        )

        assert serie.product_name == "Arroz"
        assert serie.count == 2
        assert serie.days_with_data == 2
        assert serie.minimum == Decimal("300")
        assert serie.maximum == Decimal("320")
        assert serie.first_day == date(2026, 8, 10)
        assert serie.last_day == date(2026, 8, 20)

    def test_un_solo_dia_no_es_una_evolucion(self) -> None:
        """Dos observaciones el mismo día son dispersión, no evolución. La
        tarjeta ya publica su rango; un eje temporal con un punto no dice nada."""
        assert build_series([_obs(10, "300"), _obs(10, "320")], window_days=VENTANA, hoy=HOY) == []
        assert MIN_DAYS == 2

    def test_los_huecos_se_cuentan_y_se_muestran(self) -> None:
        """El día sin anuncios es información, no una falla del gráfico."""
        (serie,) = build_series(
            [_obs(10, "300"), _obs(11, "310"), _obs(20, "320")],
            window_days=VENTANA,
            hoy=HOY,
        )

        assert serie.days_with_data == 3
        assert serie.empty_days == 27

    def test_el_eje_abarca_la_ventana_no_el_rango_de_los_datos(self) -> None:
        """Si las observaciones se apelotonan en unos días, tiene que VERSE
        apelotonado dentro del mes. Estirarlas hasta ocupar todo el ancho
        fingiría una cobertura pareja que no existe.

        Ventana del 31/07 al 30/08 = 30 días. El 10/08 es el día 10 de 30, o sea
        1/3 del ancho útil (588) más el margen izquierdo (6): 6 + 196 = 202.
        """
        (serie,) = build_series(
            [_obs(10, "300"), _obs(11, "310")], window_days=VENTANA, hoy=HOY
        )
        primero = serie.points[0]

        assert primero.day == date(2026, 8, 10)
        assert round(primero.x) == 202
        assert serie.points[-1].x < WIDTH  # muy lejos del borde derecho

    def test_el_precio_mas_alto_queda_arriba(self) -> None:
        """En SVG la `y` crece hacia abajo: el máximo tiene que dar la `y` menor."""
        (serie,) = build_series(
            [_obs(10, "300"), _obs(20, "320")], window_days=VENTANA, hoy=HOY
        )
        por_precio = {p.price: p for p in serie.points}

        assert por_precio[Decimal("320")].y < por_precio[Decimal("300")].y
        assert serie.band_top < serie.band_bottom

    def test_todas_al_mismo_precio_no_divide_por_cero(self) -> None:
        """Caso real: Azúcar/La Habana con dos observaciones casi iguales. La
        línea va al medio en vez de romper o pegarse a un borde."""
        (serie,) = build_series(
            [_obs(10, "400"), _obs(20, "400")], window_days=VENTANA, hoy=HOY
        )

        medio = PAD_TOP + (HEIGHT - PAD_TOP - 26.0) / 2
        assert all(p.y == round(medio, 1) for p in serie.points)
        assert serie.band_top == serie.band_bottom

    def test_descarta_sin_provincia_y_sin_conversion(self) -> None:
        """Mismos descartes que las tarjetas y por los mismos motivos."""
        sin_provincia = [_obs(10, "300", province=None), _obs(20, "320", province=None)]
        sin_tasa = [_obs(10, "300", unconverted=True), _obs(20, "320", unconverted=True)]

        assert build_series(sin_provincia, window_days=VENTANA, hoy=HOY) == []
        assert build_series(sin_tasa, window_days=VENTANA, hoy=HOY) == []

    def test_separa_las_celdas_y_ordena_por_muestra(self) -> None:
        series = build_series(
            [
                _obs(10, "300"), _obs(20, "320"), _obs(21, "310"),
                _obs(10, "800", product="Pollo"), _obs(20, "850", product="Pollo"),
            ],
            window_days=VENTANA,
            hoy=HOY,
        )

        assert [(s.product_name, s.count) for s in series] == [("Arroz", 3), ("Pollo", 2)]

    def test_los_puntos_salen_ordenados_por_fecha(self) -> None:
        (serie,) = build_series(
            [_obs(20, "320"), _obs(10, "300"), _obs(15, "310")],
            window_days=VENTANA,
            hoy=HOY,
        )

        assert [p.day.day for p in serie.points] == [10, 15, 20]
