"""La partición de la métrica de la Fase 0 (slice B3-3).

Contrato: `docs/spec-slice-B3.md`, sección 4 y casos 21 a 28. Los esperados
están escritos A MANO antes que el código: cada anuncio sintético se arma con
el estado y los eventos que dice la especificación, y la respuesta correcta
(el casillero, N1, los extremos de N2) se define acá, no se deriva de correr
`automation_summary`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import Session

from saimc.automation import Partition, automation_summary
from saimc.config import Settings
from saimc.models import (
    ContentType,
    CurationAction,
    CurationEvent,
    CurationMode,
    ProcessingStatus,
    RawIngestion,
    SourceType,
)

# Un miércoles: su semana (lunes a domingo) arranca el 2026-09-28.
HOY = date(2026, 10, 7)
SEMANA = date(2026, 9, 28)
INGRESO = datetime(2026, 9, 29, 10, 0, tzinfo=UTC)
DECISION = datetime(2026, 9, 30, 10, 0, tzinfo=UTC)

_contador = 0


def _anuncio(
    session: Session,
    estado: ProcessingStatus,
    *eventos: tuple[str, str],
    tipo: ContentType = ContentType.TEXT,
    ingreso: datetime = INGRESO,
) -> uuid.UUID:
    """Un anuncio en `estado` con los eventos `(acción, modo)` en ese orden."""
    global _contador
    _contador += 1
    anuncio = RawIngestion(
        source_type=SourceType.INBOX_TEXT,
        file_path=f"raw/{_contador}.txt",
        content_hash=f"hash-{_contador}",
        content_type=tipo,
        original_text="Arroz a 320 la libra",
        processing_status=estado,
        captured_at=ingreso,
        created_at=ingreso,
    )
    session.add(anuncio)
    session.flush()
    for orden, (accion, modo) in enumerate(eventos):
        session.add(
            CurationEvent(
                ingestion_id=anuncio.id,
                action=accion,
                mode=modo,
                decided_at=DECISION + timedelta(hours=orden),
            )
        )
    session.commit()
    return anuncio.id


def _resumen(session: Session):
    return automation_summary(session, Settings(), today=HOY)


def _total(session: Session) -> Partition:
    return _resumen(session).total.both


def _cohorte(session: Session) -> Partition:
    """La cohorte de la semana de ingreso de los anuncios de arriba."""
    (fila,) = [c for c in _resumen(session).cohorts if c.week_start == SEMANA]
    return fila.both


def test_caso_21_diez_anuncios_dan_n1_y_los_extremos_de_n2(session: Session) -> None:
    for _ in range(5):
        _anuncio(
            session,
            ProcessingStatus.DISCARDED,
            (CurationAction.DISCARDED, CurationMode.AUTO),
        )
    _anuncio(
        session, ProcessingStatus.CURATED, (CurationAction.CURATED, CurationMode.AUTO)
    )
    _anuncio(
        session,
        ProcessingStatus.CURATED,
        (CurationAction.CURATED, CurationMode.INDIVIDUAL),
    )
    _anuncio(
        session,
        ProcessingStatus.DISCARDED,
        (CurationAction.DISCARDED, CurationMode.INDIVIDUAL),
    )
    _anuncio(session, ProcessingStatus.PENDING)
    _anuncio(session, ProcessingStatus.PENDING)

    p = _cohorte(session)

    assert (p.a, p.h, p.d0, p.dh, p.p) == (1, 1, 5, 1, 2)
    assert p.total == 10
    assert p.n1 == 6 / 10
    # Al menos A / (A + H + P) = 1/4; a lo sumo A / (A + H) = 1/2.
    assert p.n2_min == 1 / 4
    assert p.n2_max == 1 / 2


def test_caso_22_descarte_rescatado_y_pendiente_cuenta_en_p(session: Session) -> None:
    _anuncio(
        session,
        ProcessingStatus.PENDING,
        (CurationAction.DISCARDED, CurationMode.AUTO),
        (CurationAction.RESCUED, CurationMode.INDIVIDUAL),
    )

    p = _cohorte(session)

    assert (p.d0, p.p) == (0, 1)


def test_descarte_rescatado_y_vuelto_a_descartar_a_mano_cuenta_en_dh(
    session: Session,
) -> None:
    _anuncio(
        session,
        ProcessingStatus.DISCARDED,
        (CurationAction.DISCARDED, CurationMode.AUTO),
        (CurationAction.RESCUED, CurationMode.INDIVIDUAL),
        (CurationAction.DISCARDED, CurationMode.INDIVIDUAL),
    )

    p = _cohorte(session)

    assert (p.d0, p.dh) == (0, 1)


def test_caso_23_aceptado_solo_marcado_invalido_cuenta_en_dh(session: Session) -> None:
    # La auditoría lo descartó por una decisión individual: sigue `discarded`
    # y su último descarte no es del carril 0.
    _anuncio(
        session,
        ProcessingStatus.DISCARDED,
        (CurationAction.CURATED, CurationMode.AUTO),
        (CurationAction.DISCARDED, CurationMode.INDIVIDUAL),
    )

    p = _cohorte(session)

    assert (p.a, p.dh) == (0, 1)
    assert p.n1 == 0
    assert p.n2_min is None or p.n2_min == 0


def test_caso_24_aceptado_solo_corregido_en_la_auditoria_cuenta_en_a(
    session: Session,
) -> None:
    _anuncio(
        session,
        ProcessingStatus.CURATED,
        (CurationAction.CURATED, CurationMode.AUTO),
        (CurationAction.AUDITED, CurationMode.INDIVIDUAL),
    )

    assert _cohorte(session).a == 1


def test_caso_25_descarte_por_lote_cuenta_en_dh_no_en_d0(session: Session) -> None:
    _anuncio(
        session,
        ProcessingStatus.DISCARDED,
        (CurationAction.DISCARDED, CurationMode.BATCH_OUT_OF_FOCUS),
    )
    _anuncio(
        session,
        ProcessingStatus.DISCARDED,
        (CurationAction.DISCARDED, CurationMode.BATCH_MODEL),
        tipo=ContentType.IMAGE,
    )

    p = _cohorte(session)

    assert (p.d0, p.dh) == (0, 2)


def test_caso_26_sin_pendientes_los_dos_extremos_coinciden(session: Session) -> None:
    _anuncio(
        session, ProcessingStatus.CURATED, (CurationAction.CURATED, CurationMode.AUTO)
    )
    _anuncio(
        session,
        ProcessingStatus.CURATED,
        (CurationAction.CURATED, CurationMode.INDIVIDUAL),
    )
    _anuncio(
        session,
        ProcessingStatus.CURATED,
        (CurationAction.CURATED, CurationMode.INDIVIDUAL),
    )

    p = _cohorte(session)

    assert p.p == 0
    assert p.n2_min == p.n2_max == 1 / 3


def test_caso_27_sin_validos_ni_pendientes_n2_es_sin_datos(session: Session) -> None:
    _anuncio(
        session,
        ProcessingStatus.DISCARDED,
        (CurationAction.DISCARDED, CurationMode.AUTO),
    )
    _anuncio(
        session,
        ProcessingStatus.DISCARDED,
        (CurationAction.DISCARDED, CurationMode.INDIVIDUAL),
    )

    p = _cohorte(session)

    assert p.a + p.h + p.p == 0
    assert p.n2_min is None
    assert p.n2_max is None


def test_n2_solo_pendientes_no_tiene_extremo_superior(session: Session) -> None:
    _anuncio(session, ProcessingStatus.PENDING)

    p = _cohorte(session)

    # Piso 0 / 1; sin ningún resuelto válido no hay techo que calcular.
    assert p.n2_min == 0
    assert p.n2_max is None


def test_caso_28_los_cinco_casilleros_suman_el_total(session: Session) -> None:
    _anuncio(
        session, ProcessingStatus.CURATED, (CurationAction.CURATED, CurationMode.AUTO)
    )
    _anuncio(
        session,
        ProcessingStatus.CURATED,
        (CurationAction.CURATED, CurationMode.INDIVIDUAL),
        tipo=ContentType.IMAGE,
    )
    _anuncio(
        session,
        ProcessingStatus.DISCARDED,
        (CurationAction.DISCARDED, CurationMode.AUTO),
    )
    _anuncio(
        session,
        ProcessingStatus.DISCARDED,
        (CurationAction.DISCARDED, CurationMode.INDIVIDUAL),
        tipo=ContentType.IMAGE,
    )
    _anuncio(session, ProcessingStatus.PENDING)
    # Anterior a la bitácora: sin ningún evento.
    _anuncio(session, ProcessingStatus.CURATED)
    _anuncio(session, ProcessingStatus.DISCARDED)

    resumen = _resumen(session)

    for fila in [*resumen.cohorts, resumen.total]:
        for p in (fila.text, fila.image, fila.both):
            assert p.a + p.h + p.d0 + p.dh + p.p == p.total
        assert fila.text.total + fila.image.total == fila.both.total
    p = _cohorte(session)
    assert p.total == 7
    # Lo anterior a la bitácora cae en H o en Dh, que es lo que fue.
    assert (p.h, p.dh) == (2, 2)


def test_abre_texto_e_imagen(session: Session) -> None:
    _anuncio(
        session, ProcessingStatus.CURATED, (CurationAction.CURATED, CurationMode.AUTO)
    )
    _anuncio(session, ProcessingStatus.PENDING, tipo=ContentType.IMAGE)

    fila = next(c for c in _resumen(session).cohorts if c.week_start == SEMANA)

    assert (fila.text.a, fila.text.total) == (1, 1)
    assert (fila.image.p, fila.image.total) == (1, 1)


def test_cohorte_por_semana_de_ingreso_y_semana_en_curso_parcial(
    session: Session,
) -> None:
    _anuncio(session, ProcessingStatus.PENDING, ingreso=INGRESO)
    # Ingresó la semana en curso (lunes 2026-10-05): parcial.
    _anuncio(
        session,
        ProcessingStatus.PENDING,
        ingreso=datetime(2026, 10, 6, 9, 0, tzinfo=UTC),
    )

    resumen = _resumen(session)
    semanas = [c.week_start for c in resumen.cohorts]

    # Ocho semanas cerradas y la en curso, de la más vieja a la más nueva.
    assert len(semanas) == 9
    assert semanas[-1] == date(2026, 10, 5)
    assert semanas[-2] == SEMANA
    assert [c.partial for c in resumen.cohorts] == [False] * 8 + [True]
    assert resumen.cohorts[-1].both.total == 1
    assert resumen.cohorts[-2].both.total == 1


def test_el_total_arranca_en_la_semana_del_primer_evento_auto(session: Session) -> None:
    # Anterior al primer evento `auto`: no entra al total.
    _anuncio(
        session,
        ProcessingStatus.PENDING,
        ingreso=datetime(2026, 9, 7, 9, 0, tzinfo=UTC),
    )
    # La semana del primer evento `auto` (decidido el 2026-09-30).
    _anuncio(
        session, ProcessingStatus.CURATED, (CurationAction.CURATED, CurationMode.AUTO)
    )
    _anuncio(session, ProcessingStatus.PENDING)

    resumen = _resumen(session)

    assert resumen.auto_since == SEMANA
    assert resumen.total.both.total == 2


def test_sin_eventos_auto_el_total_queda_vacio(session: Session) -> None:
    _anuncio(
        session,
        ProcessingStatus.CURATED,
        (CurationAction.CURATED, CurationMode.INDIVIDUAL),
    )

    resumen = _resumen(session)

    assert resumen.auto_since is None
    assert resumen.total.both.total == 0


def test_sin_nada_auditado_el_bloque_lo_dice(session: Session) -> None:
    _anuncio(
        session, ProcessingStatus.CURATED, (CurationAction.CURATED, CurationMode.AUTO)
    )

    resumen = _resumen(session)

    assert resumen.lane1.audited == 0
    assert all(b.n == 0 for b in resumen.lane0.values())
    assert resumen.provisional


# --- measure.py ----------------------------------------------------------------


def _correr_measure(session: Session, runs_path):
    import argparse

    from saimc.extraction import measure

    lineas: list[str] = []
    codigo = measure.run(
        argparse.Namespace(no_record=False),
        session,
        runs_path=runs_path,
        transcribe=lambda path: "",
        out=lineas.append,
    )
    return codigo, lineas


def _congelar_uno(session: Session) -> None:
    """Un curado a mano con una fila, congelado: lo mínimo para que H0 mida."""
    from decimal import Decimal

    from saimc.extraction import reference_set
    from saimc.models import Currency, PriceHistory, Product

    producto = Product(name="Pollo", category="carnes", unit="lb", aliases=[], is_pilot=True)
    session.add(producto)
    session.flush()
    anuncio_id = _anuncio(
        session,
        ProcessingStatus.CURATED,
        (CurationAction.CURATED, CurationMode.INDIVIDUAL),
    )
    session.add(
        PriceHistory(
            provenance="human",
            product_id=producto.id,
            price=Decimal("100"),
            currency=Currency.CUP.value,
            source_id=anuncio_id,
            published_date=date(2026, 9, 29),
        )
    )
    session.commit()
    reference_set.freeze(session, reference_set.select_members(session, assist_log_path=None))
    session.commit()


def test_measure_imprime_primero_la_metrica_de_la_fase_0_y_despues_h0(
    session: Session, tmp_path
) -> None:
    _anuncio(
        session, ProcessingStatus.CURATED, (CurationAction.CURATED, CurationMode.AUTO)
    )
    _congelar_uno(session)

    codigo, lineas = _correr_measure(session, tmp_path / "h0-runs.jsonl")
    salida = "\n".join(lineas)

    assert codigo == 0
    assert salida.index("Métrica de la Fase 0") < salida.index("=== H0 —")
    assert "Resuelto sin el operador (N1)" in salida
    assert "Publicado sin el operador (N2)" in salida
    assert "tasa de automatización" not in salida.lower()
    # Nada auditado: el bloque lo dice y el número va como provisional.
    assert "sin auditar todavía" in salida
    assert "PROVISIONAL" in salida


def test_la_linea_registrada_trae_la_foto_de_la_particion(
    session: Session, tmp_path
) -> None:
    import json

    _anuncio(
        session, ProcessingStatus.CURATED, (CurationAction.CURATED, CurationMode.AUTO)
    )
    _anuncio(session, ProcessingStatus.PENDING)
    _congelar_uno(session)
    runs = tmp_path / "h0-runs.jsonl"

    _correr_measure(session, runs)
    (linea,) = runs.read_text(encoding="utf-8").splitlines()
    registro = json.loads(linea)

    # La partición total: el congelado es un curado individual (H), más A y P.
    assert (registro["phase0_a"], registro["phase0_h"], registro["phase0_p"]) == (1, 1, 1)
    assert registro["phase0_n1"] == 1 / 3
    assert registro["phase0_n2_min"] == 1 / 3
    assert registro["phase0_n2_max"] == 1 / 2
    assert registro["phase0_lane1_audited"] == 0
    assert registro["phase0_lane0_text_audited"] == 0
    assert registro["phase0_lane0_image_audited"] == 0
    # Las claves de H0 siguen ahí.
    assert "complete_accuracy" in registro
    assert "reference_set_version" in registro


def test_una_linea_vieja_sin_particion_se_sigue_leyendo(tmp_path) -> None:
    import json

    from saimc.extraction.measure import read_h0_runs

    runs = tmp_path / "h0-runs.jsonl"
    runs.write_text(
        json.dumps({"timestamp": "2026-09-01T00:00:00+00:00", "complete_accuracy": 0.4})
        + "\n",
        encoding="utf-8",
    )

    (resumen,) = read_h0_runs(runs)

    assert resumen.complete_accuracy == 0.4
