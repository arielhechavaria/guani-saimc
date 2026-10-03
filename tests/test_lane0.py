"""Carril 0 encendido: descarte automático con auditoría (slice B2a).

Contrato: `docs/spec-slice-B2a.md`. Los casos esperados están escritos A MANO
acá, antes que el código y con independencia de él: los conteos salen de
contar el fixture a ojo y las cotas de Clopper-Pearson, de la propia
especificación (0 en 30 da 9,5 %; 1 en 15 da 27,9 %). Ningún esperado sale de
correr el pre-cálculo ni la auditoría.

El modelo es un doble: ningún test toca la red ni necesita clave real.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from saimc.config import Settings, get_settings
from saimc.extraction import prefill
from saimc.models import (
    ContentType,
    CurationAction,
    CurationEvent,
    CurationMode,
    LlmProposal,
    LlmReview,
    ProcessingStatus,
    Product,
    Province,
    RawIngestion,
    ReviewOutcome,
    SourceType,
)
from saimc.validation import lane0
from saimc.web.app import app
from saimc.web.auth import require_operator
from saimc.web.deps import get_db

# --- el modelo falso ---------------------------------------------------------

_DESCARTA_FUERA_DE_FOCO = {"discard": True, "discard_reason": "off_focus", "rows": []}
_DESCARTA_SIN_DATOS = {"discard": True, "discard_reason": "no_data", "rows": []}


class ClienteFalso:
    """Doble de `anthropic.Anthropic`: siempre contesta `cuerpo`, salvo que
    `errores` traiga una excepción para la llamada (cola, una por llamada)."""

    def __init__(self, cuerpo: object, errores: list | None = None) -> None:
        self._cuerpo = cuerpo
        self._errores = list(errores or [])
        self.llamadas: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw: Any) -> SimpleNamespace:
        self.llamadas.append(kw)
        error = self._errores.pop(0) if self._errores else None
        if error is not None:
            raise error
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=json.dumps(self._cuerpo))],
            usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
        )


@pytest.fixture
def catalogo(session: Session) -> SimpleNamespace:
    arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True)
    lh = Province(code="LH", name="La Habana")
    session.add_all([arroz, lh])
    session.commit()
    return SimpleNamespace(arroz=arroz, lh=lh)


_n = 0


def _ingesta(
    session: Session,
    *,
    texto: str | None = "Buenos días grupo, feliz domingo",
    tipo: str = ContentType.TEXT,
    estado: str = ProcessingStatus.PENDING,
    minutos: int = 0,
) -> RawIngestion:
    global _n
    _n += 1
    ingesta = RawIngestion(
        source_type=SourceType.INBOX_TEXT if tipo == ContentType.TEXT else SourceType.INBOX_IMAGE,
        file_path=f"raw/l0-{_n}.png" if tipo == ContentType.IMAGE else f"raw/l0-{_n}.txt",
        content_hash=f"l0-{_n}",
        content_type=tipo,
        original_text=texto,
        processing_status=estado,
        captured_at=datetime(2026, 9, 1, tzinfo=UTC) + timedelta(minutes=minutos),
    )
    session.add(ingesta)
    session.commit()
    return ingesta


def _ajustes(tmp_path: Path, **kw: Any) -> Settings:
    base: dict[str, Any] = {
        "anthropic_api_key": "clave-de-prueba",
        "llm_assist_log_path": tmp_path / "assist.jsonl",
        "raw_store_dir": tmp_path / "raw",
    }
    base.update(kw)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


def _correr(
    session: Session,
    tmp_path: Path,
    cliente: ClienteFalso,
    *,
    transcribe: Any = None,
    **ajustes: Any,
) -> str:
    salida: list[str] = []
    prefill.execute(
        prefill.parse_args(["--execute"]),
        session,
        _ajustes(tmp_path, **ajustes),
        client=cliente,
        transcribe=transcribe or (lambda path: ""),
        out=salida.append,
    )
    return "\n".join(salida)


def _eventos(session: Session, ingesta: RawIngestion | None = None) -> list[CurationEvent]:
    stmt = select(CurationEvent)
    if ingesta is not None:
        stmt = stmt.where(CurationEvent.ingestion_id == ingesta.id)
    return list(session.execute(stmt).scalars())


def _sigue_pendiente_sin_evento(session: Session, ingesta: RawIngestion) -> None:
    session.refresh(ingesta)
    assert ingesta.processing_status == ProcessingStatus.PENDING
    assert ingesta.discard_reason is None
    assert _eventos(session, ingesta) == []


# --- caso 1: interruptores apagados -------------------------------------------


def test_caso_1_interruptores_apagados_el_precalculo_es_el_de_hoy(
    session: Session, catalogo: SimpleNamespace, tmp_path: Path
) -> None:
    en_foco = _ingesta(session, texto="arroz 100 la libra")
    sin_foco = _ingesta(session, texto="Buenos días grupo", minutos=1)
    cliente = ClienteFalso(_DESCARTA_FUERA_DE_FOCO)

    _correr(session, tmp_path, cliente)

    # Solo el texto en foco pasó por el modelo, como hoy.
    propuestas = list(session.execute(select(LlmProposal)).scalars())
    assert [p.ingestion_id for p in propuestas] == [en_foco.id]
    # El modelo lo descarta, pero con el interruptor apagado nadie lo aplica.
    _sigue_pendiente_sin_evento(session, en_foco)
    _sigue_pendiente_sin_evento(session, sin_foco)
    assert [e for e in _eventos(session) if e.mode == "auto"] == []


# --- caso 2: los textos sin foco entran con el interruptor encendido ------------


def test_caso_2_texto_sin_piloto_entra_en_candidatos_solo_con_el_interruptor(
    session: Session, catalogo: SimpleNamespace
) -> None:
    en_foco = _ingesta(session, texto="arroz 100 la libra")
    sin_foco = _ingesta(session, texto="Buenos días grupo", minutos=1)
    productos = [catalogo.arroz]

    apagado = prefill.select_candidates(session, productos)
    encendido = prefill.select_candidates(session, productos, include_off_focus=True)

    assert [i.id for i in apagado] == [en_foco.id]
    assert [i.id for i in encendido] == [en_foco.id, sin_foco.id]


# --- caso 3: el carril 0 descarta un texto --------------------------------------


def test_caso_3_texto_descartado_por_fuera_de_foco_queda_descartado_con_evento_auto(
    session: Session, catalogo: SimpleNamespace, tmp_path: Path
) -> None:
    ingesta = _ingesta(session, texto="Buenos días grupo, feliz domingo")
    cliente = ClienteFalso(_DESCARTA_FUERA_DE_FOCO)

    salida = _correr(session, tmp_path, cliente, lane0_text_enabled=True)

    session.refresh(ingesta)
    assert ingesta.processing_status == ProcessingStatus.DISCARDED
    assert ingesta.discard_reason == "off_focus"
    propuesta = session.execute(select(LlmProposal)).scalars().one()
    revision = session.execute(select(LlmReview)).scalars().one()
    assert revision.ingestion_id == ingesta.id
    assert revision.proposal_id == propuesta.id
    assert revision.outcome == ReviewOutcome.DISCARDED
    assert revision.discard_reason == "off_focus"
    (evento,) = _eventos(session, ingesta)
    assert evento.action == CurationAction.DISCARDED
    assert evento.mode == "auto"
    assert evento.discard_reason == "off_focus"
    assert evento.proposal_id == propuesta.id
    assert evento.shown_at is None
    assert evento.decided_at is not None
    # El resumen dice cuántos se descartaron solos, por tipo.
    assert "Descartados solos (carril 0): texto 1, imagen 0" in salida


def test_caso_3_tambien_por_falta_de_datos(
    session: Session, catalogo: SimpleNamespace, tmp_path: Path
) -> None:
    ingesta = _ingesta(session, texto="Hola, alguien sabe a qué hora abren")

    _correr(session, tmp_path, ClienteFalso(_DESCARTA_SIN_DATOS), lane0_text_enabled=True)

    session.refresh(ingesta)
    assert ingesta.processing_status == ProcessingStatus.DISCARDED
    assert ingesta.discard_reason == "no_data"


# --- casos 4, 5 y 6: lo que el carril 0 no toca --------------------------------


def test_caso_4_texto_que_nombra_un_piloto_sigue_pendiente(
    session: Session, catalogo: SimpleNamespace, tmp_path: Path
) -> None:
    ingesta = _ingesta(session, texto="arroz 100 la libra")

    _correr(session, tmp_path, ClienteFalso(_DESCARTA_FUERA_DE_FOCO), lane0_text_enabled=True)

    _sigue_pendiente_sin_evento(session, ingesta)
    assert list(session.execute(select(LlmReview)).scalars()) == []


def test_caso_5_motivo_fuera_de_off_focus_y_no_data_sigue_pendiente(
    session: Session, catalogo: SimpleNamespace, tmp_path: Path
) -> None:
    ingesta = _ingesta(session)
    cuerpo = {"discard": True, "discard_reason": "spam", "rows": []}

    _correr(session, tmp_path, ClienteFalso(cuerpo), lane0_text_enabled=True)

    _sigue_pendiente_sin_evento(session, ingesta)


def test_caso_5_descarte_sin_motivo_sigue_pendiente(
    session: Session, catalogo: SimpleNamespace, tmp_path: Path
) -> None:
    ingesta = _ingesta(session)
    cuerpo: dict[str, Any] = {"discard": True, "discard_reason": None, "rows": []}

    _correr(session, tmp_path, ClienteFalso(cuerpo), lane0_text_enabled=True)

    _sigue_pendiente_sin_evento(session, ingesta)


def test_caso_6_propuesta_degradada_sigue_pendiente(
    session: Session, catalogo: SimpleNamespace, tmp_path: Path
) -> None:
    ingesta = _ingesta(session)
    cliente = ClienteFalso(_DESCARTA_FUERA_DE_FOCO, errores=[RuntimeError("boom")])

    _correr(session, tmp_path, cliente, lane0_text_enabled=True, lane0_image_enabled=True)

    _sigue_pendiente_sin_evento(session, ingesta)
    propuesta = session.execute(select(LlmProposal)).scalars().one()
    assert propuesta.status == "degraded"


# --- caso 7: cada tipo con su interruptor ---------------------------------------


def test_caso_7_imagen_con_su_interruptor_apagado_no_se_descarta_sola(
    session: Session, catalogo: SimpleNamespace, tmp_path: Path
) -> None:
    imagen = _ingesta(session, texto=None, tipo=ContentType.IMAGE)

    _correr(
        session,
        tmp_path,
        ClienteFalso(_DESCARTA_FUERA_DE_FOCO),
        transcribe=lambda path: "foto de un perro en la playa",
        lane0_text_enabled=True,
        lane0_image_enabled=False,
    )

    _sigue_pendiente_sin_evento(session, imagen)


def test_imagen_con_su_interruptor_encendido_se_descarta_sin_repetir_el_ocr(
    session: Session, catalogo: SimpleNamespace, tmp_path: Path
) -> None:
    imagen = _ingesta(session, texto=None, tipo=ContentType.IMAGE)
    texto_ = _ingesta(session, texto="Buenos días grupo", minutos=1)
    ocr: list[Path] = []

    def transcribe(path: Path) -> str:
        ocr.append(path)
        return "foto de un perro en la playa"

    salida = _correr(
        session,
        tmp_path,
        ClienteFalso(_DESCARTA_FUERA_DE_FOCO),
        transcribe=transcribe,
        lane0_text_enabled=False,
        lane0_image_enabled=True,
    )

    session.refresh(imagen)
    assert imagen.processing_status == ProcessingStatus.DISCARDED
    assert [e.mode for e in _eventos(session, imagen)] == ["auto"]
    assert len(ocr) == 1  # el contexto del motor recibe el texto que ya vio el modelo
    # Con el de texto apagado, el texto sin foco ni siquiera pasó por el modelo.
    _sigue_pendiente_sin_evento(session, texto_)
    assert "Descartados solos (carril 0): texto 0, imagen 1" in salida


def test_imagen_cuyo_ocr_nombra_un_piloto_sigue_pendiente(
    session: Session, catalogo: SimpleNamespace, tmp_path: Path
) -> None:
    imagen = _ingesta(session, texto=None, tipo=ContentType.IMAGE)

    _correr(
        session,
        tmp_path,
        ClienteFalso(_DESCARTA_FUERA_DE_FOCO),
        transcribe=lambda path: "Arroz importado 300 CUP",
        lane0_image_enabled=True,
    )

    _sigue_pendiente_sin_evento(session, imagen)


# --- auditoría: armado del fixture ----------------------------------------------

# Semana cerrada de prueba: lunes 2026-09-21 a domingo 2026-09-27 (ISO 2026-W39).
SEMANA = date(2026, 9, 21)
# «Hoy» para las pruebas de funciones: jueves de la semana siguiente (W40).
HOY = date(2026, 10, 1)


def _auto_descartado(
    session: Session,
    *,
    tipo: str = ContentType.TEXT,
    cuando: datetime | None = None,
    motivo: str = "off_focus",
) -> RawIngestion:
    """Un anuncio como lo deja el carril 0: descartado, con su propuesta `ok`,
    su revisión y el evento `discarded/auto`."""
    ingesta = _ingesta(
        session,
        texto="Buenos días grupo" if tipo == ContentType.TEXT else None,
        tipo=tipo,
        estado=ProcessingStatus.DISCARDED,
    )
    ingesta.discard_reason = motivo
    propuesta = LlmProposal(
        ingestion_id=ingesta.id,
        model="modelo-de-prueba",
        prompt_version="prueba",
        status="ok",
        discard=True,
        discard_reason=motivo,
        rows=[],
        input_tokens=0,
        output_tokens=0,
    )
    session.add(propuesta)
    session.flush()
    decidido = cuando or datetime(2026, 9, 22, 15, 0, tzinfo=UTC)
    session.add(
        LlmReview(
            ingestion_id=ingesta.id,
            proposal_id=propuesta.id,
            outcome=ReviewOutcome.DISCARDED,
            discard_reason=motivo,
            rows=[],
            created_at=decidido,
        )
    )
    session.add(
        CurationEvent(
            ingestion_id=ingesta.id,
            action=CurationAction.DISCARDED,
            mode="auto",
            discard_reason=motivo,
            proposal_id=propuesta.id,
            decided_at=decidido,
        )
    )
    session.commit()
    return ingesta


def _ids(items: Any) -> list[uuid.UUID]:
    return [item.ingestion.id for item in items]


# --- caso 8: la muestra -----------------------------------------------------------


def test_caso_8_la_muestra_es_estable_y_tiene_a_lo_sumo_30_textos_y_15_imagenes(
    session: Session, catalogo: SimpleNamespace
) -> None:
    textos = [_auto_descartado(session) for _ in range(40)]
    imagenes = [_auto_descartado(session, tipo=ContentType.IMAGE) for _ in range(20)]

    una = lane0.week_sample(session, SEMANA, today=HOY)
    otra = lane0.week_sample(session, SEMANA, today=HOY)

    assert _ids(una.texts) == _ids(otra.texts)
    assert _ids(una.images) == _ids(otra.images)
    assert len(una.texts) == 30
    assert len(una.images) == 15
    assert set(_ids(una.texts)) <= {t.id for t in textos}
    assert set(_ids(una.images)) <= {i.id for i in imagenes}
    assert len(set(_ids(una.texts))) == 30  # sin repetidos


def test_caso_8_si_hay_menos_entran_todos(session: Session, catalogo: SimpleNamespace) -> None:
    textos = [_auto_descartado(session) for _ in range(5)]
    imagenes = [_auto_descartado(session, tipo=ContentType.IMAGE) for _ in range(2)]
    # Fuera de la semana: el lunes siguiente ya es otra.
    _auto_descartado(session, cuando=datetime(2026, 9, 28, 0, 30, tzinfo=UTC))

    muestra = lane0.week_sample(session, SEMANA, today=HOY)

    assert set(_ids(muestra.texts)) == {t.id for t in textos}
    assert set(_ids(muestra.images)) == {i.id for i in imagenes}


def test_la_semilla_sale_de_la_semana() -> None:
    assert lane0.week_label(SEMANA) == "2026-W39"
    assert lane0.sample_seed(SEMANA) == "lane0-audit-2026-W39"
    assert lane0.parse_week("2026-W39") == SEMANA
    assert lane0.parse_week("cualquier cosa") is None
    assert lane0.last_closed_week(HOY) == SEMANA


def test_las_metas_y_los_tamanos_son_los_de_g1a() -> None:
    assert lane0.TARGETS == {"text": 0.10, "image": 0.20}
    assert lane0.SAMPLE_SIZES == {"text": 30, "image": 15}


# --- caso 9: la semana en curso no se muestrea -------------------------------------


def test_caso_9_un_evento_de_la_semana_en_curso_no_entra_en_ninguna_muestra(
    session: Session, catalogo: SimpleNamespace
) -> None:
    en_curso = _auto_descartado(session, cuando=datetime(2026, 9, 29, 15, 0, tzinfo=UTC))

    muestra = lane0.week_sample(session, date(2026, 9, 28), today=HOY)

    assert muestra.texts == []
    assert muestra.images == []
    assert lane0.rescue(session, en_curso.id, date(2026, 9, 28), today=HOY) is False
    session.refresh(en_curso)
    assert en_curso.processing_status == ProcessingStatus.DISCARDED


# --- casos 10 y 11: rescatar -----------------------------------------------------


def test_caso_10_rescatar_vuelve_a_pending_y_el_precalculo_no_lo_retoma(
    session: Session, catalogo: SimpleNamespace
) -> None:
    ingesta = _auto_descartado(session)

    assert lane0.rescue(session, ingesta.id, SEMANA, today=HOY) is True
    session.commit()

    session.refresh(ingesta)
    assert ingesta.processing_status == ProcessingStatus.PENDING
    assert ingesta.discard_reason is None
    rescate = [e for e in _eventos(session, ingesta) if e.action == "rescued"]
    assert len(rescate) == 1
    assert rescate[0].mode == CurationMode.INDIVIDUAL
    # Ya tiene propuesta `ok`: el pre-cálculo no lo vuelve a tomar.
    candidatos = prefill.select_candidates(
        session, [catalogo.arroz], include_off_focus=True
    )
    assert ingesta.id not in [c.id for c in candidatos]


def test_caso_11_rescatar_lo_que_no_esta_descartado_no_cambia_nada(
    session: Session, catalogo: SimpleNamespace
) -> None:
    ingesta = _auto_descartado(session)
    assert lane0.rescue(session, ingesta.id, SEMANA, today=HOY) is True
    session.commit()
    eventos_antes = len(_eventos(session, ingesta))

    # Ya está pendiente: un segundo rescate no escribe nada.
    assert lane0.rescue(session, ingesta.id, SEMANA, today=HOY) is False
    session.commit()

    assert len(_eventos(session, ingesta)) == eventos_antes


def test_caso_11_rescatar_lo_que_no_esta_en_la_muestra_no_cambia_nada(
    session: Session, catalogo: SimpleNamespace
) -> None:
    textos = [_auto_descartado(session) for _ in range(35)]
    muestra = set(_ids(lane0.week_sample(session, SEMANA, today=HOY).texts))
    fuera = next(t for t in textos if t.id not in muestra)
    # Ni de la semana: un descarte de a uno del operador.
    ajeno = _ingesta(session, estado=ProcessingStatus.DISCARDED)

    assert lane0.rescue(session, fuera.id, SEMANA, today=HOY) is False
    assert lane0.rescue(session, ajeno.id, SEMANA, today=HOY) is False
    assert lane0.rescue(session, uuid.uuid4(), SEMANA, today=HOY) is False
    session.commit()

    session.refresh(fuera)
    assert fuera.processing_status == ProcessingStatus.DISCARDED
    assert [e.action for e in _eventos(session, fuera)] == ["discarded"]
    assert _eventos(session, ajeno) == []


def test_bien_descartado_escribe_audited_y_nada_mas(
    session: Session, catalogo: SimpleNamespace
) -> None:
    ingesta = _auto_descartado(session)

    assert lane0.mark_audited(session, ingesta.id, SEMANA, today=HOY) is True
    session.commit()

    session.refresh(ingesta)
    assert ingesta.processing_status == ProcessingStatus.DISCARDED
    assert ingesta.discard_reason == "off_focus"
    auditado = [e for e in _eventos(session, ingesta) if e.action == "audited"]
    assert len(auditado) == 1
    assert auditado[0].mode == CurationMode.INDIVIDUAL


# --- caso 12: la cota --------------------------------------------------------------


def _textos_auditados_sin_rescate(session: Session) -> None:
    for _ in range(30):
        ingesta = _auto_descartado(session)
        assert lane0.mark_audited(session, ingesta.id, SEMANA, today=HOY)
    session.commit()


def _imagenes_con_un_rescate(session: Session, semana: date = SEMANA) -> None:
    cuando = datetime.combine(semana + timedelta(days=1), datetime.min.time(), UTC) + timedelta(
        hours=15
    )
    imagenes = [
        _auto_descartado(session, tipo=ContentType.IMAGE, cuando=cuando) for _ in range(15)
    ]
    today = semana + timedelta(days=10)
    assert lane0.rescue(session, imagenes[0].id, semana, today=today)
    for imagen in imagenes[1:]:
        assert lane0.mark_audited(session, imagen.id, semana, today=today)
    session.commit()


def test_caso_12_la_cota_por_tipo_contra_su_meta(
    session: Session, catalogo: SimpleNamespace
) -> None:
    _textos_auditados_sin_rescate(session)
    _imagenes_con_un_rescate(session)

    cotas = lane0.audit_bounds(session, today=HOY)

    texto, imagen = cotas["text"], cotas["image"]
    assert (texto.k, texto.n) == (0, 30)
    assert texto.upper == pytest.approx(0.095, abs=0.0005)
    assert texto.exceeded is False
    assert (imagen.k, imagen.n) == (1, 15)
    assert imagen.upper == pytest.approx(0.279, abs=0.0005)
    assert imagen.exceeded is True


def test_la_cota_se_acumula_entre_semanas(session: Session, catalogo: SimpleNamespace) -> None:
    _imagenes_con_un_rescate(session, SEMANA)
    _imagenes_con_un_rescate(session, SEMANA - timedelta(weeks=1))

    imagen = lane0.audit_bounds(session, today=HOY)["image"]

    assert (imagen.k, imagen.n) == (2, 30)


def test_lo_pendiente_de_auditar_no_entra_en_la_cota(
    session: Session, catalogo: SimpleNamespace
) -> None:
    for _ in range(3):
        _auto_descartado(session)

    texto = lane0.audit_bounds(session, today=HOY)["text"]

    assert (texto.k, texto.n) == (0, 0)
    assert texto.upper is None
    assert texto.exceeded is False


# --- casos 12 y 15 en pantalla --------------------------------------------------


@pytest.fixture
def client(session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    def _get_db_override() -> Iterator[Session]:
        yield session

    inbox = tmp_path / "inbox"
    inbox.mkdir()
    monkeypatch.setenv("SAIMC_INBOX_DIR", str(inbox))
    monkeypatch.setenv("SAIMC_RAW_STORE_DIR", str(tmp_path / "data" / "raw"))
    get_settings.cache_clear()
    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[require_operator] = lambda: None
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    get_settings.cache_clear()


def _ultima_semana_cerrada() -> date:
    hoy = date.today()
    return hoy - timedelta(days=hoy.weekday()) - timedelta(weeks=1)


def test_caso_12_la_pantalla_marca_la_cota_que_supera_la_meta(
    client: TestClient, session: Session, catalogo: SimpleNamespace
) -> None:
    _imagenes_con_un_rescate(session, _ultima_semana_cerrada())

    html = client.get("/curation/auditoria").text

    assert "27,9" in html
    assert "SAIMC_LANE0_IMAGE_ENABLED" in html
    assert "is-over-target" in html


def test_la_pantalla_lista_la_muestra_de_la_ultima_semana_cerrada(
    client: TestClient, session: Session, catalogo: SimpleNamespace
) -> None:
    semana = _ultima_semana_cerrada()
    cuando = datetime.combine(semana + timedelta(days=2), datetime.min.time(), UTC) + timedelta(
        hours=15
    )
    ingesta = _auto_descartado(session, cuando=cuando)

    html = client.get("/curation/auditoria").text

    assert lane0.week_label(semana) in html
    assert f"/curation/auditoria/{ingesta.id}/rescatar" in html
    assert f"/curation/auditoria/{ingesta.id}/bien" in html
    assert "Buenos días grupo" in html


def test_rescatar_desde_la_pantalla(
    client: TestClient, session: Session, catalogo: SimpleNamespace
) -> None:
    semana = _ultima_semana_cerrada()
    cuando = datetime.combine(semana + timedelta(days=2), datetime.min.time(), UTC) + timedelta(
        hours=15
    )
    ingesta = _auto_descartado(session, cuando=cuando)

    response = client.post(
        f"/curation/auditoria/{ingesta.id}/rescatar",
        data={"semana": lane0.week_label(semana)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    session.refresh(ingesta)
    assert ingesta.processing_status == ProcessingStatus.PENDING


def test_caso_15_control_muestra_los_automaticos_pero_no_la_cota(
    client: TestClient, session: Session, catalogo: SimpleNamespace
) -> None:
    hoy = date.today()
    lunes = hoy - timedelta(days=hoy.weekday())
    cuando = datetime.combine(lunes, datetime.min.time(), UTC) + timedelta(hours=1)
    for _ in range(3):
        _auto_descartado(session, cuando=cuando)
    _auto_descartado(session, tipo=ContentType.IMAGE, cuando=cuando)

    html = client.get("/control").text

    assert "Descartes automáticos" in html
    assert "cota" not in html.lower()
    assert "Clopper" not in html


def test_caso_15_la_auditoria_pide_operador(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def _get_db_override() -> Iterator[Session]:
        yield session

    monkeypatch.setenv("SAIMC_CURATION_PASSWORD", "correcta")
    get_settings.cache_clear()
    app.dependency_overrides[get_db] = _get_db_override
    try:
        with TestClient(app) as test_client:
            assert test_client.get("/curation/auditoria").status_code == 401
            falso = uuid.uuid4()
            assert (
                test_client.post(f"/curation/auditoria/{falso}/rescatar").status_code == 401
            )
            assert test_client.post(f"/curation/auditoria/{falso}/bien").status_code == 401
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()


# --- lo rescatado sale de los lotes y se cura de a uno ----------------------------


def _rescatado_en_la_ultima_semana(session: Session, tipo: str = ContentType.TEXT) -> RawIngestion:
    semana = _ultima_semana_cerrada()
    cuando = datetime.combine(semana + timedelta(days=2), datetime.min.time(), UTC) + timedelta(
        hours=15
    )
    ingesta = _auto_descartado(session, tipo=tipo, cuando=cuando)
    assert lane0.rescue(session, ingesta.id, semana)
    session.commit()
    return ingesta


def test_un_rescatado_no_aparece_en_el_lote_de_textos_y_otro_si(
    client: TestClient, session: Session, catalogo: SimpleNamespace
) -> None:
    rescatado = _rescatado_en_la_ultima_semana(session)
    otro = _ingesta(session, texto="Buenos días a todos")

    html = client.get("/curation/sin-foco").text

    assert str(rescatado.id) not in html
    assert str(otro.id) in html


def test_el_lote_de_textos_no_descarta_un_rescatado_aunque_venga_en_el_form(
    client: TestClient, session: Session, catalogo: SimpleNamespace
) -> None:
    rescatado = _rescatado_en_la_ultima_semana(session)

    client.post("/curation/sin-foco/discard", data={"ingestion_id": str(rescatado.id)})

    session.refresh(rescatado)
    assert rescatado.processing_status == ProcessingStatus.PENDING


def test_un_rescatado_no_aparece_en_el_lote_de_imagenes_y_otra_si(
    client: TestClient, session: Session, catalogo: SimpleNamespace
) -> None:
    rescatada = _rescatado_en_la_ultima_semana(session, tipo=ContentType.IMAGE)
    otra = _ingesta(session, texto=None, tipo=ContentType.IMAGE)
    session.add(
        LlmProposal(
            ingestion_id=otra.id,
            model="modelo-de-prueba",
            prompt_version="prueba",
            status="ok",
            discard=True,
            discard_reason="off_focus",
            rows=[],
            input_tokens=0,
            output_tokens=0,
        )
    )
    session.commit()

    html = client.get("/curation/sin-foco/imagenes").text
    client.post("/curation/sin-foco/imagenes/discard", data={"ingestion_id": str(rescatada.id)})

    assert str(rescatada.id) not in html
    assert str(otra.id) in html
    session.refresh(rescatada)
    assert rescatada.processing_status == ProcessingStatus.PENDING


def test_la_auditoria_ofrece_curar_ahora_un_rescatado_pendiente(
    client: TestClient, session: Session, catalogo: SimpleNamespace
) -> None:
    rescatado = _rescatado_en_la_ultima_semana(session)

    html = client.get("/curation/auditoria").text

    assert f'href="/curation?id={rescatado.id}"' in html
    assert "Curar ahora" in html


def test_curar_ahora_abre_el_formulario_de_un_texto_sin_piloto(
    client: TestClient, session: Session, catalogo: SimpleNamespace
) -> None:
    rescatado = _rescatado_en_la_ultima_semana(session)

    response = client.get(f"/curation?id={rescatado.id}")

    assert response.status_code == 200
    assert f'action="/curation/{rescatado.id}"' in response.text
