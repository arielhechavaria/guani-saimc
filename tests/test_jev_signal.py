"""`saimc.validation.jev_signal` — la señal de Jev en sombra (slice A6b-1).

Casos 1 (la parte de las llamadas), 2 a 7, 11 a 14 y 18 a 22 de «Casos
esperados» de `docs/spec-slice-A6b.md`. Lo que pasa al curar, al editar y en
`revalidate` (casos 1, 2, 3, 15, 16 y 17) está en `test_validation_persist.py`;
la pasada dentro del pre-cálculo, en `test_prefill.py`; `/control`, en
`test_control.py`.

Los valores esperados salen de la especificación, escritos antes que el
código. Fixture común: pilotos arroz, pollo y aceite; café existe y no es
piloto. Nada toca la red (`tests/jev_fakes.py`).
"""

from __future__ import annotations

import importlib.util
import logging
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from saimc.config import Settings
from saimc.models import (
    ContentType,
    JevSignal,
    ProcessingStatus,
    Product,
    RawIngestion,
    SourceType,
)
from saimc.validation import jev_client, jev_signal
from tests.jev_fakes import ANSWERS_OK, FAKE_KEY, Api, body, choice, client, noul

ASKED_AT = datetime(2026, 10, 5, 12, 0, 7, tzinfo=UTC)

_n = 0


@pytest.fixture
def mundo(session: Session) -> SimpleNamespace:
    arroz = Product(name="Arroz", category="granos", unit="lb", aliases=[], is_pilot=True)
    pollo = Product(name="Pollo", category="carnes", unit="lb", aliases=[], is_pilot=True)
    aceite = Product(name="Aceite", category="aceites", unit="l", aliases=[], is_pilot=True)
    cafe = Product(name="Café", category="otros", unit="lb", aliases=[], is_pilot=False)
    session.add_all([arroz, pollo, aceite, cafe])
    session.commit()
    return SimpleNamespace(arroz=arroz, pollo=pollo, aceite=aceite, cafe=cafe)


def _ingesta(
    session: Session,
    *,
    texto: str | None = "Arroz 320 CUP la libra",
    tipo: str = ContentType.TEXT,
    estado: str = ProcessingStatus.PENDING,
    minutos: int = 0,
) -> RawIngestion:
    global _n
    _n += 1
    ingesta = RawIngestion(
        source_type=SourceType.INBOX_TEXT,
        file_path=f"raw/jev-{_n}.txt",
        content_hash=f"hash-jev-{_n}",
        content_type=tipo,
        original_text=texto,
        processing_status=estado,
        captured_at=datetime(2026, 10, 1, tzinfo=UTC) + timedelta(minutes=minutos),
    )
    session.add(ingesta)
    session.commit()
    return ingesta


def _ajustes(**kw: Any) -> Settings:
    base: dict[str, Any] = {"llm_jev_enabled": True, "llm_jev_api_key": FAKE_KEY}
    base.update(kw)
    # `_env_file=None`: que el `.env` del operador (y su clave) no se cuele.
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


def _pasada(
    session: Session, api: Api, *, ajustes: Settings | None = None, execute: bool = True
) -> tuple[jev_signal.PassResult | None, list[float], str]:
    esperas: list[float] = []
    salida: list[str] = []
    with client(api) as http:
        resultado = jev_signal.prefill_pass(
            session,
            ajustes or _ajustes(),
            execute=execute,
            client=http,
            sleep=esperas.append,
            out=salida.append,
        )
    return resultado, esperas, "\n".join(salida)


def _senales(session: Session) -> list[JevSignal]:
    session.expire_all()
    return list(session.execute(select(JevSignal).order_by(JevSignal.created_at)).scalars())


# --- Configuración --------------------------------------------------------------


def test_los_valores_por_defecto_dejan_a_jev_apagado() -> None:
    ajustes = Settings(_env_file=None)  # type: ignore[call-arg]

    assert ajustes.llm_jev_enabled is False
    assert ajustes.llm_jev_api_key is None
    assert ajustes.llm_jev_timeout == 10.0
    assert ajustes.llm_jev_max_cost == 0.01
    assert jev_signal.is_active(ajustes) is False


@pytest.mark.parametrize(
    ("encendido", "clave", "activo"),
    [
        (False, None, False),
        (False, FAKE_KEY, False),
        (True, None, False),
        (True, "", False),
        (True, FAKE_KEY, True),
    ],
)
def test_activo_es_interruptor_encendido_y_clave_no_vacia(
    encendido: bool, clave: str | None, activo: bool
) -> None:
    ajustes = _ajustes(llm_jev_enabled=encendido, llm_jev_api_key=clave)

    assert jev_signal.is_active(ajustes) is activo


@pytest.mark.parametrize("valor", ["abc", "-1", "0", "nan"])
def test_un_tiempo_limite_mal_escrito_cae_en_el_valor_por_defecto_y_avisa(
    valor: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("SAIMC_LLM_JEV_TIMEOUT", valor)

    with caplog.at_level(logging.WARNING):
        ajustes = Settings(_env_file=None)  # type: ignore[call-arg]

    assert ajustes.llm_jev_timeout == 10.0
    assert "SAIMC_LLM_JEV_TIMEOUT" in caplog.text


@pytest.mark.parametrize("valor", ["mucho", "-0.5", "inf"])
def test_un_tope_mal_escrito_cae_en_el_valor_por_defecto_y_avisa(
    valor: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("SAIMC_LLM_JEV_MAX_COST", valor)

    with caplog.at_level(logging.WARNING):
        ajustes = Settings(_env_file=None)  # type: ignore[call-arg]

    assert ajustes.llm_jev_max_cost == 0.01
    assert "SAIMC_LLM_JEV_MAX_COST" in caplog.text


def test_un_tope_en_cero_es_valido(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SAIMC_LLM_JEV_MAX_COST", "0")

    assert Settings(_env_file=None).llm_jev_max_cost == 0.0  # type: ignore[call-arg]


# --- Opciones y huella ----------------------------------------------------------


def test_las_opciones_salen_de_los_pilotos(session: Session, mundo: SimpleNamespace) -> None:
    opciones = jev_signal.load_options(session)

    assert opciones.products == {"arroz": "Arroz", "pollo": "Pollo", "aceite": "Aceite"}
    # Qué producto era cada opción al preguntar: clave -> id, como texto.
    assert opciones.product_keys == {
        "arroz": str(mundo.arroz.id),
        "pollo": str(mundo.pollo.id),
        "aceite": str(mundo.aceite.id),
    }
    assert set(opciones.questions) == {"triage", "product", "currency"}
    assert opciones.questions_hash == jev_client.questions_hash(opciones.questions)


def test_caso_10_sumar_un_piloto_cambia_la_huella(session: Session, mundo: SimpleNamespace) -> None:
    antes = jev_signal.load_options(session).questions_hash

    mundo.cafe.is_pilot = True
    session.commit()

    assert jev_signal.load_options(session).questions_hash != antes


# --- Caso 1: apagado, la pasada no llama ----------------------------------------


@pytest.mark.parametrize(
    "ajustes",
    [
        {"llm_jev_enabled": False},
        {"llm_jev_enabled": True, "llm_jev_api_key": None},
    ],
)
def test_caso_1_inactivo_no_hace_ninguna_llamada(
    session: Session, mundo: SimpleNamespace, ajustes: dict[str, Any]
) -> None:
    _ingesta(session)
    api = Api()

    resultado, _, _ = _pasada(session, api, ajustes=_ajustes(**ajustes))

    assert resultado is None
    assert api.requests == []
    assert _senales(session) == []


def test_encendido_y_sin_clave_avisa_en_la_salida(session: Session, mundo: SimpleNamespace) -> None:
    _, _, salida = _pasada(session, Api(), ajustes=_ajustes(llm_jev_api_key=None))

    assert "SAIMC_LLM_JEV_API_KEY" in salida


def test_apagado_no_dice_nada(session: Session, mundo: SimpleNamespace) -> None:
    _, _, salida = _pasada(session, Api(), ajustes=_ajustes(llm_jev_enabled=False))

    assert salida == ""


def test_en_seco_informa_cuantos_preguntaria_y_el_costo_sin_red(
    session: Session, mundo: SimpleNamespace
) -> None:
    _ingesta(session)
    _ingesta(session, minutos=1)
    api = Api()

    resultado, _, salida = _pasada(session, api, execute=False)

    assert resultado is None
    assert api.requests == []
    assert _senales(session) == []
    assert "2 textos" in salida
    assert "USD" in salida


# --- La pasada: alta de la fila -------------------------------------------------


def test_la_pasada_guarda_una_fila_por_anuncio_con_la_respuesta_tal_cual(
    session: Session, mundo: SimpleNamespace
) -> None:
    anuncio = _ingesta(session)
    api = Api()

    resultado, _, _ = _pasada(session, api)

    assert resultado is not None
    assert (resultado.asked, resultado.failed, resultado.invalid, resultado.stopped) == (1, 0, 0, "")
    assert resultado.cost_usd == pytest.approx(0.00003)
    (senal,) = _senales(session)
    assert senal.ingestion_id == anuncio.id
    assert senal.model == "typesafe-ai/jev"
    assert senal.questions_hash == jev_signal.load_options(session).questions_hash
    assert senal.answers == ANSWERS_OK
    assert senal.product_keys == {
        "arroz": str(mundo.arroz.id),
        "pollo": str(mundo.pollo.id),
        "aceite": str(mundo.aceite.id),
    }
    assert (senal.input_tokens, senal.output_tokens) == (500, 0)
    assert senal.cost_usd == Decimal("0.00003")
    assert senal.source == "prefill"


def test_si_el_gateway_no_informa_modelo_ni_costo_se_guarda_el_pedido_y_la_estimacion(
    session: Session, mundo: SimpleNamespace
) -> None:
    _ingesta(session)
    cuerpo = body(cost=None)
    del cuerpo["model"]

    _pasada(session, Api([httpx.Response(200, json=cuerpo)]))

    (senal,) = _senales(session)
    assert senal.model == "typesafe-ai/jev"
    # 500 tokens de entrada a 0,042 USD el millón.
    assert senal.cost_usd == Decimal("0.000021")


def test_caso_8_lo_que_sale_es_el_texto_redactado_y_nada_que_identifique_al_anuncio(
    session: Session, mundo: SimpleNamespace
) -> None:
    anuncio = _ingesta(session, texto="Arroz 320 CUP la libra, llamar al 5 234 5678")
    api = Api()

    _pasada(session, api)

    (pedido,) = api.payloads()
    assert set(pedido) == {"model", "state", "questions"}
    assert "5 234 5678" not in pedido["state"]
    assert "[teléfono removido]" in pedido["state"]
    assert set(pedido["questions"]) == {"triage", "product", "currency"}
    crudo = api.requests[0].content.decode("utf-8")
    assert str(anuncio.id) not in crudo
    assert api.requests[0].headers["authorization"] == f"Bearer {FAKE_KEY}"


# --- Caso 7: selección ----------------------------------------------------------


def test_caso_7_seleccion(session: Session, mundo: SimpleNamespace) -> None:
    opciones = jev_signal.load_options(session)
    sin_senal = _ingesta(session, texto="texto sin señal", minutos=1)
    huella_vieja = _ingesta(session, texto="texto con huella vieja", minutos=2)
    _ingesta(session, texto=None, tipo=ContentType.IMAGE, minutos=3)
    _ingesta(session, texto="texto ya curado", estado=ProcessingStatus.CURATED, minutos=4)
    vigente = _ingesta(session, texto="texto con señal vigente", minutos=5)
    _ingesta(session, texto="   ", minutos=6)
    session.add_all(
        [
            JevSignal(
                ingestion_id=huella_vieja.id,
                model="typesafe-ai/jev",
                questions_hash="huella-vieja-000",
                answers=ANSWERS_OK,
                product_keys={},
                input_tokens=1,
                output_tokens=0,
                cost_usd=Decimal("0.00001"),
                source="prefill",
            ),
            JevSignal(
                ingestion_id=vigente.id,
                model="typesafe-ai/jev",
                questions_hash=opciones.questions_hash,
                answers=ANSWERS_OK,
                product_keys=opciones.product_keys,
                input_tokens=1,
                output_tokens=0,
                cost_usd=Decimal("0.00001"),
                source="prefill",
            ),
        ]
    )
    session.commit()
    api = Api()

    resultado, _, _ = _pasada(session, api)

    assert resultado is not None and resultado.asked == 2
    # Más viejo primero.
    assert [p["state"] for p in api.payloads()] == ["texto sin señal", "texto con huella vieja"]
    por_anuncio: dict[uuid.UUID, int] = {}
    for senal in _senales(session):
        por_anuncio[senal.ingestion_id] = por_anuncio.get(senal.ingestion_id, 0) + 1
    assert por_anuncio == {sin_senal.id: 1, huella_vieja.id: 2, vigente.id: 1}


def test_una_segunda_pasada_no_vuelve_a_preguntar(session: Session, mundo: SimpleNamespace) -> None:
    _ingesta(session)
    api = Api()

    _pasada(session, api)
    resultado, _, _ = _pasada(session, api)

    assert len(api.requests) == 1
    assert resultado is not None and resultado.asked == 0
    assert len(_senales(session)) == 1


# --- Caso 11: sin duplicados ----------------------------------------------------


def test_caso_11_un_segundo_alta_no_agrega_fila_ni_lanza(session: Session, mundo: SimpleNamespace) -> None:
    anuncio = _ingesta(session)
    opciones = jev_signal.load_options(session)
    respuesta = jev_client.parse_response(body())

    primera = jev_signal.save_signal(session, ingestion_id=anuncio.id, result=respuesta, options=opciones)
    session.commit()
    segunda = jev_signal.save_signal(session, ingestion_id=anuncio.id, result=respuesta, options=opciones)
    session.commit()

    assert primera is not None
    assert segunda is None
    assert len(_senales(session)) == 1
    # La sesión sigue sirviendo después del choque.
    assert session.get(RawIngestion, anuncio.id) is not None


def test_el_origen_solo_admite_prefill_o_backfill(session: Session, mundo: SimpleNamespace) -> None:
    anuncio = _ingesta(session)
    session.add(
        JevSignal(
            ingestion_id=anuncio.id,
            model="typesafe-ai/jev",
            questions_hash="0123456789abcdef",
            answers={},
            product_keys={},
            input_tokens=0,
            output_tokens=0,
            cost_usd=Decimal(0),
            source="otro",
        )
    )

    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


# --- Caso 4: tipo inesperado y opción fuera del catálogo ------------------------

ANSWERS_RARAS: dict[str, Any] = {
    "triage": {"noul": "alto"},
    "product": {"choice": "cafe", "probabilities": {"cafe": 0.9}, "confidence": 0.9},
    "currency": choice("CUP", 0.90, 0.88),
}


def test_caso_4_se_guarda_tal_cual_y_el_contador_de_invalidas_vale_dos(
    session: Session, mundo: SimpleNamespace
) -> None:
    _ingesta(session)

    resultado, _, _ = _pasada(session, Api(answers=ANSWERS_RARAS))

    assert resultado is not None
    assert (resultado.asked, resultado.failed, resultado.invalid) == (1, 0, 2)
    (senal,) = _senales(session)
    assert senal.answers == ANSWERS_RARAS


# --- Caso 5: red, clave y tiempo ------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [httpx.ConnectError("sin red"), httpx.ReadTimeout("tiempo agotado")],
    ids=["a-conexion", "c-tiempo"],
)
def test_caso_5_un_fallo_en_los_dos_intentos_no_deja_fila_y_los_otros_se_preguntan(
    session: Session, mundo: SimpleNamespace, error: Exception
) -> None:
    primero = _ingesta(session, minutos=1)
    _ingesta(session, minutos=2)
    _ingesta(session, minutos=3)
    api = Api([error, error])

    resultado, _, _ = _pasada(session, api)

    assert resultado is not None
    assert (resultado.asked, resultado.failed, resultado.stopped) == (2, 1, "")
    assert len(api.requests) == 4
    senales = _senales(session)
    assert len(senales) == 2
    assert primero.id not in {s.ingestion_id for s in senales}


def test_caso_5b_un_401_corta_la_pasada_con_una_sola_llamada(
    session: Session, mundo: SimpleNamespace
) -> None:
    for minutos in range(3):
        _ingesta(session, minutos=minutos)
    api = Api([httpx.Response(401, json={"message": "clave rechazada"})])

    resultado, _, _ = _pasada(session, api)

    assert resultado is not None
    assert resultado.stopped == "auth"
    assert (resultado.asked, resultado.failed) == (0, 1)
    assert len(api.requests) == 1
    assert _senales(session) == []


def test_un_200_que_no_es_json_pasa_al_siguiente(session: Session, mundo: SimpleNamespace) -> None:
    _ingesta(session, minutos=1)
    _ingesta(session, minutos=2)
    api = Api([httpx.Response(200, text="<html>no soy json</html>")])

    resultado, _, _ = _pasada(session, api)

    assert resultado is not None
    assert (resultado.asked, resultado.failed, resultado.stopped) == (1, 1, "")
    assert len(_senales(session)) == 1


def test_un_200_sin_answers_pasa_al_siguiente(session: Session, mundo: SimpleNamespace) -> None:
    _ingesta(session, minutos=1)
    _ingesta(session, minutos=2)
    api = Api([httpx.Response(200, json={"message": "sin respuestas"})])

    resultado, _, _ = _pasada(session, api)

    assert resultado is not None
    assert (resultado.asked, resultado.failed) == (1, 1)


# --- Caso 6: tope de gasto ------------------------------------------------------


def test_caso_6_el_tope_corta_antes_de_la_segunda_llamada(session: Session, mundo: SimpleNamespace) -> None:
    for minutos in range(3):
        _ingesta(session, minutos=minutos)
    api = Api(cost="0.00003")

    resultado, _, _ = _pasada(session, api, ajustes=_ajustes(llm_jev_max_cost=0.00005))

    # 0,00003 gastados + 0,00003 de la siguiente pasa 0,00005.
    assert len(api.requests) == 1
    assert resultado is not None
    assert resultado.stopped == "max_cost"
    assert resultado.asked == 1
    assert resultado.cost_usd == pytest.approx(0.00003)
    assert len(_senales(session)) == 1


def test_con_el_tope_en_cero_no_llama(session: Session, mundo: SimpleNamespace) -> None:
    _ingesta(session)
    api = Api()

    resultado, _, _ = _pasada(session, api, ajustes=_ajustes(llm_jev_max_cost=0.0))

    assert api.requests == []
    assert resultado is not None and resultado.asked == 0
    assert _senales(session) == []


# --- Casos 18 y 19: 429 y 403 ---------------------------------------------------


def test_caso_18_un_429_espera_retry_after_y_guarda_la_fila(session: Session, mundo: SimpleNamespace) -> None:
    _ingesta(session)
    api = Api([httpx.Response(429, headers={"retry-after": "30"}, json={})])

    resultado, esperas, _ = _pasada(session, api)

    assert esperas == [30.0]
    assert resultado is not None and resultado.asked == 1 and resultado.stopped == ""
    assert len(_senales(session)) == 1


def test_caso_18_dos_429_cortan_con_rate_limit(session: Session, mundo: SimpleNamespace) -> None:
    _ingesta(session, minutos=1)
    _ingesta(session, minutos=2)
    api = Api([httpx.Response(429, json={}), httpx.Response(429, json={})])

    resultado, _, _ = _pasada(session, api)

    assert resultado is not None
    assert resultado.stopped == "rate_limit"
    assert len(api.requests) == 2
    assert _senales(session) == []


def test_caso_19_un_403_corta_con_forbidden_y_conserva_el_motivo(
    session: Session, mundo: SimpleNamespace
) -> None:
    _ingesta(session, minutos=1)
    _ingesta(session, minutos=2)
    api = Api(
        [
            httpx.Response(
                403,
                json={"error": {"type": "forbidden", "message": "el plan no incluye el modelo"}},
            )
        ]
    )

    resultado, _, salida = _pasada(session, api)

    assert resultado is not None
    assert resultado.stopped == "forbidden"
    assert resultado.stopped != "auth"
    assert "el plan no incluye el modelo" in resultado.stop_detail
    assert "el plan no incluye el modelo" in salida
    assert len(api.requests) == 1


# --- Caso 20: la clave y el texto no van al registro ----------------------------


def test_caso_20_el_registro_lleva_clave_en_lugar_de_la_clave_y_nunca_el_texto(
    session: Session, mundo: SimpleNamespace, caplog: pytest.LogCaptureFixture
) -> None:
    anuncio = _ingesta(session, texto="Arroz muy particular 320 CUP")
    api = Api([httpx.Response(401, json={"message": f"token {FAKE_KEY} inválido"})])

    with caplog.at_level(logging.WARNING):
        resultado, _, salida = _pasada(session, api)

    assert "jev.fallo" in caplog.text
    assert "[clave]" in caplog.text
    assert "401" in caplog.text
    assert str(anuncio.id) in caplog.text
    for texto in (caplog.text, salida, resultado.stop_detail if resultado else ""):
        assert FAKE_KEY not in texto
        assert "Arroz muy particular" not in texto


# --- Caso 21: tres fallos seguidos ----------------------------------------------


def test_caso_21_tres_fallos_seguidos_cortan_con_failures(session: Session, mundo: SimpleNamespace) -> None:
    for minutos in range(5):
        _ingesta(session, minutos=minutos)
    api = Api([httpx.ConnectError("sin red")] * 10)

    resultado, _, _ = _pasada(session, api)

    assert resultado is not None
    assert resultado.stopped == "failures"
    assert resultado.failed == 3
    # Tres anuncios, dos intentos cada uno.
    assert len(api.requests) == 6


def test_un_acierto_en_el_medio_reinicia_la_cuenta_de_fallos(
    session: Session, mundo: SimpleNamespace
) -> None:
    for minutos in range(5):
        _ingesta(session, minutos=minutos)
    caida = httpx.ConnectError("sin red")
    # Falla, falla, anda, falla, falla: nunca tres seguidos.
    api = Api([caida, caida, caida, caida, None, caida, caida, caida, caida])

    resultado, _, _ = _pasada(session, api)

    assert resultado is not None
    assert resultado.stopped == ""
    assert (resultado.asked, resultado.failed) == (1, 4)


# --- Anotación: casos 2, 3, 4, 12, 13 y 14 (código puro) ------------------------


def _lectura(mundo: SimpleNamespace, answers: dict[str, Any]) -> jev_signal.Reading:
    """La lectura de una señal armada en memoria, sin base."""
    senal = JevSignal(
        id=uuid.UUID("0b6f6c1e-3a1d-4a55-9a43-6d1f2f0c9b11"),
        ingestion_id=uuid.uuid4(),
        model="typesafe-ai/jev",
        questions_hash="9c41d07ab3e5f210",
        answers=answers,
        product_keys={
            "arroz": str(mundo.arroz.id),
            "pollo": str(mundo.pollo.id),
            "aceite": str(mundo.aceite.id),
        },
        input_tokens=500,
        output_tokens=0,
        cost_usd=Decimal("0.00003"),
        source="prefill",
        created_at=ASKED_AT,
    )
    return jev_signal.read_signal(senal)


def _fila(producto: Product, moneda: str = "CUP") -> SimpleNamespace:
    """Lo único que la anotación mira de una fila: producto y moneda."""
    return SimpleNamespace(product_id=producto.id, currency=moneda)


def test_caso_2_coincide_el_bloque_completo(mundo: SimpleNamespace) -> None:
    (bloque,) = jev_signal.annotate(_lectura(mundo, ANSWERS_OK), [_fila(mundo.arroz)])

    assert bloque == {
        "signal_id": "0b6f6c1e-3a1d-4a55-9a43-6d1f2f0c9b11",
        "model": "typesafe-ai/jev",
        "questions_hash": "9c41d07ab3e5f210",
        "asked_at": "2026-10-05T12:00:07+00:00",
        "source": "prefill",
        "triage": {"status": "ok", "probability": 0.98},
        "product": {
            "status": "ok",
            "answer": "arroz",
            "probability": 0.97,
            "confidence": 0.96,
            "row_value": "arroz",
            "agrees": True,
        },
        "currency": {
            "status": "ok",
            "answer": "CUP",
            "probability": 0.99,
            "confidence": 0.98,
            "row_value": "CUP",
            "agrees": True,
        },
    }


def test_caso_3_discrepa_en_el_producto(mundo: SimpleNamespace) -> None:
    answers = {**ANSWERS_OK, "product": choice("pollo", 0.93, 0.92)}

    (bloque,) = jev_signal.annotate(_lectura(mundo, answers), [_fila(mundo.arroz)])

    assert bloque["product"] == {
        "status": "ok",
        "answer": "pollo",
        "probability": 0.93,
        "confidence": 0.92,
        "row_value": "arroz",
        "agrees": False,
    }
    assert bloque["currency"]["agrees"] is True


def test_la_moneda_discrepa_como_en_el_ejemplo_de_la_especificacion(
    mundo: SimpleNamespace,
) -> None:
    answers = {**ANSWERS_OK, "currency": choice("USD", 0.88, 0.86)}

    (bloque,) = jev_signal.annotate(_lectura(mundo, answers), [_fila(mundo.arroz, "CUP")])

    assert bloque["currency"] == {
        "status": "ok",
        "answer": "USD",
        "probability": 0.88,
        "confidence": 0.86,
        "row_value": "CUP",
        "agrees": False,
    }


def test_caso_4_las_preguntas_ilegibles_quedan_invalid_con_su_motivo(
    mundo: SimpleNamespace,
) -> None:
    (bloque,) = jev_signal.annotate(_lectura(mundo, ANSWERS_RARAS), [_fila(mundo.arroz)])

    for pregunta in ("triage", "product"):
        assert set(bloque[pregunta]) == {"status", "reason"}
        assert bloque[pregunta]["status"] == "invalid"
        assert bloque[pregunta]["reason"]
    assert bloque["currency"]["status"] == "ok"
    assert bloque["currency"]["answer"] == "CUP"
    assert bloque["currency"]["probability"] == 0.90
    assert bloque["currency"]["confidence"] == 0.88


def test_una_pregunta_que_no_vino_queda_invalid(mundo: SimpleNamespace) -> None:
    (bloque,) = jev_signal.annotate(_lectura(mundo, {"triage": noul(0.5)}), [_fila(mundo.arroz)])

    assert bloque["triage"] == {"status": "ok", "probability": 0.5}
    assert bloque["product"]["status"] == "invalid"
    assert bloque["currency"]["status"] == "invalid"


def test_el_triage_no_lleva_agrees(mundo: SimpleNamespace) -> None:
    (bloque,) = jev_signal.annotate(_lectura(mundo, ANSWERS_OK), [_fila(mundo.arroz)])

    assert set(bloque["triage"]) == {"status", "probability"}


def test_caso_12_dos_filas_y_jev_elige_una_de_ellas(mundo: SimpleNamespace) -> None:
    filas = [_fila(mundo.arroz), _fila(mundo.pollo)]

    de_arroz, de_pollo = jev_signal.annotate(_lectura(mundo, ANSWERS_OK), filas)

    # El acuerdo es por anuncio: coincide con ALGUNA fila.
    assert de_arroz["product"]["agrees"] is True
    assert de_pollo["product"]["agrees"] is True
    assert de_arroz["product"]["row_value"] == "arroz"
    assert de_pollo["product"]["row_value"] == "pollo"


def test_caso_12_dos_filas_y_jev_elige_otro_producto(mundo: SimpleNamespace) -> None:
    filas = [_fila(mundo.arroz), _fila(mundo.pollo)]
    answers = {**ANSWERS_OK, "product": choice("aceite", 0.8, 0.7)}

    de_arroz, de_pollo = jev_signal.annotate(_lectura(mundo, answers), filas)

    assert de_arroz["product"]["agrees"] is False
    assert de_pollo["product"]["agrees"] is False


def test_caso_13_un_producto_fuera_del_foco_vale_other(mundo: SimpleNamespace) -> None:
    answers = {**ANSWERS_OK, "product": choice("other", 0.8, 0.7)}

    (bloque,) = jev_signal.annotate(_lectura(mundo, answers), [_fila(mundo.cafe)])

    assert bloque["product"]["row_value"] == "other"
    assert bloque["product"]["agrees"] is True


def test_caso_14_una_sola_probabilidad(mundo: SimpleNamespace) -> None:
    answers = {**ANSWERS_OK, "product": {"choice": "arroz", "confidence": 0.96}}

    (bloque,) = jev_signal.annotate(_lectura(mundo, answers), [_fila(mundo.arroz)])

    assert bloque["product"]["status"] == "ok"
    assert bloque["product"]["probability"] is None
    assert bloque["product"]["confidence"] == 0.96


def test_la_senal_vigente_es_la_de_la_huella_pedida(session: Session, mundo: SimpleNamespace) -> None:
    anuncio = _ingesta(session)
    opciones = jev_signal.load_options(session)
    respuesta = jev_client.parse_response(body())
    jev_signal.save_signal(session, ingestion_id=anuncio.id, result=respuesta, options=opciones)
    session.commit()

    assert jev_signal.current_signal(session, anuncio.id, opciones.questions_hash) is not None
    assert jev_signal.current_signal(session, anuncio.id, "otra-huella-0000") is None


# --- Caso 22: migración ---------------------------------------------------------


def _cargar_migracion(nombre: str) -> ModuleType:
    ruta = Path(__file__).resolve().parent.parent / "alembic" / "versions" / nombre
    spec = importlib.util.spec_from_file_location(nombre.removesuffix(".py"), ruta)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


class TestMigracion0030:
    def test_caso_22_sube_baja_y_el_unique_rechaza_un_duplicado(self) -> None:
        modulo = _cargar_migracion("0030_jev_signals.py")
        assert modulo.revision == "0030"
        assert modulo.down_revision == "0029"
        engine = create_engine("sqlite://")
        metadata = sa.MetaData()
        sa.Table("raw_ingestions", metadata, sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True))
        metadata.create_all(engine)

        with engine.connect() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                modulo.upgrade()
            conn.commit()
            insp = inspect(conn)
            assert "jev_signals" in insp.get_table_names()
            columnas = {c["name"] for c in insp.get_columns("jev_signals")}
            assert columnas == {
                "id",
                "ingestion_id",
                "model",
                "questions_hash",
                "answers",
                "product_keys",
                "input_tokens",
                "output_tokens",
                "cost_usd",
                "source",
                "created_at",
            }
            unicas = {u["name"]: tuple(u["column_names"]) for u in insp.get_unique_constraints("jev_signals")}
            assert unicas == {"uq_jev_signals_ingestion_hash": ("ingestion_id", "questions_hash")}
            # El índice del UNIQUE alcanza: no hay otro.
            assert insp.get_indexes("jev_signals") == []

            alta = sa.text(
                "INSERT INTO jev_signals (id, ingestion_id, model, questions_hash, answers, "
                "product_keys, input_tokens, output_tokens, cost_usd, source, created_at) "
                "VALUES (:id, :ing, 'm', 'h', '{}', '{}', 0, 0, 0, 'prefill', '2026-10-05')"
            )
            conn.execute(alta, {"id": "a" * 32, "ing": "c" * 32})
            with pytest.raises(IntegrityError):
                conn.execute(alta, {"id": "b" * 32, "ing": "c" * 32})
            conn.rollback()

        with engine.connect() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                modulo.downgrade()
            conn.commit()
            assert "jev_signals" not in inspect(conn).get_table_names()
        engine.dispose()

    def test_el_modelo_y_la_migracion_tienen_las_mismas_columnas(self) -> None:
        modulo = _cargar_migracion("0030_jev_signals.py")
        engine = create_engine("sqlite://")
        metadata = sa.MetaData()
        sa.Table("raw_ingestions", metadata, sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True))
        metadata.create_all(engine)
        with engine.connect() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                modulo.upgrade()
            conn.commit()
            migradas = {c["name"] for c in inspect(conn).get_columns("jev_signals")}
        engine.dispose()

        assert migradas == set(JevSignal.__table__.columns.keys())


# --- Aviso al arrancar ----------------------------------------------------------


def test_al_arrancar_avisa_si_esta_encendido_y_sin_clave(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        jev_signal.warn_if_enabled_without_key(_ajustes(llm_jev_api_key=None))

    assert "jev.sin_clave" in caplog.text
    assert "SAIMC_LLM_JEV_API_KEY" in caplog.text


@pytest.mark.parametrize(
    "ajustes", [{"llm_jev_enabled": False, "llm_jev_api_key": None}, {}], ids=["apagado", "activo"]
)
def test_al_arrancar_no_avisa_si_esta_apagado_o_activo(
    ajustes: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        jev_signal.warn_if_enabled_without_key(_ajustes(**ajustes))

    assert caplog.text == ""
