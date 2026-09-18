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
from saimc.web.price_charts import build_month_vs_month
from saimc.web.watchlist_view import build_watchlist

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


def _lista(active: list[str], provincia: str = "la-habana"):
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
    def test_una_fila_por_producto_piloto_en_su_orden(self) -> None:
        assert [r.product_name for r in _lista([]).rows] == ["Aceite", "Arroz", "Pollo"]

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
        pollo = _fila(_lista(["Arroz"]), "Pollo")

        assert pollo.query == (
            "/?productos=pollo&provincia=la-habana&desde=2026-07-31&hasta=2026-08-30&vista=tablero"
        )

    def test_todos_quita_el_producto_y_preserva_el_resto(self) -> None:
        assert _lista(["Arroz"]).all_query == (
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
