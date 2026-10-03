"""Validar una fila ya guardada (`validate_stored_row`), slice B1.

Los casos 1 a 8 de «Casos esperados» de `docs/spec-slice-B1.md`. Las
expectativas salen de esa especificación, no de correr el motor.

Todo se arma a mano y nada toca la base: la fila, su celda, las filas recientes
del canal y el canal. `min_cell_n = 5` es el valor que fija B1; los dos
parámetros de la regla 4 son valores DE PRUEBA (el sistema no los tiene
fijados), y solo los pasa el caso 8.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from saimc.extraction.engine import ExtractionProposal
from saimc.models import Product, Province
from saimc.validation import (
    CellReference,
    ChannelContext,
    Outcome,
    RecentRow,
    RowContext,
    RowValidation,
    validate_stored_row,
)

ARROZ = Product(id=uuid.uuid4(), name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True)
HABANA = Province(id=uuid.uuid4(), code="la-habana", name="La Habana")
PILOTOS = frozenset({ARROZ.id})
PROVINCIAS = frozenset({HABANA.id})

AHORA = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)

# Canal con provincia y con un historial limpio: 40 decisiones individuales, 2
# de 50 filas propuestas cambiadas (4%). El historial solo pesa en el caso 8.
CANAL = ChannelContext(province_id=HABANA.id, decisions=40, proposed_rows=50, changed_rows=2)


def _celda(*valores: str) -> CellReference:
    precios = tuple(Decimal(v) for v in valores)
    return CellReference(own_province=precios, all_provinces=precios)


# Exactamente `min_cell_n` referencias (5), ya en CUP por libra.
CELDA_5 = _celda("300", "310", "320", "330", "340")
CELDA_4 = _celda("300", "310", "320", "330")


def _fila(
    precio: str = "320",
    *,
    moneda: str = "CUP",
    cantidad: str | None = "1",
    provincia: Province | None = HABANA,
    base: str | None = "unit",
) -> ExtractionProposal:
    return ExtractionProposal(
        product=ARROZ,
        price=Decimal(precio),
        currency=moneda,
        quantity=None if cantidad is None else Decimal(cantidad),
        unit="lb",
        province=provincia,
        price_basis=base,
    )


def _validar(
    fila: ExtractionProposal | None = None,
    *,
    celda: CellReference = CELDA_5,
    price_cup: Decimal | None = None,
    recientes: tuple[RecentRow, ...] = (),
    **parametros: Any,
) -> RowValidation:
    return validate_stored_row(
        fila if fila is not None else _fila(),
        RowContext(cell=celda, price_cup=price_cup),
        published_at=AHORA,
        channel=CANAL,
        pilot_ids=PILOTOS,
        province_ids=PROVINCIAS,
        recent_channel_rows=recientes,
        verified=True,
        min_cell_n=5,
        **parametros,
    )


def test_caso_1_fila_sana_queda_unverified_con_puntaje_83() -> None:
    resultado = _validar()

    assert resultado.structure == Outcome.PASS
    assert resultado.price == Outcome.PASS
    assert resultado.duplicate == Outcome.PASS
    assert resultado.quantity_price == Outcome.PASS
    assert resultado.trust == Outcome.UNKNOWN
    assert resultado.status == "unverified"
    assert resultado.confidence_score == 83


def test_caso_2_precio_diez_veces_fuera_de_la_banda_es_anomaly() -> None:
    resultado = _validar(_fila("3200"))

    assert resultado.price == Outcome.FAIL
    assert resultado.status == "anomaly"


def test_caso_3_con_4_referencias_la_celda_no_opina() -> None:
    resultado = _validar(celda=CELDA_4)

    assert resultado.price == Outcome.UNKNOWN
    assert resultado.quantity_price == Outcome.UNKNOWN
    assert resultado.status == "unverified"


def test_caso_4_sin_provincia_es_incomplete_aunque_el_precio_este_fuera() -> None:
    # El precio está fuera de la banda, pero gana la regla 1.
    resultado = _validar(_fila("3200", provincia=None))

    assert resultado.structure == Outcome.FAIL
    assert resultado.status == "incomplete"


def test_caso_5_la_otra_base_queda_mas_cerca_es_inconsistent() -> None:
    # 32000 «por libra» queda a ×100 de la celda; leído como total del saco
    # de 100 lb da 320.
    resultado = _validar(_fila("32000", cantidad="100", base="unit"))

    assert resultado.quantity_price == Outcome.FAIL
    assert resultado.price == Outcome.UNKNOWN
    assert resultado.status == "inconsistent"


def test_caso_6_fila_repetida_del_canal_es_possible_duplicate() -> None:
    repetida = RecentRow(
        product_id=ARROZ.id,
        province_id=HABANA.id,
        price=Decimal("320"),
        published_at=AHORA - timedelta(hours=3),
    )

    resultado = _validar(recientes=(repetida,))

    assert resultado.duplicate == Outcome.FAIL
    assert resultado.status == "possible_duplicate"


def test_caso_7_divisa_sin_price_cup_deja_precio_y_base_en_unknown() -> None:
    resultado = _validar(_fila("1", moneda="USD"), price_cup=None)

    assert resultado.price == Outcome.UNKNOWN
    assert resultado.quantity_price == Outcome.UNKNOWN


def test_caso_8_con_la_regla_4_y_canal_limpio_sale_approved() -> None:
    resultado = _validar(channel_min_history=10, channel_max_edit_rate=0.2)

    assert resultado.trust == Outcome.PASS
    assert resultado.status == "approved"
    assert resultado.confidence_score == 100


def test_caso_8_sin_los_parametros_de_la_regla_4_nunca_sale_approved() -> None:
    # Ni pasando uno solo de los dos.
    assert _validar().status != "approved"
    assert _validar(channel_min_history=10).status != "approved"
    assert _validar(channel_max_edit_rate=0.2).status != "approved"


# --- Lo que la especificación dice fuera de la lista numerada ------------------


def test_una_fila_no_verificada_no_sale_approved_aunque_pase_las_seis() -> None:
    """`approved` exige que la fila esté verificada («El motor», Verificación)."""
    resultado = validate_stored_row(
        _fila(),
        RowContext(cell=CELDA_5),
        published_at=AHORA,
        channel=CANAL,
        pilot_ids=PILOTOS,
        province_ids=PROVINCIAS,
        verified=False,
        min_cell_n=5,
        channel_min_history=10,
        channel_max_edit_rate=0.2,
    )

    assert resultado.confidence_score == 100
    assert resultado.status == "unverified"


def test_los_motivos_guardan_todas_las_reglas_que_fallaron() -> None:
    """Sin provincia y con el precio fuera de la banda: gana la regla 1, pero
    los motivos nombran las dos («Guarda todas las reglas que fallaron»)."""
    resultado = _validar(_fila("3200", provincia=None))

    assert resultado.price == Outcome.FAIL
    assert "falta producto piloto, precio, moneda o provincia" in resultado.reasons
    assert "el precio cae fuera de la banda de su celda" in resultado.reasons


def test_divisa_con_price_cup_se_compara_en_cup() -> None:
    resultado = _validar(_fila("1", moneda="USD"), price_cup=Decimal("320"))

    assert resultado.price == Outcome.PASS
