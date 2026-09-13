"""`price_charts.build_province_comparison`: provincias lado a lado, para
responder «¿dónde está más caro?».

Valores esperados calculados a mano, antes de correr el constructor.
"""

import uuid
from datetime import date
from decimal import Decimal

from saimc.cells import RetailObservation
from saimc.web.price_charts import COMPARE_LEFT, COMPARE_RIGHT, COMPARE_WIDTH, build_province_comparison


def _obs(
    province: str, precio: str, *, product: str = "Arroz", unit: str = "lb"
) -> RetailObservation:
    return RetailObservation(
        product_name=product,
        province_name=province,
        currency="CUP",
        unit=unit,
        unconverted=False,
        day=date(2026, 8, 20),
        unit_price=Decimal(precio),
        evidence_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        evidence_is_public=False,
    )


HABANA = [_obs("La Habana", p) for p in ("300", "310", "320", "330", "340")]
SANTIAGO = [_obs("Santiago de Cuba", p) for p in ("400", "420")]


class TestProvinciasLadoALado:
    def test_una_comparacion_por_producto_con_una_fila_por_provincia(self) -> None:
        (comp,) = build_province_comparison(HABANA + SANTIAGO)

        assert comp.product_name == "Arroz"
        assert {r.province_name for r in comp.rows} == {"La Habana", "Santiago de Cuba"}

    def test_la_mas_cara_primero(self) -> None:
        (comp,) = build_province_comparison(HABANA + SANTIAGO)

        # Mediana de Santiago 410 contra 320 de La Habana.
        assert [r.province_name for r in comp.rows] == ["Santiago de Cuba", "La Habana"]

    def test_rango_mitad_central_mediana_y_muestra(self) -> None:
        (comp,) = build_province_comparison(HABANA + SANTIAGO)
        habana = next(r for r in comp.rows if r.province_name == "La Habana")

        assert habana.minimum == Decimal("300")
        assert habana.q1 == Decimal("310")
        assert habana.median == Decimal("320")
        assert habana.q3 == Decimal("330")
        assert habana.maximum == Decimal("340")
        assert habana.count == 5

    def test_una_escala_comun_de_izquierda_a_derecha(self) -> None:
        (comp,) = build_province_comparison(HABANA + SANTIAGO)
        habana, santiago = (
            next(r for r in comp.rows if r.province_name == p)
            for p in ("La Habana", "Santiago de Cuba")
        )

        assert habana.x_min < habana.x_q1 < habana.x_median < habana.x_q3 < habana.x_max
        assert santiago.x_median > habana.x_median
        assert habana.x_min >= COMPARE_LEFT
        assert santiago.x_max <= COMPARE_WIDTH - COMPARE_RIGHT

    def test_el_precio_imposible_no_estira_la_escala(self) -> None:
        filas = [_obs("La Habana", "300")] * 4 + [_obs("La Habana", "24800")] + SANTIAGO

        (comp,) = build_province_comparison(filas)
        habana = next(r for r in comp.rows if r.province_name == "La Habana")

        assert habana.maximum == Decimal("300")
        assert habana.count == 5
        assert habana.outliers == 1

    def test_unidades_distintas_no_se_comparan_en_el_mismo_eje(self) -> None:
        filas = HABANA + [_obs("Santiago de Cuba", "3400", product="Arroz", unit="kg")]

        comps = build_province_comparison(filas)

        assert sorted(c.unit for c in comps) == ["kg", "lb"]
