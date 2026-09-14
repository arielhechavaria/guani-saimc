"""Constructores de los gráficos del rediseño: escala robusta, rango por
período y cifra con su muestra.

Los valores esperados están calculados a mano acá, antes e independientemente
de correr los constructores.

Lo que protegen: un precio mal cargado (una caja tomada como libra, a ×40 de su
celda) no estira la escala ni mueve la cifra, pero tampoco desaparece: se cuenta
y se marca. Y el rango se agrupa por período hacia atrás desde hoy, con los
períodos sin anuncios a la vista.
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest

from saimc.cells import RetailObservation
from saimc.web.price_charts import (
    PAD_LEFT,
    PAD_RIGHT,
    WIDTH,
    bin_days,
    build_headlines,
    build_range_series,
    robust_fence,
    split_outliers,
)

HOY = date(2026, 8, 30)
_EVIDENCE_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


def _obs(
    dia: int,
    precio: str,
    *,
    province: str | None = "La Habana",
    unconverted: bool = False,
    format_packaging: str | None = None,
    format_quantity: str | None = None,
    format_unit: str | None = None,
) -> RetailObservation:
    return RetailObservation(
        product_name="Arroz",
        province_name=province,
        currency="CUP",
        unit="lb",
        unconverted=unconverted,
        day=date(2026, 8, dia),
        unit_price=Decimal(precio),
        evidence_id=_EVIDENCE_ID,
        evidence_is_public=False,
        format_packaging=format_packaging,
        format_quantity=Decimal(format_quantity) if format_quantity is not None else None,
        format_unit=format_unit,
    )


class TestCercoRobusto:
    def test_sin_dispersion_el_cerco_es_la_mitad_y_el_doble(self) -> None:
        # Cuatro precios iguales: la mitad central no tiene ancho, así que manda
        # el mínimo de ×2 → de 50 a 200 alrededor de 100.
        lo, hi = robust_fence([Decimal("100")] * 4 + [Decimal("1000")])

        assert lo == pytest.approx(50)
        assert hi == pytest.approx(200)

    def test_separa_el_precio_imposible(self) -> None:
        filas = [_obs(d, "300") for d in (1, 2, 3, 4)] + [_obs(5, "24800")]

        dentro, fuera = split_outliers(filas)

        assert [o.unit_price for o in fuera] == [Decimal("24800")]
        assert len(dentro) == 4

    def test_con_dos_precios_no_juzga(self) -> None:
        # Con dos puntos la mitad central abarca casi todo: no hay vecindario
        # contra el cual declarar a uno imposible.
        _, fuera = split_outliers([_obs(1, "300"), _obs(2, "30000")])

        assert fuera == []


class TestTamanoDelPeriodo:
    def test_semanas_con_30_o_90_dias_y_dias_con_7(self) -> None:
        assert bin_days(30) == 7
        assert bin_days(90) == 7
        assert bin_days(7) == 1


class TestRangoPorPeriodo:
    CASO = [_obs(30, "300"), _obs(29, "320"), _obs(23, "310"), _obs(10, "290")]

    def test_cinco_semanas_en_una_ventana_de_30_dias(self) -> None:
        # 31 días (del 30/07 al 30/08) en bloques de 7 → 5 semanas.
        (serie,) = build_range_series(self.CASO, window_days=30, hoy=HOY)

        assert len(serie.bins) == 5

    def test_agrupa_por_semana_hacia_atras_desde_hoy(self) -> None:
        (serie,) = build_range_series(self.CASO, window_days=30, hoy=HOY)

        ultima = serie.bins[-1]
        assert (ultima.start, ultima.end) == (date(2026, 8, 24), date(2026, 8, 30))
        assert ultima.count == 2
        assert ultima.minimum == Decimal("300")
        assert ultima.median == Decimal("310")
        assert ultima.maximum == Decimal("320")
        # 17–23 agosto: un anuncio; 10–16 agosto: otro.
        assert serie.bins[-2].median == Decimal("310")
        assert serie.bins[-3].median == Decimal("290")
        # Las dos semanas más viejas no tienen anuncios y quedan vacías.
        assert not serie.bins[0].has_range
        assert not serie.bins[1].has_range
        assert serie.bins[0].count == 0

    def test_lo_mas_viejo_a_la_izquierda(self) -> None:
        (serie,) = build_range_series(self.CASO, window_days=30, hoy=HOY)

        xs = [b.x for b in serie.bins]
        assert xs == sorted(xs)
        ancho = (WIDTH - PAD_LEFT - PAD_RIGHT) / 5
        assert serie.bins[0].x == pytest.approx(PAD_LEFT + ancho / 2, abs=0.1)

    def test_mas_caro_mas_arriba(self) -> None:
        (serie,) = build_range_series(self.CASO, window_days=30, hoy=HOY)

        ultima = serie.bins[-1]
        assert ultima.y_max < ultima.y_median < ultima.y_min

    def test_el_precio_imposible_no_estira_la_escala_pero_cuenta(self) -> None:
        filas = [_obs(d, "300") for d in (26, 27, 28, 29)] + [_obs(30, "24800")]

        (serie,) = build_range_series(filas, window_days=30, hoy=HOY)

        ultima = serie.bins[-1]
        assert ultima.count == 5
        assert ultima.maximum == Decimal("300")
        (marca,) = serie.outliers
        assert marca.above
        assert marca.price == Decimal("24800")
        assert marca.x == ultima.x

    def test_una_ventana_de_7_dias_va_por_dia(self) -> None:
        (serie,) = build_range_series(
            [_obs(30, "300"), _obs(28, "310")], window_days=7, hoy=HOY
        )

        # 8 días (del 23 al 30) en bloques de 1.
        assert len(serie.bins) == 8
        assert serie.bins[-1].start == serie.bins[-1].end == HOY

    def test_descarta_sin_provincia_y_sin_convertir(self) -> None:
        filas = [_obs(30, "300", province=None), _obs(29, "300", unconverted=True)]

        assert build_range_series(filas, window_days=30, hoy=HOY) == []


class TestCifraConSuMuestra:
    def test_mediana_rango_muestra_y_cambio(self) -> None:
        actual = [_obs(30, "300"), _obs(25, "320"), _obs(20, "310")]
        previo = [_obs(1, "290"), _obs(2, "300")]

        (cifra,) = build_headlines(actual, previo)

        assert cifra.median == Decimal("310")
        assert cifra.minimum == Decimal("300")
        assert cifra.maximum == Decimal("320")
        assert cifra.count == 3
        assert cifra.previous_count == 2
        assert cifra.previous_median == Decimal("295")
        # 310 / 295 = 1,0508 → +5%.
        assert cifra.change_pct == 5
        assert cifra.change_text == "+5%"
        assert cifra.last_day == date(2026, 8, 30)

    def test_sin_periodo_anterior_no_hay_cambio(self) -> None:
        (cifra,) = build_headlines([_obs(30, "300")], [])

        assert cifra.previous_median is None
        assert cifra.change_pct is None
        assert cifra.is_single_value

    def test_una_baja_se_escribe_con_signo_menos(self) -> None:
        # 285 / 300 = 0,95 → −5%.
        (cifra,) = build_headlines([_obs(30, "285")], [_obs(1, "300")])

        assert cifra.change_pct == -5
        assert cifra.change_text == "−5%"

    def test_el_precio_imposible_no_mueve_la_cifra(self) -> None:
        actual = [_obs(d, "300") for d in (26, 27, 28, 29)] + [_obs(30, "24800")]

        (cifra,) = build_headlines(actual, [])

        assert cifra.maximum == Decimal("300")
        assert cifra.count == 5
        assert cifra.outliers == 1


class TestEjeDeFormato:
    """El formato entra al eje de agrupado (2026-09-14): una botella y una
    garrafa del mismo producto ya no se funden en una sola cifra o serie —
    mismo eje que `/control` ya usaba en la matriz (`cells.py`), que era
    justo lo que le faltaba al panel flotante de la home pública."""

    def test_build_headlines_separa_por_formato(self) -> None:
        botella = [
            _obs(30, "300", format_packaging="botella", format_quantity="1", format_unit="L"),
            _obs(29, "310", format_packaging="botella", format_quantity="1", format_unit="L"),
        ]
        garrafa = [
            _obs(28, "1400", format_packaging="garrafa", format_quantity="5", format_unit="L")
        ]

        cifras = build_headlines(botella + garrafa, [])

        assert len(cifras) == 2
        por_formato = {c.format_label: c for c in cifras}
        assert por_formato["botella de 1 L"].count == 2
        assert por_formato["garrafa de 5 L"].count == 1

    def test_build_range_series_separa_por_formato(self) -> None:
        botella = [_obs(30, "300", format_packaging="botella", format_quantity="1", format_unit="L")]
        garrafa = [
            _obs(30, "1400", format_packaging="garrafa", format_quantity="5", format_unit="L")
        ]

        series = build_range_series(botella + garrafa, window_days=7, hoy=HOY)

        assert {s.format_label for s in series} == {"botella de 1 L", "garrafa de 5 L"}

    def test_format_label_display_sube_solo_la_primera_letra(self) -> None:
        """`capitalize` de Jinja pondría en minúscula TODO el resto —«Botella
        de 1 l», con la unidad rota—; esta property solo toca la primera."""
        (cifra,) = build_headlines(
            [_obs(30, "300", format_packaging="botella", format_quantity="1", format_unit="L")],
            [],
        )

        assert cifra.format_label == "botella de 1 L"
        assert cifra.format_label_display == "Botella de 1 L"

    def test_format_label_display_sin_formato_es_none(self) -> None:
        (cifra,) = build_headlines([_obs(30, "300")], [])

        assert cifra.format_label_display is None

    def test_sin_formato_sigue_agrupando_junto_como_antes(self) -> None:
        """Sin formato declarado en ninguna fila, el comportamiento no
        cambia: sigue siendo una sola cifra."""
        (cifra,) = build_headlines([_obs(30, "300"), _obs(29, "310")], [])

        assert cifra.format_label is None
        assert cifra.count == 2
