"""Chequeos puros sobre lo que propuso el modelo (`llm_checks`).

Todas las expectativas están calculadas a mano, antes e independientemente del
código: son anuncios sintéticos con la forma de uno real.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from saimc.extraction import llm_checks
from saimc.extraction.engine import ExtractionProposal
from saimc.extraction.llm_checks import RowMeta
from saimc.models import Product

ARROZ = Product(id=uuid.uuid4(), name="Arroz", category="granos", unit="lb", aliases=[])
POLLO = Product(id=uuid.uuid4(), name="Pollo", category="carnes", unit="lb", aliases=[])


def _fila(producto, precio, moneda="CUP", cantidad=None, unidad=None) -> ExtractionProposal:
    return ExtractionProposal(
        product=producto,
        price=None if precio is None else Decimal(precio),
        currency=moneda,
        quantity=None if cantidad is None else Decimal(cantidad),
        unit=unidad,
    )


# --- verify_evidence --------------------------------------------------------


def _verificar(texto: str, evidencia: str | None, precio: str | None) -> RowMeta:
    (meta,) = llm_checks.verify_evidence(
        texto, [_fila(ARROZ, precio)], [RowMeta(evidence=evidencia, currency_explicit=True)]
    )
    return meta


def test_evidencia_literal_se_encuentra() -> None:
    meta = _verificar("Vendo Arroz 25 lb a 30885 cup\nLlamar", "Arroz 25 lb a 30885", "30885")

    assert meta.evidence_found is True
    assert meta.price_in_evidence is True


def test_evidencia_inventada_no_se_encuentra() -> None:
    meta = _verificar("Vendo Arroz 25 lb a 30885 cup", "Arroz 50 lb a 40000", "30885")

    assert meta.evidence_found is False
    assert meta.price_in_evidence is False


def test_evidencia_tolera_mayusculas_tildes_y_espacios() -> None:
    texto = "ARROZ   blanco\n  en   Santiago de Cuba: 500"
    meta = _verificar(texto, "Arroz blanco en santiágo de cuba: 500", "500")

    assert meta.evidence_found is True
    assert meta.price_in_evidence is True


@pytest.mark.parametrize("escrito", ["30.885", "30 885", "30885", "30,885"])
def test_el_precio_se_reconoce_con_cualquier_formato_de_miles(escrito: str) -> None:
    meta = _verificar(f"Arroz 25 lb {escrito} cup", f"25 lb {escrito}", "30885")

    assert meta.price_in_evidence is True


def test_el_precio_decimal_se_reconoce() -> None:
    assert _verificar("Pollo 1 lb a 43,5 usd", "1 lb a 43,5 usd", "43.5").price_in_evidence is True


def test_un_numero_distinto_no_cuenta_como_el_precio() -> None:
    # 30885 no está en el fragmento aunque "885" sí: no es coincidencia parcial.
    meta = _verificar("Arroz 885 gramos", "Arroz 885 gramos", "30885")

    assert meta.evidence_found is True
    assert meta.price_in_evidence is False


def test_sin_evidencia_o_sin_precio() -> None:
    sin_evidencia = _verificar("Arroz 100", None, "100")
    assert sin_evidencia.evidence_found is False
    assert sin_evidencia.price_in_evidence is False

    vacia = _verificar("Arroz 100", "   ", "100")
    assert vacia.evidence_found is False

    sin_precio = _verificar("Arroz consultar", "Arroz consultar", None)
    assert sin_precio.evidence_found is True
    assert sin_precio.price_in_evidence is None


def test_verify_conserva_evidencia_y_moneda_explicita() -> None:
    (meta,) = llm_checks.verify_evidence(
        "Arroz 100", [_fila(ARROZ, "100")], [RowMeta(evidence="Arroz 100", currency_explicit=False)]
    )

    assert (meta.evidence, meta.currency_explicit) == ("Arroz 100", False)


# --- compare_with_rules -----------------------------------------------------


def test_coincide_en_todos_los_campos() -> None:
    modelo = [_fila(ARROZ, "100", "CUP", "1", "lb")]
    reglas = [_fila(ARROZ, "100", "CUP", "1", "lb")]

    (a,) = llm_checks.compare_with_rules(modelo, reglas)

    assert (a.product, a.price, a.currency, a.quantity, a.unit) == ("coincide",) * 5
    assert a.status == "coincide"


def test_difiere_en_un_campo_y_lo_dice() -> None:
    modelo = [_fila(POLLO, "200", "CUP", "1", "lb")]
    reglas = [_fila(POLLO, "210", "CUP", "1", "lb")]

    (a,) = llm_checks.compare_with_rules(modelo, reglas)

    assert a.price == "difiere"
    assert (a.product, a.currency, a.quantity, a.unit) == ("coincide",) * 4
    assert a.status == "difiere"


def test_moneda_distinta_difiere() -> None:
    # El caso «42 usd o 28560»: el modelo dice USD 42, las reglas CUP 28560.
    modelo = [_fila(ARROZ, "42", "USD", "1", "lb")]
    reglas = [_fila(ARROZ, "28560", "CUP", "1", "lb")]

    (a,) = llm_checks.compare_with_rules(modelo, reglas)

    assert (a.price, a.currency) == ("difiere", "difiere")
    assert a.status == "difiere"


def test_lo_que_las_reglas_no_produjeron_es_sin_regla_y_no_bloquea_el_coincide() -> None:
    modelo = [_fila(POLLO, "200", "CUP", "1", "lb")]
    reglas = [_fila(POLLO, "200", "CUP", None, None)]

    (a,) = llm_checks.compare_with_rules(modelo, reglas)

    assert (a.quantity, a.unit) == ("sin_regla", "sin_regla")
    assert (a.product, a.price, a.currency) == ("coincide",) * 3
    assert a.status == "coincide"


def test_el_modelo_sin_cantidad_donde_las_reglas_la_tienen_difiere() -> None:
    modelo = [_fila(POLLO, "200", "CUP", None, None)]
    reglas = [_fila(POLLO, "200", "CUP", "1", "lb")]

    (a,) = llm_checks.compare_with_rules(modelo, reglas)

    assert (a.quantity, a.unit) == ("difiere", "difiere")
    assert a.status == "difiere"


def test_fila_sin_fila_de_reglas_es_sin_regla_en_todo() -> None:
    # Las reglas solo sacaron Arroz: la fila de Pollo no tiene con qué compararse.
    modelo = [_fila(ARROZ, "100", "CUP", "1", "lb"), _fila(POLLO, "200", "CUP", "1", "lb")]
    reglas = [_fila(ARROZ, "100", "CUP", "1", "lb")]

    arroz, pollo = llm_checks.compare_with_rules(modelo, reglas)

    assert arroz.status == "coincide"
    assert (pollo.product, pollo.price, pollo.currency, pollo.quantity, pollo.unit) == (
        "sin_regla",
    ) * 5
    assert pollo.status == "sin_regla"


def test_sin_ninguna_regla_todo_es_sin_regla() -> None:
    (a,) = llm_checks.compare_with_rules([_fila(ARROZ, "100")], [])

    assert a.status == "sin_regla"


def test_usa_el_mismo_emparejado_por_mejor_ajuste_que_h0() -> None:
    # Dos filas de Arroz en orden cruzado: por mejor ajuste cada una encuentra la suya.
    modelo = [_fila(ARROZ, "100", "CUP", "1", "lb"), _fila(ARROZ, "5000", "CUP", "25", "lb")]
    reglas = [_fila(ARROZ, "5000", "CUP", "25", "lb"), _fila(ARROZ, "100", "CUP", "1", "lb")]

    a, b = llm_checks.compare_with_rules(modelo, reglas)

    assert a.status == "coincide"
    assert b.status == "coincide"


# --- slice 3d: base del precio, formato de venta y envase ------------------


def _con(fila: ExtractionProposal, **campos) -> ExtractionProposal:
    from dataclasses import replace

    return replace(fila, **campos)


def test_base_del_precio_se_compara_con_la_de_las_reglas() -> None:
    # Las reglas leyeron «Sale 515 Cup Lb» y dicen `unit`; el modelo dice `total`.
    modelo = [_con(_fila(POLLO, "25750", "CUP", "50", "lb"), price_basis="total")]
    reglas = [_con(_fila(POLLO, "25750", "CUP", "50", "lb"), price_basis="unit")]
    igual = [_con(_fila(POLLO, "25750", "CUP", "50", "lb"), price_basis="total")]

    (difiere,) = llm_checks.compare_with_rules(modelo, reglas)
    (coincide,) = llm_checks.compare_with_rules(modelo, igual)

    assert difiere.price_basis == "difiere"
    assert coincide.price_basis == "coincide"
    assert difiere.fields()["price_basis"] == "difiere"


def test_base_sin_respuesta_de_las_reglas_es_sin_regla() -> None:
    modelo = [_con(_fila(POLLO, "27300", "CUP", "40", "lb"), price_basis="total")]
    reglas = [_fila(POLLO, "27300", "CUP", "40", "lb")]

    (a,) = llm_checks.compare_with_rules(modelo, reglas)

    assert a.price_basis == "sin_regla"


def test_formato_y_envase_no_tienen_contraparte_en_las_reglas() -> None:
    modelo = [
        _con(
            _fila(POLLO, "27300", "CUP", "40", "lb"),
            sale_format="retail", packaging="caja",
        )
    ]
    reglas = [_fila(POLLO, "27300", "CUP", "40", "lb")]

    (a,) = llm_checks.compare_with_rules(modelo, reglas)

    assert (a.sale_format, a.packaging) == ("sin_regla", "sin_regla")
    assert {"sale_format", "packaging", "price_basis"} <= set(a.fields())


def test_status_resume_solo_los_campos_de_h0() -> None:
    """La base que difiere no cambia `status`: el resumen es la señal medida
    en el slice 2 contra la extracción completa de H0, que no incluye la base.
    La base se marca por campo.
    """
    modelo = [_con(_fila(ARROZ, "100", "CUP", "1", "lb"), price_basis="total")]
    reglas = [_con(_fila(ARROZ, "100", "CUP", "1", "lb"), price_basis="unit")]

    (a,) = llm_checks.compare_with_rules(modelo, reglas)

    assert a.price_basis == "difiere"
    assert a.status == "coincide"


# --- match_rule_rows --------------------------------------------------------


def test_match_rule_rows_devuelve_la_fila_de_las_reglas_que_le_toca() -> None:
    regla_arroz = _fila(ARROZ, "500", cantidad="25", unidad="lb")
    regla_pollo = _fila(POLLO, "800", cantidad="10", unidad="lb")
    modelo = [_fila(POLLO, "800"), _fila(ARROZ, "500")]

    emparejadas = llm_checks.match_rule_rows(modelo, [regla_arroz, regla_pollo])

    # Mismo orden que `modelo`: la primera fila es Pollo, la segunda Arroz.
    assert emparejadas == [regla_pollo, regla_arroz]


def test_match_rule_rows_da_none_cuando_las_reglas_no_tienen_esa_fila() -> None:
    modelo = [_fila(POLLO, "800")]

    assert llm_checks.match_rule_rows(modelo, []) == [None]


# --- highlight_segments -----------------------------------------------------


def test_highlight_marca_el_fragmento_exacto() -> None:
    segmentos = llm_checks.highlight_segments("Vendo Arroz 25 lb a 500\nLlamar", ["Arroz 25 lb a 500"])

    assert segmentos == [("Vendo ", False), ("Arroz 25 lb a 500", True), ("\nLlamar", False)]


def test_highlight_sin_fragmentos_devuelve_el_texto_entero() -> None:
    assert llm_checks.highlight_segments("Vendo arroz", []) == [("Vendo arroz", False)]


def test_highlight_omite_en_silencio_lo_que_no_esta_literal() -> None:
    # Distinta capitalización: la coincidencia es EXACTA, no normalizada.
    segmentos = llm_checks.highlight_segments("Vendo Arroz 25 lb", ["arroz 25 lb", "Pollo"])

    assert segmentos == [("Vendo Arroz 25 lb", False)]


def test_highlight_une_fragmentos_que_se_solapan_y_ordena_los_separados() -> None:
    texto = "AAA BBB CCC DDD"

    segmentos = llm_checks.highlight_segments(texto, ["CCC DDD", "AAA BBB", "BBB CCC"])

    # Los tres se encadenan por solapamiento: todo el texto queda marcado.
    assert segmentos == [("AAA BBB CCC DDD", True)]


def test_highlight_no_pierde_ni_duplica_texto() -> None:
    texto = "uno dos tres cuatro"

    segmentos = llm_checks.highlight_segments(texto, ["tres", "uno"])

    assert segmentos == [("uno", True), (" dos ", False), ("tres", True), (" cuatro", False)]
    assert "".join(s for s, _ in segmentos) == texto


def test_highlight_ignora_fragmentos_vacios() -> None:
    assert llm_checks.highlight_segments("abc", ["", "  "]) == [("abc", False)]
