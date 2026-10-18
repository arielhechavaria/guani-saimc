"""`rules.basis_correction`: cuándo el formulario de la cola preselecciona la
otra base (`docs/analisis-otra-base.md`).

Los casos se definieron a mano antes del código, con la forma de los anuncios
reales del análisis: aceite de 900 ml «c/u» guardado como `unit` y el saco de
arroz marcado `unit`.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from saimc.extraction.engine import ExtractionProposal
from saimc.models import Product, Province
from saimc.validation import CellReference
from saimc.validation.rules import basis_correction, check_quantity_price
from saimc.validation.types import Outcome

ARROZ = Product(id=uuid.uuid4(), name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True)
ACEITE = Product(id=uuid.uuid4(), name="Aceite", category="aceites", unit="l", aliases=[], is_pilot=True)
HABANA = Province(id=uuid.uuid4(), code="la-habana", name="La Habana")


def _refs(*values: str) -> tuple[Decimal, ...]:
    return tuple(Decimal(v) for v in values)


REF_ARROZ = _refs("300", "310", "320", "325", "330", "340")
CELDA_ARROZ = CellReference(own_province=REF_ARROZ, all_provinces=REF_ARROZ)
REF_ACEITE = _refs("2500", "2600", "2700", "2800", "2900", "3000")
CELDA_ACEITE = CellReference(own_province=REF_ACEITE, all_provinces=REF_ACEITE)


def _row(
    product: Product,
    price: str,
    quantity: str,
    unit: str,
    basis: str | None,
    currency: str = "CUP",
) -> ExtractionProposal:
    return ExtractionProposal(
        product=product,
        price=Decimal(price),
        currency=currency,
        quantity=Decimal(quantity),
        unit=unit,
        province=HABANA,
        price_basis=basis,
    )


def test_aceite_de_900_ml_por_unidad_se_corrige_a_total() -> None:
    # 2300 CUP la botella de 900 ml: «por ml» da 2.300.000 CUP/l, «total» 2.556.
    row = _row(ACEITE, "2300", "900", "ml", "unit")

    fix = basis_correction(row, price_cup=None, cell=CELDA_ACEITE, min_cell_n=5)

    assert fix is not None
    assert fix.flipped_basis == "total"
    assert fix.declared == Decimal("2300000")
    assert round(fix.other) == 2556
    assert fix.reference == Decimal("2750")
    assert fix.unit == "l"
    assert fix.from_all_provinces is False


def test_saco_de_arroz_por_unidad_se_corrige_a_total() -> None:
    row = _row(ARROZ, "32000", "100", "lb", "unit")

    fix = basis_correction(row, price_cup=None, cell=CELDA_ARROZ, min_cell_n=5)

    assert fix is not None
    assert fix.flipped_basis == "total"


def test_contenedor_por_botella_declarado_total_se_corrige_a_unidad() -> None:
    # 1,86 la botella de 1 l, caja de 15: «total» da 0,12 por litro.
    row = _row(ACEITE, "1.86", "15", "l", "total", currency="CUP")
    celda = CellReference(own_province=_refs("1", "1.5", "2", "2.5", "3"), all_provinces=())

    fix = basis_correction(row, price_cup=None, cell=celda, min_cell_n=5)

    assert fix is not None
    assert fix.flipped_basis == "unit"


def test_base_declarada_correcta_no_se_toca() -> None:
    row = _row(ARROZ, "32000", "100", "lb", "total")

    assert basis_correction(row, price_cup=None, cell=CELDA_ARROZ, min_cell_n=5) is None


def test_celda_con_menos_referencias_que_el_minimo_no_opina() -> None:
    row = _row(ARROZ, "32000", "100", "lb", "unit")
    pocas = CellReference(own_province=REF_ARROZ[:3], all_provinces=REF_ARROZ[:3])

    assert basis_correction(row, price_cup=None, cell=pocas, min_cell_n=5) is None


def test_cantidad_uno_no_tiene_otra_lectura() -> None:
    row = _row(ARROZ, "320", "1", "lb", "unit")

    assert basis_correction(row, price_cup=None, cell=CELDA_ARROZ, min_cell_n=5) is None


def test_sin_cantidad_no_opina() -> None:
    row = _row(ARROZ, "32000", "100", "lb", "unit")
    row = ExtractionProposal(**{**row.__dict__, "quantity": None})

    assert basis_correction(row, price_cup=None, cell=CELDA_ARROZ, min_cell_n=5) is None


def test_el_repliegue_a_todas_las_provincias_queda_dicho() -> None:
    row = _row(ARROZ, "32000", "100", "lb", "unit")
    celda = CellReference(own_province=(), all_provinces=REF_ARROZ)

    fix = basis_correction(row, price_cup=None, cell=celda, min_cell_n=5)

    assert fix is not None
    assert fix.from_all_provinces is True


def test_coincide_con_la_regla_6_en_toda_fila() -> None:
    # La corrección existe exactamente cuando la regla 6 sale `fail`.
    casos = [
        (_row(ACEITE, "2300", "900", "ml", "unit"), CELDA_ACEITE),
        (_row(ARROZ, "32000", "100", "lb", "unit"), CELDA_ARROZ),
        (_row(ARROZ, "32000", "100", "lb", "total"), CELDA_ARROZ),
        (_row(ARROZ, "320", "1", "lb", "unit"), CELDA_ARROZ),
    ]
    for row, celda in casos:
        fail = check_quantity_price(row, price_cup=None, cell=celda, min_cell_n=5) is Outcome.FAIL
        fix = basis_correction(row, price_cup=None, cell=celda, min_cell_n=5)
        assert (fix is not None) == fail
