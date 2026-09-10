"""Gráficos de la ventana «Cómo se movió»: mes contra mes, las provincias en el
tiempo y el calendario de anuncios.

Valores esperados calculados a mano, antes de correr los constructores.
HOY = domingo 30/08/2026; la ventana de 30 días va del viernes 31/07 al 30/08.
"""

import uuid
from datetime import date
from decimal import Decimal

from saimc.cells import RetailObservation
from saimc.web.price_charts import (
    build_calendar,
    build_evolution_blocks,
    build_median_steps,
    build_month_vs_month,
)

HOY = date(2026, 8, 30)


def _obs(province: str, dia: int, precio: str, *, mes: int = 8) -> RetailObservation:
    return RetailObservation(
        product_name="Arroz",
        province_name=province,
        currency="CUP",
        unit="lb",
        unconverted=False,
        day=date(2026, mes, dia),
        unit_price=Decimal(precio),
        evidence_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        evidence_is_public=False,
    )


class TestMesContraMes:
    ACTUAL = [
        _obs("La Habana", 30, "300"),
        _obs("La Habana", 25, "320"),
        _obs("Santiago de Cuba", 29, "400"),
    ]
    ANTERIOR = [_obs("La Habana", 5, "290", mes=7), _obs("La Habana", 6, "300", mes=7)]

    def test_dos_medianas_por_provincia_y_su_cambio(self) -> None:
        (chart,) = build_month_vs_month(self.ACTUAL, self.ANTERIOR, window_days=30, hoy=HOY)
        habana = next(l for l in chart.lines if l.province_name == "La Habana")

        assert habana.before_median == Decimal("295")
        assert habana.now_median == Decimal("310")
        assert habana.before_count == 2
        assert habana.now_count == 2
        # 310 / 295 = 1,0508 → +5%.
        assert habana.change_pct == 5
        assert habana.has_line

    def test_sin_periodo_anterior_hay_punto_pero_no_linea(self) -> None:
        (chart,) = build_month_vs_month(self.ACTUAL, self.ANTERIOR, window_days=30, hoy=HOY)
        santiago = next(l for l in chart.lines if l.province_name == "Santiago de Cuba")

        assert santiago.before_median is None
        assert santiago.now_median == Decimal("400")
        assert not santiago.has_line
        assert santiago.change_pct is None

    def test_mas_caro_mas_arriba_y_los_periodos_rotulados(self) -> None:
        (chart,) = build_month_vs_month(self.ACTUAL, self.ANTERIOR, window_days=30, hoy=HOY)
        habana, santiago = (
            next(l for l in chart.lines if l.province_name == p)
            for p in ("La Habana", "Santiago de Cuba")
        )

        assert santiago.y_now < habana.y_now
        # Antes: 60 a 31 días atrás; ahora: los últimos 30 (más hoy).
        assert chart.before_label == "01/07 – 30/07"
        assert chart.now_label == "31/07 – 30/08"

    def test_las_etiquetas_no_se_enciman(self) -> None:
        casi_iguales = [_obs("La Habana", 30, "300"), _obs("Santiago de Cuba", 30, "301")]

        (chart,) = build_month_vs_month(casi_iguales, [], window_days=30, hoy=HOY)
        ys = sorted(l.label_y_now for l in chart.lines)

        assert ys[1] - ys[0] >= 14


class TestProvinciasEnElTiempo:
    OBS = [
        _obs("La Habana", 30, "300"),
        _obs("La Habana", 23, "310"),
        _obs("La Habana", 5, "290"),
        _obs("Santiago de Cuba", 30, "400"),
    ]

    def test_la_linea_se_corta_en_la_semana_sin_anuncios(self) -> None:
        (chart,) = build_median_steps(self.OBS, window_days=30, hoy=HOY)
        habana = next(l for l in chart.lines if l.province_name == "La Habana")

        # Semanas con dato: 3–9 ago, 17–23 ago y 24–30 ago; la del 10–16 vacía
        # → dos tramos.
        assert len(habana.points) == 3
        assert habana.path.count("M") == 2
        xs = [p.x for p in habana.points]
        assert xs == sorted(xs)

    def test_cada_provincia_mantiene_su_color(self) -> None:
        (chart,) = build_median_steps(self.OBS, window_days=30, hoy=HOY)

        slots = {l.province_name: l.slot for l in chart.lines}
        assert slots == {"La Habana": 0, "Santiago de Cuba": 1}

    def test_el_color_sigue_a_la_provincia_aunque_falte_la_otra(self) -> None:
        solo_santiago = [_obs("Santiago de Cuba", 30, "400"), *self.OBS[:1]]

        (chart,) = build_median_steps(solo_santiago, window_days=30, hoy=HOY)

        assert {l.province_name: l.slot for l in chart.lines}["Santiago de Cuba"] == 1


class TestCalendario:
    OBS = [
        _obs("La Habana", 30, "300"),
        _obs("Santiago de Cuba", 30, "400"),
        _obs("La Habana", 28, "310"),
        *[_obs("La Habana", 1, "290")] * 5,
    ]

    def test_un_cuadro_por_dia_de_la_ventana(self) -> None:
        (cal,) = build_calendar(self.OBS, window_days=30, hoy=HOY)

        # Del 31/07 al 30/08 inclusive.
        assert len(cal.days) == 31
        assert cal.days[0].day == date(2026, 7, 31)
        assert cal.days[-1].day == HOY

    def test_semanas_en_columnas_y_dias_en_filas(self) -> None:
        (cal,) = build_calendar(self.OBS, window_days=30, hoy=HOY)
        por_dia = {d.day: d for d in cal.days}

        # El viernes 31/07 va en la primera columna, fila 4 (lunes = 0); el
        # domingo 30/08, en la quinta columna, fila 6.
        assert por_dia[date(2026, 7, 31)].column == 0
        assert por_dia[date(2026, 7, 31)].row == 4
        assert por_dia[HOY].column == 4
        assert por_dia[HOY].row == 6

    def test_el_tono_dice_cuantos_anuncios(self) -> None:
        (cal,) = build_calendar(self.OBS, window_days=30, hoy=HOY)
        por_dia = {d.day: d for d in cal.days}

        assert por_dia[date(2026, 8, 29)].level == 0
        assert por_dia[date(2026, 8, 28)].level == 1  # 1 anuncio
        assert por_dia[HOY].level == 2  # 2 anuncios (dos provincias)
        assert por_dia[date(2026, 8, 1)].level == 3  # 5 anuncios
        assert por_dia[HOY].count == 2


class TestBloquesDeEvolucion:
    """Un bloque por producto con dato en la ventana: lo que la ventana «Cómo
    se movió» dibuja de él."""

    def test_junta_los_tres_graficos_del_producto(self) -> None:
        (bloque,) = build_evolution_blocks(
            TestProvinciasEnElTiempo.OBS, TestMesContraMes.ANTERIOR, window_days=30, hoy=HOY
        )

        assert bloque.product_name == "Arroz"
        assert len(bloque.slopes) == 1
        assert len(bloque.steps) == 1
        assert bloque.calendar.product_name == "Arroz"

    def test_la_leyenda_nombra_cada_provincia_con_su_color(self) -> None:
        (bloque,) = build_evolution_blocks(
            TestProvinciasEnElTiempo.OBS, [], window_days=30, hoy=HOY
        )

        assert bloque.legend == ((0, "La Habana"), (1, "Santiago de Cuba"))

    def test_un_solo_periodo_con_anuncios_no_dibuja_escalones(self) -> None:
        """Dos provincias en la misma semana no son una evolución: hacen falta
        dos períodos distintos con dato."""
        misma_semana = [_obs("La Habana", 30, "300"), _obs("Santiago de Cuba", 29, "400")]

        (bloque,) = build_evolution_blocks(misma_semana, [], window_days=30, hoy=HOY)

        assert bloque.steps == ()
        # El calendario sí está: dice qué días hubo anuncios.
        assert sum(d.count for d in bloque.calendar.days) == 2

    def test_producto_sin_anuncios_en_la_ventana_no_tiene_bloque(self) -> None:
        assert build_evolution_blocks([], TestMesContraMes.ANTERIOR, window_days=30, hoy=HOY) == []


class TestAnunciosDelBloque:
    """La tabla de anuncios del bloque: cada precio lleva a su anuncio (§6.5).
    Es lo que antes daban los puntos enlazados del gráfico viejo."""

    def test_un_renglon_por_anuncio_el_mas_reciente_primero(self) -> None:
        (bloque,) = build_evolution_blocks(
            TestProvinciasEnElTiempo.OBS, [], window_days=30, hoy=HOY
        )

        assert [(a.day.day, a.province_name, a.price) for a in bloque.ads] == [
            (30, "La Habana", Decimal("300")),
            (30, "Santiago de Cuba", Decimal("400")),
            (23, "La Habana", Decimal("310")),
            (5, "La Habana", Decimal("290")),
        ]

    def test_el_fuera_de_escala_tambien_esta_en_la_tabla(self) -> None:
        """El gráfico lo marca en el borde; la tabla lo lista con su precio real
        y su enlace, que es por donde se llega a corregirlo."""
        con_error = [*TestProvinciasEnElTiempo.OBS, _obs("La Habana", 29, "30000")]

        (bloque,) = build_evolution_blocks(con_error, [], window_days=30, hoy=HOY)

        assert Decimal("30000") in {a.price for a in bloque.ads}

    def test_fuera_de_la_ventana_no_entra(self) -> None:
        viejo = [*TestProvinciasEnElTiempo.OBS, _obs("La Habana", 20, "250", mes=7)]

        (bloque,) = build_evolution_blocks(viejo, [], window_days=30, hoy=HOY)

        assert len(bloque.ads) == 4
