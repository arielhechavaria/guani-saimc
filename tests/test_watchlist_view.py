"""Lista de productos del tablero (dirección C, 2026-09-18): una fila por
producto piloto, una celda por provincia con su mediana de la ventana y el
cambio contra el período anterior del mismo largo.

Valores esperados calculados a mano, antes de escribir `build_watchlist`.
HOY = domingo 30/08/2026; ventana de 30 días (31/07–30/08), período anterior
del mismo largo (01/07–30/07).

- Arroz, La Habana: ahora 300 y 320 → mediana 310; antes 300 y 300 → 300.
  Cambio 310/300 − 1 = +3,3 % → «+3%».
- Arroz, Santiago: ahora 400; sin anuncios antes → sin cambio (no se inventa).
- Pollo, La Habana: ahora 700; antes 750 → 700/750 − 1 = −6,7 % → «−7%».
- Pollo, Santiago y Aceite en todas: sin datos → celda vacía.
"""

import uuid
from datetime import date
from decimal import Decimal

from saimc.cells import RetailObservation
from saimc.units import UNITS
from saimc.web.price_charts import build_month_vs_month
from saimc.web.watchlist_view import Watchlist, WatchRow, build_watchlist

HOY = date(2026, 8, 30)
DESDE = date(2026, 7, 31)
PROVINCIAS = ["La Habana", "Santiago de Cuba"]
PILOTO = ["Aceite", "Arroz", "Pollo"]


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
    _obs("Arroz", "La Habana", 8, 20, "300"),
    _obs("Arroz", "La Habana", 8, 25, "320"),
    _obs("Arroz", "Santiago de Cuba", 8, 22, "400"),
    _obs("Pollo", "La Habana", 8, 18, "700"),
]
ANTERIOR = [
    _obs("Arroz", "La Habana", 7, 10, "300"),
    _obs("Arroz", "La Habana", 7, 12, "300"),
    _obs("Pollo", "La Habana", 7, 10, "750"),
]


def _lista(active: list[str], provincia: str = ""):
    slopes = build_month_vs_month(ACTUAL, ANTERIOR, window_days=30, hoy=HOY)
    return build_watchlist(
        PILOTO,
        active,
        slopes,
        provinces=PROVINCIAS,
        selected_province=provincia,
        desde=DESDE,
        hasta=HOY,
    )


def _fila(lista, nombre: str):
    return next(r for r in lista.rows if r.product_name == nombre)


class TestFilas:
    def test_una_fila_por_cada_producto_piloto(self) -> None:
        """Todos los piloto aparecen, tengan o no anuncios (el orden lo cubre
        `TestOrdenPorMuestra`)."""
        assert sorted(r.product_name for r in _lista([]).rows) == ["Aceite", "Arroz", "Pollo"]

    def test_las_columnas_son_las_provincias_recibidas(self) -> None:
        assert _lista([]).provinces == ("La Habana", "Santiago de Cuba")

    def test_celda_con_mediana_muestra_y_cambio(self) -> None:
        habana = _fila(_lista([]), "Arroz").cells[0]

        assert habana is not None
        assert habana.median_text == "310"
        assert habana.count == 2
        assert habana.change_text == "+3%"

    def test_cambio_negativo_con_signo_menos_tipografico(self) -> None:
        habana = _fila(_lista([]), "Pollo").cells[0]

        assert habana is not None
        assert habana.change_text == "−7%"

    def test_sin_periodo_anterior_no_inventa_cambio(self) -> None:
        santiago = _fila(_lista([]), "Arroz").cells[1]

        assert santiago is not None
        assert santiago.median_text == "400"
        assert santiago.change_text == ""

    def test_provincia_sin_dato_deja_la_celda_vacia(self) -> None:
        assert _fila(_lista([]), "Pollo").cells[1] is None

    def test_producto_sin_datos_sigue_en_la_lista_con_celdas_vacias(self) -> None:
        aceite = _fila(_lista([]), "Aceite")

        assert aceite.cells == (None, None)
        assert aceite.unit == ""

    def test_la_unidad_de_la_fila_sale_del_grafico(self) -> None:
        arroz = _fila(_lista([]), "Arroz")

        assert (arroz.currency, arroz.unit) == ("CUP", "lb")


class TestSeleccion:
    def test_cada_fila_elige_un_solo_producto_y_preserva_el_resto(self) -> None:
        """A diferencia de los chips (que suman a la multi-selección), una
        fila de la lista reemplaza la selección entera."""
        pollo = _fila(_lista(["Arroz"], provincia="la-habana"), "Pollo")

        assert pollo.query == (
            "/?productos=pollo&provincia=la-habana&desde=2026-07-31&hasta=2026-08-30&vista=tablero"
        )

    def test_todos_quita_el_producto_y_preserva_el_resto(self) -> None:
        assert _lista(["Arroz"], provincia="la-habana").all_query == (
            "/?provincia=la-habana&desde=2026-07-31&hasta=2026-08-30&vista=tablero"
        )

    def test_sin_producto_activo_esta_marcado_todos(self) -> None:
        lista = _lista([])

        assert lista.all_is_active is True
        assert not any(r.is_active for r in lista.rows)

    def test_con_un_producto_activo_se_marca_esa_fila(self) -> None:
        lista = _lista(["Arroz"])

        assert lista.all_is_active is False
        assert [r.product_name for r in lista.rows if r.is_active] == ["Arroz"]

    def test_una_url_vieja_con_varios_productos_marca_todos_los_activos(self) -> None:
        """`?productos=arroz,pollo` sigue siendo una URL válida (enlaces
        guardados, el mapa): la lista marca los dos, no rompe."""
        lista = _lista(["Arroz", "Pollo"])

        assert lista.all_is_active is False
        assert [r.product_name for r in lista.rows if r.is_active] == ["Arroz", "Pollo"]

    def test_sin_provincia_elegida_la_query_no_la_lleva(self) -> None:
        pollo = _fila(_lista([], provincia=""), "Pollo")

        assert "provincia=" not in pollo.query


class TestMasBarata:
    """La lista absorbe a la tira «dónde está más barato» (2026-09-19, S5): la
    celda de la provincia con la mediana más baja lleva la etiqueta. Solo si hay
    con qué comparar —dos provincias con dato— y sin empate: con dos medianas
    iguales, ninguna es «la más barata».

    Valores esperados, a mano (ver el docstring del módulo): Arroz, La Habana
    310 y Santiago 400 → La Habana. Pollo solo tiene La Habana."""

    def test_la_provincia_mas_barata_se_marca(self) -> None:
        arroz = _fila(_lista([]), "Arroz")

        assert [c.is_cheapest for c in arroz.cells if c] == [True, False]

    def test_con_una_sola_provincia_con_dato_no_hay_con_que_comparar(self) -> None:
        (habana, santiago) = _fila(_lista([]), "Pollo").cells

        assert habana is not None and habana.is_cheapest is False
        assert santiago is None

    def test_un_producto_sin_datos_no_marca_nada(self) -> None:
        assert _fila(_lista([]), "Aceite").cells == (None, None)

    def test_con_medianas_iguales_ninguna_es_la_mas_barata(self) -> None:
        ahora = [
            _obs("Arroz", "La Habana", 8, 20, "300"),
            _obs("Arroz", "Santiago de Cuba", 8, 22, "300"),
        ]
        slopes = build_month_vs_month(ahora, [], window_days=30, hoy=HOY)
        lista = build_watchlist(
            ["Arroz"], [], slopes, provinces=PROVINCIAS, selected_province="",
            desde=DESDE, hasta=HOY,
        )

        assert [c.is_cheapest for c in lista.rows[0].cells if c] == [False, False]


class TestMiProvincia:
    """S8 (2026-09-19): con una provincia elegida la lista se enfoca en ella —
    una sola columna— y las demás pasan a una línea chica por fila (`otras`),
    para que «más barata» siga teniendo con qué compararse a la vista.

    Valores esperados, a mano (ver el docstring del módulo): Arroz, La Habana
    310 y Santiago 400; Pollo solo tiene La Habana."""

    def test_con_provincia_elegida_hay_una_sola_columna(self) -> None:
        lista = _lista([], "santiago-de-cuba")

        assert lista.provinces == ("Santiago de Cuba",)
        assert all(len(r.cells) == 1 for r in lista.rows)

    def test_la_celda_principal_es_la_de_la_provincia_elegida(self) -> None:
        (celda,) = _fila(_lista([], "santiago-de-cuba"), "Arroz").cells

        assert celda is not None
        assert celda.province_name == "Santiago de Cuba"
        assert celda.median_text == "400"

    def test_las_demas_provincias_pasan_a_otras(self) -> None:
        (otra,) = _fila(_lista([], "santiago-de-cuba"), "Arroz").otras

        assert otra.province_name == "La Habana"
        assert otra.median_text == "310"

    def test_una_provincia_sin_dato_del_producto_deja_vacia_su_celda(self) -> None:
        """Pollo solo tiene La Habana: elegida Santiago, la celda principal
        queda vacía y La Habana se ve en `otras`. No se esconde el dato."""
        pollo = _fila(_lista([], "santiago-de-cuba"), "Pollo")

        assert pollo.cells == (None,)
        assert [c.province_name for c in pollo.otras] == ["La Habana"]

    def test_la_mas_barata_se_decide_entre_todas_no_solo_la_elegida(self) -> None:
        arroz = _fila(_lista([], "santiago-de-cuba"), "Arroz")

        assert arroz.cells[0] is not None and arroz.cells[0].is_cheapest is False
        assert arroz.otras[0].is_cheapest is True

    def test_sin_provincia_elegida_no_hay_otras(self) -> None:
        assert all(r.otras == () for r in _lista([]).rows)

    def test_una_provincia_que_no_es_columna_deja_la_lista_completa(self) -> None:
        """Un slug válido pero sin dato piloto (o basura, o una cookie
        vieja) no vacía la lista: se ven todas las columnas."""
        lista = _lista([], "camaguey")

        assert lista.provinces == ("La Habana", "Santiago de Cuba")
        assert all(r.otras == () for r in lista.rows)


class TestParaElDetalleYElResumen:
    """Reconstrucción sobre el prototipo E (2026-09-19): el detalle de un
    producto compara todas las provincias aunque la lista esté enfocada en
    una, y la página dice cuántos anuncios respaldan las cifras.

    Valores esperados, a mano (ver el docstring del módulo): Arroz, La Habana
    310 (2 anuncios) y Santiago 400 (1); Pollo solo La Habana (1). En la
    ventana hay 2 + 1 + 1 = 4 anuncios."""

    def test_el_total_de_anuncios_suma_todas_las_celdas_de_la_ventana(self) -> None:
        assert _lista([]).total_ads == 4

    def test_el_total_no_cambia_con_una_provincia_elegida(self) -> None:
        assert _lista([], "santiago-de-cuba").total_ads == 4

    def test_todas_trae_la_fila_completa_aunque_haya_foco(self) -> None:
        arroz = _fila(_lista([], "santiago-de-cuba"), "Arroz")

        assert [c.province_name for c in arroz.todas if c] == ["La Habana", "Santiago de Cuba"]

    def test_las_provincias_de_todas_no_dependen_del_foco(self) -> None:
        assert _lista([], "santiago-de-cuba").all_provinces == ("La Habana", "Santiago de Cuba")

    def test_la_mas_barata_y_contra_quien_se_compara(self) -> None:
        arroz = _fila(_lista([]), "Arroz")

        assert arroz.barata is not None and arroz.barata.province_name == "La Habana"
        assert [c.province_name for c in arroz.contra] == ["Santiago de Cuba"]

    def test_sin_con_que_comparar_no_hay_mas_barata(self) -> None:
        pollo = _fila(_lista([]), "Pollo")

        assert pollo.barata is None
        assert pollo.contra == ()


class TestOrdenPorMuestra:
    """Prototipo E (2026-09-20): los productos se listan de más a menos
    anuncios, no alfabéticamente —la lista contesta «qué cuesta hoy», y lo
    que más se anuncia es lo que más gente mira—. El menú lateral del detalle
    recorre las mismas filas, así que hereda el orden.

    Valores esperados, a mano (ver el docstring del módulo): Arroz 3 anuncios
    (La Habana 2 + Santiago 1), Pollo 1, Aceite 0."""

    def test_las_filas_van_de_mas_a_menos_anuncios(self) -> None:
        assert [r.product_name for r in _lista([]).rows] == ["Arroz", "Pollo", "Aceite"]

    def test_el_orden_no_depende_de_la_provincia_elegida(self) -> None:
        """Con Santiago elegida, Arroz sigue primero: el orden sale de todos
        los anuncios del producto, no de los de la provincia enfocada (ahí
        Arroz tiene 1 y Pollo 0)."""
        assert [r.product_name for r in _lista([], "santiago-de-cuba").rows] == [
            "Arroz",
            "Pollo",
            "Aceite",
        ]

    def test_con_la_misma_muestra_desempata_el_alfabeto(self) -> None:
        """Dos productos sin anuncios: el orden tiene que ser estable entre
        cargas, no el que devuelva el diccionario."""
        slopes = build_month_vs_month([], [], window_days=30, hoy=HOY)
        lista = build_watchlist(
            ["Pollo", "Aceite", "Arroz"], [], slopes, provinces=PROVINCIAS,
            selected_province="", desde=DESDE, hasta=HOY,
        )

        assert [r.product_name for r in lista.rows] == ["Aceite", "Arroz", "Pollo"]

    def test_el_desempate_ordena_como_el_espanol(self) -> None:
        """«Ñame» va entre «Nabo» y «Ocra», no detrás de todo: comparar el
        texto crudo lo mandaba al final porque U+00D1 es mayor que «P». La
        tilde tampoco separa: «Ají» queda junto a «Ajo»."""
        slopes = build_month_vs_month([], [], window_days=30, hoy=HOY)
        lista = build_watchlist(
            ["Ocra", "Ñame", "Nabo", "Ajo", "Ají"], [], slopes, provinces=PROVINCIAS,
            selected_province="", desde=DESDE, hasta=HOY,
        )

        assert [r.product_name for r in lista.rows] == ["Ají", "Ajo", "Nabo", "Ñame", "Ocra"]


class TestUnidadEnPalabra:
    """`WatchRow.unit_noun` es lo que el subtítulo de «Por envase» escribe
    dentro de la frase («no por libra»). Fuera del vocabulario no se inventa
    una palabra: cae al valor canónico, que se lee peor pero no miente."""

    def _fila(self, unit: str) -> WatchRow:
        return WatchRow(
            product_name="Arroz", slug="arroz", icon=None, currency="CUP", unit=unit,
            cells=(), query="", is_active=False,
        )

    def test_del_vocabulario_sale_la_palabra(self) -> None:
        assert self._fila("lb").unit_noun == "libra"

    def test_fuera_del_vocabulario_queda_la_unidad_canonica(self) -> None:
        assert self._fila("onza").unit_noun == "onza"

    def test_sin_unidad_no_hay_palabra(self) -> None:
        """Un producto sin anuncios en la ventana no trae unidad, y la
        plantilla se apoya en que esto sea falso para no escribir «(no por )»."""
        assert self._fila("").unit_noun == ""

    def test_hay_palabra_exactamente_cuando_hay_unidad(self) -> None:
        """El invariante del que se cuelga `Watchlist.medida`, que filtra por
        `unit_noun` porque es lo que escribe: mientras `unit_noun` sea vacío
        exactamente cuando `unit` lo es, filtrar por uno u otro da lo mismo.
        Si alguien hiciera que una unidad conocida devolviera vacío, esto cae
        acá y no en una frase rota en pantalla.

        Se recorre `UNITS` y no una lista escrita a mano: la unidad que se
        agregue mañana al catálogo entra sola en la prueba, que es justo
        cuando hace falta."""
        assert UNITS, "sin catálogo no hay invariante que probar"
        for unidad in (*UNITS, "onza"):
            assert self._fila(unidad).unit_noun != "", unidad
        assert self._fila("").unit_noun == ""


class TestEnQueEstaEscritaLaMediana:
    """`Watchlist.medida` arma «en CUP por libra o por litro», la frase que el
    prototipo E pone en «Sobre los datos». Se deriva de lo que hay en
    pantalla: un literal se volvería mentira el día que entre otra moneda."""

    def _lista(self, *pares: tuple[str, str]) -> Watchlist:
        return Watchlist(
            provinces=("La Habana",),
            rows=tuple(
                WatchRow(
                    product_name=f"P{i}", slug=f"p{i}", icon=None, currency=moneda,
                    unit=unidad, cells=(), query="", is_active=False,
                )
                for i, (moneda, unidad) in enumerate(pares)
            ),
            all_query="",
            all_is_active=False,
        )

    def test_una_moneda_y_dos_unidades(self) -> None:
        assert self._lista(("CUP", "lb"), ("CUP", "l")).medida == "en CUP por libra o por litro"

    def test_una_sola_unidad_no_lleva_disyuntiva(self) -> None:
        assert self._lista(("CUP", "lb"), ("CUP", "lb")).medida == "en CUP por libra"

    def test_tres_unidades_se_enumeran(self) -> None:
        lista = self._lista(("CUP", "lb"), ("CUP", "l"), ("CUP", "docena"))

        assert lista.medida == "en CUP por docena, por libra o por litro"

    def test_con_dos_monedas_se_calla(self) -> None:
        """No hay una frase honesta que cubra las dos, así que no se escribe
        ninguna: la plantilla deja la viñeta sin la medida."""
        assert self._lista(("CUP", "lb"), ("USD", "lb")).medida == ""

    def test_sin_filas_con_dato_se_calla(self) -> None:
        """Un producto piloto sin anuncios en la ventana no trae moneda ni
        unidad, y no hay ninguna cifra de la que hablar."""
        assert self._lista(("", "")).medida == ""
        assert self._lista().medida == ""


class TestBusquedaDeLaFila:
    """`WatchRow.busqueda` es el nombre plegado que cada fila de la lista lleva
    en `data-busqueda`. El script del navegador pliega la consulta igual y
    busca por subcadena, así que esta mitad es la que decide qué encuentra
    quién escribe sin tildes ni mayúsculas.

    Los pares se escriben a mano: «Azúcar» → «azucar» es lo que un cubano
    espera al teclear en un teléfono sin acentos."""

    def _fila(self, nombre: str) -> WatchRow:
        return WatchRow(
            product_name=nombre, slug="x", icon=None, currency="CUP", unit="lb",
            cells=(), query="", is_active=False,
        )

    def test_quita_las_tildes_y_pasa_a_minusculas(self) -> None:
        assert self._fila("Azúcar blanca").busqueda == "azucar blanca"
        assert self._fila("JAMÓN").busqueda == "jamon"

    def test_la_enie_se_pliega_a_la_ene(self) -> None:
        """Quien escribe «name» encuentra «Ñame», y quien escribe «ñame» también:
        la consulta se pliega igual del otro lado."""
        assert self._fila("Ñame").busqueda == "name"

    def test_las_dieresis_tambien(self) -> None:
        assert self._fila("Pingüino").busqueda == "pinguino"

    def test_lo_que_ya_esta_plano_no_cambia(self) -> None:
        assert self._fila("arroz").busqueda == "arroz"
        assert self._fila("Aceite de oliva").busqueda == "aceite de oliva"

    def test_un_nombre_vacio_no_rompe(self) -> None:
        assert self._fila("").busqueda == ""

    def test_es_el_mismo_plegado_que_ordena_la_lista(self) -> None:
        """Una sola definición de «sin tildes» para el orden y para el buscador:
        si mañana cambia una, cambia la otra, y «Ají» sigue junto a «Ajo» en
        los dos lados."""
        lista = build_watchlist(
            ["Ajo", "Ají"], [], build_month_vs_month([], [], window_days=30, hoy=HOY),
            provinces=PROVINCIAS, selected_province="", desde=DESDE, hasta=HOY,
        )

        assert [r.busqueda for r in lista.rows] == ["aji", "ajo"]
        assert [r.product_name for r in lista.rows] == ["Ají", "Ajo"]

