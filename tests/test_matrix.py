"""Presentación de la matriz de celdas — agrupado y geometría de la barra.

Los valores esperados están calculados a mano acá, antes e independientemente
de correr el módulo.

La barra es **codificación proporcional, no veredicto**. `spec.md` §12.6
prohíbe fijar un umbral de dispersión, así que ninguna celda se marca como
buena o mala: la barra dibuja el rango que la celda ya tiene, y el ojo del
operador juzga. La escala es logarítmica porque la amplitud es una razón
(máximo ÷ mínimo), no una diferencia.
"""

import uuid
from decimal import Decimal

from saimc.cells import Cell
from saimc.web.matrix import (
    UNKNOWN_PROVINCE_SLOT,
    SeriesRow,
    build_matrix,
    build_range_bar,
    group_cells,
    resolve_column,
    select_province,
)


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
    unresolved_basis: bool = False,
    variant_id: uuid.UUID | None = None,
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
        unresolved_basis=unresolved_basis,
        variant_id=variant_id,
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


class TestGroupCellsEmparejaFormatos:
    """Dentro de un grupo, minorista y mayorista de la misma unidad quedan
    adyacentes, y el mayorista trae el ratio contra su minorista (opción A del
    2026-09-02). La brecha minorista/mayorista es estructura de mercado; hasta
    ahora quedaba como dos filas sueltas entre las series con flag.
    """

    def test_pone_minorista_arriba_del_mayorista_de_la_misma_unidad(self) -> None:
        """Entra desordenado (mayorista primero por venir antes en la lista);
        sale con la unidad de más muestra arriba y minorista antes que
        mayorista dentro de ella."""
        celdas = [
            _celda(unit="lb", sale_format="wholesale", count=3, median="250"),
            _celda(unit="kg", sale_format="retail", count=2, median="600"),
            _celda(unit="lb", sale_format="retail", count=10, median="300"),
        ]

        grupo = group_cells(celdas)[0]
        orden = [(c.cell.unit, c.cell.sale_format) for c in grupo.cells]

        # `lb` suma 13 y `kg` 2, así que `lb` va primero; y dentro de `lb`,
        # minorista antes que mayorista.
        assert orden == [("lb", "retail"), ("lb", "wholesale"), ("kg", "retail")]

    def test_el_mayorista_trae_el_ratio_minorista_sobre_mayorista(self) -> None:
        """Minorista 300 / mayorista 250 = 1,2. Calculado a mano."""
        celdas = [
            _celda(unit="lb", sale_format="retail", count=10, median="300"),
            _celda(unit="lb", sale_format="wholesale", count=3, median="250"),
        ]

        grupo = group_cells(celdas)[0]
        por_formato = {c.cell.sale_format: c for c in grupo.cells}

        assert por_formato["retail"].retail_ratio is None
        assert por_formato["wholesale"].retail_ratio == Decimal("300") / Decimal("250")

    def test_no_hay_ratio_si_la_unidad_no_coincide(self) -> None:
        """Un mayorista en `kg` no se compara con un minorista en `lb`: son
        series distintas aunque el producto sea el mismo."""
        celdas = [
            _celda(unit="lb", sale_format="retail", count=10, median="300"),
            _celda(unit="kg", sale_format="wholesale", count=3, median="250"),
        ]

        grupo = group_cells(celdas)[0]
        mayorista = [c for c in grupo.cells if c.cell.sale_format == "wholesale"][0]

        assert mayorista.retail_ratio is None

    def test_no_hay_ratio_si_una_de_las_dos_celdas_tiene_flag(self) -> None:
        """Un mayorista con `unresolved_basis` agrega totales, no precios por
        unidad: su mediana no es comparable con la del minorista."""
        celdas = [
            _celda(unit="lb", sale_format="retail", count=10, median="300"),
            _celda(
                unit="lb", sale_format="wholesale", count=3, median="250",
                unresolved_basis=True,
            ),
        ]

        grupo = group_cells(celdas)[0]
        mayorista = [c for c in grupo.cells if c.cell.sale_format == "wholesale"][0]

        assert mayorista.retail_ratio is None

    def test_sin_mayorista_ninguna_celda_trae_ratio(self) -> None:
        celdas = [
            _celda(unit="lb", sale_format="retail", count=10, median="300"),
        ]

        grupo = group_cells(celdas)[0]

        assert all(c.retail_ratio is None for c in grupo.cells)

    def test_no_hay_ratio_entre_variantes_distintas(self) -> None:
        """spec: product-variants — los ejes son libremente cruzables y no se
        funden en el emparejamiento (design.md D5): la mayorista de una
        variante no se empareja con la minorista de otra."""
        vitalin_id = uuid.uuid4()
        magdalena_id = uuid.uuid4()
        celdas = [
            _celda(unit="lb", sale_format="retail", count=10, median="300", variant_id=vitalin_id),
            _celda(
                unit="lb", sale_format="wholesale", count=3, median="250",
                variant_id=magdalena_id,
            ),
        ]

        grupo = group_cells(celdas)[0]
        mayorista = [c for c in grupo.cells if c.cell.sale_format == "wholesale"][0]

        assert mayorista.retail_ratio is None


class TestGroupCellsMarcaVariantesNoDeclaradas:
    """`CellGroup.has_undeclared_variants` — el punto donde declarar una
    variante partió la serie en dos nunca queda silencioso (design.md D6)."""

    def test_grupo_mixto_marca_has_undeclared_variants(self) -> None:
        vitalin_id = uuid.uuid4()
        celdas = [
            _celda(variant_id=vitalin_id, count=5),
            _celda(variant_id=None, count=3),
        ]

        grupo = group_cells(celdas)[0]

        assert grupo.has_undeclared_variants is True

    def test_grupo_uniforme_con_variante_no_marca(self) -> None:
        vitalin_id = uuid.uuid4()
        celdas = [
            _celda(variant_id=vitalin_id, count=5, sale_format="retail"),
            _celda(variant_id=vitalin_id, count=3, sale_format="wholesale"),
        ]

        grupo = group_cells(celdas)[0]

        assert grupo.has_undeclared_variants is False

    def test_grupo_todo_sin_declarar_no_marca(self) -> None:
        celdas = [_celda(variant_id=None, count=5)]

        grupo = group_cells(celdas)[0]

        assert grupo.has_undeclared_variants is False

    def test_un_mayorista_con_mediana_cero_no_divide(self) -> None:
        celdas = [
            _celda(unit="lb", sale_format="retail", count=10, median="300"),
            _celda(
                unit="lb", sale_format="wholesale", count=3,
                median="0", minimum="0", maximum="0",
            ),
        ]

        grupo = group_cells(celdas)[0]
        mayorista = [c for c in grupo.cells if c.cell.sale_format == "wholesale"][0]

        assert mayorista.retail_ratio is None


class TestBuildMatrix:
    """El pivote a grilla: una fila por producto, una columna por provincia.

    Lo que resuelve: apilado, «Arroz · Santiago» y «Arroz · La Habana» quedaban
    separados por otros productos y la comparación entre provincias —que es el
    objeto medido de `spec.md` §5— había que hacerla de memoria.
    """

    def test_el_mismo_producto_en_dos_provincias_es_una_fila_con_dos_columnas(self) -> None:
        celdas = [
            _celda(province="Santiago de Cuba", count=5),
            _celda(province="La Habana", count=2),
        ]

        matriz = build_matrix(celdas)

        assert len(matriz.rows) == 1
        assert matriz.rows[0].product_name == "Arroz"
        assert matriz.rows[0].total == 7
        assert [c.column.name for c in matriz.rows[0].columns] == [
            "La Habana",
            "Santiago de Cuba",
        ]
        assert [c.total for c in matriz.rows[0].columns] == [2, 5]

    def test_el_cruce_propaga_has_undeclared_variants(self) -> None:
        """El pivote no puede perder el hecho de grupo que design.md D6 pide
        surgir en el encabezado — sin esto `control.html` no tiene de dónde
        leerlo."""
        vitalin_id = uuid.uuid4()
        celdas = [
            _celda(province="Santiago de Cuba", count=5, variant_id=vitalin_id),
            _celda(province="Santiago de Cuba", count=3, variant_id=None),
        ]

        matriz = build_matrix(celdas)

        assert matriz.rows[0].columns[0].has_undeclared_variants is True

    def test_el_orden_de_columnas_es_el_de_la_especificacion(self) -> None:
        """`PHASE0_PROVINCES` manda: La Habana antes que Santiago, sin importar
        cuál tenga más muestra ni en qué orden lleguen las celdas."""
        celdas = [
            _celda(province="Santiago de Cuba", count=99),
            _celda(province="La Habana", count=1),
        ]

        matriz = build_matrix(celdas)

        assert [c.name for c in matriz.columns] == ["La Habana", "Santiago de Cuba"]

    def test_la_ranura_de_color_no_depende_de_los_datos(self) -> None:
        """El color es identidad de provincia, no veredicto ni ranking: Santiago
        conserva su ranura aunque La Habana no tenga una sola fila cargada. Si
        el color se asignara por orden de aparición, el operador vería a
        Santiago cambiar de color de un día para el otro."""
        solo_santiago = build_matrix([_celda(province="Santiago de Cuba", count=5)])
        con_las_dos = build_matrix(
            [
                _celda(province="Santiago de Cuba", count=5),
                _celda(province="La Habana", count=2),
            ]
        )

        santiago_solo = next(c for c in solo_santiago.columns if c.name == "Santiago de Cuba")
        santiago_con = next(c for c in con_las_dos.columns if c.name == "Santiago de Cuba")
        assert santiago_solo.slot == santiago_con.slot == 1

    def test_una_provincia_fuera_del_piloto_va_despues_y_no_roba_ranura(self) -> None:
        celdas = [
            _celda(province="Villa Clara", count=3),
            _celda(province="La Habana", count=3),
        ]

        matriz = build_matrix(celdas)

        assert [c.name for c in matriz.columns] == ["La Habana", "Villa Clara"]
        assert [c.slot for c in matriz.columns] == [0, 2]

    def test_la_provincia_sin_nombre_va_ultima_y_con_ranura_neutra(self) -> None:
        """«Sin provincia» no es una provincia más: va al final y con el gris
        reservado, para que ningún color de identidad la haga pasar por una."""
        celdas = [
            _celda(province=None, count=4),
            _celda(province="Santiago de Cuba", count=4),
        ]

        matriz = build_matrix(celdas)

        assert [c.name for c in matriz.columns] == ["Santiago de Cuba", None]
        assert matriz.columns[-1].slot == UNKNOWN_PROVINCE_SLOT
        assert matriz.columns[-1].label == "sin provincia"

    def test_un_hueco_de_cobertura_se_dibuja_vacio_en_vez_de_desaparecer(self) -> None:
        """La ganancia central de la grilla: si Pollo no tiene nada en Santiago,
        la columna aparece vacía. Apilado, esa combinación simplemente no
        existía y el hueco había que deducirlo."""
        celdas = [
            _celda(product="Arroz", province="La Habana", count=5),
            _celda(product="Arroz", province="Santiago de Cuba", count=5),
            _celda(product="Pollo", province="La Habana", count=3),
        ]

        matriz = build_matrix(celdas)

        pollo = next(r for r in matriz.rows if r.product_name == "Pollo")
        assert [c.column.name for c in pollo.columns] == ["La Habana", "Santiago de Cuba"]
        assert pollo.columns[1].cells == []
        assert pollo.columns[1].total == 0

    def test_las_filas_van_por_muestra_total_del_producto(self) -> None:
        """Mismo criterio que `compute_cells` y `group_cells`: lo que tiene
        muestra primero. Acá la muestra del producto suma sus dos provincias."""
        celdas = [
            _celda(product="Pollo", province="La Habana", count=10),
            _celda(product="Arroz", province="La Habana", count=6),
            _celda(product="Arroz", province="Santiago de Cuba", count=6),
        ]

        matriz = build_matrix(celdas)

        assert [r.product_name for r in matriz.rows] == ["Arroz", "Pollo"]
        assert [r.total for r in matriz.rows] == [12, 10]

    def test_las_barras_siguen_normalizadas_contra_toda_la_tabla(self) -> None:
        """El pivote es presentación: no toca la escala. Si cada columna se
        normalizara sola, una amplitud de 10× en La Habana se vería igual de
        ancha que una de 100× en Santiago."""
        celdas = [
            _celda(province="La Habana", count=4, minimum="1", median="2", maximum="10"),
            _celda(
                province="Santiago de Cuba", count=4, minimum="1", median="10", maximum="100"
            ),
        ]

        matriz = build_matrix(celdas)

        habana, santiago = matriz.rows[0].columns
        assert round(habana.cells[0].bar.width_pct, 1) == 50.0
        assert round(santiago.cells[0].bar.width_pct, 1) == 100.0

    def test_lista_vacia_no_rompe(self) -> None:
        matriz = build_matrix([])

        assert matriz.columns == []
        assert matriz.rows == []


class TestResolveColumn:
    """Qué provincia mira la vista, a partir del parámetro de la URL.

    Nada de acá puede reventar la página: un parámetro raro en una URL vieja o
    tipeada a mano tiene que caer parado en la primera provincia, no en un 500.
    """

    def _matriz(self) -> object:
        return build_matrix(
            [
                _celda(province="La Habana", count=3),
                _celda(province="Santiago de Cuba", count=9),
            ]
        )

    def test_sin_parametro_toma_la_primera_provincia(self) -> None:
        """La primera es la primera del catálogo (`PHASE0_PROVINCES`), no la de
        más muestra: si dependiera de los datos, la pestaña inicial cambiaría
        de provincia sola de un día para el otro."""
        columna = resolve_column(self._matriz(), None)

        assert columna is not None
        assert columna.name == "La Habana"

    def test_un_nombre_conocido_selecciona_esa_provincia(self) -> None:
        columna = resolve_column(self._matriz(), "Santiago de Cuba")

        assert columna is not None
        assert columna.name == "Santiago de Cuba"

    def test_un_nombre_desconocido_no_rompe_y_cae_en_la_primera(self) -> None:
        columna = resolve_column(self._matriz(), "Camagüey")

        assert columna is not None
        assert columna.name == "La Habana"

    def test_la_cadena_vacia_pide_la_columna_sin_provincia(self) -> None:
        """`?province=` es cómo se nombra «sin provincia» en los enlaces al
        historial que ya existen; la pestaña usa el mismo lenguaje."""
        matriz = build_matrix(
            [
                _celda(province="La Habana", count=3),
                _celda(province=None, count=2),
            ]
        )

        columna = resolve_column(matriz, "")

        assert columna is not None
        assert columna.name is None

    def test_la_cadena_vacia_sin_columna_sin_provincia_cae_en_la_primera(self) -> None:
        columna = resolve_column(self._matriz(), "")

        assert columna is not None
        assert columna.name == "La Habana"

    def test_una_matriz_vacia_no_tiene_columna(self) -> None:
        assert resolve_column(build_matrix([]), None) is None


class TestSelectProvince:
    """La matriz recortada a la provincia de la pestaña activa."""

    def test_deja_una_sola_columna(self) -> None:
        matriz = build_matrix(
            [
                _celda(province="La Habana", count=3),
                _celda(province="Santiago de Cuba", count=9),
            ]
        )
        habana = resolve_column(matriz, "La Habana")
        assert habana is not None

        vista = select_province(matriz, habana)

        assert [c.name for c in vista.columns] == ["La Habana"]
        assert [c.column.name for c in vista.rows[0].columns] == ["La Habana"]

    def test_las_filas_se_reordenan_por_la_muestra_de_esa_provincia(self) -> None:
        """Ordenar por el total de las dos provincias pondría arriba de la
        pestaña de La Habana un producto que en La Habana casi no tiene nada."""
        matriz = build_matrix(
            [
                _celda(product="Arroz", province="La Habana", count=1),
                _celda(product="Arroz", province="Santiago de Cuba", count=40),
                _celda(product="Pollo", province="La Habana", count=6),
            ]
        )
        habana = resolve_column(matriz, "La Habana")
        assert habana is not None

        vista = select_province(matriz, habana)

        assert [f.product_name for f in vista.rows] == ["Pollo", "Arroz"]
        assert [f.total for f in vista.rows] == [6, 1]

    def test_el_hueco_de_cobertura_se_conserva_y_va_al_final(self) -> None:
        """Un producto sin nada en esta provincia no se esconde: la pestaña
        tiene que seguir diciendo que ahí falta cargar."""
        matriz = build_matrix(
            [
                _celda(product="Arroz", province="La Habana", count=5),
                _celda(product="Pollo", province="Santiago de Cuba", count=5),
            ]
        )
        habana = resolve_column(matriz, "La Habana")
        assert habana is not None

        vista = select_province(matriz, habana)

        assert [f.product_name for f in vista.rows] == ["Arroz", "Pollo"]
        assert vista.rows[1].total == 0
        assert vista.rows[1].columns[0].cells == []

    def test_el_reparto_por_provincia_se_conserva_entero(self) -> None:
        """Se dibuja una provincia, pero la cabecera de la tarjeta tiene que
        seguir diciendo cuánto hay en todas. Si el reparto se recortara con el
        panel, la pestaña escondería dónde está la muestra y habría que ir
        pestaña por pestaña para saberlo."""
        matriz = build_matrix(
            [
                _celda(product="Arroz", province="La Habana", count=1),
                _celda(product="Arroz", province="Santiago de Cuba", count=40),
            ]
        )
        habana = resolve_column(matriz, "La Habana")
        assert habana is not None

        vista = select_province(matriz, habana)

        assert [c.column.name for c in vista.rows[0].columns] == ["La Habana"]
        assert [(c.column.name, c.total) for c in vista.rows[0].tally] == [
            ("La Habana", 1),
            ("Santiago de Cuba", 40),
        ]

    def test_las_barras_siguen_normalizadas_contra_toda_la_tabla(self) -> None:
        """Recortar a una provincia es presentación y no reescala nada: si la
        pestaña renormalizara sola, la misma serie se vería de un ancho en una
        pestaña y de otro en la de al lado."""
        matriz = build_matrix(
            [
                _celda(province="La Habana", count=4, minimum="1", median="2", maximum="10"),
                _celda(
                    province="Santiago de Cuba", count=4, minimum="1", median="10", maximum="100"
                ),
            ]
        )
        habana = resolve_column(matriz, "La Habana")
        assert habana is not None

        vista = select_province(matriz, habana)

        assert round(vista.rows[0].columns[0].cells[0].bar.width_pct, 1) == 50.0


class TestFilasConCapaMayorista:
    """Una fila por (variante, formato, unidad): el minorista es la cifra
    principal y el mayorista del mismo par es una capa secundaria dentro de
    esa misma fila (2026-10-16). Las series no se funden — la identidad de la
    celda no cambia—: cambia cómo se leen. Lo que no tiene `sale_format`
    declarado es cola de trabajo y queda como fila propia, marcada pendiente.
    """

    def _filas(self, celdas: list[Cell]) -> tuple[SeriesRow, ...]:
        (grupo,) = group_cells(celdas)
        return grupo.rows

    def test_el_mayorista_del_par_va_como_capa_de_la_fila_minorista(self) -> None:
        """Minorista 300 y mayorista 250 en lb: una sola fila, la capa trae
        el ratio 300 / 250 = 1,2, calculado a mano."""
        filas = self._filas([
            _celda(unit="lb", sale_format="retail", count=10, median="300"),
            _celda(unit="lb", sale_format="wholesale", count=3, median="250"),
        ])

        assert len(filas) == 1
        assert filas[0].primary.cell.sale_format == "retail"
        assert [c.cell.sale_format for c in filas[0].wholesale] == ["wholesale"]
        assert filas[0].wholesale[0].retail_ratio == Decimal("1.2")
        assert filas[0].wholesale_count == 3
        assert filas[0].pending is False

    def test_un_mayorista_sin_minorista_del_par_es_fila_propia_sin_cifra_principal(self) -> None:
        """Minorista en lb, mayorista en kg: no hay par. El mayorista no
        sube a cifra principal —no es la referencia—: su fila no tiene
        minorista y lo lleva en la capa."""
        filas = self._filas([
            _celda(unit="lb", sale_format="retail", count=10, median="300"),
            _celda(unit="kg", sale_format="wholesale", count=3, median="550"),
        ])

        assert [(f.primary.cell.unit if f.primary else None) for f in filas] == ["lb", None]
        assert filas[0].wholesale == ()
        assert [c.cell.unit for c in filas[1].wholesale] == ["kg"]
        assert filas[1].wholesale[0].retail_ratio is None

    def test_sin_formato_declarado_queda_visible_como_pendiente(self) -> None:
        filas = self._filas([
            _celda(unit="lb", sale_format="retail", count=10, median="300"),
            _celda(unit="lb", sale_format=None, count=2, median="320"),
        ])

        assert len(filas) == 2
        pendiente = filas[1]
        assert pendiente.primary.cell.sale_format is None
        assert pendiente.pending is True
        assert pendiente.wholesale == ()

    def test_un_minorista_con_flag_no_se_queda_con_la_capa(self) -> None:
        """El minorista limpio es la referencia del par: el mayorista va con
        él aunque el minorista con flag tenga más muestra y salga antes."""
        filas = self._filas([
            _celda(unit="lb", sale_format="retail", count=20, median="900", unresolved_basis=True),
            _celda(unit="lb", sale_format="retail", count=10, median="300"),
            _celda(unit="lb", sale_format="wholesale", count=3, median="250"),
        ])

        con_capa = [f for f in filas if f.wholesale]
        assert len(filas) == 2
        assert len(con_capa) == 1
        assert con_capa[0].primary.cell.unresolved_basis is False

    def test_todas_las_series_siguen_en_la_lista_plana(self) -> None:
        """`cells` no pierde ninguna: la fila es presentación encima."""
        celdas = [
            _celda(unit="lb", sale_format="retail", count=10, median="300"),
            _celda(unit="lb", sale_format="wholesale", count=3, median="250"),
            _celda(unit="kg", sale_format="wholesale", count=1, median="500"),
            _celda(unit="lb", sale_format=None, count=2, median="320"),
        ]
        (grupo,) = group_cells(celdas)

        en_filas = sum(
            (1 if f.primary else 0) + len(f.wholesale) for f in grupo.rows
        )
        assert len(grupo.cells) == 4
        assert en_filas == 4

    def test_la_matriz_y_la_pestana_conservan_las_filas(self) -> None:
        celdas = [
            _celda(unit="lb", sale_format="retail", count=10, median="300"),
            _celda(unit="lb", sale_format="wholesale", count=3, median="250"),
        ]
        matriz = build_matrix(celdas)
        pestana = select_province(matriz, matriz.columns[0])

        assert len(matriz.rows[0].columns[0].rows) == 1
        assert len(pestana.rows[0].columns[0].rows[0].wholesale) == 1
