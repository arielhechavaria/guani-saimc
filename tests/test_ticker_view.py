"""Cintillo del tablero (prototipo aprobado, 2026-09-26): una entrada por producto
con la mediana entre las provincias que tienen dato y su cambio.

Valores esperados calculados a mano, antes de escribir `build_ticker`.
HOY = domingo 30/08/2026; ventana de 30 días (31/07–30/08), período anterior
del mismo largo (01/07–30/07).

- Arroz, La Habana: ahora 300 y 320 → mediana 310; antes 300 y 300 → 300.
- Arroz, Santiago: ahora 400; sin anuncios antes.
  Mediana entre provincias: (310 + 400) / 2 = 355. El cambio se calcula solo
  con las provincias que tienen los dos períodos (La Habana): 310/300 − 1 =
  +3,3 % → «+3%». No se mezcla una provincia sin período anterior.
- Pollo, La Habana: ahora 700; antes 750 → 700/750 − 1 = −6,7 % → «−7%».
- Aceite: sin datos en la ventana → no aparece en el cintillo.
- Orden: por anuncios de la ventana, de más a menos: Arroz (3) antes que Pollo (1).

Con Santiago también en el período anterior (15/07: 380): medianas ahora
[310, 400] → 355; antes [300, 380] → 340; cambio 355/340 − 1 = +4,4 % → «+4%».
"""

import uuid
from datetime import date
from decimal import Decimal

from saimc.cells import RetailObservation
from saimc.web.price_charts import build_month_vs_month
from saimc.web.ticker_view import build_ticker

HOY = date(2026, 8, 30)
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


def _cintillo(actual=ACTUAL, anterior=ANTERIOR):
    slopes = build_month_vs_month(actual, anterior, window_days=30, hoy=HOY)
    return build_ticker(PILOTO, slopes, href=lambda slug: f"/?productos={slug}")


def _item(cintillo, nombre: str):
    return next(i for i in cintillo if i.product_name == nombre)


class TestCintillo:
    def test_la_mediana_es_la_de_las_provincias_con_dato(self) -> None:
        assert _item(_cintillo(), "Arroz").median_text == "355"

    def test_con_una_sola_provincia_es_su_mediana(self) -> None:
        assert _item(_cintillo(), "Pollo").median_text == "700"

    def test_el_cambio_usa_solo_provincias_con_los_dos_periodos(self) -> None:
        assert _item(_cintillo(), "Arroz").change_text == "+3%"

    def test_el_cambio_con_todas_las_provincias_comparables(self) -> None:
        anterior = [*ANTERIOR, _obs("Arroz", "Santiago de Cuba", 7, 15, "380")]

        assert _item(_cintillo(anterior=anterior), "Arroz").change_text == "+4%"

    def test_una_baja_lleva_el_signo_menos_tipografico(self) -> None:
        assert _item(_cintillo(), "Pollo").change_text == "−7%"

    def test_sin_periodo_anterior_no_inventa_cambio(self) -> None:
        assert _item(_cintillo(anterior=[]), "Arroz").change_text == ""

    def test_la_unidad_lleva_la_moneda(self) -> None:
        assert _item(_cintillo(), "Arroz").unit == "CUP/lb"

    def test_un_producto_sin_datos_no_aparece(self) -> None:
        assert "Aceite" not in [i.product_name for i in _cintillo()]

    def test_el_orden_es_por_anuncios_de_mas_a_menos(self) -> None:
        assert [i.product_name for i in _cintillo()] == ["Arroz", "Pollo"]

    def test_el_enlace_lo_arma_quien_llama_con_el_slug(self) -> None:
        assert _item(_cintillo(), "Arroz").href == "/?productos=arroz"

    def test_sin_datos_en_ningun_producto_el_cintillo_queda_vacio(self) -> None:
        assert _cintillo(actual=[], anterior=[]) == ()
