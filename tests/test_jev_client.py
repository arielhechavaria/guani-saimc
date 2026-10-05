"""`saimc.validation.jev_client` — el cliente de Jev, sin base (slice A6b-1).

Casos 8, 9, 10, 14, 18, 19, 20 y 24 de «Casos esperados» de
`docs/spec-slice-A6b.md`. Los valores esperados salen de la especificación,
escritos antes que el código. El comportamiento que el cliente ya tenía en A6
(reintentos por defecto, lectura del cuerpo) sigue cubierto en
`tests/test_jev_eval.py`, que lo ejercita a través del script.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from saimc.validation import jev_client
from tests.jev_fakes import FAKE_KEY, Api, body, client

#: El catálogo del fixture común de la especificación: tres pilotos.
PRODUCTS = {"arroz": "Arroz", "pollo": "Pollo", "aceite": "Aceite"}


# --- Caso 9: las preguntas ------------------------------------------------------


def test_caso_9_son_exactamente_tres_preguntas_con_sus_opciones() -> None:
    questions = jev_client.build_questions(PRODUCTS)

    assert set(questions) == {"triage", "product", "currency"}
    assert questions["triage"]["type"] == "noul"
    assert questions["product"]["type"] == "choice"
    assert questions["currency"]["type"] == "choice"
    assert set(questions["product"]["criteria"]) == {"arroz", "pollo", "aceite", "other"}
    assert set(questions["currency"]["criteria"]) == {"CUP", "USD", "EUR", "MLC"}
    # Provincia y formato de venta no se preguntan.
    assert "province" not in questions
    assert "sale_format" not in questions


def test_el_triage_nombra_los_pilotos() -> None:
    instructions = jev_client.build_questions(PRODUCTS)["triage"]["instructions"]

    for name in ("Arroz", "Pollo", "Aceite"):
        assert name in instructions


# --- Caso 10: la huella ---------------------------------------------------------


def test_caso_10_el_mismo_catalogo_en_otro_orden_da_la_misma_huella() -> None:
    reordered = {"aceite": "Aceite", "arroz": "Arroz", "pollo": "Pollo"}

    assert jev_client.questions_hash(jev_client.build_questions(PRODUCTS)) == (
        jev_client.questions_hash(jev_client.build_questions(reordered))
    )


def test_caso_10_sumar_un_piloto_da_otra_huella() -> None:
    more = {**PRODUCTS, "huevo": "Huevo"}

    assert jev_client.questions_hash(jev_client.build_questions(PRODUCTS)) != (
        jev_client.questions_hash(jev_client.build_questions(more))
    )


def test_la_huella_tiene_16_caracteres() -> None:
    # Es el ancho de la columna `jev_signals.questions_hash`.
    assert len(jev_client.questions_hash(jev_client.build_questions(PRODUCTS))) == 16


# --- Caso 8: redacción ----------------------------------------------------------


def test_caso_8_el_texto_sale_redactado_y_el_pedido_tiene_tres_claves() -> None:
    questions = jev_client.build_questions(PRODUCTS)

    payload = jev_client.build_request("Arroz 320 CUP la libra, llamar al 5 234 5678", questions)

    assert set(payload) == {"model", "state", "questions"}
    assert payload["model"] == "typesafe-ai/jev"
    assert "5 234 5678" not in payload["state"]
    assert "5678" not in payload["state"]
    assert "[teléfono removido]" in payload["state"]
    assert "Arroz 320 CUP la libra" in payload["state"]
    assert payload["questions"] == questions


# --- Caso 14 y lectura de una pregunta ------------------------------------------


def test_caso_14_con_una_sola_probabilidad_la_respuesta_vale() -> None:
    question = jev_client.build_questions(PRODUCTS)["currency"]

    answer = jev_client.parse_answer(question, {"choice": "USD", "confidence": 0.86})

    assert answer.invalid == ""
    assert answer.choice == "USD"
    assert answer.p is None
    assert answer.confidence == pytest.approx(0.86)


def test_una_opcion_fuera_del_catalogo_es_invalida() -> None:
    question = jev_client.build_questions(PRODUCTS)["product"]

    answer = jev_client.parse_answer(question, {"choice": "cafe", "confidence": 0.9})

    assert answer.invalid


def test_un_noul_que_no_es_numero_es_invalido() -> None:
    question = jev_client.build_questions(PRODUCTS)["triage"]

    assert jev_client.parse_answer(question, {"noul": "alto"}).invalid


# --- Casos 18 y 19, y los parámetros nuevos de `ask` ----------------------------


def _ask(api: Api, **kwargs: object) -> tuple[jev_client.ApiResult, list[float]]:
    waits: list[float] = []
    with client(api) as http:
        result = jev_client.ask(http, {"questions": {}}, FAKE_KEY, sleep=waits.append, **kwargs)  # type: ignore[arg-type]
    return result, waits


def test_caso_18_un_429_espera_lo_que_pide_retry_after_y_reintenta() -> None:
    api = Api([httpx.Response(429, headers={"retry-after": "30"}, json={})])

    result, waits = _ask(api, max_retries=1)

    assert waits == [30.0]
    assert len(api.requests) == 2
    assert result.cost == pytest.approx(0.00003)


def test_caso_18_dos_429_seguidos_se_rinden_con_un_solo_reintento() -> None:
    api = Api([httpx.Response(429, json={}), httpx.Response(429, json={})])

    with pytest.raises(jev_client.JevError) as excinfo:
        _ask(api, max_retries=1)

    assert excinfo.value.status == 429
    assert len(api.requests) == 2


def test_un_fallo_de_red_se_reintenta_una_sola_vez_si_se_pide() -> None:
    api = Api([httpx.ConnectError("sin red"), httpx.ConnectError("sin red")])

    with pytest.raises(jev_client.JevError) as excinfo:
        _ask(api, max_retries=1)

    assert excinfo.value.status is None
    assert len(api.requests) == 2


def test_caso_19_un_403_no_se_reintenta_y_conserva_el_motivo_del_gateway() -> None:
    api = Api(
        [
            httpx.Response(
                403,
                json={"error": {"type": "forbidden", "message": "el plan no incluye el modelo"}},
            )
        ]
    )

    with pytest.raises(jev_client.JevError) as excinfo:
        _ask(api, max_retries=1)

    assert excinfo.value.status == 403
    assert "el plan no incluye el modelo" in str(excinfo.value)
    assert len(api.requests) == 1


def test_caso_20_la_clave_no_aparece_en_el_mensaje_de_error() -> None:
    api = Api([httpx.Response(401, json={"message": f"token {FAKE_KEY} inválido"})])

    with pytest.raises(jev_client.JevError) as excinfo:
        _ask(api)

    assert FAKE_KEY not in str(excinfo.value)
    assert "[clave]" in str(excinfo.value)


def test_el_tiempo_limite_viaja_en_el_pedido() -> None:
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.extensions["timeout"]))
        return httpx.Response(200, json=body())

    with client(handler) as http:
        jev_client.ask(http, {"questions": {}}, FAKE_KEY, timeout=7.0)

    assert seen[0]["read"] == 7.0
    assert seen[0]["connect"] == 7.0


def test_el_costo_estimado_de_un_pedido_corto_entra_en_el_tope_del_caso_6() -> None:
    # Caso 6: con tope 0,00005 la primera llamada de un texto corto se hace,
    # así que su estimación (caracteres / 3,5 por 2,3, a 0,042 USD el millón)
    # tiene que quedar por debajo. La especificación da el piso medido de una
    # llamada de tres preguntas: 403 tokens, 0,000017 USD.
    payload = jev_client.build_request("Arroz 320 CUP la libra", jev_client.build_questions(PRODUCTS))

    cost = jev_client.tokens_cost(jev_client.request_tokens(payload))

    assert 0.000010 < cost < 0.00005


# --- Caso 24: pureza ------------------------------------------------------------


@pytest.mark.parametrize("module", ["engine.py", "rules.py", "types.py", "fence.py"])
def test_caso_24_el_motor_no_conoce_a_jev_ni_a_la_red(module: str) -> None:
    source = (Path(__file__).resolve().parent.parent / "src" / "saimc" / "validation" / module).read_text(
        encoding="utf-8"
    )

    for forbidden in ("httpx", "jev_client", "jev_signal"):
        assert forbidden not in source


def test_el_cliente_no_toca_la_base() -> None:
    source = Path(jev_client.__file__).read_text(encoding="utf-8")

    # No abre sesión ni consulta: de los modelos solo toma el enum de monedas.
    assert "sqlalchemy" not in source
    assert "saimc.db" not in source
    assert "Session" not in source
