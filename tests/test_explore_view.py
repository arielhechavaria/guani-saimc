"""Datos de la vista «Explorar» (P1, 2026-09-21): por producto, un árbol
provincia → formato → anuncios con mediana, cambio, rango, muestra, último día,
tendencia y calor semanal.

Valores esperados calculados a mano, antes de escribir `build_explore_products`.
HOY = domingo 30/08/2026; ventana de 30 días → actual 31/07–30/08, anterior
01/07–30/07. La tendencia mira SIEMPRE 13 semanas hacia atrás desde HOY
(91 días), aunque la ventana sea más corta: la casilla j (0–12) cubre
(HOY − (12−j)·7 − 6 .. HOY − (12−j)·7), así que la 12 es 24/08–30/08, la 11
17/08–23/08, la 10 10/08–16/08, la 9 03/08–09/08 y la 5 06/07–12/07.

Arroz (CUP por libra):
- La Habana, sin formato: 27/08 → 300, 25/08 → 320, 18/08 → 290, 05/08 → 280.
  Mediana (290+300)/2 = 295, rango 280–320, 4 anuncios. Antes: 10/07 y 12/07 →
  250 y 250 → mediana 250. Cambio 295/250 − 1 = +18 %.
- La Habana, «saco de 100 lb»: 26/08 → 240, 12/08 → 250. Mediana 245, rango
  240–250, 2 anuncios, sin período anterior → sin cambio.
- La Habana, fundidos los dos formatos: 6 anuncios 240 250 280 290 300 320 →
  mediana (280+290)/2 = 285, rango 240–320. Antes 250 → +14 %. Último: 27/08,
  «hace 3 d».
- Santiago de Cuba, sin formato: 28/08 → 400. Mediana 400, un anuncio, sin cambio.

Semanas de La Habana fundida (26/08 y 27/08 y 25/08 caen en la 12; 18/08 en la
11; 12/08 en la 10; 05/08 en la 9; 10/07 y 12/07 en la 5):
5 → 250, 9 → 280, 10 → 250, 11 → 290, 12 → mediana(300, 320, 240) = 300.
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import ClassVar

from saimc.cells import RetailObservation
from saimc.web.explore_view import (
    build_explore_detail,
    build_explore_map,
    build_explore_nav,
    build_explore_products,
    sparkline_from_values,
)
from saimc.web.price_charts import build_headlines, build_month_vs_month

HOY = date(2026, 8, 30)
PROVINCIAS = ["La Habana", "Santiago de Cuba", "Guantánamo"]
SACO = ("saco", "100", "lb")


def _obs(
    product: str,
    province: str,
    mes: int,
    dia: int,
    precio: str,
    formato: tuple[str, str, str] | None = None,
    unit: str = "lb",
) -> RetailObservation:
    empaque, cantidad, unidad = formato or (None, None, None)
    return RetailObservation(
        product_name=product,
        province_name=province,
        currency="CUP",
        unit=unit,
        unconverted=False,
        day=date(2026, mes, dia),
        unit_price=Decimal(precio),
        evidence_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        evidence_is_public=True,
        format_packaging=empaque,
        format_quantity=None if cantidad is None else Decimal(cantidad),
        format_unit=unidad,
    )


OBS = [
    _obs("Arroz", "La Habana", 8, 27, "300"),
    _obs("Arroz", "La Habana", 8, 25, "320"),
    _obs("Arroz", "La Habana", 8, 18, "290"),
    _obs("Arroz", "La Habana", 8, 5, "280"),
    _obs("Arroz", "La Habana", 7, 10, "250"),
    _obs("Arroz", "La Habana", 7, 12, "250"),
    _obs("Arroz", "La Habana", 8, 26, "240", SACO),
    _obs("Arroz", "La Habana", 8, 12, "250", SACO),
    _obs("Arroz", "Santiago de Cuba", 8, 28, "400"),
    _obs("Pollo", "La Habana", 8, 30, "700"),
]


def _productos(active: list[str], observaciones=OBS, provincias=None):
    return build_explore_products(
        active, observaciones, window_days=30, hoy=HOY, provinces=provincias or PROVINCIAS
    )


def _arroz():
    return _productos(["Arroz"])[0]


def _nodo(producto, provincia: str):
    return next(n for n in producto.nodes if n.name == provincia)


def _enteros(valores):
    return [None if v is None else int(v) for v in valores]


class TestProductos:
    def test_un_producto_por_cada_activo_en_el_orden_recibido(self) -> None:
        assert [p.name for p in _productos(["Pollo", "Arroz"])] == ["Pollo", "Arroz"]

    def test_sin_productos_activos_no_hay_nada(self) -> None:
        assert _productos([]) == ()

    def test_la_moneda_y_la_unidad_salen_de_los_anuncios(self) -> None:
        arroz = _arroz()

        assert (arroz.currency, arroz.unit, arroz.unit_noun) == ("CUP", "lb", "libra")

    def test_con_dos_unidades_manda_la_de_mas_anuncios(self) -> None:
        """Comparar medianas de unidades distintas no tiene sentido: se queda
        la unidad con más anuncios en la ventana, igual que la lista del tablero."""
        obs = [
            _obs("Aceite", "La Habana", 8, 20, "1400", unit="l"),
            _obs("Aceite", "La Habana", 8, 21, "1410", unit="l"),
            _obs("Aceite", "La Habana", 8, 22, "1420", unit="l"),
            _obs("Aceite", "La Habana", 8, 23, "700", unit="lb"),
        ]
        aceite = _productos(["Aceite"], obs)[0]

        assert aceite.unit == "l"
        assert [n.name for n in aceite.nodes] == ["La Habana"]
        assert aceite.count == 3

    def test_un_producto_sin_anuncios_lista_todas_las_provincias_como_sin_dato(self) -> None:
        aceite = _productos(["Aceite"])[0]

        assert not aceite.has_data
        assert aceite.nodes == ()
        assert aceite.sin_datos == ("Guantánamo", "La Habana", "Santiago de Cuba")

    def test_las_provincias_sin_anuncios_van_aparte_y_en_orden_alfabetico(self) -> None:
        assert _arroz().sin_datos == ("Guantánamo",)

    def test_el_titular_es_el_rango_de_medianas_y_no_una_cifra_nueva(self) -> None:
        arroz = _arroz()

        assert (arroz.low_text, arroz.high_text) == ("285", "400")
        assert arroz.count == 7
        assert arroz.province_count == 2


class TestNodoProvincia:
    def test_las_provincias_van_de_mayor_a_menor_mediana(self) -> None:
        assert [n.name for n in _arroz().nodes] == ["Santiago de Cuba", "La Habana"]

    def test_la_fila_funde_los_formatos_para_comparar_provincias(self) -> None:
        habana = _nodo(_arroz(), "La Habana")

        assert habana.median_text == "285"
        assert habana.count == 6
        assert habana.change_text == "+14%"
        assert (habana.minimum_text, habana.maximum_text) == ("240", "320")

    def test_sin_periodo_anterior_no_inventa_cambio(self) -> None:
        assert _nodo(_arroz(), "Santiago de Cuba").change_text == ""

    def test_el_ultimo_dia_se_dice_en_dias_atras(self) -> None:
        assert _nodo(_arroz(), "La Habana").last_text == "hace 3 d"
        assert _nodo(_arroz(), "Santiago de Cuba").last_text == "hace 2 d"

    def test_un_anuncio_de_hoy_dice_hoy(self) -> None:
        pollo = _productos(["Pollo"])[0]

        assert _nodo(pollo, "La Habana").last_text == "hoy"

    def test_un_precio_fuera_de_escala_cuenta_pero_no_mueve_la_mediana(self) -> None:
        """Una caja tomada como libra queda a ×40: se cuenta (la muestra es la
        que es) y se marca, pero no arrastra la mediana ni el rango. Cinco
        precios normales (700–740) y uno de 30000: mediana 720, rango 700–740."""
        obs = [
            _obs("Pollo", "La Habana", 8, 20 + i, p)
            for i, p in enumerate(("700", "710", "720", "730", "740", "30000"))
        ]
        habana = _nodo(_productos(["Pollo"], obs)[0], "La Habana")

        assert habana.count == 6
        assert habana.outliers == 1
        assert habana.median_text == "720"
        assert habana.maximum_text == "740"

    def test_el_tinte_del_mapa_va_de_0_la_mas_barata_a_1_la_mas_cara(self) -> None:
        arroz = _arroz()

        assert _nodo(arroz, "La Habana").tint == 0.0
        assert _nodo(arroz, "Santiago de Cuba").tint == 1.0

    def test_con_una_sola_provincia_el_tinte_queda_a_medio_tono(self) -> None:
        assert _nodo(_productos(["Pollo"])[0], "La Habana").tint == 0.5


class TestNodoFormato:
    def test_los_formatos_van_del_de_mas_anuncios_al_de_menos(self) -> None:
        habana = _nodo(_arroz(), "La Habana")

        assert [c.name for c in habana.children] == ["Libra", "Saco de 100 lb"]

    def test_cada_formato_trae_su_propia_cifra(self) -> None:
        libra, saco = _nodo(_arroz(), "La Habana").children

        assert (libra.median_text, libra.count, libra.change_text) == ("295", 4, "+18%")
        assert (saco.median_text, saco.count, saco.change_text) == ("245", 2, "")

    def test_una_provincia_sin_formato_declarado_trae_un_solo_hijo_con_el_nombre_de_la_unidad(
        self,
    ) -> None:
        (hijo,) = _nodo(_arroz(), "Santiago de Cuba").children

        assert hijo.name == "Libra"
        assert hijo.kind == "format"

    def test_los_anuncios_van_del_mas_reciente_al_mas_viejo_y_con_su_enlace(self) -> None:
        libra, _ = _nodo(_arroz(), "La Habana").children

        assert [a.day_text for a in libra.ads] == ["27/08", "25/08", "18/08", "05/08"]
        assert [a.price_text for a in libra.ads] == ["300", "320", "290", "280"]
        assert all(a.evidence_is_public for a in libra.ads)

    def test_los_anuncios_son_los_de_la_ventana_y_no_los_del_periodo_anterior(self) -> None:
        libra, _ = _nodo(_arroz(), "La Habana").children

        assert len(libra.ads) == libra.count

    def test_la_fila_de_provincia_no_repite_los_anuncios(self) -> None:
        assert _nodo(_arroz(), "La Habana").ads == ()


class TestSemanas:
    def test_la_tendencia_son_trece_semanas_hacia_atras_desde_hoy(self) -> None:
        habana = _nodo(_arroz(), "La Habana")

        assert len(habana.weekly) == 13
        assert _enteros(habana.weekly) == [
            None,
            None,
            None,
            None,
            None,
            250,
            None,
            None,
            None,
            280,
            250,
            290,
            300,
        ]

    def test_el_periodo_anterior_a_la_ventana_entra_en_la_tendencia_pero_no_en_la_cifra(
        self,
    ) -> None:
        """Los anuncios de julio dibujan la semana 5 de la tendencia y no
        cuentan en los 6 de la ventana."""
        habana = _nodo(_arroz(), "La Habana")

        assert habana.weekly[5] == Decimal("250")
        assert habana.count == 6

    def test_cada_formato_tiene_su_propia_serie(self) -> None:
        libra, saco = _nodo(_arroz(), "La Habana").children

        assert _enteros(libra.weekly)[5:] == [250, None, None, None, 280, None, 290, 310]
        assert _enteros(saco.weekly)[10:] == [250, None, 240]

    def test_el_calor_muestra_las_ultimas_ocho_semanas_con_su_cifra(self) -> None:
        habana = _nodo(_arroz(), "La Habana")

        assert [w.text for w in habana.weeks] == ["250", "", "", "", "280", "250", "290", "300"]

    def test_una_semana_sin_anuncios_no_tiene_tinte(self) -> None:
        habana = _nodo(_arroz(), "La Habana")

        assert habana.weeks[1].tint is None

    def test_el_tinte_se_mide_contra_todo_el_producto(self) -> None:
        """Dominio de las ocho semanas de Arroz: 240 (saco, semana 12) a 400
        (Santiago, semana 12). 300 → (300−240)/160 = 0,375 y 400 → 1."""
        arroz = _arroz()

        assert _nodo(arroz, "La Habana").weeks[7].tint == 0.375
        assert _nodo(arroz, "Santiago de Cuba").weeks[7].tint == 1.0

    def test_las_etiquetas_de_semana_son_el_inicio_de_cada_una(self) -> None:
        assert _arroz().week_labels == (
            "06/07",
            "13/07",
            "20/07",
            "27/07",
            "03/08",
            "10/08",
            "17/08",
            "24/08",
        )


class TestRangoEnBarra:
    def test_la_barra_va_del_minimo_al_maximo_con_la_raya_en_la_mediana(self) -> None:
        """Dominio de Arroz: 240–400 (160). La Habana 240–320, mediana 285:
        izquierda 0 %, ancho 80/160 = 50 %, raya (285−240)/160 = 28,1 %."""
        barra = _nodo(_arroz(), "La Habana").range_bar

        assert (barra.left, barra.width, barra.tick) == (0.0, 50.0, 28.1)

    def test_un_solo_precio_es_una_raya_sin_ancho(self) -> None:
        barra = _nodo(_arroz(), "Santiago de Cuba").range_bar

        assert (barra.left, barra.width, barra.tick) == (100.0, 0.0, 100.0)


class TestSparkline:
    """Lienzo 92 × 24 con 3 de margen: x = 3 + 86·i/12, y entre 3 (máximo) y 21
    (mínimo), normalizado contra la propia serie."""

    def test_la_linea_une_semanas_seguidas_y_se_corta_donde_no_hubo_anuncios(self) -> None:
        spark = _nodo(_arroz(), "La Habana").spark

        assert spark is not None
        assert spark.line == "M38.8,21M67.5,10.2L74.7,21L81.8,6.6L89,3"

    def test_el_punto_final_es_la_ultima_semana_con_dato(self) -> None:
        spark = _nodo(_arroz(), "La Habana").spark

        assert spark is not None
        assert spark.end == (89.0, 3.0)

    def test_un_solo_dato_queda_a_media_altura_en_su_semana(self) -> None:
        spark = _nodo(_arroz(), "Santiago de Cuba").spark

        assert spark is not None
        assert spark.line == "M89,12"
        assert spark.end == (89.0, 12.0)

    def test_sin_ningun_dato_no_hay_sparkline(self) -> None:
        assert sparkline_from_values([None] * 13, width=92, height=24) is None

    def test_las_barras_son_una_por_semana_con_dato(self) -> None:
        spark = sparkline_from_values([None, 5, None, 7, 9] + [None] * 8, width=92, height=24)

        assert spark is not None
        assert spark.cols.count("M") == 3


class TestCoherenciaConElResto:
    """Esta vista no puede contradecir a las que ya existen: la fila de
    provincia sale de la misma cuenta que la lista del tablero y «Cómo se
    movió» (`build_month_vs_month`), y la de cada formato de la que ya publica
    el panel del mapa (`build_headlines`). Son las mismas observaciones
    repartidas como las reparte la ruta: ventana 31/07–30/08 y anterior
    01/07–30/07."""

    ACTUAL: ClassVar[list[RetailObservation]] = [o for o in OBS if o.day >= date(2026, 7, 31)]
    ANTERIOR: ClassVar[list[RetailObservation]] = [
        o for o in OBS if date(2026, 7, 1) <= o.day <= date(2026, 7, 30)
    ]

    def test_la_fila_de_provincia_coincide_con_la_lista_del_tablero(self) -> None:
        (grafico,) = [
            g
            for g in build_month_vs_month(self.ACTUAL, self.ANTERIOR, window_days=30, hoy=HOY)
            if g.product_name == "Arroz"
        ]
        arroz = _arroz()

        assert {linea.province_name for linea in grafico.lines} == {n.name for n in arroz.nodes}
        for linea in grafico.lines:
            nodo = _nodo(arroz, linea.province_name)
            assert nodo.median_text == linea.now_text
            assert nodo.count == linea.now_count
            assert nodo.change_text == linea.change_text

    def test_la_fila_de_formato_coincide_con_la_cifra_del_panel_del_mapa(self) -> None:
        cifras = [
            h
            for h in build_headlines(self.ACTUAL, self.ANTERIOR)
            if h.product_name == "Arroz" and h.province_name == "La Habana"
        ]
        hijos = _nodo(_arroz(), "La Habana").children

        assert len(cifras) == len(hijos) == 2
        for cifra in cifras:
            hijo = next(h for h in hijos if h.median_text == cifra.median_text)
            assert (hijo.count, hijo.outliers, hijo.change_text) == (
                cifra.count,
                cifra.outliers,
                cifra.change_text,
            )
            assert (hijo.minimum_text, hijo.maximum_text) == (cifra.minimum_text, cifra.maximum_text)
            assert hijo.last_day == cifra.last_day


class TestDetalle:
    """El panel derecho cuenta la provincia elegida: sus cifras, la mediana
    semana a semana y los anuncios detrás, de todos sus formatos."""

    def _detalle(self, provincia: str = "La Habana"):
        arroz = _arroz()
        return build_explore_detail(arroz, _nodo(arroz, provincia))

    def test_el_encabezado_dice_producto_y_cuantos_formatos(self) -> None:
        assert self._detalle().name == "La Habana"
        assert self._detalle().sub == "Arroz · 2 formatos"
        assert self._detalle("Santiago de Cuba").sub == "Arroz · 1 formato"

    def test_las_cifras_son_las_de_la_fila(self) -> None:
        d = self._detalle()

        assert (d.median_text, d.change_text, d.count) == ("285", "+14%", 6)

    def test_el_grafico_marca_el_maximo_y_el_minimo_de_las_semanas_con_dato(self) -> None:
        d = self._detalle()

        assert d.chart is not None
        assert (d.ymax_text, d.ymin_text) == ("300", "250")

    def test_los_anuncios_juntan_los_de_todos_los_formatos_del_mas_reciente_al_mas_viejo(self) -> None:
        d = self._detalle()

        assert [a.day_text for a in d.ads] == ["27/08", "26/08", "25/08", "18/08", "12/08", "05/08"]
        assert [a.format_name for a in d.ads] == [
            "Libra",
            "Saco de 100 lb",
            "Libra",
            "Libra",
            "Saco de 100 lb",
            "Libra",
        ]
        assert [a.price_text for a in d.ads] == ["300", "240", "320", "290", "250", "280"]

    def test_el_detalle_muestra_como_mucho_seis_anuncios(self) -> None:
        obs = [_obs("Pollo", "La Habana", 8, 10 + i, str(700 + i)) for i in range(9)]
        pollo = _productos(["Pollo"], obs)[0]

        assert len(build_explore_detail(pollo, _nodo(pollo, "La Habana")).ads) == 6


class TestMapa:
    def test_trae_las_dieciseis_provincias_en_un_lienzo_fijo(self) -> None:
        mapa = build_explore_map(_arroz(), "santiago-de-cuba")

        assert len(mapa.cells) == 16
        assert mapa.width == 340.0
        assert mapa.viewbox.startswith("0 0 340 ")

    def test_la_elegida_va_marcada_y_con_su_tinte(self) -> None:
        cells = {c.slug: c for c in build_explore_map(_arroz(), "santiago-de-cuba").cells}

        assert cells["santiago-de-cuba"].is_selected
        assert cells["santiago-de-cuba"].tint == 1.0
        assert not cells["la-habana"].is_selected
        assert cells["la-habana"].tint == 0.0

    def test_una_provincia_sin_anuncios_del_producto_queda_sin_tinte(self) -> None:
        cells = {c.slug: c for c in build_explore_map(_arroz(), "santiago-de-cuba").cells}

        assert not cells["guantanamo"].has_data
        assert cells["guantanamo"].tint is None
        assert cells["camaguey"].tint is None

    def test_sin_producto_ninguna_tiene_dato_y_ninguna_esta_elegida(self) -> None:
        cells = build_explore_map(None, None).cells

        assert len(cells) == 16
        assert not any(c.has_data or c.is_selected for c in cells)


class TestNavegacion:
    """Los productos de la izquierda, las pestañas del centro y el «+»: todo
    son enlaces a la misma vista con otro `?productos=` y `?pestana=`, así
    funciona sin JavaScript (P4 lo hace en el cliente, sin ida y vuelta al
    server salvo para abrir un producto que no estaba)."""

    def _nav(self, activos: list[str], activa: str = ""):
        productos = _productos(["Arroz", "Pollo", "Aceite"])
        return build_explore_nav(productos, activos, activa, provincia="", desde=date(2026, 7, 31), hasta=HOY)

    def test_la_lista_trae_todos_los_productos_con_su_muestra(self) -> None:
        nav = self._nav(["Arroz"], "arroz")

        assert [(p.name, p.count) for p in nav.products] == [("Arroz", 7), ("Pollo", 1), ("Aceite", 0)]

    def test_marcar_uno_nuevo_lo_agrega_al_final_y_lo_activa(self) -> None:
        nav = self._nav(["Arroz", "Pollo"], "arroz")
        por = {p.name: p for p in nav.products}

        assert por["Arroz"].is_on and por["Pollo"].is_on and not por["Aceite"].is_on
        assert por["Aceite"].query.startswith("/precios?productos=arroz,pollo,aceite&pestana=aceite&")

    def test_desmarcar_la_activa_activa_a_su_vecina(self) -> None:
        """Arroz está activa; al desmarcarla, Pollo —que ocupaba el lugar
        siguiente— pasa a activa."""
        por = {p.name: p for p in self._nav(["Arroz", "Pollo"], "arroz").products}

        assert por["Arroz"].query.startswith("/precios?productos=pollo&pestana=pollo&")

    def test_desmarcar_una_que_no_esta_activa_no_cambia_cual_lo_esta(self) -> None:
        por = {p.name: p for p in self._nav(["Arroz", "Pollo"], "arroz").products}

        assert por["Pollo"].query.startswith("/precios?productos=arroz&pestana=arroz&")

    def test_las_consultas_conservan_el_rango_y_la_vista(self) -> None:
        por = {p.name: p for p in self._nav(["Arroz"], "arroz").products}

        assert por["Pollo"].query == (
            "/precios?productos=arroz,pollo&pestana=pollo&desde=2026-07-31&hasta=2026-08-30&vista=explorar"
        )

    def test_una_pestania_por_producto_abierto_en_orden_de_apertura(self) -> None:
        tabs = self._nav(["Arroz", "Pollo"], "arroz").tabs

        assert [(t.name, t.count, t.is_on) for t in tabs] == [("Arroz", 7, True), ("Pollo", 1, False)]

    def test_activar_otra_pestania_no_reordena_la_lista_solo_cambia_cual_esta_on(
        self,
    ) -> None:
        """P4: antes, tocar una pestaña la ponía primera (P2/P3); el
        prototipo la deja en su lugar y solo mueve cuál se ve."""
        tabs = self._nav(["Arroz", "Pollo"], "pollo").tabs

        assert [t.name for t in tabs] == ["Arroz", "Pollo"]
        assert [t.is_on for t in tabs] == [False, True]

    def test_tocar_una_pestania_conserva_la_lista_y_solo_cambia_la_pestana(self) -> None:
        _, pollo = self._nav(["Arroz", "Pollo"], "arroz").tabs

        assert pollo.query == (
            "/precios?productos=arroz,pollo&pestana=pollo&desde=2026-07-31&hasta=2026-08-30&vista=explorar"
        )

    def test_la_x_de_la_activa_quita_esa_y_activa_a_su_vecina(self) -> None:
        arroz, _ = self._nav(["Arroz", "Pollo"], "arroz").tabs

        assert arroz.close_query.startswith("/precios?productos=pollo&pestana=pollo&")

    def test_la_x_de_otra_no_cambia_cual_esta_activa(self) -> None:
        _, pollo = self._nav(["Arroz", "Pollo"], "arroz").tabs

        assert pollo.close_query.startswith("/precios?productos=arroz&pestana=arroz&")

    def test_cerrar_la_ultima_deja_la_url_sin_productos_ni_pestana(self) -> None:
        (arroz,) = self._nav(["Arroz"], "arroz").tabs

        assert arroz.close_query == "/precios?desde=2026-07-31&hasta=2026-08-30&vista=explorar"

    def test_el_mas_ofrece_solo_los_que_faltan(self) -> None:
        assert [p.name for p in self._nav(["Arroz", "Pollo"], "arroz").add] == ["Aceite"]
        assert self._nav(["Arroz", "Pollo", "Aceite"], "arroz").add == ()
