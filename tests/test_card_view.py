"""Tarjetas de producto: qué se publica y con qué rótulo.

Los valores esperados están calculados a mano acá, antes e independientemente
de correr la vista.

Lo que estos casos fijan es la lectura de §9.0: se puede publicar el precio,
pero como **rango** y nunca como valor puntual, siempre con el tamaño de muestra
a la vista y rotulado «muestra en formación» mientras N siga sin calibrar.
"""

import uuid
from datetime import date
from decimal import Decimal

from saimc.cells import RetailObservation, RetailRange
from saimc.web.card_view import (
    SAMPLE_FORMING,
    attach_trends,
    build_cards,
    build_price_strips,
    format_price,
    group_cards_by_province,
    province_color_slots,
)
from saimc.web.price_charts import Headline, build_month_vs_month


def _rango(
    *,
    product: str = "Arroz",
    province: str | None = "La Habana",
    count: int = 5,
    minimum: str = "300",
    median: str = "310",
    maximum: str = "320",
    unconverted: bool = False,
    format_packaging: str | None = None,
    format_quantity: str | None = None,
    format_unit: str | None = None,
    variant_kind: str | None = None,
    variant_brand: str | None = None,
    variant_origin: str | None = None,
) -> RetailRange:
    return RetailRange(
        product_name=product,
        province_name=province,
        currency="CUP",
        unit="lb",
        format_packaging=format_packaging,
        format_quantity=Decimal(format_quantity) if format_quantity is not None else None,
        format_unit=format_unit,
        count=count,
        median=Decimal(median),
        minimum=Decimal(minimum),
        maximum=Decimal(maximum),
        unconverted=unconverted,
        variant_kind=variant_kind,
        variant_brand=variant_brand,
        variant_origin=variant_origin,
    )


class TestFormatPrice:
    def test_redondea_a_entero_por_encima_de_cien(self) -> None:
        assert format_price(Decimal("806.0606060606060606")) == "806"
        assert format_price(Decimal("312.389065219")) == "312"

    def test_usa_punto_como_separador_de_miles(self) -> None:
        assert format_price(Decimal("30885.00")) == "30.885"
        assert format_price(Decimal("1264260.00")) == "1.264.260"

    def test_conserva_decimales_por_debajo_de_cien(self) -> None:
        """Redondear 4,45 a entero lo volvería 4 y perdería el 10% del valor."""
        assert format_price(Decimal("4.45")) == "4,45"
        assert format_price(Decimal("0.5")) == "0,50"


class TestBuildCards:
    def test_traslada_el_rango_y_la_muestra(self) -> None:
        (card,) = build_cards([_rango(count=17, minimum="277.5", median="312.39", maximum="340")])

        assert card.product_name == "Arroz"
        assert card.province_name == "La Habana"
        assert card.count == 17
        assert card.minimum_text == "278"
        assert card.median_text == "312"
        assert card.maximum_text == "340"

    def test_toda_tarjeta_va_rotulada_muestra_en_formacion(self) -> None:
        """No hay estado alternativo todavía: N está sin calibrar y §9.0 dice
        que las Fases 0 y 1 SON su calibración. Un estado condicional exigiría
        el umbral que §12.6 prohíbe inventar."""
        cards = build_cards([_rango(count=2), _rango(product="Pollo", count=40)])

        assert {c.state for c in cards} == {SAMPLE_FORMING}
        assert SAMPLE_FORMING == "muestra en formación"

    def test_descarta_las_filas_sin_provincia(self) -> None:
        """La tarjeta responde «cuánto cuesta acá» y una fila sin provincia no
        contesta eso. Sigue contando en la matriz del operador."""
        assert build_cards([_rango(province=None)]) == []

    def test_descarta_las_filas_sin_conversion_resuelta(self) -> None:
        """Mostrarla junto a las de CUP afirmaría una equivalencia que nadie
        calculó — es lo que la bandera `unconverted` existe para evitar."""
        assert build_cards([_rango(unconverted=True)]) == []

    def test_ordena_por_tamaño_de_muestra(self) -> None:
        """Lo mejor respaldado primero. No es ranking de precio ni de
        importancia: es cuánta evidencia hay detrás."""
        cards = build_cards(
            [
                _rango(product="Aceite", count=3),
                _rango(product="Arroz", count=17),
                _rango(product="Pollo", count=8),
            ]
        )

        assert [c.product_name for c in cards] == ["Arroz", "Pollo", "Aceite"]

    def test_una_sola_observacion_no_es_un_rango(self) -> None:
        """Con n=1 mínimo, mediana y máximo son el mismo número. Presentarlos
        como rango sugeriría una variación que no se observó."""
        (card,) = build_cards([_rango(count=1, minimum="400", median="400", maximum="400")])

        assert card.is_single_observation

    def test_con_varias_observaciones_si_hay_rango(self) -> None:
        (card,) = build_cards([_rango(count=2)])

        assert not card.is_single_observation


class TestTarjetaConFormato:
    """La pregunta del hogar es por el formato: el titular publicado es el
    precio del formato, con el precio por unidad base al lado para comparar.
    """

    def test_la_tarjeta_lleva_la_etiqueta_y_el_precio_del_formato(self) -> None:
        (card,) = build_cards(
            [
                _rango(
                    median="302.73",
                    minimum="290",
                    maximum="315",
                    format_packaging="saco",
                    format_quantity="25",
                    format_unit="kg",
                )
            ]
        )

        assert card.format_label == "saco de 25 kg"
        # 302,73 CUP/lb x (25 kg -> 55,1156 lb) = 16.685 CUP el saco.
        assert card.format_median_text == "16.685"
        # El precio por unidad base sigue disponible como comparador.
        assert card.median_text == "303"

    def test_sin_formato_no_hay_etiqueta_ni_precio_de_formato(self) -> None:
        (card,) = build_cards([_rango()])

        assert card.format_label is None
        assert card.format_median_text is None


class TestTarjetaConVariante:
    """La variante declarada es su propia serie pública, distinguible de la
    genérica (public-price-range: "wherever product series are shown — the
    control matrix and the public range")."""

    def test_la_tarjeta_lleva_la_etiqueta_de_la_variante(self) -> None:
        (card,) = build_cards(
            [_rango(variant_kind="girasol", variant_brand="Vitalín", variant_origin="importado")]
        )

        assert card.variant_label == "girasol Vitalín importado"

    def test_sin_variante_declarada_no_hay_etiqueta(self) -> None:
        (card,) = build_cards([_rango()])

        assert card.variant_label is None

    def test_dos_variantes_del_mismo_producto_y_provincia_dan_dos_tarjetas_distintas(
        self,
    ) -> None:
        cards = build_cards(
            [
                _rango(
                    variant_kind="girasol", variant_brand="Vitalín", variant_origin="importado"
                ),
                _rango(
                    variant_kind="girasol", variant_brand="Magdalena", variant_origin="importado"
                ),
            ]
        )

        assert len(cards) == 2
        assert {c.variant_label for c in cards} == {
            "girasol Vitalín importado",
            "girasol Magdalena importado",
        }


def _cifra(
    *,
    product: str = "Arroz",
    province: str = "La Habana",
    median: str = "330",
    previous_median: str | None = "300",
) -> Headline:
    return Headline(
        product_name=product,
        province_name=province,
        currency="CUP",
        unit="lb",
        count=5,
        outliers=0,
        median=Decimal(median),
        minimum=Decimal(median),
        maximum=Decimal(median),
        previous_median=Decimal(previous_median) if previous_median is not None else None,
        previous_count=5,
        last_day=date(2026, 9, 1),
    )


class TestAttachTrends:
    """`attach_trends`: la tarjeta simple contesta «¿sube o baja?» (spec.md
    §6.7) con el mismo número sin color que la lista de productos y el
    «¿subió o bajó?» de «Cómo se movió» —los tres salen de
    `build_month_vs_month`—, nunca un semáforo, eso sigue bloqueado por §12.6.

    Hasta el 2026-09-18 salía de un titular por formato y un diccionario que
    se quedaba con el último: arroz en La Habana mostraba −12% en las once
    tarjetas, y ese −12% era el de «saco de 50 kg», con un solo anuncio.

    Valores esperados, a mano. HOY = 30/08/2026, ventana de 30 días:
    - Arroz, La Habana: ahora 300 y 330 → mediana 315; antes 300 y 300 → 300.
      315/300 − 1 = +5%.
    - Pollo, La Habana: ahora 700; antes 750 → 700/750 − 1 = −6,7% → −7%.
    - Arroz, Santiago: ahora 400, sin período anterior → sin cambio."""

    HOY = date(2026, 8, 30)

    def _obs(
        self, product: str, province: str, mes: int, dia: int, precio: str, unit: str = "lb"
    ) -> RetailObservation:
        return RetailObservation(
            product_name=product,
            province_name=province,
            currency="CUP",
            unit=unit,
            unconverted=False,
            day=date(2026, mes, dia),
            unit_price=Decimal(precio),
            evidence_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            evidence_is_public=False,
        )

    def _slopes(self, unit: str = "lb"):
        ahora = [
            self._obs("Arroz", "La Habana", 8, 20, "300", unit),
            self._obs("Arroz", "La Habana", 8, 25, "330", unit),
            self._obs("Arroz", "Santiago de Cuba", 8, 22, "400", unit),
            self._obs("Pollo", "La Habana", 8, 18, "700", unit),
        ]
        antes = [
            self._obs("Arroz", "La Habana", 7, 10, "300", unit),
            self._obs("Arroz", "La Habana", 7, 12, "300", unit),
            self._obs("Pollo", "La Habana", 7, 10, "750", unit),
        ]
        return build_month_vs_month(ahora, antes, window_days=30, hoy=self.HOY)

    def test_pega_el_cambio_de_la_provincia_y_el_producto_de_la_tarjeta(self) -> None:
        (card,) = attach_trends(
            build_cards([_rango(product="Arroz", province="La Habana")]), self._slopes()
        )

        assert card.change_text == "+5%"

    def test_cambio_negativo_con_signo_menos_tipografico(self) -> None:
        (card,) = attach_trends(
            build_cards([_rango(product="Pollo", province="La Habana")]), self._slopes()
        )

        assert card.change_text == "−7%"

    def test_todos_los_formatos_de_la_celda_comparten_el_mismo_cambio(self) -> None:
        """El caso que se rompía: varios formatos de arroz en La Habana. Cada
        tarjeta de formato lleva el cambio del producto en esa provincia, no
        el de uno de los formatos —peor si ese formato tiene un solo anuncio—."""
        tarjetas = attach_trends(
            build_cards(
                [
                    _rango(product="Arroz", format_packaging="saco", format_quantity="100",
                           format_unit="lb"),
                    _rango(product="Arroz", format_packaging="saco", format_quantity="50",
                           format_unit="kg", count=1),
                    _rango(product="Arroz", format_packaging="paquete", format_quantity="1",
                           format_unit="kg"),
                ]
            ),
            self._slopes(),
        )

        assert len(tarjetas) == 3
        assert {c.change_text for c in tarjetas} == {"+5%"}

    def test_sin_grafico_del_producto_el_cambio_queda_vacio(self) -> None:
        (card,) = attach_trends(
            build_cards([_rango(product="Aceite", province="La Habana")]), self._slopes()
        )

        assert card.change_text == ""

    def test_no_mezcla_el_cambio_de_otra_provincia(self) -> None:
        """Arroz tiene cambio en La Habana pero la tarjeta es de Camagüey: no
        hay que pegarle un cambio que no le corresponde."""
        (card,) = attach_trends(
            build_cards([_rango(product="Arroz", province="Camagüey")]), self._slopes()
        )

        assert card.change_text == ""

    def test_sin_periodo_anterior_no_hay_cambio_que_mostrar(self) -> None:
        (card,) = attach_trends(
            build_cards([_rango(product="Arroz", province="Santiago de Cuba")]), self._slopes()
        )

        assert card.change_text == ""

    def test_no_mezcla_el_cambio_de_otra_unidad(self) -> None:
        """La tarjeta es por libra; el cambio que existe es por litro."""
        (card,) = attach_trends(
            build_cards([_rango(product="Arroz", province="La Habana")]),
            self._slopes(unit="l"),
        )

        assert card.change_text == ""


class TestIconoDeTarjeta:
    """Ancla visual de la tarjeta (2026-09-14): la misma ilustración/glifo
    que ya usa el panel flotante, no un ícono nuevo."""

    def test_producto_piloto_tiene_ilustracion(self) -> None:
        (card,) = build_cards([_rango(product="Aceite")])

        assert card.has_illustration

    def test_producto_sin_entrada_no_tiene_ilustracion(self) -> None:
        (card,) = build_cards([_rango(product="Café")])

        assert not card.has_illustration


class TestAgrupadoPorProvincia:
    """Para el modo Tablero (2026-09-16): mismas tarjetas, agrupadas."""

    def test_agrupa_en_el_orden_de_province_names(self) -> None:
        cards = build_cards(
            [
                _rango(product="Arroz", province="Santiago de Cuba"),
                _rango(product="Arroz", province="La Habana"),
                _rango(product="Aceite", province="La Habana"),
            ]
        )

        agrupado = group_cards_by_province(cards, ["La Habana", "Santiago de Cuba"])

        assert [nombre for nombre, _ in agrupado] == ["La Habana", "Santiago de Cuba"]
        productos_habana = {c.product_name for c in dict(agrupado)["La Habana"]}
        assert productos_habana == {"Arroz", "Aceite"}
        productos_santiago = {c.product_name for c in dict(agrupado)["Santiago de Cuba"]}
        assert productos_santiago == {"Arroz"}

    def test_una_provincia_sin_ninguna_tarjeta_no_entra(self) -> None:
        cards = build_cards([_rango(product="Arroz", province="La Habana")])

        agrupado = group_cards_by_province(cards, ["La Habana", "Santiago de Cuba"])

        assert [nombre for nombre, _ in agrupado] == ["La Habana"]

    def test_sin_tarjetas_no_hay_grupos(self) -> None:
        assert group_cards_by_province([], ["La Habana", "Santiago de Cuba"]) == []


class TestSlotsDeColorPorProvincia:
    """Identidad de color por posición en el catálogo (Dirección D, 2026-09-16)."""

    def test_las_tres_primeras_tienen_slot_propio(self) -> None:
        slots = province_color_slots(["La Habana", "Santiago de Cuba", "Matanzas"])

        assert slots == {"La Habana": 0, "Santiago de Cuba": 1, "Matanzas": 2}

    def test_de_la_cuarta_en_adelante_cae_en_el_neutral(self) -> None:
        slots = province_color_slots(
            ["La Habana", "Santiago de Cuba", "Matanzas", "Holguín", "Camagüey"]
        )

        assert slots["Holguín"] == 9
        assert slots["Camagüey"] == 9

    def test_lista_vacia_no_produce_slots(self) -> None:
        assert province_color_slots([]) == {}


class TestTiraDePrecio:
    """La tira de calor: dónde está más barato, provincia por provincia.

    Compara con el **precio por unidad**, que es el rol que le asigna la
    decisión del 2026-09-04 («con el precio por unidad base al lado como
    comparador»).

    Desde el 2026-09-18 (slice 4) sus cifras salen de `build_month_vs_month`,
    la misma fuente que la lista de productos, el detalle y las tarjetas: una
    mediana por producto × provincia con **todos los formatos juntos**, que
    ya usan las comparaciones entre provincias desde el 2026-09-14. Antes la
    tira elegía «el formato mejor respaldado» de cada provincia porque «no
    existe un agregado producto×provincia»; existe, y esa elección hacía que
    la lista dijera 463 y la tira 530 para el mismo picadillo.

    Valores esperados calculados a mano, antes de correr el constructor.
    HOY = 30/08/2026; ventana de 30 días; solo período actual.
    """

    HOY = date(2026, 8, 30)

    def _slopes(self, **por_provincia: list[str]):
        """`Arroz` por provincia: {"La Habana": ["300", "310"], ...}."""
        return self._slopes_de({("Arroz", p): precios for p, precios in por_provincia.items()})

    def _slopes_de(self, celdas: dict[tuple[str, str], list[str]], *, antes=None):
        ahora = [
            RetailObservation(
                product_name=producto,
                province_name=provincia.replace("_", " "),
                currency="CUP",
                unit="lb",
                unconverted=False,
                day=date(2026, 8, 10 + i),
                unit_price=Decimal(precio),
                evidence_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
                evidence_is_public=False,
            )
            for (producto, provincia), precios in celdas.items()
            for i, precio in enumerate(precios)
        ]
        return build_month_vs_month(ahora, antes or [], window_days=30, hoy=self.HOY)

    def test_una_celda_por_provincia_ordenadas_de_mas_barata_a_mas_cara(self) -> None:
        slopes = self._slopes_de(
            {
                ("Arroz", "La Habana"): ["300"],
                ("Arroz", "Santiago de Cuba"): ["282"],
                ("Arroz", "Holguín"): ["315"],
            }
        )

        (tira,) = build_price_strips(slopes)

        assert [c.province_name for c in tira.cells] == [
            "Santiago de Cuba",
            "La Habana",
            "Holguín",
        ]

    def test_junta_todos_los_anuncios_de_la_provincia_sin_elegir_un_formato(self) -> None:
        """El caso que se contradecía con la lista. La Habana: 300, 300, 400,
        420, 440 → ordenados, la mediana es el del medio, 400, con 5 anuncios.
        Elegir «el formato con más anuncios» (420, 440 y 400 → 420) daba otro
        número y otra muestra para la misma provincia."""
        slopes = self._slopes_de({("Arroz", "La Habana"): ["300", "300", "400", "420", "440"]})

        (tira,) = build_price_strips(slopes)

        (celda,) = tira.cells
        assert celda.unit_price == Decimal("400")
        assert celda.count == 5

    def test_la_cifra_es_la_misma_que_la_de_la_lista_de_productos(self) -> None:
        """Una sola fuente: lo que dice la tira coincide con la celda de la
        lista de productos (`watchlist_view`) para el mismo producto y
        provincia, mediana y muestra."""
        from saimc.web.watchlist_view import build_watchlist

        slopes = self._slopes_de({("Arroz", "La Habana"): ["300", "300", "400", "420", "440"]})

        (tira,) = build_price_strips(slopes)
        lista = build_watchlist(
            ["Arroz"], [], slopes, provinces=["La Habana"], selected_province="",
            desde=date(2026, 7, 31), hasta=self.HOY,
        )

        celda_tira = tira.cells[0]
        celda_lista = lista.rows[0].cells[0]
        assert celda_lista is not None
        assert celda_tira.price_text == celda_lista.median_text
        assert celda_tira.count == celda_lista.count

    def test_con_tres_o_mas_provincias_la_escala_va_de_0_a_100(self) -> None:
        slopes = self._slopes_de(
            {
                ("Arroz", "Santiago de Cuba"): ["200"],
                ("Arroz", "La Habana"): ["250"],
                ("Arroz", "Holguín"): ["300"],
            }
        )

        (tira,) = build_price_strips(slopes)

        # 200 es el piso, 300 el techo, 250 queda justo en el medio.
        assert [c.pct for c in tira.cells] == [0, 50, 100]
        assert tira.has_gradient

    def test_con_menos_de_tres_provincias_no_hay_degradado(self) -> None:
        """Dos puntos no son una escala: pintarlos de punta a punta haría ver
        un 6% de diferencia como si fuera el rango entero del país."""
        slopes = self._slopes_de(
            {("Arroz", "La Habana"): ["300"], ("Arroz", "Santiago de Cuba"): ["282"]}
        )

        (tira,) = build_price_strips(slopes)

        assert not tira.has_gradient
        assert [c.pct for c in tira.cells] == [None, None]

    def test_todas_al_mismo_precio_no_produce_degradado(self) -> None:
        """Sin dispersión no hay nada que graduar, y dividir por cero menos."""
        slopes = self._slopes_de(
            {
                ("Arroz", "La Habana"): ["300"],
                ("Arroz", "Santiago de Cuba"): ["300"],
                ("Arroz", "Holguín"): ["300"],
            }
        )

        (tira,) = build_price_strips(slopes)

        assert not tira.has_gradient

    def test_cada_producto_tiene_su_propia_tira(self) -> None:
        slopes = self._slopes_de(
            {
                ("Arroz", "La Habana"): ["300"] * 9,
                ("Pollo", "La Habana"): ["700"] * 4,
            }
        )

        tiras = build_price_strips(slopes)

        assert [t.product_name for t in tiras] == ["Arroz", "Pollo"]

    def test_las_tiras_van_por_muestra_total(self) -> None:
        slopes = self._slopes_de(
            {
                ("Pollo", "La Habana"): ["700"] * 3,
                ("Pollo", "Santiago de Cuba"): ["690"] * 9,
                ("Arroz", "La Habana"): ["300"] * 8,
            }
        )

        tiras = build_price_strips(slopes)

        assert [t.product_name for t in tiras] == ["Pollo", "Arroz"]
        assert [t.count for t in tiras] == [12, 8]

    def test_la_celda_conserva_la_muestra_que_la_sostiene(self) -> None:
        """§9.0: el número de observaciones va a la vista, siempre."""
        slopes = self._slopes(**{"La_Habana": ["300"] * 9})

        (tira,) = build_price_strips(slopes)

        assert tira.cells[0].count == 9

    def test_una_provincia_sin_anuncios_en_la_ventana_no_tiene_celda(self) -> None:
        """Solo tiene dato del período anterior: no hay precio de ahora que
        poner en la tira, y no se inventa uno."""
        antes = [
            RetailObservation(
                product_name="Arroz",
                province_name="Holguín",
                currency="CUP",
                unit="lb",
                unconverted=False,
                day=date(2026, 7, 10),
                unit_price=Decimal("310"),
                evidence_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
                evidence_is_public=False,
            )
        ]
        slopes = self._slopes_de({("Arroz", "La Habana"): ["300"]}, antes=antes)

        (tira,) = build_price_strips(slopes)

        assert [c.province_name for c in tira.cells] == ["La Habana"]

    def test_el_filtro_de_producto_deja_solo_esas_tiras(self) -> None:
        """Igual que los chips de hoy: ninguno prendido muestra todos."""
        slopes = self._slopes_de(
            {("Arroz", "La Habana"): ["300"], ("Pollo", "La Habana"): ["700"]}
        )

        assert [t.product_name for t in build_price_strips(slopes, products=["Pollo"])] == ["Pollo"]
        assert len(build_price_strips(slopes, products=[])) == 2

    def test_el_filtro_de_provincia_deja_solo_esa_celda(self) -> None:
        slopes = self._slopes_de(
            {("Arroz", "La Habana"): ["300"], ("Arroz", "Santiago de Cuba"): ["282"]}
        )

        (tira,) = build_price_strips(slopes, province="Santiago de Cuba")

        assert [c.province_name for c in tira.cells] == ["Santiago de Cuba"]

    def test_una_tira_que_se_queda_sin_celdas_no_se_devuelve(self) -> None:
        slopes = self._slopes_de({("Arroz", "La Habana"): ["300"]})

        assert build_price_strips(slopes, province="Holguín") == []

    def test_sin_datos_no_hay_tiras(self) -> None:
        assert build_price_strips([]) == []
