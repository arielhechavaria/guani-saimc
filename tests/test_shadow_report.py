"""Informe del modo sombra (`saimc.validation.shadow`) y armado del contexto
(`saimc.validation.context`), slice A5c.

Los valores esperados están escritos A MANO acá, antes y con independencia del
código: las cotas de Clopper-Pearson salen de `docs/spec-motor-validacion.md`
(«Protocolo del modo sombra») y los conteos, de contar el fixture a ojo. Ningún
esperado sale de correr el informe.

Los parámetros de `CONFIG` son valores DE PRUEBA: el sistema no trae ninguno
por defecto.

Al final, el bloque «Jev» del informe (slice A6b-2): casos 27 a 31 de «Casos
esperados» de `docs/spec-slice-A6b.md`, con los esperados también a mano.
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from saimc.models import (
    Channel,
    ContentType,
    CurationAction,
    CurationEvent,
    CurationMode,
    JevSignal,
    LlmProposal,
    LlmReview,
    PriceHistory,
    ProcessingStatus,
    Product,
    Province,
    RawIngestion,
    ReviewOutcome,
    SourceType,
)
from saimc.validation import (
    LANE_ACCEPT,
    LANE_DISCARD,
    LANE_REVIEW,
    AdContext,
    ValidationConfig,
    context,
    jev_client,
    jev_signal,
    shadow,
)
from tests import test_validation_engine as motor
from tests.jev_fakes import ANSWERS_OK, choice, noul
from tests.test_jev_signal import _lectura

CONFIG = ValidationConfig(
    min_cell_n=3,
    channel_min_history=1,
    channel_max_edit_rate=0.5,
    signal_high_probability=None,
    lane1_max_rows=None,
)

# El anuncio bajo prueba: 10 de septiembre de 2026, mediodía de Cuba.
AD_AT = datetime(2026, 9, 10, 16, 0, tzinfo=UTC)


# --- Cálculo puro: Clopper-Pearson -------------------------------------------


@pytest.mark.parametrize(
    ("errores", "n", "cota_pct"),
    [
        # Los tres de la especificación y el del oro v6 (1 falso descarte en 49).
        (0, 60, "4.9"),
        (0, 30, "9.5"),
        (1, 49, "9.3"),
        (3, 150, "5.1"),
    ],
)
def test_cota_superior_de_clopper_pearson_contra_valores_conocidos(
    errores: int, n: int, cota_pct: str
) -> None:
    cota = shadow.clopper_pearson_upper(errores, n)
    assert cota is not None
    assert f"{100 * cota:.1f}" == cota_pct


def test_cota_sin_muestra_no_existe_y_con_todo_errores_es_uno() -> None:
    assert shadow.clopper_pearson_upper(0, 0) is None
    assert shadow.clopper_pearson_upper(7, 7) == 1.0


def test_un_porcentaje_nunca_se_imprime_sin_su_n_y_su_cota() -> None:
    texto = shadow.format_rate(1, 49)
    assert "1/49" in texto
    assert "2,0 %" in texto
    assert "9,3 %" in texto
    # Sin muestra no hay porcentaje que mostrar.
    assert shadow.format_rate(0, 0) == "0/0 (sin muestra)"


# --- Cálculo puro: qué es un error del carril 1 ------------------------------

_PROPUESTA = {
    "product_id": "p-arroz",
    "price": "500",
    "currency": "CUP",
    "quantity": "1",
    "unit": "lb",
    "province_id": None,
    "sale_format": "retail",
    "price_basis": "unit",
    "packaging": None,
}


def _entrada(indice: int | None, final: dict[str, Any] | None, **propuesto: Any) -> dict[str, Any]:
    return {
        "proposal_index": indice,
        "price_history_id": None,
        "proposed": None if indice is None else {**_PROPUESTA, **propuesto},
        "final": final,
        "edited": [],  # no se lee: el informe recalcula contra `final`
    }


def test_error_del_carril_1_por_campo_en_una_revision() -> None:
    igual = {**_PROPUESTA, "price": "500.00", "quantity": "1.000"}
    filas = [
        _entrada(0, igual),  # aceptada tal cual: «500» y «500.00» son el mismo precio
        _entrada(1, None),  # borrada
        _entrada(2, {**igual, "price": "550.00", "unit": "kg"}),  # editó precio y unidad
        _entrada(None, igual),  # agregada a mano
    ]
    diff = context.diff_review(filas, channel_province_id=None)
    assert diff.proposed_rows == 3
    assert diff.deleted_rows == 1
    assert diff.edited_rows == 1
    assert diff.added_rows == 1
    assert diff.changed_rows == 2
    assert diff.field_errors == {"price": 1, "unit": 1}
    assert diff.has_error


def test_revision_aceptada_tal_cual_no_es_error() -> None:
    igual = {**_PROPUESTA, "price": "500.00", "quantity": "1.000"}
    diff = context.diff_review([_entrada(0, igual)], channel_province_id=None)
    assert (diff.proposed_rows, diff.changed_rows, diff.added_rows) == (1, 0, 0)
    assert diff.field_errors == {}
    assert not diff.has_error


def test_la_provincia_que_impone_el_canal_no_cuenta_como_edicion() -> None:
    # El modelo no dio provincia y el formulario puso la del canal («el canal
    # siempre manda»): el operador no corrigió nada.
    final = {**_PROPUESTA, "province_id": "prov-habana"}
    con_canal = context.diff_review([_entrada(0, final)], channel_province_id="prov-habana")
    assert not con_canal.has_error
    # Sin provincia de canal, el mismo cambio sí lo hizo el operador.
    sin_canal = context.diff_review([_entrada(0, final)], channel_province_id=None)
    assert sin_canal.field_errors == {"province_id": 1}


def _fila_oro(producto: str, precio: str, cantidad: str = "1", unidad: str = "lb") -> dict[str, Any]:
    return {
        "product": producto,
        "price": precio,
        "currency": "CUP",
        "quantity": cantidad,
        "unit": unidad,
    }


def test_error_del_carril_1_contra_las_filas_del_oro() -> None:
    # Igual salvo el formato del número: no hay diferencia.
    igual = shadow.diff_expected(
        [_fila_oro("Arroz", "320")], [_fila_oro("Arroz", "320.00", "1.000")]
    )
    assert not igual.has_error

    # Dos filas propuestas contra una del operador: la de arroz difiere en
    # precio y cantidad; la de pollo está de más (el operador la borraría).
    diff = shadow.diff_expected(
        [_fila_oro("Arroz", "320"), _fila_oro("Pollo", "450")],
        [_fila_oro("Arroz", "300", "2")],
    )
    assert diff.proposed_rows == 2
    assert diff.edited_rows == 1
    assert diff.deleted_rows == 1
    assert diff.added_rows == 0
    assert diff.field_errors == {"price": 1, "quantity": 1}

    # Una fila del operador que el modelo no propuso: el operador la agregaría.
    falta = shadow.diff_expected(
        [_fila_oro("Arroz", "320")], [_fila_oro("Arroz", "320"), _fila_oro("Pollo", "450")]
    )
    assert (falta.added_rows, falta.changed_rows) == (1, 0)
    assert falta.has_error


def test_el_emparejado_del_oro_prefiere_la_fila_identica() -> None:
    # Dos filas de arroz en cada lado, en distinto orden: no hay diferencia.
    diff = shadow.diff_expected(
        [_fila_oro("Arroz", "320"), _fila_oro("Arroz", "300")],
        [_fila_oro("Arroz", "300"), _fila_oro("Arroz", "320")],
    )
    assert not diff.has_error


# --- Cálculo puro: conteos del resumen ---------------------------------------


def _resultado(
    carril: int,
    *,
    tipo: str = "text",
    conservado: bool = True,
    diff: context.ProposalDiff | None = None,
    motivos: tuple[str, ...] = (),
) -> shadow.CaseResult:
    return shadow.CaseResult(
        ingestion_id=str(uuid.uuid4()),
        content_type=tipo,
        lane=carril,
        kept=conservado,
        diff=diff,
        reasons=motivos,
    )


_SIN_ERROR = context.ProposalDiff(proposed_rows=2)
_CON_ERROR = context.ProposalDiff(
    proposed_rows=2, edited_rows=1, field_errors={"price": 1}
)


def test_conteos_del_resumen_escritos_a_mano() -> None:
    resultados = (
        # Carril 0: 5 textos (1 conservado por el operador) y 2 imágenes (0).
        [_resultado(LANE_DISCARD, conservado=False) for _ in range(4)]
        + [_resultado(LANE_DISCARD, conservado=True)]
        + [_resultado(LANE_DISCARD, tipo="image", conservado=False) for _ in range(2)]
        # Carril 1: 3 anuncios, 6 filas; uno con el precio editado en una fila.
        + [_resultado(LANE_ACCEPT, diff=_SIN_ERROR) for _ in range(2)]
        + [_resultado(LANE_ACCEPT, diff=_CON_ERROR)]
        # Carril 2: 10 anuncios; 3 solo por la moneda.
        + [
            _resultado(
                LANE_REVIEW,
                diff=_SIN_ERROR,
                motivos=("fila 1: reglas y modelo no coinciden en moneda",),
            )
            for _ in range(3)
        ]
        + [
            _resultado(
                LANE_REVIEW,
                diff=_SIN_ERROR,
                motivos=(
                    "fila 1: reglas y modelo no coinciden en precio, moneda",
                    "fila 2: reglas y modelo no coinciden en precio",
                    "fila 1: el canal no tiene historial suficiente",
                ),
            )
            for _ in range(7)
        ]
    )
    resumen = shadow.summarize(resultados)

    assert resumen.total == 20
    assert resumen.lanes == {0: 7, 1: 3, 2: 10}
    assert (resumen.automation.k, resumen.automation.n) == (10, 20)
    assert (resumen.false_discards_text.k, resumen.false_discards_text.n) == (1, 5)
    assert (resumen.false_discards_image.k, resumen.false_discards_image.n) == (0, 2)
    assert (resumen.lane1_ad_errors.k, resumen.lane1_ad_errors.n) == (1, 3)
    assert (resumen.lane1_field_errors["price"].k, resumen.lane1_field_errors["price"].n) == (1, 6)
    assert (resumen.lane1_field_errors["unit"].k, resumen.lane1_field_errors["unit"].n) == (0, 6)
    # Los motivos se cuentan por anuncio, no por fila, y un motivo que nombra
    # varios campos cuenta en cada uno.
    assert resumen.reasons["reglas y modelo no coinciden en moneda"] == 10
    assert resumen.reasons["reglas y modelo no coinciden en precio"] == 7
    assert resumen.reasons["el canal no tiene historial suficiente"] == 7


def test_un_descarte_del_operador_en_el_carril_1_es_un_error() -> None:
    # El motor lo habría aceptado solo y el operador lo descartó entero.
    descartado = context.ProposalDiff(proposed_rows=1, deleted_rows=1)
    resumen = shadow.summarize([_resultado(LANE_ACCEPT, conservado=False, diff=descartado)])
    assert (resumen.lane1_ad_errors.k, resumen.lane1_ad_errors.n) == (1, 1)


# --- Fixture sobre SQLite ----------------------------------------------------

_n = 0


def _ingesta(
    session: Session,
    canal: Channel | None,
    *,
    cuando: datetime = AD_AT,
    texto: str = "Arroz 320 CUP la libra",
    estado: str = ProcessingStatus.CURATED,
) -> RawIngestion:
    global _n
    _n += 1
    ingesta = RawIngestion(
        source_type=SourceType.WHATSAPP_CHANNEL,
        source_reference=canal.name if canal is not None else None,
        channel_id=canal.id if canal is not None else None,
        file_path=f"raw/{_n}.txt",
        content_hash=f"hash-{_n}",
        content_type=ContentType.TEXT,
        original_text=texto,
        processing_status=estado,
        captured_at=cuando,
    )
    session.add(ingesta)
    session.flush()
    return ingesta


def _precio(
    session: Session,
    mundo: SimpleNamespace,
    origen: RawIngestion,
    precio: str,
    dia: date,
    *,
    provincia: Province | None = None,
) -> PriceHistory:
    fila = PriceHistory(
        provenance="human",
        product_id=mundo.arroz.id,
        province_id=(provincia or mundo.habana).id,
        price=Decimal(precio),
        currency="CUP",
        quantity=Decimal(1),
        unit="lb",
        published_date=dia,
        source_id=origen.id,
    )
    session.add(fila)
    session.flush()
    return fila


def _propuesta(
    session: Session, ingesta: RawIngestion, mundo: SimpleNamespace, *, descarta: bool = False
) -> LlmProposal:
    filas = (
        []
        if descarta
        else [
            {
                "product_id": str(mundo.arroz.id),
                "price": "320",
                "currency": "CUP",
                "quantity": "1",
                "unit": "lb",
                "province_id": None,
                "sale_format": "retail",
                "price_basis": "unit",
                "packaging": None,
                "evidence": "Arroz 320 CUP la libra",
                "currency_explicit": True,
                "evidence_found": True,
                "price_in_evidence": True,
            }
        ]
    )
    propuesta = LlmProposal(
        ingestion_id=ingesta.id,
        model="modelo-de-prueba",
        prompt_version="prueba",
        status="ok",
        discard=descarta,
        discard_reason="no_data" if descarta else None,
        rows=filas,
        raw_response=None,
        input_tokens=0,
        output_tokens=0,
    )
    session.add(propuesta)
    session.flush()
    return propuesta


def _revision(
    session: Session,
    propuesta: LlmProposal,
    *,
    resultado: str,
    modo: str | None,
    filas: list[dict[str, Any]] | None = None,
    cuando: datetime | None = None,
) -> LlmReview:
    """Una revisión y, si `modo` no es `None`, su evento de bitácora."""
    revision = LlmReview(
        ingestion_id=propuesta.ingestion_id,
        proposal_id=propuesta.id,
        outcome=resultado,
        rows=filas or [],
        created_at=cuando or AD_AT + timedelta(days=5),
    )
    session.add(revision)
    if modo is not None:
        session.add(
            CurationEvent(
                ingestion_id=propuesta.ingestion_id,
                action=(
                    CurationAction.CURATED
                    if resultado == ReviewOutcome.CURATED
                    else CurationAction.DISCARDED
                ),
                mode=modo,
                proposal_id=propuesta.id,
                decided_at=revision.created_at,
            )
        )
    session.flush()
    return revision


@pytest.fixture
def mundo(session: Session) -> SimpleNamespace:
    arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True)
    habana = Province(code="la-habana", name="La Habana")
    santiago = Province(code="santiago-de-cuba", name="Santiago de Cuba")
    session.add_all([arroz, habana, santiago])
    session.flush()
    canal = Channel(name="Mercado Habana", platform="whatsapp_channel", province_id=habana.id)
    otro_canal = Channel(name="Otro canal", platform="whatsapp_channel", province_id=habana.id)
    session.add_all([canal, otro_canal])
    session.flush()
    return SimpleNamespace(
        arroz=arroz, habana=habana, santiago=santiago, canal=canal, otro_canal=otro_canal
    )


def _contexto(session: Session, anuncio: RawIngestion, mundo: SimpleNamespace, **kw: Any):
    propuesta = context.stored_proposal(session, _propuesta(session, anuncio, mundo))
    return context.build_ad_context(
        session, anuncio, model=propuesta, catalog=context.load_catalog(session), **kw
    )


# --- Armado del contexto -----------------------------------------------------


def test_un_precio_posterior_al_anuncio_no_entra_en_las_referencias(
    session: Session, mundo: SimpleNamespace
) -> None:
    viejo = _ingesta(session, mundo.canal, cuando=AD_AT - timedelta(days=10))
    _precio(session, mundo, viejo, "300", date(2026, 9, 1))
    _precio(session, mundo, viejo, "310", date(2026, 9, 9))  # el día anterior: entra
    _precio(session, mundo, viejo, "999", date(2026, 9, 10))  # el mismo día: no entra
    _precio(session, mundo, viejo, "888", date(2026, 9, 20))  # posterior: no entra
    _precio(session, mundo, viejo, "330", date(2026, 9, 2), provincia=mundo.santiago)

    anuncio = _ingesta(session, mundo.canal)
    # La fila que el operador guardó de ESTE anuncio tampoco es referencia suya.
    _precio(session, mundo, anuncio, "777", date(2026, 9, 1))

    ctx = _contexto(session, anuncio, mundo)

    assert len(ctx.row_contexts) == 1
    celda = ctx.row_contexts[0].cell
    assert sorted(celda.own_province) == [Decimal("300"), Decimal("310")]
    assert sorted(celda.all_provinces) == [Decimal("300"), Decimal("310"), Decimal("330")]


def test_las_referencias_van_por_unidad_base_del_producto(
    session: Session, mundo: SimpleNamespace
) -> None:
    viejo = _ingesta(session, mundo.canal, cuando=AD_AT - timedelta(days=10))
    # Un saco de 100 lb a 32000 CUP en total: 320 CUP por libra.
    session.add(
        PriceHistory(
            provenance="human",
            product_id=mundo.arroz.id,
            province_id=mundo.habana.id,
            price=Decimal("32000"),
            currency="CUP",
            quantity=Decimal(100),
            unit="lb",
            price_basis="total",
            published_date=date(2026, 9, 1),
            source_id=viejo.id,
        )
    )
    # Sin base del precio y con cantidad distinta de 1: no se sabe si dividir.
    session.add(
        PriceHistory(
            provenance="human",
            product_id=mundo.arroz.id,
            province_id=mundo.habana.id,
            price=Decimal("5000"),
            currency="CUP",
            quantity=Decimal(10),
            unit="lb",
            price_basis=None,
            published_date=date(2026, 9, 1),
            source_id=viejo.id,
        )
    )
    session.flush()
    anuncio = _ingesta(session, mundo.canal)

    ctx = _contexto(session, anuncio, mundo)

    assert list(ctx.row_contexts[0].cell.own_province) == [Decimal("320")]


def test_el_contexto_trae_texto_canal_y_la_provincia_del_canal_en_las_filas(
    session: Session, mundo: SimpleNamespace
) -> None:
    anuncio = _ingesta(session, mundo.canal)

    ctx = _contexto(session, anuncio, mundo)

    assert ctx.content_type == "text"
    assert ctx.text == "Arroz 320 CUP la libra"
    assert ctx.published_at == AD_AT
    assert ctx.channel is not None
    assert ctx.channel.province_id == mundo.habana.id
    # «El canal siempre manda»: el modelo no dio provincia y la fila la lleva.
    assert ctx.model is not None
    assert ctx.model.rows[0].province is not None
    assert ctx.model.rows[0].province.id == mundo.habana.id
    assert [p.id for p in ctx.pilot_products] == [mundo.arroz.id]
    assert ctx.province_ids == frozenset({mundo.habana.id, mundo.santiago.id})
    # Las reglas se recalculan sobre el mismo texto.
    assert [r.price for r in ctx.rule_rows] == [Decimal("320")]
    assert ctx.signals == ()


def test_filas_del_mismo_canal_en_las_24_h_previas(
    session: Session, mundo: SimpleNamespace
) -> None:
    hace_3h = _ingesta(session, mundo.canal, cuando=AD_AT - timedelta(hours=3))
    _precio(session, mundo, hace_3h, "320", date(2026, 9, 10))
    hace_30h = _ingesta(session, mundo.canal, cuando=AD_AT - timedelta(hours=30))
    _precio(session, mundo, hace_30h, "321", date(2026, 9, 9))
    despues = _ingesta(session, mundo.canal, cuando=AD_AT + timedelta(hours=2))
    _precio(session, mundo, despues, "322", date(2026, 9, 10))
    ajeno = _ingesta(session, mundo.otro_canal, cuando=AD_AT - timedelta(hours=1))
    _precio(session, mundo, ajeno, "323", date(2026, 9, 10))
    anuncio = _ingesta(session, mundo.canal)
    _precio(session, mundo, anuncio, "324", date(2026, 9, 10))  # su propia fila

    ctx = _contexto(session, anuncio, mundo)

    assert [(r.price, r.published_at) for r in ctx.recent_channel_rows] == [
        (Decimal("320"), AD_AT - timedelta(hours=3))
    ]


def test_el_historial_del_canal_solo_suma_decisiones_individuales(
    session: Session, mundo: SimpleNamespace
) -> None:
    propuesto = {**_PROPUESTA, "product_id": str(mundo.arroz.id)}
    aceptada = {**propuesto, "price": "500.00", "province_id": str(mundo.habana.id)}
    editada = {**aceptada, "price": "550.00"}

    # 1) Individual, curada: 2 filas propuestas, 1 editada.
    a = _ingesta(session, mundo.canal, cuando=AD_AT - timedelta(days=3))
    _revision(
        session,
        _propuesta(session, a, mundo),
        resultado=ReviewOutcome.CURATED,
        modo=CurationMode.INDIVIDUAL,
        filas=[
            {"proposal_index": 0, "proposed": propuesto, "final": aceptada, "edited": []},
            {"proposal_index": 1, "proposed": propuesto, "final": editada, "edited": ["price"]},
        ],
    )
    # 2) Sin evento en la bitácora (anterior a ella): individual, 1 fila borrada.
    b = _ingesta(session, mundo.canal, cuando=AD_AT - timedelta(days=2))
    _revision(
        session,
        _propuesta(session, b, mundo),
        resultado=ReviewOutcome.CURATED,
        modo=None,
        filas=[{"proposal_index": 0, "proposed": propuesto, "final": None, "edited": []}],
    )
    # 3) Descarte aceptado en lote: no cuenta.
    c = _ingesta(session, mundo.canal, estado=ProcessingStatus.DISCARDED)
    _revision(
        session,
        _propuesta(session, c, mundo, descarta=True),
        resultado=ReviewOutcome.DISCARDED,
        modo=CurationMode.BATCH_MODEL,
    )
    # 4) Individual pero de OTRO canal: no cuenta.
    d = _ingesta(session, mundo.otro_canal)
    _revision(
        session,
        _propuesta(session, d, mundo),
        resultado=ReviewOutcome.CURATED,
        modo=CurationMode.INDIVIDUAL,
        filas=[{"proposal_index": 0, "proposed": propuesto, "final": aceptada, "edited": []}],
    )
    # 5) La revisión del propio anuncio: tampoco es historial suyo.
    anuncio = _ingesta(session, mundo.canal)
    propuesta = _propuesta(session, anuncio, mundo)
    _revision(
        session,
        propuesta,
        resultado=ReviewOutcome.CURATED,
        modo=CurationMode.INDIVIDUAL,
        filas=[{"proposal_index": 0, "proposed": propuesto, "final": aceptada, "edited": []}],
    )

    ctx = context.build_ad_context(
        session,
        anuncio,
        model=context.stored_proposal(session, propuesta),
        catalog=context.load_catalog(session),
    )

    assert ctx.channel is not None
    assert (ctx.channel.decisions, ctx.channel.proposed_rows, ctx.channel.changed_rows) == (2, 3, 2)


def test_el_historial_del_canal_se_puede_cortar_a_una_fecha(
    session: Session, mundo: SimpleNamespace
) -> None:
    propuesto = {**_PROPUESTA, "product_id": str(mundo.arroz.id)}
    final = {**propuesto, "province_id": str(mundo.habana.id)}
    filas = [{"proposal_index": 0, "proposed": propuesto, "final": final, "edited": []}]
    corte = AD_AT + timedelta(days=5)
    for cuando in (corte - timedelta(days=1), corte + timedelta(days=1)):
        otro = _ingesta(session, mundo.canal)
        _revision(
            session,
            _propuesta(session, otro, mundo),
            resultado=ReviewOutcome.CURATED,
            modo=CurationMode.INDIVIDUAL,
            filas=filas,
            cuando=cuando,
        )
    anuncio = _ingesta(session, mundo.canal)

    ctx = _contexto(session, anuncio, mundo, history_before=corte)

    assert ctx.channel is not None
    assert ctx.channel.decisions == 1


# --- Conjunto de evaluación --------------------------------------------------


def test_una_revision_de_lote_no_cuenta_como_evidencia(
    session: Session, mundo: SimpleNamespace
) -> None:
    individual = _ingesta(session, mundo.canal, estado=ProcessingStatus.DISCARDED)
    _revision(
        session,
        _propuesta(session, individual, mundo, descarta=True),
        resultado=ReviewOutcome.DISCARDED,
        modo=CurationMode.INDIVIDUAL,
    )
    sin_evento = _ingesta(session, mundo.canal, estado=ProcessingStatus.DISCARDED)
    _revision(
        session,
        _propuesta(session, sin_evento, mundo, descarta=True),
        resultado=ReviewOutcome.DISCARDED,
        modo=None,
    )
    for _ in range(3):
        lote = _ingesta(session, mundo.canal, estado=ProcessingStatus.DISCARDED)
        _revision(
            session,
            _propuesta(session, lote, mundo, descarta=True),
            resultado=ReviewOutcome.DISCARDED,
            modo=CurationMode.BATCH_MODEL,
        )

    conjunto = shadow.load_evaluation(session, context.load_catalog(session))

    assert {c.ingestion_id for c in conjunto.cases} == {str(individual.id), str(sin_evento.id)}
    assert conjunto.notes["revisiones en total"] == 5
    assert conjunto.notes["decididas en lote (no cuentan)"] == 3
    assert conjunto.notes["individuales sin evento en la bitácora"] == 1


def _rescatado_y_descartado_de_a_uno(session: Session, mundo: SimpleNamespace) -> RawIngestion:
    """Caso 13 de B2a: el carril 0 lo descarta solo, la auditoría lo rescata y
    después el operador lo descarta de a uno. Quedan dos revisiones: la del
    carril 0 (no cuenta) y la del operador (cuenta)."""
    anuncio = _ingesta(session, mundo.canal, estado=ProcessingStatus.DISCARDED)
    propuesta = _propuesta(session, anuncio, mundo, descarta=True)
    _revision(
        session,
        propuesta,
        resultado=ReviewOutcome.DISCARDED,
        modo="auto",
        cuando=AD_AT + timedelta(days=1),
    )
    session.add(
        CurationEvent(
            ingestion_id=anuncio.id,
            action="rescued",
            mode=CurationMode.INDIVIDUAL,
            proposal_id=propuesta.id,
            decided_at=AD_AT + timedelta(days=8),
        )
    )
    _revision(
        session,
        propuesta,
        resultado=ReviewOutcome.DISCARDED,
        modo=CurationMode.INDIVIDUAL,
        cuando=AD_AT + timedelta(days=9),
    )
    return anuncio


def test_rescatado_y_descartado_de_a_uno_cuenta_como_individual_en_el_modo_sombra(
    session: Session, mundo: SimpleNamespace
) -> None:
    anuncio = _rescatado_y_descartado_de_a_uno(session, mundo)

    conjunto = shadow.load_evaluation(session, context.load_catalog(session))

    assert [c.ingestion_id for c in conjunto.cases] == [str(anuncio.id)]
    assert conjunto.notes["revisiones en total"] == 2
    assert conjunto.notes["decididas en lote (no cuentan)"] == 1


def test_rescatado_y_descartado_de_a_uno_cuenta_en_el_historial_del_canal(
    session: Session, mundo: SimpleNamespace
) -> None:
    _rescatado_y_descartado_de_a_uno(session, mundo)
    otro = _ingesta(session, mundo.canal, cuando=AD_AT + timedelta(days=20))

    historial = context.channel_history(session, mundo.canal, exclude_ingestion_id=otro.id)

    assert historial.decisions == 1


# --- El comando --------------------------------------------------------------


def _oro(tmp_path: Path, session: Session, mundo: SimpleNamespace) -> Path:
    """Un oro de dos líneas: un descarte y un anuncio conservado."""
    descartado = _ingesta(
        session, mundo.canal, texto="Buenos días grupo", estado=ProcessingStatus.DISCARDED
    )
    conservado = _ingesta(session, mundo.canal)
    lineas = [
        {
            "ingestion_id": str(descartado.id),
            "content_type": "text",
            "expected": "discarded",
            "expected_rows": [],
            "degraded": False,
            "proposal": {
                "discard": True,
                "discard_reason": "no_data",
                "rows": [],
                "row_meta": [],
                "dropped": 0,
            },
        },
        {
            "ingestion_id": str(conservado.id),
            "content_type": "text",
            "expected": "kept",
            "expected_rows": [_fila_oro("Arroz", "320.00", "1.000")],
            "degraded": False,
            "proposal": {
                "discard": False,
                "discard_reason": None,
                "rows": [
                    {
                        "product_id": str(mundo.arroz.id),
                        "price": "320",
                        "currency": "CUP",
                        "quantity": "1",
                        "unit": "lb",
                        "province_id": str(mundo.habana.id),
                        "sale_format": None,
                        "price_basis": "unit",
                        "packaging": None,
                    }
                ],
                "row_meta": [
                    {
                        "evidence": "Arroz 320 CUP la libra",
                        "currency_explicit": True,
                        "evidence_found": True,
                        "price_in_evidence": True,
                    }
                ],
                "dropped": 0,
            },
        },
    ]
    ruta = tmp_path / "oro.jsonl"
    ruta.write_text("\n".join(json.dumps(linea) for linea in lineas) + "\n", encoding="utf-8")
    session.commit()
    return ruta


_PARAMETROS = [
    "--min-cell-n", "3",
    "--channel-min-history", "1",
    "--channel-max-edit-rate", "0.5",
]


def test_en_seco_no_escribe_el_registro_de_corridas(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    oro = _oro(tmp_path, session, mundo)
    registro = tmp_path / "shadow-runs.jsonl"
    salida: list[str] = []

    codigo = shadow.run(
        shadow.parse_args(["--gold", str(oro), "--runs-file", str(registro), *_PARAMETROS]),
        session,
        out=salida.append,
    )

    assert codigo == 0
    assert not registro.exists()
    texto = "\n".join(salida)
    assert "EN SECO" in texto
    # El descarte del oro cae en el carril 0 y el operador lo había descartado.
    assert "Carril 0" in texto


def test_con_execute_agrega_exactamente_una_linea(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    oro = _oro(tmp_path, session, mundo)
    registro = tmp_path / "shadow-runs.jsonl"
    registro.write_text('{"previa": true}\n', encoding="utf-8")
    argumentos = ["--gold", str(oro), "--runs-file", str(registro), "--execute", *_PARAMETROS]

    shadow.run(shadow.parse_args(argumentos), session, out=lambda _linea: None)

    lineas = registro.read_text(encoding="utf-8").splitlines()
    assert len(lineas) == 2
    assert json.loads(lineas[0]) == {"previa": True}
    corrida = json.loads(lineas[1])
    assert corrida["grid"] is False
    assert len(corrida["runs"]) == 1
    unica = corrida["runs"][0]
    assert unica["params"] == {
        "min_cell_n": 3,
        "channel_min_history": 1,
        "channel_max_edit_rate": 0.5,
        "signal_high_probability": None,
        "lane1_max_rows": None,
        "lane1_model_over_rules": False,
    }
    calibracion = unica["sets"]["calibration"]
    assert calibracion["n"] == 2
    # El saludo: el modelo descarta y las reglas no ven ningún piloto.
    assert calibracion["lanes"]["0"] == 1
    assert calibracion["false_discards_text"] == {
        "k": 0,
        "n": 1,
        "rate": 0.0,
        "upper95": pytest.approx(0.95),  # 0 de 1: la cota es 1 − 0,05
    }
    assert unica["sets"]["evaluation"]["n"] == 0


def test_sin_parametros_ni_grilla_el_comando_no_corre() -> None:
    # No hay valores por defecto: o se pasan los tres, o se pide la grilla.
    with pytest.raises(SystemExit):
        shadow.parse_args(["--gold", "oro.jsonl"])
    with pytest.raises(SystemExit):
        shadow.parse_args(["--gold", "oro.jsonl", "--min-cell-n", "3"])
    assert shadow.parse_args(["--gold", "oro.jsonl", "--grid"]).grid is True


def test_el_tope_de_filas_es_opcional_y_va_a_todas_las_combinaciones() -> None:
    # Sin el argumento no hay tope.
    (sin_tope,) = shadow.configs_from_args(shadow.parse_args(["--gold", "oro.jsonl", *_PARAMETROS]))
    assert sin_tope.lane1_max_rows is None
    assert "lane1_max_rows=sin tope" in shadow._params_line(sin_tope)

    (con_tope,) = shadow.configs_from_args(
        shadow.parse_args(["--gold", "oro.jsonl", "--lane1-max-rows", "1", *_PARAMETROS])
    )
    assert con_tope.lane1_max_rows == 1
    assert "lane1_max_rows=1" in shadow._params_line(con_tope)
    assert shadow._params_dict(con_tope)["lane1_max_rows"] == 1

    # No entra a la grilla: la misma cantidad de combinaciones, todas con el tope.
    grilla = shadow.configs_from_args(
        shadow.parse_args(["--gold", "oro.jsonl", "--grid", "--lane1-max-rows", "2"])
    )
    sin_argumento = shadow.configs_from_args(shadow.parse_args(["--gold", "oro.jsonl", "--grid"]))
    assert len(grilla) == len(sin_argumento)
    assert {config.lane1_max_rows for config in grilla} == {2}
    assert {config.lane1_max_rows for config in sin_argumento} == {None}

    with pytest.raises(SystemExit):
        shadow.parse_args(["--gold", "oro.jsonl", "--lane1-max-rows", "0", *_PARAMETROS])


def test_el_tope_de_filas_saca_del_carril_1_en_el_informe() -> None:
    # Caso 25 del motor: dos filas sin tocar, con todo en `pass`.
    caso = shadow.Case(
        ingestion_id=str(uuid.uuid4()),
        content_type="text",
        ctx=motor._dos_filas(),
        kept=True,
        diff=context.ProposalDiff(proposed_rows=2),
    )

    sin_tope = shadow.evaluate_case(caso, motor.CONFIG)
    con_tope = shadow.evaluate_case(caso, dataclasses.replace(motor.CONFIG, lane1_max_rows=1))

    assert sin_tope.lane == LANE_ACCEPT
    assert con_tope.lane == LANE_REVIEW
    assert shadow.summarize([con_tope]).reasons == {
        "la propuesta trae más filas que el tope del carril 1 (1)": 1
    }


def test_la_grilla_corre_todas_las_combinaciones_declaradas(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    oro = _oro(tmp_path, session, mundo)
    registro = tmp_path / "shadow-runs.jsonl"
    argumentos = ["--gold", str(oro), "--runs-file", str(registro), "--execute", "--grid"]

    shadow.run(shadow.parse_args(argumentos), session, out=lambda _linea: None)

    (linea,) = registro.read_text(encoding="utf-8").splitlines()
    corrida = json.loads(linea)
    esperadas = (
        len(shadow.GRID_MIN_CELL_N)
        * len(shadow.GRID_CHANNEL_MIN_HISTORY)
        * len(shadow.GRID_CHANNEL_MAX_EDIT_RATE)
    )
    assert corrida["grid"] is True
    assert len(corrida["runs"]) == esperadas


# =============================================================================
# El bloque «Jev» (slice A6b-2)
# =============================================================================

# Los pilotos del catálogo del motor (`test_validation_engine`), con la forma
# que pide `_lectura`: las claves de producto de la señal salen de acá.
_PILOTOS = SimpleNamespace(arroz=motor.ARROZ, pollo=motor.POLLO, aceite=motor.ACEITE)


def _caso_jev(
    ctx: AdContext,
    respuestas: dict[str, Any] | None,
    *,
    conservado: bool = True,
    diff: context.ProposalDiff | None = None,
    curadas: tuple[shadow.JevRow, ...] = (),
    tipo: str = "text",
) -> shadow.Case:
    """Un caso del informe con su señal de Jev ya leída (o sin señal)."""
    return shadow.Case(
        ingestion_id=str(uuid.uuid4()),
        content_type=tipo,
        ctx=ctx,
        kept=conservado,
        diff=diff,
        jev=None if respuestas is None else _lectura(_PILOTOS, respuestas),
        curated_rows=curadas,
    )


def _fila_jev(tabla: shadow.JevLane1, probabilidad: str, umbral: float | None) -> shadow.JevLane1Row:
    (fila,) = [f for f in tabla.rows if f.probability == probabilidad and f.threshold == umbral]
    return fila


_UNA_FILA_SIN_TOCAR = context.ProposalDiff(proposed_rows=1)
_UNA_FILA_EDITADA = context.ProposalDiff(proposed_rows=1, edited_rows=1, field_errors={"product_id": 1})


def _caso_21(respuestas: dict[str, Any], diff: context.ProposalDiff) -> shadow.Case:
    """El caso 21 de `spec-motor-validacion.md`, SIN la señal en el contexto:
    «Arroz 320 CUP lb», todo en `pass`. La señal la pone el informe."""
    return _caso_jev(motor._caso_1(texto="Arroz 320 CUP lb"), respuestas, diff=diff)


def test_caso_27_jev_saca_del_carril_1_y_el_informe_cuenta_uno() -> None:
    # Jev dice `pollo`: 0,95 con `probabilities[choice]` y 0,60 con `confidence`.
    en_desacuerdo = {**ANSWERS_OK, "product": choice("pollo", 0.95, 0.60)}
    caso = _caso_21(en_desacuerdo, _UNA_FILA_SIN_TOCAR)

    tabla = shadow.jev_lane1([caso], motor.CONFIG)

    # Sin señales: carril 1, sin error.
    assert (tabla.without.k, tabla.without.n) == (0, 1)
    # «Cualquier desacuerdo»: lo saca, con cualquiera de las dos probabilidades.
    for probabilidad in ("probability", "confidence"):
        fila = _fila_jev(tabla, probabilidad, None)
        assert fila.lane1.n == 0
        assert fila.removed == 1
        # El operador no había tocado nada: es automatización perdida.
        assert (fila.removed_errors, fila.removed_correct) == (0, 1)
        assert fila.entered == 0
    # Con umbral, saca solo si la probabilidad elegida lo alcanza.
    sacados = {(f.probability, f.threshold): f.removed for f in tabla.rows if f.threshold is not None}
    assert sacados == {
        ("probability", 0.5): 1,
        ("probability", 0.7): 1,
        ("probability", 0.9): 1,
        ("confidence", 0.5): 1,
        ("confidence", 0.7): 0,
        ("confidence", 0.9): 0,
    }
    # El que no se saca sigue en el carril 1.
    assert _fila_jev(tabla, "confidence", 0.9).lane1.n == 1


def test_caso_27_si_el_operador_habia_editado_lo_que_jev_saca_era_un_error() -> None:
    en_desacuerdo = {**ANSWERS_OK, "product": choice("pollo", 0.95, 0.60)}
    caso = _caso_21(en_desacuerdo, _UNA_FILA_EDITADA)

    tabla = shadow.jev_lane1([caso], motor.CONFIG)

    # Sin Jev: 1 anuncio en el carril 1, y es un error.
    assert (tabla.without.k, tabla.without.n) == (1, 1)
    fila = _fila_jev(tabla, "probability", None)
    assert (fila.removed, fila.removed_errors, fila.removed_correct) == (1, 1, 0)
    assert (fila.lane1.k, fila.lane1.n) == (0, 0)


def test_un_acuerdo_no_saca_del_carril_1() -> None:
    caso = _caso_21(ANSWERS_OK, _UNA_FILA_SIN_TOCAR)  # Jev dice `arroz` y `CUP`

    tabla = shadow.jev_lane1([caso], motor.CONFIG)

    assert all(fila.removed == 0 and fila.lane1.n == 1 for fila in tabla.rows)


def test_caso_28_jev_no_mete_en_el_carril_1() -> None:
    # El caso 22 del motor (el 6: las reglas no dan moneda) con Jev de acuerdo en todo.
    ctx = motor._caso_1(
        texto="Arroz 320 la libra",
        modelo=motor._modelo(motor._fila(base="unit"), metas=(motor.MONEDA_NO_ESCRITA,)),
        reglas=(motor.REGLA_SIN_MONEDA,),
    )
    caso = _caso_jev(ctx, ANSWERS_OK, diff=_UNA_FILA_SIN_TOCAR)

    tabla = shadow.jev_lane1([caso], motor.CONFIG)

    # Carril 2 con y sin la señal: nada entra, nada sale.
    assert tabla.without.n == 0
    assert len(tabla.rows) == 8  # 2 probabilidades × («cualquiera», 0,5, 0,7, 0,9)
    for fila in tabla.rows:
        assert (fila.lane1.n, fila.removed, fila.entered) == (0, 0, 0)


def _descartado_por_el_motor() -> AdContext:
    """Caso 3 del motor: el modelo dice `no_data` y las reglas no ven piloto."""
    return motor._ctx("Buenos días grupo", modelo=motor._descarte("no_data"))


def _a_revision() -> AdContext:
    """Sin propuesta del modelo: carril 2 (caso 19 del motor)."""
    return motor._ctx("Arroz 320 CUP lb", modelo=None)


def _casos_del_triage() -> list[shadow.Case]:
    """Caso 29: cuatro descartados con P(sí) 0,05, 0,10, 0,15 y 0,60 y dos
    conservados con 0,22 y 0,95. El motor descartaría solo dos de los
    descartados: los de 0,05 y 0,60."""
    descartados = [
        (0.05, _descartado_por_el_motor()),
        (0.10, _a_revision()),
        (0.15, _a_revision()),
        (0.60, _descartado_por_el_motor()),
    ]
    casos = [_caso_jev(ctx, {**ANSWERS_OK, "triage": noul(p)}, conservado=False) for p, ctx in descartados]
    casos += [
        _caso_jev(_a_revision(), {**ANSWERS_OK, "triage": noul(p)}, conservado=True) for p in (0.22, 0.95)
    ]
    return casos


def test_caso_29_tabla_de_triage() -> None:
    triage = shadow.jev_triage(_casos_del_triage())

    assert (triage.n, triage.invalid) == (6, 0)
    assert [fila.threshold for fila in triage.rows] == [0.1, 0.2, 0.3, 0.5]
    por_umbral = {fila.threshold: fila for fila in triage.rows}
    # `k` = conservados por el operador (falsos descartes), `n` = descartaría.
    assert (por_umbral[0.2].discards.k, por_umbral[0.2].discards.n) == (0, 3)
    assert (por_umbral[0.5].discards.k, por_umbral[0.5].discards.n) == (1, 4)
    # Estrictamente menor: 0,10 no es menor que 0,1.
    assert (por_umbral[0.1].discards.k, por_umbral[0.1].discards.n) == (0, 1)
    assert (por_umbral[0.3].discards.k, por_umbral[0.3].discards.n) == (1, 4)


def test_caso_29_cruce_del_triage_con_el_carril_0_del_motor() -> None:
    triage = shadow.jev_triage(_casos_del_triage())

    por_umbral = {fila.threshold: fila for fila in triage.rows}
    # Con 0,2 Jev descarta {0,05, 0,10, 0,15}; el motor, {0,05, 0,60}.
    con_02 = por_umbral[0.2]
    assert (con_02.both.n, con_02.jev_only.n, con_02.engine_only.n) == (1, 2, 1)
    assert (con_02.both.k, con_02.jev_only.k, con_02.engine_only.k) == (0, 0, 0)
    # Con 0,5 se suma el conservado de 0,22, que solo Jev descartaría.
    con_05 = por_umbral[0.5]
    assert (con_05.both.n, con_05.jev_only.n, con_05.engine_only.n) == (1, 3, 1)
    assert (con_05.jev_only.k, con_05.jev_only.n) == (1, 3)


def test_un_triage_ilegible_queda_fuera_de_la_tabla_y_se_cuenta() -> None:
    casos = [
        _caso_jev(_a_revision(), {**ANSWERS_OK, "triage": {"noul": "alto"}}, conservado=False),
        _caso_jev(_a_revision(), {**ANSWERS_OK, "triage": noul(0.05)}, conservado=False),
    ]

    triage = shadow.jev_triage(casos)

    assert (triage.n, triage.invalid) == (1, 1)
    assert all(fila.discards.n == 1 for fila in triage.rows)


def test_acierto_de_producto_y_moneda_contra_las_filas_curadas() -> None:
    cafe = uuid.uuid4()  # existe y no es piloto: vale `other`

    def curada(producto: uuid.UUID, moneda: str | None = "CUP") -> shadow.JevRow:
        return shadow.JevRow(product_id=producto, currency=moneda)

    ctx = _a_revision()
    casos = [
        # Acierta las dos.
        _caso_jev(ctx, ANSWERS_OK, curadas=(curada(motor.ARROZ.id),)),
        # Dos filas: el producto acierta (es el de alguna fila); la moneda no.
        _caso_jev(ctx, ANSWERS_OK, curadas=(curada(motor.POLLO.id, "USD"), curada(motor.ARROZ.id, "USD"))),
        # Fuera del foco y sin moneda (una fila sin moneda es en pesos).
        _caso_jev(ctx, {**ANSWERS_OK, "product": choice("other", 0.8, 0.7)}, curadas=(curada(cafe, None),)),
        # Producto ilegible; la moneda acierta.
        _caso_jev(
            ctx, {**ANSWERS_OK, "product": choice("cafe", 0.9, 0.9)}, curadas=(curada(motor.ARROZ.id),)
        ),
        # Falla el producto.
        _caso_jev(ctx, ANSWERS_OK, curadas=(curada(motor.POLLO.id),)),
        # No cuentan: un descartado, y un conservado sin filas curadas.
        _caso_jev(ctx, ANSWERS_OK, conservado=False),
        _caso_jev(ctx, ANSWERS_OK),
    ]

    campos = shadow.jev_fields(casos)

    assert campos.n == 5
    producto, moneda = campos.fields["product"], campos.fields["currency"]
    assert (producto.accuracy.k, producto.accuracy.n, producto.invalid) == (3, 4, 1)
    assert (moneda.accuracy.k, moneda.accuracy.n, moneda.invalid) == (4, 5, 0)


def test_al_bloque_solo_entran_los_textos_con_senal() -> None:
    ctx = _a_revision()
    conjunto = shadow.CaseSet(
        name="evaluation",
        title="prueba",
        fields=(),
        cases=[
            _caso_jev(ctx, ANSWERS_OK),
            _caso_jev(ctx, ANSWERS_OK, conservado=False),
            _caso_jev(ctx, None),  # texto sin señal
            _caso_jev(ctx, ANSWERS_OK, tipo="image"),  # una imagen no entra
        ],
    )

    resumen = shadow.summarize_jev(conjunto, [motor.CONFIG])

    assert (resumen.n, resumen.texts) == (2, 3)
    assert len(resumen.lane1) == 1
    texto = "\n".join(shadow.render_jev(resumen, [motor.CONFIG]))
    assert "n = 2 de 3 textos" in texto


def test_el_informe_no_elige_ningun_valor() -> None:
    conjunto = shadow.CaseSet(name="evaluation", title="prueba", fields=(), cases=_casos_del_triage())

    texto = "\n".join(shadow.render_jev(shadow.summarize_jev(conjunto, [motor.CONFIG]), [motor.CONFIG]))

    # Las tres tablas están, con sus grillas completas, y nada recomienda.
    assert "Carril 1" in texto
    assert "Triage" in texto
    assert "Campos" in texto
    for umbral in ("0,1", "0,2", "0,3", "0,5", "0,7", "0,9"):
        assert umbral in texto
    minusculas = texto.lower()
    assert "recomend" not in minusculas
    assert "conviene" not in minusculas
    assert "no elige" in minusculas


# --- El bloque «Jev» sobre SQLite ---------------------------------------------


def _senal(
    session: Session,
    anuncio_id: uuid.UUID,
    respuestas: dict[str, Any],
    *,
    huella: str | None = None,
) -> None:
    """Una fila de `jev_signals`, como la habría dejado el relleno."""
    opciones = jev_signal.load_options(session)
    session.add(
        JevSignal(
            ingestion_id=anuncio_id,
            model="typesafe-ai/jev",
            questions_hash=huella or opciones.questions_hash,
            answers=respuestas,
            product_keys=opciones.product_keys,
            input_tokens=500,
            output_tokens=0,
            cost_usd=Decimal("0.00003"),
            source="backfill",
        )
    )
    session.flush()


def _foto(session: Session, modelo: type[PriceHistory] | type[JevSignal]) -> list[tuple[str, ...]]:
    """Todas las filas de una tabla, columna por columna, para comparar."""
    session.expire_all()
    columnas = [columna.name for columna in modelo.__table__.columns]
    return sorted(
        tuple(str(getattr(fila, columna)) for columna in columnas)
        for fila in session.execute(select(modelo)).scalars()
    )


def _ids_del_oro(oro: Path) -> tuple[uuid.UUID, uuid.UUID]:
    descartado, conservado = (
        json.loads(linea)["ingestion_id"] for linea in oro.read_text(encoding="utf-8").splitlines()
    )
    return uuid.UUID(descartado), uuid.UUID(conservado)


def test_caso_30_sin_senales_el_bloque_dice_n_0_y_el_informe_termina_bien(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    oro = _oro(tmp_path, session, mundo)
    registro = tmp_path / "shadow-runs.jsonl"
    salida: list[str] = []
    argumentos = ["--gold", str(oro), "--runs-file", str(registro), "--execute", *_PARAMETROS]

    codigo = shadow.run(shadow.parse_args(argumentos), session, out=salida.append)

    assert codigo == 0
    lineas_jev = [linea for linea in salida if "Jev" in linea and "n = 0" in linea]
    # Una por conjunto con casos (la evaluación está vacía y no abre bloque).
    assert len(lineas_jev) == 1
    assert "n = 0 de 2 textos" in lineas_jev[0]
    corrida = json.loads(registro.read_text(encoding="utf-8"))
    assert corrida["jev"]["calibration"]["n"] == 0
    assert corrida["jev"]["calibration"]["texts"] == 2
    assert corrida["jev"]["evaluation"]["n"] == 0


def test_una_senal_de_otra_huella_no_entra_al_bloque(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    oro = _oro(tmp_path, session, mundo)
    _, conservado = _ids_del_oro(oro)
    _senal(session, conservado, ANSWERS_OK, huella="huella-vieja-000")
    session.commit()
    salida: list[str] = []

    shadow.run(shadow.parse_args(["--gold", str(oro), *_PARAMETROS]), session, out=salida.append)

    assert any("Jev" in linea and "n = 0 de 2 textos" in linea for linea in salida)


def _oro_con_senales(tmp_path: Path, session: Session, mundo: SimpleNamespace) -> Path:
    """El oro de dos líneas con una señal vigente por anuncio y la fila curada
    del conservado. Jev le da 0,05 al saludo y acierta arroz y CUP en el otro."""
    oro = _oro(tmp_path, session, mundo)
    descartado, conservado = _ids_del_oro(oro)
    _senal(session, descartado, {**ANSWERS_OK, "triage": noul(0.05)})
    _senal(session, conservado, ANSWERS_OK)
    origen = session.get(RawIngestion, conservado)
    assert origen is not None
    _precio(session, mundo, origen, "320", date(2026, 9, 10))
    session.commit()
    return oro


def test_el_bloque_jev_sale_en_la_terminal_y_en_el_registro(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    oro = _oro_con_senales(tmp_path, session, mundo)
    registro = tmp_path / "shadow-runs.jsonl"
    salida: list[str] = []
    argumentos = ["--gold", str(oro), "--runs-file", str(registro), "--execute", *_PARAMETROS]

    codigo = shadow.run(shadow.parse_args(argumentos), session, out=salida.append)

    assert codigo == 0
    assert any("Jev" in linea and "n = 2 de 2 textos" in linea for linea in salida)
    jev = json.loads(registro.read_text(encoding="utf-8"))["jev"]["calibration"]
    assert (jev["n"], jev["texts"]) == (2, 2)
    # Una combinación de parámetros: una tabla del carril 1, de ocho filas.
    assert len(jev["lane1"]) == 1
    assert jev["lane1"][0]["params"]["min_cell_n"] == 3
    assert len(jev["lane1"][0]["rows"]) == 8
    # Triage: con 0,2 descartaría el saludo (0,05), que el oro también descarta
    # y que el motor manda al carril 0.
    con_02 = next(fila for fila in jev["triage"]["rows"] if fila["threshold"] == 0.2)
    assert (con_02["discards"]["k"], con_02["discards"]["n"]) == (0, 1)
    assert (con_02["both"]["n"], con_02["jev_only"]["n"], con_02["engine_only"]["n"]) == (1, 0, 0)
    # Campos: un solo conservado con fila curada, y Jev acierta los dos.
    assert jev["fields"]["n"] == 1
    assert (jev["fields"]["product"]["k"], jev["fields"]["product"]["n"]) == (1, 1)
    assert (jev["fields"]["currency"]["k"], jev["fields"]["currency"]["n"]) == (1, 1)


def test_caso_31_el_informe_no_escribe_ni_llama_a_nadie(
    session: Session, mundo: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    oro = _oro_con_senales(tmp_path, session, mundo)

    def _sin_red(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("el informe no puede llamar a Jev")

    monkeypatch.setattr(jev_client, "ask", _sin_red)
    antes = (_foto(session, PriceHistory), _foto(session, JevSignal))
    registro = tmp_path / "shadow-runs.jsonl"
    argumentos = ["--gold", str(oro), "--runs-file", str(registro), "--execute", "--grid"]

    codigo = shadow.run(shadow.parse_args(argumentos), session, out=lambda _linea: None)

    assert codigo == 0
    # Nada pendiente en la sesión y las dos tablas, idénticas.
    assert not session.new
    assert not session.dirty
    assert not session.deleted
    assert (_foto(session, PriceHistory), _foto(session, JevSignal)) == antes
    assert len(antes[0]) == 1
    assert len(antes[1]) == 2


def test_con_la_grilla_el_registro_trae_una_tabla_del_carril_1_por_combinacion(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    oro = _oro_con_senales(tmp_path, session, mundo)
    registro = tmp_path / "shadow-runs.jsonl"
    argumentos = ["--gold", str(oro), "--runs-file", str(registro), "--execute", "--grid"]
    salida: list[str] = []

    shadow.run(shadow.parse_args(argumentos), session, out=salida.append)

    corrida = json.loads(registro.read_text(encoding="utf-8"))
    assert len(corrida["jev"]["calibration"]["lane1"]) == len(corrida["runs"])
    assert any("Jev" in linea and "n = 2 de 2 textos" in linea for linea in salida)
