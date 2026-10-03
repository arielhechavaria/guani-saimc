"""Motor de validación §6.2 y carriles (`saimc.validation`), slice A5b.

Los 23 casos de «Casos esperados» de `docs/spec-motor-validacion.md`, uno por
test, con el carril y el estado tal como figuran en esa tabla. Las expectativas
salen de la especificación aprobada, no de correr el motor.

Cada `AdContext` se arma a mano: las filas del modelo, las de las reglas, las
referencias de la celda y el historial del canal. Nada toca la base.

Los parámetros de `CONFIG` son valores DE PRUEBA declarados acá: el sistema no
tiene valores por defecto (los fija A5c), y sin configuración todo va al
carril 2 (caso 23).
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from saimc.extraction.engine import ExtractionProposal
from saimc.extraction.llm_checks import RowMeta
from saimc.extraction.llm_propose_ad import AdProposal
from saimc.models import Product, Province
from saimc.validation import (
    AdContext,
    AdValidation,
    CellReference,
    ChannelContext,
    ExternalSignal,
    Outcome,
    RecentRow,
    RowContext,
    ValidationConfig,
    validate_ad,
)

# --- Catálogo del caso (spec, «Casos esperados») -----------------------------

ARROZ = Product(id=uuid.uuid4(), name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True)
POLLO = Product(id=uuid.uuid4(), name="Pollo", category="carnes", unit="lb", aliases=[], is_pilot=True)
ACEITE = Product(id=uuid.uuid4(), name="Aceite", category="aceites", unit="l", aliases=[], is_pilot=True)
PILOTOS = (ARROZ, POLLO, ACEITE)

HABANA = Province(id=uuid.uuid4(), code="la-habana", name="La Habana")
SANTIAGO = Province(id=uuid.uuid4(), code="santiago-de-cuba", name="Santiago de Cuba")
PROVINCIAS = frozenset({HABANA.id, SANTIAGO.id})

# Valores de prueba, no del sistema.
CONFIG = ValidationConfig(
    min_cell_n=5,
    channel_min_history=10,
    channel_max_edit_rate=0.2,
    signal_high_probability=0.9,
)

# Canal «Mercado Habana»: provincia La Habana, historial limpio (40 decisiones
# individuales; el operador cambió 2 de 50 filas propuestas: 4%, bajo el 20%).
MERCADO_HABANA = ChannelContext(
    province_id=HABANA.id, decisions=40, proposed_rows=50, changed_rows=2
)


def _precios(*valores: str) -> tuple[Decimal, ...]:
    return tuple(Decimal(v) for v in valores)


# Celdas: más referencias que `min_cell_n` (6 > 5), ya en CUP por unidad base.
REF_ARROZ = _precios("300", "310", "320", "325", "330", "340")
REF_POLLO = _precios("400", "420", "430", "440", "450", "460")
CELDA_ARROZ = CellReference(own_province=REF_ARROZ, all_provinces=REF_ARROZ)
CELDA_POLLO = CellReference(own_province=REF_POLLO, all_provinces=REF_POLLO)

AHORA = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def _fila(
    producto: Product | None = ARROZ,
    precio: str | None = "320",
    *,
    moneda: str = "CUP",
    cantidad: str | None = "1",
    unidad: str | None = "lb",
    provincia: Province | None = HABANA,
    base: str | None = None,
) -> ExtractionProposal:
    return ExtractionProposal(
        product=producto,
        price=None if precio is None else Decimal(precio),
        currency=moneda,
        quantity=None if cantidad is None else Decimal(cantidad),
        unit=unidad,
        province=provincia,
        price_basis=base,
    )


# Cita verificada y moneda dicha en el texto.
META_OK = RowMeta(
    evidence="cita", currency_explicit=True, evidence_found=True, price_in_evidence=True
)


def _modelo(*filas: ExtractionProposal, metas: tuple[RowMeta, ...] | None = None) -> AdProposal:
    return AdProposal(
        rows=list(filas),
        row_meta=list(metas if metas is not None else (META_OK,) * len(filas)),
        discard=False,
    )


def _descarte(motivo: str) -> AdProposal:
    return AdProposal(discard=True, discard_reason=motivo)


def _ctx(
    texto: str = "Arroz 320 CUP la libra, Habana",
    *,
    modelo: AdProposal | None,
    reglas: tuple[ExtractionProposal, ...] = (),
    celdas: tuple[CellReference, ...] = (),
    tipo: str = "text",
    canal: ChannelContext | None = MERCADO_HABANA,
    recientes: tuple[RecentRow, ...] = (),
    senales: tuple[ExternalSignal, ...] = (),
) -> AdContext:
    return AdContext(
        content_type=tipo,
        text=texto,
        published_at=AHORA,
        channel=canal,
        model=modelo,
        rule_rows=reglas,
        pilot_products=PILOTOS,
        province_ids=PROVINCIAS,
        row_contexts=tuple(RowContext(cell=c) for c in celdas),
        recent_channel_rows=recientes,
        signals=senales,
    )


def _caso_1(**cambios: Any) -> AdContext:
    """«Arroz 320 CUP la libra, Habana»: modelo y reglas dicen lo mismo."""
    argumentos: dict[str, Any] = {
        "modelo": _modelo(_fila(base="unit")),
        "reglas": (_fila(),),
        "celdas": (CELDA_ARROZ,),
    }
    argumentos.update(cambios)
    return _ctx(**argumentos)


def _estados(resultado: AdValidation) -> list[str]:
    return [fila.status for fila in resultado.rows]


# --- Los 23 casos de la especificación ---------------------------------------


def test_caso_01_todo_en_pass_va_al_carril_1() -> None:
    resultado = validate_ad(_caso_1(), CONFIG)

    assert resultado.lane == 1
    assert _estados(resultado) == ["approved"]
    assert resultado.rows[0].confidence_score == 100


def test_caso_02_off_focus_sin_piloto_va_al_carril_0() -> None:
    ctx = _ctx("Vendo iPhone 13, 450 USD", modelo=_descarte("off_focus"))

    resultado = validate_ad(ctx, CONFIG)

    assert resultado.lane == 0
    assert resultado.rows == ()


def test_caso_03_no_data_sin_piloto_va_al_carril_0() -> None:
    ctx = _ctx("Buenos días grupo", modelo=_descarte("no_data"))

    resultado = validate_ad(ctx, CONFIG)

    assert resultado.lane == 0
    assert resultado.rows == ()


def test_caso_04_descarte_con_piloto_en_el_texto_va_a_revision() -> None:
    ctx = _ctx("Arroz importado, escribir al privado", modelo=_descarte("no_data"))

    resultado = validate_ad(ctx, CONFIG)

    assert resultado.lane == 2
    assert resultado.rows == ()


def test_caso_05_precio_fuera_de_la_banda_es_anomaly() -> None:
    ctx = _caso_1(
        texto="Arroz 3200 CUP la libra",
        modelo=_modelo(_fila(precio="3200", base="unit")),
        reglas=(_fila(precio="3200"),),
    )

    resultado = validate_ad(ctx, CONFIG)

    assert resultado.lane == 2
    assert _estados(resultado) == ["anomaly"]


def test_caso_06_moneda_asumida_no_coincide_y_queda_unverified() -> None:
    # El modelo pone CUP sin que el texto lo diga. `ExtractionProposal` no
    # puede decir «sin moneda»: la fila de las reglas trae su CUP de relleno,
    # igual que en producción.
    ctx = _caso_1(
        texto="Arroz 320 la libra",
        modelo=_modelo(_fila(base="unit"), metas=(replace(META_OK, currency_explicit=False),)),
    )

    resultado = validate_ad(ctx, CONFIG)

    assert resultado.lane == 2
    assert _estados(resultado) == ["unverified"]


def test_caso_07_abstencion_en_cantidad_y_unidad_queda_unverified() -> None:
    ctx = _caso_1(
        texto="Arroz 320 CUP",
        modelo=_modelo(_fila(cantidad=None, unidad=None)),
        reglas=(_fila(cantidad=None, unidad=None),),
    )

    resultado = validate_ad(ctx, CONFIG)

    assert resultado.lane == 2
    assert _estados(resultado) == ["unverified"]


def test_caso_08_una_fila_sin_cita_verificada_manda_todo_a_revision() -> None:
    sin_cita = replace(META_OK, evidence_found=False, price_in_evidence=False)
    ctx = _ctx(
        "Arroz 320 CUP lb\nPollo 430 CUP lb",
        modelo=_modelo(
            _fila(base="unit"),
            _fila(POLLO, "430", base="unit"),
            metas=(META_OK, sin_cita),
        ),
        reglas=(_fila(), _fila(POLLO, "430")),
        celdas=(CELDA_ARROZ, CELDA_POLLO),
    )

    resultado = validate_ad(ctx, CONFIG)

    assert resultado.lane == 2
    assert _estados(resultado) == ["approved", "unverified"]


def test_caso_09_canal_sin_historial_queda_unverified() -> None:
    sin_historial = ChannelContext(
        province_id=HABANA.id, decisions=0, proposed_rows=0, changed_rows=0
    )
    ctx = _caso_1(texto="Arroz 320 CUP lb", canal=sin_historial)

    resultado = validate_ad(ctx, CONFIG)

    assert resultado.lane == 2
    assert _estados(resultado) == ["unverified"]
    assert resultado.rows[0].trust == Outcome.UNKNOWN


def test_caso_10_canal_con_historial_sucio_es_untrusted_source() -> None:
    # 30 de 50 filas propuestas cambiadas por el operador: 60%, sobre el 20%.
    sucio = ChannelContext(province_id=HABANA.id, decisions=40, proposed_rows=50, changed_rows=30)
    ctx = _caso_1(texto="Arroz 320 CUP lb", canal=sucio)

    resultado = validate_ad(ctx, CONFIG)

    assert resultado.lane == 2
    assert _estados(resultado) == ["untrusted_source"]


def test_caso_11_provincia_distinta_de_la_del_canal_queda_unverified() -> None:
    # Santiago no tiene referencias propias: la celda se repliega a todas.
    ctx = _caso_1(
        texto="Arroz 320 CUP lb, Santiago",
        modelo=_modelo(_fila(provincia=SANTIAGO, base="unit")),
        reglas=(_fila(provincia=SANTIAGO),),
        celdas=(CellReference(own_province=(), all_provinces=REF_ARROZ),),
    )

    resultado = validate_ad(ctx, CONFIG)

    assert resultado.lane == 2
    assert _estados(resultado) == ["unverified"]
    assert resultado.rows[0].location == Outcome.UNKNOWN


def _repetido(horas: float) -> RecentRow:
    return RecentRow(
        product_id=ARROZ.id,
        province_id=HABANA.id,
        price=Decimal("320"),
        published_at=AHORA - timedelta(hours=horas),
    )


def test_caso_12_repetido_a_las_3_horas_es_possible_duplicate() -> None:
    ctx = _caso_1(texto="Arroz 320 CUP lb", recientes=(_repetido(3),))

    resultado = validate_ad(ctx, CONFIG)

    assert resultado.lane == 2
    assert _estados(resultado) == ["possible_duplicate"]


def test_caso_13_repetido_a_las_30_horas_va_al_carril_1() -> None:
    ctx = _caso_1(texto="Arroz 320 CUP lb", recientes=(_repetido(30),))

    resultado = validate_ad(ctx, CONFIG)

    assert resultado.lane == 1
    assert _estados(resultado) == ["approved"]


def test_caso_14_saco_con_base_unit_es_inconsistent() -> None:
    # 32000 «por libra» queda a ×100 de la celda; leído como total da 320.
    ctx = _caso_1(
        texto="Saco de arroz 100 lb, 32000 CUP",
        modelo=_modelo(_fila(precio="32000", cantidad="100", base="unit")),
        reglas=(_fila(precio="32000", cantidad="100"),),
    )

    resultado = validate_ad(ctx, CONFIG)

    assert resultado.lane == 2
    assert _estados(resultado) == ["inconsistent"]


def test_caso_15_saco_con_base_total_va_al_carril_1() -> None:
    ctx = _caso_1(
        texto="Saco de arroz 100 lb, 32000 CUP",
        modelo=_modelo(_fila(precio="32000", cantidad="100", base="total")),
        reglas=(_fila(precio="32000", cantidad="100"),),
    )

    resultado = validate_ad(ctx, CONFIG)

    assert resultado.lane == 1
    assert _estados(resultado) == ["approved"]


def test_caso_16_celda_con_menos_de_min_cell_n_queda_unverified() -> None:
    pocas = _precios("880", "910")
    ctx = _ctx(
        "Aceite 900 CUP el litro",
        modelo=_modelo(_fila(ACEITE, "900", unidad="l", base="unit")),
        reglas=(_fila(ACEITE, "900", unidad="l"),),
        celdas=(CellReference(own_province=pocas, all_provinces=pocas),),
    )

    resultado = validate_ad(ctx, CONFIG)

    assert resultado.lane == 2
    assert _estados(resultado) == ["unverified"]
    assert resultado.rows[0].price == Outcome.UNKNOWN
    assert resultado.rows[0].quantity_price == Outcome.UNKNOWN


def test_caso_17_imagen_con_todo_en_pass_no_entra_al_carril_1() -> None:
    ctx = _caso_1(texto="Arroz 320 CUP lb", tipo="image")

    resultado = validate_ad(ctx, CONFIG)

    assert resultado.lane == 2
    assert _estados(resultado) == ["approved"]


def test_caso_18_imagen_off_focus_sin_piloto_va_al_carril_0() -> None:
    ctx = _ctx("Zapatos Nike talla 42", modelo=_descarte("off_focus"), tipo="image")

    resultado = validate_ad(ctx, CONFIG)

    assert resultado.lane == 0
    assert resultado.rows == ()


def test_caso_19_sin_propuesta_del_modelo_va_a_revision() -> None:
    ctx = _caso_1(modelo=None, celdas=())

    resultado = validate_ad(ctx, CONFIG)

    assert resultado.lane == 2
    assert resultado.rows == ()


def test_caso_20_propuesta_degradada_va_a_revision() -> None:
    ctx = _caso_1(modelo=AdProposal(degraded=True, failure="timeout"), celdas=())

    resultado = validate_ad(ctx, CONFIG)

    assert resultado.lane == 2
    assert resultado.rows == ()


def test_caso_21_senal_externa_en_desacuerdo_saca_del_carril_1() -> None:
    senal = ExternalSignal(row_index=0, field="product", value=POLLO.id, probability=0.95)
    ctx = _caso_1(texto="Arroz 320 CUP lb", senales=(senal,))

    resultado = validate_ad(ctx, CONFIG)

    assert resultado.lane == 2
    assert _estados(resultado) == ["approved"]


def test_caso_22_senal_externa_de_acuerdo_no_mete_en_ningun_carril() -> None:
    senal = ExternalSignal(row_index=0, field="currency", value="CUP", probability=0.99)
    ctx = _caso_1(
        texto="Arroz 320 la libra",
        modelo=_modelo(_fila(base="unit"), metas=(replace(META_OK, currency_explicit=False),)),
        senales=(senal,),
    )

    resultado = validate_ad(ctx, CONFIG)

    assert resultado.lane == 2
    assert _estados(resultado) == ["unverified"]


def test_caso_23_sin_configuracion_no_se_automatiza() -> None:
    resultado = validate_ad(_caso_1(), None)

    assert resultado.lane == 2
    assert _estados(resultado) == ["unverified"]


# --- Lo que la tabla no cubre -------------------------------------------------


def test_sin_configuracion_tampoco_hay_carril_0() -> None:
    ctx = _ctx("Vendo iPhone 13, 450 USD", modelo=_descarte("off_focus"))

    assert validate_ad(ctx, None).lane == 2


def test_la_configuracion_no_tiene_valores_por_defecto() -> None:
    with pytest.raises(TypeError):
        ValidationConfig()  # type: ignore[call-arg]


def test_todo_carril_2_explica_por_que_en_castellano() -> None:
    resultado = validate_ad(_caso_1(texto="Arroz 320 CUP lb", tipo="image"), CONFIG)

    assert resultado.lane == 2
    assert any("imagen" in motivo for motivo in resultado.reasons)


def test_el_detalle_tiene_la_forma_de_la_spec_mas_quantity_price() -> None:
    """JSON de §6.2 con `pass|fail|unknown` y la clave de la regla 6."""
    fila = validate_ad(_caso_1(), CONFIG).rows[0]

    assert fila.detail() == {
        "validation_status": "approved",
        "confidence_score": 100,
        "rules": {
            "structure": "pass",
            "price": "pass",
            "location": "pass",
            "trust": "pass",
            "duplicate": "pass",
            "quantity_price": "pass",
        },
    }


def test_confidence_score_es_reglas_en_pass_sobre_seis() -> None:
    """Caso 9: solo la regla 4 queda en `unknown` -> 5 de 6 -> 83."""
    sin_historial = ChannelContext(
        province_id=HABANA.id, decisions=0, proposed_rows=0, changed_rows=0
    )

    fila = validate_ad(_caso_1(canal=sin_historial), CONFIG).rows[0]

    assert fila.confidence_score == 83


def test_si_fallan_varias_gana_la_primera_y_el_detalle_guarda_todas() -> None:
    """Precio fuera de banda, canal sucio y repetido: gana `anomaly`."""
    sucio = ChannelContext(province_id=HABANA.id, decisions=40, proposed_rows=50, changed_rows=30)
    repetido = replace(_repetido(1), price=Decimal("3200"))
    ctx = _caso_1(
        modelo=_modelo(_fila(precio="3200", base="unit")),
        reglas=(_fila(precio="3200"),),
        canal=sucio,
        recientes=(repetido,),
    )

    fila = validate_ad(ctx, CONFIG).rows[0]

    assert fila.status == "anomaly"
    assert fila.price == Outcome.FAIL
    assert fila.trust == Outcome.FAIL
    assert fila.duplicate == Outcome.FAIL


def test_fila_sin_provincia_es_incomplete() -> None:
    ctx = _caso_1(
        modelo=_modelo(_fila(provincia=None, base="unit")),
        reglas=(_fila(provincia=None),),
    )

    resultado = validate_ad(ctx, CONFIG)

    assert resultado.lane == 2
    assert _estados(resultado) == ["incomplete"]


def test_producto_fuera_de_los_pilotos_es_incomplete() -> None:
    frijol = Product(id=uuid.uuid4(), name="Frijol", category="granos", unit="lb", aliases=[])
    ctx = _caso_1(
        modelo=_modelo(_fila(frijol, base="unit")),
        reglas=(_fila(frijol),),
    )

    assert _estados(validate_ad(ctx, CONFIG)) == ["incomplete"]


def test_provincia_fuera_del_catalogo_es_untrusted_source() -> None:
    """Regla 3 en `fail`: la provincia no es del catálogo."""
    otra = Province(id=uuid.uuid4(), code="miami", name="Miami")
    ctx = _caso_1(
        modelo=_modelo(_fila(provincia=otra, base="unit")),
        reglas=(_fila(provincia=otra),),
    )

    fila = validate_ad(ctx, CONFIG).rows[0]

    assert fila.location == Outcome.FAIL
    assert fila.status == "untrusted_source"


def test_canal_sin_provincia_deja_la_regla_3_en_unknown() -> None:
    canal = replace(MERCADO_HABANA, province_id=None)

    fila = validate_ad(_caso_1(canal=canal), CONFIG).rows[0]

    assert fila.location == Outcome.UNKNOWN
    assert fila.status == "unverified"


def test_banda_con_celda_vacia_es_unknown() -> None:
    ctx = _caso_1(celdas=(CellReference(own_province=(), all_provinces=()),))

    resultado = validate_ad(ctx, CONFIG)

    assert resultado.rows[0].price == Outcome.UNKNOWN
    assert resultado.rows[0].quantity_price == Outcome.UNKNOWN
    assert resultado.lane == 2


def test_la_celda_se_repliega_a_todas_las_provincias() -> None:
    """La provincia propia no llega a `min_cell_n` (2 < 5); todas juntas sí."""
    celda = CellReference(own_province=_precios("300", "340"), all_provinces=REF_ARROZ)

    resultado = validate_ad(_caso_1(celdas=(celda,)), CONFIG)

    assert resultado.rows[0].price == Outcome.PASS
    assert resultado.lane == 1


def test_con_referencias_propias_suficientes_manda_la_provincia() -> None:
    """El repliegue no reemplaza: 320 cae fuera de la banda de la provincia
    propia (3000-3400) aunque caería dentro de la de todas juntas."""
    cara = _precios("3000", "3100", "3200", "3250", "3300", "3400")
    celda = CellReference(own_province=cara, all_provinces=REF_ARROZ)

    fila = validate_ad(_caso_1(celdas=(celda,)), CONFIG).rows[0]

    assert fila.price == Outcome.FAIL


def test_ventana_de_24_horas_exactas_cuenta_como_duplicado() -> None:
    """El borde se lee del lado conservador: a las 24 h justas va a revisión."""
    resultado = validate_ad(_caso_1(recientes=(_repetido(24),)), CONFIG)

    assert resultado.rows[0].duplicate == Outcome.FAIL
    assert resultado.lane == 2


def test_un_minuto_despues_de_las_24_horas_ya_no_es_duplicado() -> None:
    resultado = validate_ad(_caso_1(recientes=(_repetido(24 + 1 / 60),)), CONFIG)

    assert resultado.rows[0].duplicate == Outcome.PASS
    assert resultado.lane == 1


def test_una_fila_posterior_al_anuncio_no_es_duplicado() -> None:
    resultado = validate_ad(_caso_1(recientes=(_repetido(-2),)), CONFIG)

    assert resultado.rows[0].duplicate == Outcome.PASS


@pytest.mark.parametrize(
    "cambio",
    [
        {"product_id": POLLO.id},
        {"province_id": SANTIAGO.id},
        {"price": Decimal("330")},
    ],
)
def test_el_duplicado_exige_mismo_producto_provincia_y_precio(cambio: dict) -> None:
    resultado = validate_ad(_caso_1(recientes=(replace(_repetido(3), **cambio),)), CONFIG)

    assert resultado.rows[0].duplicate == Outcome.PASS


def test_filas_caidas_del_modelo_sacan_del_carril_1() -> None:
    modelo = replace(_modelo(_fila(base="unit")), dropped=1)

    resultado = validate_ad(_caso_1(modelo=modelo), CONFIG)

    assert resultado.lane == 2
    assert _estados(resultado) == ["approved"]


def test_reglas_y_modelo_con_precio_distinto_van_a_revision() -> None:
    resultado = validate_ad(_caso_1(reglas=(_fila(precio="350"),)), CONFIG)

    assert resultado.lane == 2
    assert _estados(resultado) == ["unverified"]


def test_sin_fila_de_las_reglas_no_hay_acuerdo() -> None:
    resultado = validate_ad(_caso_1(reglas=()), CONFIG)

    assert resultado.lane == 2
    assert _estados(resultado) == ["unverified"]


def test_abstencion_en_price_basis_va_a_revision() -> None:
    resultado = validate_ad(_caso_1(modelo=_modelo(_fila(base=None))), CONFIG)

    assert resultado.lane == 2
    assert _estados(resultado) == ["unverified"]


def test_descarte_con_motivo_fuera_de_la_lista_va_a_revision() -> None:
    ctx = _ctx("Vendo iPhone 13, 450 USD", modelo=_descarte("wholesale"))

    assert validate_ad(ctx, CONFIG).lane == 2


def test_propuesta_sin_veredicto_ni_filas_va_a_revision() -> None:
    ctx = _ctx("Vendo iPhone 13, 450 USD", modelo=AdProposal(discard=False))

    assert validate_ad(ctx, CONFIG).lane == 2


def test_senal_en_desacuerdo_con_probabilidad_baja_no_saca_del_carril_1() -> None:
    senal = ExternalSignal(row_index=0, field="product", value=POLLO.id, probability=0.4)

    assert validate_ad(_caso_1(senales=(senal,)), CONFIG).lane == 1


def test_sin_probabilidad_alta_fijada_todo_desacuerdo_saca_del_carril_1() -> None:
    """El parámetro lo fija A6b; mientras sea `None` gana la lectura conservadora."""
    senal = ExternalSignal(row_index=0, field="product", value=POLLO.id, probability=0.4)
    config = replace(CONFIG, signal_high_probability=None)

    assert validate_ad(_caso_1(senales=(senal,)), config).lane == 2


@pytest.mark.parametrize(
    "senal",
    [
        # Pregunta que el contrato no admite: nada sobre el precio.
        ExternalSignal(row_index=0, field="price", value=Decimal("999"), probability=0.99),
        # Probabilidad de tipo inesperado.
        ExternalSignal(row_index=0, field="product", value=POLLO.id, probability="alta"),  # type: ignore[arg-type]
        # Probabilidad fuera de 0..1.
        ExternalSignal(row_index=0, field="product", value=POLLO.id, probability=7.0),
        # Respuesta ausente.
        ExternalSignal(row_index=0, field="product", value=None, probability=0.99),
        # Fila que no existe.
        ExternalSignal(row_index=5, field="product", value=POLLO.id, probability=0.99),
    ],
)
def test_senal_ausente_o_de_tipo_inesperado_equivale_a_sin_senal(senal: ExternalSignal) -> None:
    assert validate_ad(_caso_1(senales=(senal,)), CONFIG).lane == 1


def test_precio_en_divisa_sin_conversion_deja_la_banda_en_unknown() -> None:
    """Sin `price_cup` la fila no es comparable con su celda, que está en CUP."""
    ctx = _caso_1(
        texto="Arroz 1 USD la libra, Habana",
        modelo=_modelo(_fila(precio="1", moneda="USD", base="unit")),
        reglas=(_fila(precio="1", moneda="USD"),),
    )

    fila = validate_ad(ctx, CONFIG).rows[0]

    assert fila.price == Outcome.UNKNOWN
    assert fila.quantity_price == Outcome.UNKNOWN


def test_precio_en_divisa_con_conversion_se_compara_en_cup() -> None:
    ctx = _caso_1(
        texto="Arroz 1 USD la libra, Habana",
        modelo=_modelo(_fila(precio="1", moneda="USD", base="unit")),
        reglas=(_fila(precio="1", moneda="USD"),),
    )
    ctx = replace(ctx, row_contexts=(RowContext(cell=CELDA_ARROZ, price_cup=Decimal("320")),))

    resultado = validate_ad(ctx, CONFIG)

    assert resultado.rows[0].price == Outcome.PASS
    assert resultado.lane == 1


def test_el_precio_se_normaliza_a_la_unidad_base_del_producto() -> None:
    """705 CUP el kilo son 319,8 la libra: dentro de la banda del arroz por libra."""
    ctx = _caso_1(
        texto="Arroz 705 CUP el kg, Habana",
        modelo=_modelo(_fila(precio="705", unidad="kg", base="unit")),
        reglas=(_fila(precio="705", unidad="kg"),),
    )

    assert validate_ad(ctx, CONFIG).rows[0].price == Outcome.PASS


def test_las_filas_y_sus_contextos_tienen_que_ir_en_paralelo() -> None:
    with pytest.raises(ValueError):
        validate_ad(_caso_1(celdas=()), CONFIG)


# --- B2a: el carril 0 solo, sin configuración (docs/spec-slice-B2a.md) --------


def test_decide_lane_0_descarta_sin_configuracion_con_los_mismos_predicados() -> None:
    from saimc.validation import decide_lane_0

    for motivo in ("off_focus", "no_data"):
        for tipo in ("text", "image"):
            ctx = _ctx("Vendo iPhone 13, 450 USD", modelo=_descarte(motivo), tipo=tipo)
            assert decide_lane_0(ctx).lane == 0


def test_decide_lane_0_manda_a_revision_todo_lo_demas() -> None:
    from saimc.validation import decide_lane_0

    casos = [
        _ctx("Vendo iPhone 13", modelo=None),
        _ctx("Vendo iPhone 13", modelo=AdProposal(degraded=True, failure="timeout")),
        _ctx("Vendo iPhone 13", modelo=AdProposal(discard=None)),
        _ctx("Vendo iPhone 13", modelo=_modelo(_fila())),  # no descarta
        _ctx("Vendo iPhone 13", modelo=AdProposal(discard=True, discard_reason=None)),
        _ctx("Arroz 320 la libra", modelo=_descarte("off_focus")),  # nombra un piloto
        _ctx(
            "Vendo iPhone 13",
            modelo=AdProposal(discard=True, discard_reason="off_focus", degraded=True),
        ),
    ]
    for ctx in casos:
        resultado = decide_lane_0(ctx)
        assert resultado.lane == 2
        assert resultado.reasons


def test_validate_ad_sigue_sin_carril_0_sin_configuracion() -> None:
    ctx = _ctx("Vendo iPhone 13, 450 USD", modelo=_descarte("off_focus"))

    assert validate_ad(ctx, None).lane == 2
    assert validate_ad(ctx, CONFIG).lane == 0
