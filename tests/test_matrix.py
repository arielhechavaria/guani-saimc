"""Presentación de la matriz de celdas — agrupado y geometría de la barra.

Los valores esperados están calculados a mano acá, antes e independientemente
de correr el módulo.

La barra es **codificación proporcional, no veredicto**. `spec.md` §12.6
prohíbe fijar un umbral de dispersión, así que ninguna celda se marca como
buena o mala: la barra dibuja el rango que la celda ya tiene, y el ojo del
operador juzga. La escala es logarítmica porque la amplitud es una razón
(máximo ÷ mínimo), no una diferencia.
"""

from decimal import Decimal

from saimc.cells import Cell
from saimc.web.matrix import build_range_bar, group_cells


def _celda(
    *,
    product: str = "Arroz",
    province: str | None = "Santiago de Cuba",
    unit: str | None = "lb",
    sale_format: str | None = "retail",
    count: int = 1,
    median: str = "100",
    minimum: str = "100",
    maximum: str = "100",
) -> Cell:
    return Cell(
        product_name=product,
        province_name=province,
        currency="CUP",
        unit=unit,
        sale_format=sale_format,
        count=count,
        median=Decimal(median),
        minimum=Decimal(minimum),
        maximum=Decimal(maximum),
    )


class TestBuildRangeBar:
    def test_la_barra_mas_ancha_es_la_de_mayor_amplitud(self) -> None:
        """Amplitud 100 contra un máximo de 100 en la tabla: barra completa."""
        bar = build_range_bar(
            minimum=Decimal("1"),
            median=Decimal("10"),
            maximum=Decimal("100"),
            max_spread=Decimal("100"),
        )

        assert bar.has_range is True
        assert round(bar.width_pct, 1) == 100.0
        # ln(10)/ln(100) = 0.5 exacto: la mediana cae justo al medio en escala
        # logarítmica.
        assert round(bar.median_pct, 1) == 50.0

    def test_una_amplitud_menor_da_una_barra_mas_corta(self) -> None:
        """Amplitud 10 contra un máximo de 100: ln(10)/ln(100) = 50%."""
        bar = build_range_bar(
            minimum=Decimal("1"),
            median=Decimal("2"),
            maximum=Decimal("10"),
            max_spread=Decimal("100"),
        )

        assert round(bar.width_pct, 1) == 50.0
        # ln(2)/ln(10) = 0.30103
        assert round(bar.median_pct, 1) == 30.1

    def test_la_mediana_pegada_al_minimo_delata_el_maximo_extremo(self) -> None:
        """Caso real del 2026-08-13: Arroz kg minorista, 730–32500 con mediana
        760. La mediana queda contra el borde izquierdo, que es exactamente la
        forma de una celda que mezcla minorista con saco."""
        bar = build_range_bar(
            minimum=Decimal("730"),
            median=Decimal("760"),
            maximum=Decimal("32500"),
            max_spread=Decimal("103.125"),
        )

        # ln(760/730) / ln(32500/730) = 0.0402693 / 3.796139 = 1.06%
        assert round(bar.median_pct, 1) == 1.1

    def test_sin_rango_no_dibuja_barra(self) -> None:
        """Una sola observación (o todas iguales): mínimo == máximo."""
        bar = build_range_bar(
            minimum=Decimal("29480"),
            median=Decimal("29480"),
            maximum=Decimal("29480"),
            max_spread=Decimal("103.125"),
        )

        assert bar.has_range is False
        assert bar.width_pct == 0.0

    def test_un_minimo_en_cero_no_rompe(self) -> None:
        """`Cell.spread` ya devuelve `None` con mínimo cero; la barra tiene que
        degradar igual en vez de dividir por cero."""
        bar = build_range_bar(
            minimum=Decimal("0"),
            median=Decimal("50"),
            maximum=Decimal("100"),
            max_spread=Decimal("100"),
        )

        assert bar.has_range is False

    def test_si_ninguna_celda_tiene_rango_no_divide_por_cero(self) -> None:
        bar = build_range_bar(
            minimum=Decimal("100"),
            median=Decimal("100"),
            maximum=Decimal("100"),
            max_spread=Decimal("1"),
        )

        assert bar.has_range is False
        assert bar.width_pct == 0.0


class TestGroupCells:
    def test_agrupa_por_producto_y_provincia(self) -> None:
        celdas = [
            _celda(unit="kg", count=8, minimum="730", median="760", maximum="32500"),
            _celda(unit="lb", count=8, minimum="320", median="360", maximum="33000"),
            _celda(product="Pollo", count=3, minimum="7200", median="7300", maximum="7300"),
        ]

        grupos = group_cells(celdas)

        assert len(grupos) == 2
        assert grupos[0].product_name == "Arroz"
        assert grupos[0].province_name == "Santiago de Cuba"
        assert len(grupos[0].cells) == 2
        assert grupos[1].product_name == "Pollo"

    def test_ordena_los_grupos_por_muestra_total(self) -> None:
        """Mismo criterio que `compute_cells`: lo que tiene muestra primero."""
        celdas = [
            _celda(product="Pollo", count=3),
            _celda(product="Arroz", unit="kg", count=8),
            _celda(product="Arroz", unit="lb", count=8),
        ]

        grupos = group_cells(celdas)

        # Arroz suma 16 entre sus dos series; Pollo tiene 3.
        assert [g.product_name for g in grupos] == ["Arroz", "Pollo"]
        assert grupos[0].total == 16
        assert grupos[1].total == 3

    def test_el_mismo_producto_en_dos_provincias_son_dos_grupos(self) -> None:
        """La provincia es parte de la identidad de la serie: fundir Arroz de
        dos provincias en un grupo afirmaría una comparación que nadie pidió."""
        celdas = [
            _celda(province="Santiago de Cuba", count=5),
            _celda(province="La Habana", count=2),
        ]

        grupos = group_cells(celdas)

        assert len(grupos) == 2
        assert [g.province_name for g in grupos] == ["Santiago de Cuba", "La Habana"]

    def test_cada_celda_viene_con_su_barra_calculada(self) -> None:
        celdas = [
            _celda(unit="kg", count=8, minimum="1", median="10", maximum="100"),
            _celda(unit="lb", count=4, minimum="1", median="2", maximum="10"),
        ]

        grupos = group_cells(celdas)

        barras = [c.bar for c in grupos[0].cells]
        # La amplitud máxima de la tabla es 100, así que esa celda manda.
        assert round(barras[0].width_pct, 1) == 100.0
        assert round(barras[1].width_pct, 1) == 50.0

    def test_la_barra_se_escala_contra_toda_la_tabla_no_contra_el_grupo(self) -> None:
        """Si cada grupo se normalizara solo, la amplitud de Azúcar se vería
        igual de ancha que la de Arroz y la comparación entre productos
        mentiría."""
        celdas = [
            _celda(product="Arroz", count=8, minimum="1", median="10", maximum="100"),
            _celda(product="Azúcar blanca", count=6, minimum="1", median="2", maximum="10"),
        ]

        grupos = group_cells(celdas)

        assert round(grupos[0].cells[0].bar.width_pct, 1) == 100.0
        assert round(grupos[1].cells[0].bar.width_pct, 1) == 50.0

    def test_lista_vacia_no_rompe(self) -> None:
        assert group_cells([]) == []
