"""Informe del modo sombra (`saimc.validation.shadow`) y armado del contexto
(`saimc.validation.context`), slice A5c.

Los valores esperados están escritos A MANO acá, antes y con independencia del
código: las cotas de Clopper-Pearson salen de `docs/spec-motor-validacion.md`
(«Protocolo del modo sombra») y los conteos, de contar el fixture a ojo. Ningún
esperado sale de correr el informe.

Los parámetros de `CONFIG` son valores DE PRUEBA: el sistema no trae ninguno
por defecto.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.orm import Session

from saimc.models import (
    Channel,
    ContentType,
    CurationAction,
    CurationEvent,
    CurationMode,
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
from saimc.validation import LANE_ACCEPT, LANE_DISCARD, LANE_REVIEW, ValidationConfig, context, shadow

CONFIG = ValidationConfig(
    min_cell_n=3,
    channel_min_history=1,
    channel_max_edit_rate=0.5,
    signal_high_probability=None,
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
    solo_moneda: bool = False,
) -> shadow.CaseResult:
    return shadow.CaseResult(
        ingestion_id=str(uuid.uuid4()),
        content_type=tipo,
        lane=carril,
        kept=conservado,
        diff=diff,
        reasons=motivos,
        only_currency=solo_moneda,
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
        # Carril 2: 10 anuncios; 3 solo por la moneda no escrita.
        + [
            _resultado(
                LANE_REVIEW,
                diff=_SIN_ERROR,
                motivos=("fila 1: reglas y modelo no coinciden en moneda",),
                solo_moneda=True,
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
    assert resumen.only_currency == 3
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
