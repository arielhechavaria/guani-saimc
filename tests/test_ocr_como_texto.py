"""El texto del OCR de una imagen entra al flujo de texto (slice OCR como texto).

Contrato: `docs/spec-slice-ocr-como-texto.md`, sección «Tests primero». Los
esperados están escritos A MANO desde la especificación, antes que el código:

- la imagen trae, leído por el OCR, «Arroz a 320 la libra» en un canal de La
  Habana, así que lo aceptado es lo mismo que para el texto nativo (arroz, 320
  CUP, 1 lb, La Habana) y el evento dice `text_source = "ocr"`;
- el freno por tipo usa el criterio del carril 1: pasado el arranque (60
  auditados), frena si la cota superior del error pasa el 10 %. 7 errores en
  60 son 11,7 % observado: frena seguro;
- Jev sobre el OCR: P(sí) = 0,9 dice «es un anuncio» (veta un descarte), 0,05
  dice «no lo es» (lo confirma), con el umbral 0,2 del carril 0.

Ningún esperado sale de correr el código. El modelo, el OCR, Jev y la tasa
son dobles: nada toca la red ni corre tesseract.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from saimc import alerting
from saimc.config import Settings, get_settings
from saimc.extraction import llm_propose_ad, ocr_store, prefill
from saimc.models import (
    Channel,
    ContentType,
    CurationAction,
    CurationEvent,
    JevSignal,
    LlmProposal,
    Platform,
    PriceHistory,
    ProcessingStatus,
    Product,
    Province,
    RawIngestion,
    SourceType,
)
from saimc.validation import jev_signal, lane0, lane1
from saimc.web.app import app
from saimc.web.auth import require_operator
from saimc.web.deps import get_db
from tests.jev_fakes import FAKE_KEY, Api, choice, client, noul

TEXT = ContentType.TEXT.value
IMAGE = ContentType.IMAGE.value

OCR_ARROZ = "Arroz a 320 la libra"
OCR_FUERA_DE_FOCO = "Vendo iPhone 13, 450 USD"
TELEFONO = "5 234 5678"

# --- dobles ---------------------------------------------------------------------


def _fila(**cambios: Any) -> dict[str, Any]:
    fila: dict[str, Any] = {
        "product": "Arroz",
        "price": 320,
        "currency": "CUP",
        "quantity": 1,
        "unit": "lb",
        "province": None,
        "sale_format": "retail",
        "price_basis": "unit",
        "packaging": None,
        "evidence": "Arroz a 320 la libra",
        "currency_explicit": False,
    }
    fila.update(cambios)
    return fila


_ACEPTA = {"discard": False, "discard_reason": None, "rows": [_fila()]}
_DESCARTA = {"discard": True, "discard_reason": "off_focus", "rows": []}


class ClienteFalso:
    """Doble de `anthropic.Anthropic`: siempre contesta `cuerpo`."""

    def __init__(self, cuerpo: object) -> None:
        self._cuerpo = cuerpo
        self.pedidos: list[str] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw: Any) -> SimpleNamespace:
        self.pedidos.append(json.dumps(kw, default=str, ensure_ascii=False))
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=json.dumps(self._cuerpo))],
            usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
        )


def _sin_tasa(key: str, day: date) -> Decimal | None:
    return None


def _ocr_prohibido(path: Path) -> str:
    raise AssertionError("no se paga OCR: la transcripción ya estaba guardada")


# --- datos ----------------------------------------------------------------------


@pytest.fixture
def mundo(session: Session) -> SimpleNamespace:
    arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True)
    lh = Province(code="LH", name="La Habana")
    session.add_all([arroz, lh])
    session.flush()
    canal = Channel(name="Mercado Habana", platform=Platform.WHATSAPP_GROUP, province_id=lh.id)
    otro = Channel(name="Otro Mercado", platform=Platform.WHATSAPP_GROUP, province_id=lh.id)
    session.add_all([canal, otro])
    session.commit()
    return SimpleNamespace(arroz=arroz, lh=lh, canal=canal, otro=otro)


_n = 0


def _anuncio(
    session: Session,
    canal: Channel,
    *,
    tipo: str = TEXT,
    texto: str | None = OCR_ARROZ,
    minutos: int = 0,
) -> RawIngestion:
    """Un pendiente del canal. Una imagen nace sin texto, como en la ingesta."""
    global _n
    _n += 1
    ingesta = RawIngestion(
        source_type=SourceType.WHATSAPP_CHANNEL,
        channel_id=canal.id,
        source_reference=canal.name,
        file_path=f"raw/ocr-{_n}.jpg" if tipo == IMAGE else f"raw/ocr-{_n}.txt",
        content_hash=f"ocr-{_n}",
        content_type=tipo,
        original_text=None if tipo == IMAGE else texto,
        processing_status=ProcessingStatus.PENDING,
        captured_at=datetime(2026, 9, 1, 12, 0, tzinfo=UTC) + timedelta(minutes=minutos),
    )
    session.add(ingesta)
    session.commit()
    return ingesta


def _con_ocr(session: Session, imagen: RawIngestion, texto: str, tmp_path: Path) -> None:
    """Guarda la transcripción de la imagen, como `ocr_backfill`."""
    ocr_store.stored_transcription(session, imagen, tmp_path / "raw", lambda path: texto)
    session.commit()


def _ajustes(tmp_path: Path, **kw: Any) -> Settings:
    base: dict[str, Any] = {
        "anthropic_api_key": "clave-de-prueba",
        "llm_assist_log_path": tmp_path / "assist.jsonl",
        "raw_store_dir": tmp_path / "raw",
        "prefill_lock_path": tmp_path / "prefill.lock",
        "prefill_status_path": tmp_path / "prefill-status.json",
    }
    base.update(kw)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


def _precalcular(
    session: Session,
    tmp_path: Path,
    cuerpo: object = _ACEPTA,
    *,
    ocr: Any = None,
    api: Api | None = None,
    **ajustes: Any,
) -> str:
    salida: list[str] = []
    with client(api or Api()) as http:
        prefill.execute(
            prefill.parse_args(["--execute"]),
            session,
            _ajustes(tmp_path, **ajustes),
            client=ClienteFalso(cuerpo),
            transcribe=ocr or (lambda path: OCR_ARROZ),
            jev_http=http,
            jev_sleep=lambda s: None,
            rate_fetcher=_sin_tasa,
            out=salida.append,
        )
    return "\n".join(salida)


def _filas(session: Session, ingesta: RawIngestion) -> list[PriceHistory]:
    return list(
        session.execute(select(PriceHistory).where(PriceHistory.source_id == ingesta.id)).scalars()
    )


def _eventos(session: Session, ingesta: RawIngestion) -> list[CurationEvent]:
    return list(
        session.execute(
            select(CurationEvent).where(CurationEvent.ingestion_id == ingesta.id)
        ).scalars()
    )


def _estado(session: Session, ingesta: RawIngestion) -> str:
    session.refresh(ingesta)
    return str(ingesta.processing_status)


# --- los tres interruptores ------------------------------------------------------


def test_los_tres_interruptores_nacen_apagados() -> None:
    ajustes = Settings(_env_file=None)  # type: ignore[call-arg]

    assert ajustes.lane0_image_enabled is False
    assert ajustes.lane1_image_enabled is False
    assert ajustes.jev_ocr_enabled is False


# --- carril 1: aceptación automática de imágenes ---------------------------------


def test_con_el_interruptor_de_imagenes_apagado_una_imagen_con_ocr_perfecto_no_se_acepta(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    imagen = _anuncio(session, mundo.canal, tipo=IMAGE)

    _precalcular(session, tmp_path, lane1_enabled=True)

    assert _estado(session, imagen) == ProcessingStatus.PENDING
    assert _filas(session, imagen) == []
    assert _eventos(session, imagen) == []


def test_el_interruptor_de_imagenes_exige_el_del_carril_1(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    imagen = _anuncio(session, mundo.canal, tipo=IMAGE)

    _precalcular(session, tmp_path, lane1_enabled=False, lane1_image_enabled=True)

    assert _estado(session, imagen) == ProcessingStatus.PENDING
    assert _filas(session, imagen) == []


def test_encendido_una_imagen_con_propuesta_completa_se_acepta_y_el_evento_dice_ocr(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    imagen = _anuncio(session, mundo.canal, tipo=IMAGE)

    salida = _precalcular(session, tmp_path, lane1_enabled=True, lane1_image_enabled=True)

    assert _estado(session, imagen) == ProcessingStatus.CURATED
    (fila,) = _filas(session, imagen)
    assert fila.product_id == mundo.arroz.id
    assert fila.price == Decimal("320")
    assert fila.currency == "CUP"
    assert fila.quantity == Decimal("1")
    assert fila.unit == "lb"
    assert fila.province_id == mundo.lh.id
    assert fila.published_date == date(2026, 9, 1)
    # `provenance` no cambia: es una fila automática como las de texto.
    assert fila.provenance == "auto"
    (evento,) = _eventos(session, imagen)
    assert (evento.action, evento.mode) == (CurationAction.CURATED, "auto")
    assert evento.detail == {"row_count": 1, "text_source": "ocr"}
    assert "imagen 1" in salida


def test_un_texto_aceptado_no_lleva_text_source(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    texto = _anuncio(session, mundo.canal)

    _precalcular(session, tmp_path, lane1_enabled=True, lane1_image_enabled=True)

    (evento,) = _eventos(session, texto)
    assert evento.detail == {"row_count": 1}


# --- freno por tipo ---------------------------------------------------------------


def _aceptados_y_auditados(
    session: Session, tmp_path: Path, canal: Channel, tipo: str, *, errores: int
) -> None:
    """60 anuncios de `tipo` aceptados solos y auditados: `errores` no válidos y
    el resto «está bien»."""
    ingestas = [_anuncio(session, canal, tipo=tipo, minutos=i) for i in range(60)]
    # Sin el tope de arranque de las imágenes (slice auditoría en sombra): esto
    # arma el estado «60 aceptadas», no prueba el tope.
    _precalcular(
        session,
        tmp_path,
        lane1_enabled=True,
        lane1_image_enabled=True,
        lane1_image_weekly_cap=0,
    )
    for ingesta in ingestas:
        assert _estado(session, ingesta) == ProcessingStatus.CURATED
    for ingesta in ingestas[:errores]:
        assert lane1.mark_invalid(session, ingesta.id, "no_data")
    for ingesta in ingestas[errores:]:
        assert lane1.mark_ok(session, ingesta.id)
    session.commit()


def test_freno_60_imagenes_con_error_frenan_las_imagenes_y_no_los_textos(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    _aceptados_y_auditados(session, tmp_path, mundo.canal, IMAGE, errores=7)
    ajustes = _ajustes(tmp_path)

    assert lane1.brake(session, ajustes, content_type=IMAGE).on is True
    assert lane1.brake(session, ajustes, content_type=TEXT).on is False
    imagenes = lane1.audit_state(session, ajustes, content_type=IMAGE)
    assert (imagenes.audited, imagenes.errors) == (60, 7)
    textos = lane1.audit_state(session, ajustes, content_type=TEXT)
    assert (textos.audited, textos.errors) == (0, 0)

    # Y en la corrida: el texto entra, la imagen no.
    texto = _anuncio(session, mundo.otro, minutos=500)
    imagen = _anuncio(session, mundo.otro, tipo=IMAGE, minutos=501)
    salida = _precalcular(session, tmp_path, lane1_enabled=True, lane1_image_enabled=True)

    assert _estado(session, texto) == ProcessingStatus.CURATED
    assert _estado(session, imagen) == ProcessingStatus.PENDING
    assert "freno de imágenes puesto" in salida


def test_freno_60_textos_con_error_frenan_los_textos_y_no_las_imagenes(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    _aceptados_y_auditados(session, tmp_path, mundo.canal, TEXT, errores=7)
    ajustes = _ajustes(tmp_path)

    assert lane1.brake(session, ajustes, content_type=TEXT).on is True
    assert lane1.brake(session, ajustes, content_type=IMAGE).on is False

    texto = _anuncio(session, mundo.otro, minutos=500)
    imagen = _anuncio(session, mundo.otro, tipo=IMAGE, minutos=501)
    _precalcular(session, tmp_path, lane1_enabled=True, lane1_image_enabled=True)

    assert _estado(session, texto) == ProcessingStatus.PENDING
    assert _estado(session, imagen) == ProcessingStatus.CURATED


def test_el_resumen_muestra_las_dos_cotas_por_separado(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    _aceptados_y_auditados(session, tmp_path, mundo.canal, IMAGE, errores=7)

    resumen = lane1.summary(session, _ajustes(tmp_path, lane1_image_enabled=True))

    assert (resumen.state.audited, resumen.state.errors) == (0, 0)
    assert resumen.brake.on is False
    assert (resumen.images.audited, resumen.images.errors) == (60, 7)
    assert resumen.image_brake.on is True


# --- Jev sobre el OCR ---------------------------------------------------------------


def _jev(**kw: Any) -> dict[str, Any]:
    return {"llm_jev_enabled": True, "llm_jev_api_key": FAKE_KEY, **kw}


def test_jev_pregunta_por_una_imagen_con_ocr_guardado_solo_con_su_interruptor(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    texto = _anuncio(session, mundo.canal, texto="Arroz 320 CUP la libra")
    con_ocr = _anuncio(session, mundo.canal, tipo=IMAGE, minutos=1)
    _con_ocr(session, con_ocr, "Pollo 900 el paquete", tmp_path)
    sin_ocr = _anuncio(session, mundo.canal, tipo=IMAGE, minutos=2)
    huella = jev_signal.load_options(session).questions_hash

    # Apagado: solo el texto, como hoy.
    apagado = jev_signal.select_pending(session, huella, include_images=False)
    assert [i.id for i in apagado] == [texto.id]

    # Encendido: el texto y la imagen con OCR guardado, más viejo primero.
    encendido = jev_signal.select_pending(session, huella, include_images=True)
    assert [i.id for i in encendido] == [texto.id, con_ocr.id]
    assert sin_ocr.id not in [i.id for i in encendido]

    # El interruptor gobierna la pasada del pre-cálculo y lo que viaja es el OCR.
    api = Api()
    with client(api) as http:
        jev_signal.prefill_pass(
            session,
            _ajustes(tmp_path, **_jev(jev_ocr_enabled=True)),
            execute=True,
            client=http,
            sleep=lambda s: None,
            out=lambda line: None,
        )
    assert [p["state"] for p in api.payloads()] == ["Arroz 320 CUP la libra", "Pollo 900 el paquete"]


def test_jev_apagado_para_el_ocr_la_pasada_no_pregunta_por_imagenes(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    imagen = _anuncio(session, mundo.canal, tipo=IMAGE)
    _con_ocr(session, imagen, "Pollo 900 el paquete", tmp_path)
    api = Api()

    with client(api) as http:
        jev_signal.prefill_pass(
            session,
            _ajustes(tmp_path, **_jev()),
            execute=True,
            client=http,
            sleep=lambda s: None,
            out=lambda line: None,
        )

    assert api.requests == []


def test_el_interruptor_de_jev_ocr_exige_jev_activo(tmp_path: Path) -> None:
    assert jev_signal.ocr_enabled(_ajustes(tmp_path, jev_ocr_enabled=True)) is False
    assert (
        jev_signal.ocr_enabled(_ajustes(tmp_path, jev_ocr_enabled=True, llm_jev_enabled=True))
        is False
    )
    assert jev_signal.ocr_enabled(_ajustes(tmp_path, **_jev(jev_ocr_enabled=True))) is True
    assert jev_signal.ocr_enabled(_ajustes(tmp_path, **_jev())) is False


def _senal(session: Session, ingesta: RawIngestion, p: float) -> None:
    session.add(
        JevSignal(
            ingestion_id=ingesta.id,
            model="typesafe-ai/jev",
            questions_hash=jev_signal.load_options(session).questions_hash,
            answers={
                "triage": noul(p),
                "product": choice("other", 0.9, 0.9),
                "currency": choice("USD", 0.9, 0.9),
            },
            product_keys={"arroz": "x"},
            input_tokens=100,
            output_tokens=0,
            cost_usd=Decimal("0.00003"),
            source="prefill",
        )
    )
    session.commit()


def _imagen_fuera_de_foco(
    session: Session, mundo: SimpleNamespace, tmp_path: Path, p: float
) -> RawIngestion:
    imagen = _anuncio(session, mundo.canal, tipo=IMAGE)
    _con_ocr(session, imagen, OCR_FUERA_DE_FOCO, tmp_path)
    _senal(session, imagen, p)
    return imagen


_CARRIL0_IMAGEN_CON_JEV = {"lane0_image_enabled": True, "lane0_jev_enabled": True}


def test_carril_0_con_jev_ocr_apagado_una_imagen_no_tiene_segundo_juez(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    # Jev dice «es un anuncio» (0,9), pero apagado no se lo consulta.
    imagen = _imagen_fuera_de_foco(session, mundo, tmp_path, 0.9)

    _precalcular(
        session, tmp_path, _DESCARTA, ocr=_ocr_prohibido, **_jev(**_CARRIL0_IMAGEN_CON_JEV)
    )

    assert _estado(session, imagen) == ProcessingStatus.DISCARDED
    (evento,) = _eventos(session, imagen)
    assert evento.detail is None


def test_carril_0_con_jev_ocr_encendido_jev_veta_el_descarte_de_una_imagen(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    imagen = _imagen_fuera_de_foco(session, mundo, tmp_path, 0.9)

    _precalcular(
        session,
        tmp_path,
        _DESCARTA,
        ocr=_ocr_prohibido,
        **_jev(jev_ocr_enabled=True, **_CARRIL0_IMAGEN_CON_JEV),
    )

    assert _estado(session, imagen) == ProcessingStatus.PENDING
    assert _eventos(session, imagen) == []


def test_carril_0_con_jev_ocr_encendido_jev_confirma_el_descarte_y_queda_su_p(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    imagen = _imagen_fuera_de_foco(session, mundo, tmp_path, 0.05)

    _precalcular(
        session,
        tmp_path,
        _DESCARTA,
        ocr=_ocr_prohibido,
        **_jev(jev_ocr_enabled=True, **_CARRIL0_IMAGEN_CON_JEV),
    )

    assert _estado(session, imagen) == ProcessingStatus.DISCARDED
    (evento,) = _eventos(session, imagen)
    assert evento.detail == {"jev_p": 0.05}


_ARBITRO = {**_CARRIL0_IMAGEN_CON_JEV, "lane0_jev_arbiter_enabled": True}
_DESCARTA_SIN_DATOS = {"discard": True, "discard_reason": "no_data", "rows": []}


@pytest.mark.parametrize(("jev_ocr", "estado"), [(False, "pending"), (True, "discarded")])
def test_carril_0_jev_arbitra_una_imagen_solo_con_jev_ocr(
    session: Session, mundo: SimpleNamespace, tmp_path: Path, jev_ocr: bool, estado: str
) -> None:
    # El modelo descarta, pero el OCR nombra un piloto con dígitos: sin
    # árbitro va a la cola; con Jev en 0,05 sobre el OCR, se descarta.
    imagen = _anuncio(session, mundo.canal, tipo=IMAGE)
    _con_ocr(session, imagen, "Arroz importado 300 CUP", tmp_path)
    _senal(session, imagen, 0.05)

    _precalcular(
        session,
        tmp_path,
        _DESCARTA_SIN_DATOS,
        ocr=_ocr_prohibido,
        **_jev(jev_ocr_enabled=jev_ocr, **_ARBITRO),
    )

    assert _estado(session, imagen) == estado
    if jev_ocr:
        (evento,) = _eventos(session, imagen)
        assert evento.detail == {"jev_p": 0.05, "decider": "jev_arbiter"}


def test_el_comando_del_carril_0_mira_las_imagenes_por_su_ocr_guardado(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    imagen = _anuncio(session, mundo.canal, tipo=IMAGE)
    _precalcular(session, tmp_path, _DESCARTA, ocr=lambda path: OCR_FUERA_DE_FOCO)
    assert _estado(session, imagen) == ProcessingStatus.PENDING
    salida: list[str] = []

    codigo = lane0.run_pending(
        session, _ajustes(tmp_path, lane0_image_enabled=True), execute=True, out=salida.append
    )

    assert codigo == 0
    assert _estado(session, imagen) == ProcessingStatus.DISCARDED


# --- el pre-cálculo pasa el texto redactado a las tres piezas ---------------------


def test_run_prefill_pasa_el_texto_redactado_de_la_imagen_a_las_tres_piezas(
    session: Session, mundo: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    imagen = _anuncio(session, mundo.canal, tipo=IMAGE)
    _con_ocr(session, imagen, f"Arroz a 320 la libra, llamar al {TELEFONO}", tmp_path)
    vistos: dict[str, str] = {}
    original_0, original_1 = lane0.apply_lane_0, lane1.apply_lane_1

    def espia_0(*args: Any, **kw: Any) -> bool:
        vistos["carril 0"] = kw["text"]
        return original_0(*args, **kw)

    def espia_1(*args: Any, **kw: Any) -> bool:
        vistos["carril 1"] = kw["text"]
        return original_1(*args, **kw)

    monkeypatch.setattr(lane0, "apply_lane_0", espia_0)
    monkeypatch.setattr(lane1, "apply_lane_1", espia_1)
    api = Api()

    _precalcular(
        session,
        tmp_path,
        ocr=_ocr_prohibido,
        api=api,
        **_jev(
            jev_ocr_enabled=True,
            lane0_image_enabled=True,
            lane1_enabled=True,
            lane1_image_enabled=True,
        ),
    )

    (pedido,) = api.payloads()
    vistos["Jev"] = pedido["state"]
    assert set(vistos) == {"carril 0", "carril 1", "Jev"}
    for pieza, texto in vistos.items():
        assert TELEFONO not in texto, pieza
        assert "[teléfono removido]" in texto, pieza
        assert "Arroz a 320 la libra" in texto, pieza


# --- selección del pre-cálculo ------------------------------------------------------


def test_el_foco_de_una_imagen_con_ocr_guardado_se_decide_con_ese_texto(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    en_foco = _anuncio(session, mundo.canal, tipo=IMAGE)
    _con_ocr(session, en_foco, OCR_ARROZ, tmp_path)
    fuera = _anuncio(session, mundo.canal, tipo=IMAGE, minutos=1)
    _con_ocr(session, fuera, OCR_FUERA_DE_FOCO, tmp_path)
    sin_ocr = _anuncio(session, mundo.canal, tipo=IMAGE, minutos=2)

    ids = [i.id for i in prefill.select_candidates(session, [mundo.arroz])]

    assert en_foco.id in ids
    assert fuera.id not in ids
    # Sin OCR guardado su foco no se sabe: entra, como hoy.
    assert sin_ocr.id in ids


def test_con_el_carril_0_de_imagenes_entran_las_imagenes_sin_foco(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    fuera = _anuncio(session, mundo.canal, tipo=IMAGE)
    _con_ocr(session, fuera, OCR_FUERA_DE_FOCO, tmp_path)

    ids = [
        i.id
        for i in prefill.select_candidates(
            session, [mundo.arroz], include_off_focus_images=True
        )
    ]

    assert ids == [fuera.id]


def test_stale_incluye_una_imagen_con_ocr_guardado_y_no_una_sin_ocr(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    con_ocr = _anuncio(session, mundo.canal, tipo=IMAGE)
    _con_ocr(session, con_ocr, OCR_ARROZ, tmp_path)
    sin_ocr = _anuncio(session, mundo.canal, tipo=IMAGE, minutos=1)
    for ingesta in (con_ocr, sin_ocr):
        session.add(
            LlmProposal(
                ingestion_id=ingesta.id,
                model="claude-sonnet-5",
                prompt_version="2026-09-25.3",
                status="ok",
                rows=[],
                input_tokens=0,
                output_tokens=0,
            )
        )
    session.commit()

    candidatos = prefill.select_stale_candidates(
        session, [mundo.arroz], llm_propose_ad.PROMPT_VERSION
    )

    assert [c.id for c in candidatos] == [con_ocr.id]


# --- el comando del carril 1 sobre lo pendiente ------------------------------------


def _comando(session: Session, tmp_path: Path, *argv: str, **ajustes: Any) -> str:
    salida: list[str] = []
    lane1.run(
        lane1.parse_args(list(argv)),
        session,
        _ajustes(tmp_path, **ajustes),
        rate_fetcher=_sin_tasa,
        out=salida.append,
    )
    return "\n".join(salida)


def test_el_comando_del_carril_1_en_seco_muestra_las_imagenes_aparte(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    imagen = _anuncio(session, mundo.canal, tipo=IMAGE)
    _precalcular(session, tmp_path)  # deja la propuesta, con el carril apagado

    salida = _comando(session, tmp_path)

    assert "imágenes 1 de 1" in salida
    assert _estado(session, imagen) == ProcessingStatus.PENDING


def test_el_comando_del_carril_1_acepta_imagenes_solo_con_su_interruptor(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    imagen = _anuncio(session, mundo.canal, tipo=IMAGE)
    _precalcular(session, tmp_path)

    _comando(session, tmp_path, "--execute", lane1_enabled=True)
    assert _estado(session, imagen) == ProcessingStatus.PENDING

    _comando(session, tmp_path, "--execute", lane1_enabled=True, lane1_image_enabled=True)
    assert _estado(session, imagen) == ProcessingStatus.CURATED
    (evento,) = _eventos(session, imagen)
    assert evento.detail == {"row_count": 1, "text_source": "ocr"}


# --- los informes muestran los dos tipos por separado -------------------------------


@pytest.fixture
def web(session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
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


def _visible(html: str) -> str:
    return " ".join(html.split())


def test_la_auditoria_muestra_la_imagen_por_su_ocr_y_su_cota_aparte(
    session: Session, mundo: SimpleNamespace, tmp_path: Path, web: TestClient
) -> None:
    _anuncio(session, mundo.canal, tipo=IMAGE)
    _precalcular(session, tmp_path, lane1_enabled=True, lane1_image_enabled=True)

    texto = _visible(web.get("/curation/auditoria").text)

    assert "Imágenes (por su OCR)" in texto
    assert "Imagen · leída por OCR" in texto
    # La tarjeta muestra lo que leyó el motor: el OCR, no un texto vacío.
    assert OCR_ARROZ in texto


def test_con_el_freno_de_imagenes_puesto_las_pantallas_lo_dicen_aparte(
    session: Session, mundo: SimpleNamespace, tmp_path: Path, web: TestClient
) -> None:
    _aceptados_y_auditados(session, tmp_path, mundo.canal, IMAGE, errores=7)

    auditoria = _visible(web.get("/curation/auditoria").text)
    calidad = _visible(web.get("/control/calidad").text)

    for texto in (auditoria, calidad):
        assert "Freno de imágenes puesto" in texto
        # El de los textos no está puesto: no se mezclan.
        assert "Freno puesto" not in texto
    assert "7 errores en 60 auditadas" in calidad


def test_la_metodologia_publica_el_error_de_las_fotos_aparte(
    session: Session, mundo: SimpleNamespace, tmp_path: Path, web: TestClient
) -> None:
    _aceptados_y_auditados(session, tmp_path, mundo.canal, IMAGE, errores=7)

    texto = _visible(web.get("/metodologia").text)

    assert "Las fotos nunca entran solas" not in texto
    assert "Las fotos se auditan y se miden aparte de los textos" in texto
    assert "En las fotos, aparte, la auditoría encontró 7 errores en 60 fotos auditadas" in texto


def test_el_aviso_diario_nombra_el_freno_de_imagenes(
    session: Session, mundo: SimpleNamespace, tmp_path: Path
) -> None:
    _aceptados_y_auditados(session, tmp_path, mundo.canal, IMAGE, errores=7)

    alertas, resumen = alerting.collect(
        session, _ajustes(tmp_path, lane1_enabled=True, lane1_image_enabled=True)
    )

    claves = {a.key for a in alertas}
    assert "lane1_image_brake" in claves
    assert "lane1_brake" not in claves
    assert "imágenes FRENADO" in resumen
