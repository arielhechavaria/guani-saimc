"""Tests de `scripts/jev_eval.py` (slice A6: prueba de Jev fuera de línea).

Los valores esperados están calculados a mano, antes y aparte del código: el
Brier, los tramos y el ECE salen de la cuenta escrita en cada test, no de
correr la implementación.

Nada de acá toca la red: el cliente es un `httpx.Client` real con
`httpx.MockTransport`, que responde desde una función en vez de abrir una
conexión. La clave es siempre una cadena falsa.
"""

from __future__ import annotations

import json
import math
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy.orm import Session

from saimc.models import (
    Channel,
    LlmProposal,
    PriceHistory,
    Product,
    Province,
    RawIngestion,
)
from scripts import jev_eval

FAKE_KEY = "clave-falsa-de-test-123"

CATALOG = jev_eval.Catalog(
    products={"arroz": "Arroz", "pollo": "Pollo"},
    provinces={"hab": "La Habana", "scu": "Santiago de Cuba"},
)


def _ad(ingestion_id: str, *, curated: bool, text: str = "Arroz a 300 CUP la libra", **extra: Any):
    return jev_eval.Ad(ingestion_id=ingestion_id, text=text, curated=curated, **extra)


def _ok_body(request: httpx.Request, *, cost: str | None = "0.00001") -> dict[str, Any]:
    """Una respuesta con la forma documentada, armada desde las preguntas del
    pedido: sí con 0,9 al triage y la primera opción con 0,8 en cada elección."""
    sent = json.loads(request.content)
    questions = sent["questions"]
    answers: dict[str, Any] = {}
    for name, question in questions.items():
        if question["type"] == "noul":
            answers[name] = {"type": "noul", "noul": 0.9}
        else:
            first = next(iter(question["criteria"]))
            answers[name] = {
                "type": "choice",
                "choice": first,
                "probabilities": {first: 0.8},
                "confidence": 0.8,
            }
    body: dict[str, Any] = {
        "model": sent.get("model", "typesafe-ai/jev"),
        "answers": answers,
        "usage": {"input_tokens": 100, "output_tokens": 5},
    }
    if cost is not None:
        body["provider_metadata"] = {"gateway": {"cost": cost}}
    return body


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


class _Api:
    """API falsa que cuenta los pedidos que recibe."""

    def __init__(self, *, cost: str | None = "0.00001") -> None:
        self.requests: list[httpx.Request] = []
        self.cost = cost

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json=_ok_body(request, cost=self.cost))


def _run(ads: list[Any], cache_path: Path, handler: Any, **kwargs: Any) -> Any:
    kwargs.setdefault("max_cost", 1.0)
    with _client(handler) as client:
        return jev_eval.run_eval(
            ads,
            kwargs.pop("catalog", CATALOG),
            cache_path=cache_path,
            client=client,
            api_key=FAKE_KEY,
            sleep=lambda _seconds: None,
            **kwargs,
        )


# --- Métricas -----------------------------------------------------------------

# (p, y). Errores al cuadrado: 0,01 · 0,04 · 0,09 · 0,36 · 0 → suma 0,50 → /5 = 0,10.
PAIRS = [(0.9, 1), (0.8, 1), (0.3, 0), (0.6, 0), (1.0, 1)]


class TestMetrics:
    def test_brier(self) -> None:
        assert jev_eval.brier(PAIRS) == pytest.approx(0.10)

    def test_brier_perfecto_y_pesimo(self) -> None:
        assert jev_eval.brier([(1.0, 1), (0.0, 0)]) == pytest.approx(0.0)
        assert jev_eval.brier([(1.0, 0), (0.0, 1)]) == pytest.approx(1.0)

    def test_tramos(self) -> None:
        bins = jev_eval.reliability_bins(PAIRS)
        assert len(bins) == 10
        # Tramo [0,3; 0,4): solo (0,3; 0). Tramo [0,6; 0,7): solo (0,6; 0).
        assert (bins[3].n, bins[3].k) == (1, 0)
        assert bins[3].mean_p == pytest.approx(0.3)
        assert (bins[6].n, bins[6].k) == (1, 0)
        # Tramo [0,8; 0,9): solo (0,8; 1).
        assert (bins[8].n, bins[8].k) == (1, 1)
        # Último tramo: 0,9 y 1,0, los dos con y = 1. Probabilidad media 0,95.
        assert (bins[9].n, bins[9].k) == (2, 2)
        assert bins[9].mean_p == pytest.approx(0.95)
        assert bins[9].observed == pytest.approx(1.0)
        assert sum(b.n for b in bins) == 5

    def test_p_uno_cae_en_el_ultimo_tramo_y_p_cero_en_el_primero(self) -> None:
        bins = jev_eval.reliability_bins([(1.0, 1), (0.0, 0)])
        assert bins[9].n == 1
        assert bins[0].n == 1

    def test_ece(self) -> None:
        # Σ (n/N)·|observada − media|:
        # (1/5)·|0−0,3| + (1/5)·|0−0,6| + (1/5)·|1−0,8| + (2/5)·|1−0,95|
        # = 0,06 + 0,12 + 0,04 + 0,02 = 0,24
        assert jev_eval.ece(PAIRS) == pytest.approx(0.24)

    def test_lista_vacia_no_revienta(self) -> None:
        assert jev_eval.brier([]) is None
        assert jev_eval.ece([]) is None
        bins = jev_eval.reliability_bins([])
        assert len(bins) == 10
        assert all(b.n == 0 and b.mean_p is None and b.observed is None for b in bins)

    def test_exactitud_del_triage_con_umbral_un_medio(self) -> None:
        # Predicción (p ≥ 0,5): sí, sí, no, sí, sí. Verdad: sí, sí, no, no, sí. 4 de 5.
        assert jev_eval.triage_hits(PAIRS) == 4


class TestChoiceCorrectness:
    def test_la_opcion_elegida_pertenece_al_conjunto(self) -> None:
        assert jev_eval.is_correct("arroz", frozenset({"arroz"})) is True
        assert jev_eval.is_correct("pollo", frozenset({"arroz"})) is False

    def test_anuncio_con_varios_valores(self) -> None:
        truth = frozenset({"arroz", "pollo"})
        assert jev_eval.is_correct("pollo", truth) is True
        assert jev_eval.is_correct("aceite", truth) is False

    def test_none_es_un_valor_mas(self) -> None:
        assert jev_eval.is_correct("none", frozenset({"none"})) is True
        assert jev_eval.is_correct("hab", frozenset({"none"})) is False
        assert jev_eval.is_correct("none", frozenset({"hab"})) is False

    def test_truth_values_convierte_nulo_en_none(self) -> None:
        assert jev_eval.truth_values(["hab", None, "hab"]) == frozenset({"hab", "none"})


# --- Pedido -------------------------------------------------------------------


class TestRequest:
    def test_descartado_solo_lleva_triage(self) -> None:
        payload = jev_eval.build_request(_ad("a", curated=False), CATALOG)
        assert payload["model"] == "typesafe-ai/jev"
        assert set(payload["questions"]) == {"triage"}
        assert payload["questions"]["triage"]["type"] == "noul"

    def test_curado_lleva_triage_y_los_cuatro_campos(self) -> None:
        questions = jev_eval.build_request(_ad("a", curated=True), CATALOG)["questions"]
        assert set(questions) == {"triage", "product", "currency", "province", "sale_format"}
        for field in ("product", "currency", "province", "sale_format"):
            assert questions[field]["type"] == "choice"
            assert questions[field]["instructions"]

    def test_opciones_salen_de_los_catalogos(self) -> None:
        questions = jev_eval.build_request(_ad("a", curated=True), CATALOG)["questions"]
        assert set(questions["product"]["criteria"]) == {"arroz", "pollo", "other"}
        assert questions["product"]["criteria"]["arroz"] == "Arroz"
        assert set(questions["currency"]["criteria"]) == {"CUP", "USD", "EUR", "MLC"}
        # Los dos campos nulables llevan la opción «no lo dice»; la moneda no.
        assert set(questions["province"]["criteria"]) == {"hab", "scu", "none"}
        assert set(questions["sale_format"]["criteria"]) == {"retail", "wholesale", "none"}

    def test_el_texto_va_redactado(self) -> None:
        ad = _ad("a", curated=True, text="Pollo a 400 la libra, contacto 5 234 5678")
        payload = jev_eval.build_request(ad, CATALOG)
        assert "5 234 5678" not in payload["state"]
        assert "5678" not in payload["state"]
        assert "Pollo a 400 la libra" in payload["state"]

    def test_hash_cambia_con_las_preguntas(self) -> None:
        curated = jev_eval.build_questions(CATALOG, curated=True)
        discarded = jev_eval.build_questions(CATALOG, curated=False)
        other = jev_eval.build_questions(
            jev_eval.Catalog(products={"arroz": "Arroz"}, provinces=CATALOG.provinces),
            curated=True,
        )
        assert jev_eval.questions_hash(curated) == jev_eval.questions_hash(
            jev_eval.build_questions(CATALOG, curated=True)
        )
        assert jev_eval.questions_hash(curated) != jev_eval.questions_hash(discarded)
        assert jev_eval.questions_hash(curated) != jev_eval.questions_hash(other)

    def test_slug(self) -> None:
        assert jev_eval.slugify("Azúcar blanca") == "azucar_blanca"
        assert jev_eval.slugify("  Pollo ") == "pollo"

    def test_estimacion_de_tokens(self) -> None:
        # 35 caracteres / 3,5 = 10 tokens; 36 caracteres redondea para arriba.
        assert jev_eval.estimate_tokens("x" * 35) == 10
        assert jev_eval.estimate_tokens("x" * 36) == 11

    def test_la_estimacion_del_pedido_lleva_el_factor_medido(self) -> None:
        # Medido el 2026-10-04 sobre 1.409 pedidos reales a Jev: 751.842 tokens
        # contra 327.015 de caracteres / 3,5. 751.842 / 327.015 = 2,299 → 2,3.
        assert jev_eval.REQUEST_OVERHEAD_FACTOR == 2.3
        # Un pedido de 35 caracteres serializados: 10 tokens × 2,3 = 23.
        assert jev_eval.request_tokens_from_text("x" * 35) == 23
        # 36 caracteres: 11 tokens × 2,3 = 25,3 → redondea para arriba a 26.
        assert jev_eval.request_tokens_from_text("x" * 36) == 26

    def test_costo_estimado(self) -> None:
        # 1.000.000 de tokens de entrada a 0,042 USD el millón.
        assert jev_eval.tokens_cost(1_000_000) == pytest.approx(0.042)


# --- Respuesta ----------------------------------------------------------------

CHOICE_QUESTION = {"type": "choice", "instructions": "…", "criteria": {"CUP": "Peso", "USD": "Dólar"}}
NOUL_QUESTION = {"type": "noul", "instructions": "…"}


class TestParse:
    def test_respuesta_completa(self) -> None:
        body = {
            "model": "typesafe-ai/jev",
            "answers": {"triage": {"type": "noul", "noul": 0.98}},
            "usage": {"input_tokens": 275, "output_tokens": 20},
            "provider_metadata": {"gateway": {"cost": "0.00001155"}},
        }
        result = jev_eval.parse_response(body)
        assert result.model == "typesafe-ai/jev"
        assert result.input_tokens == 275
        assert result.output_tokens == 20
        assert result.cost == pytest.approx(0.00001155)
        assert result.answers == {"triage": {"type": "noul", "noul": 0.98}}

    def test_sin_costo_del_gateway_se_estima_con_los_tokens(self) -> None:
        body = {"answers": {}, "usage": {"input_tokens": 1000, "output_tokens": 20}}
        # 1.000 tokens × 0,042 / 1.000.000 = 0,000042.
        assert jev_eval.parse_response(body).cost == pytest.approx(0.000042)

    def test_cuerpo_de_error_levanta_sin_reventar_el_tipo(self) -> None:
        with pytest.raises(jev_eval.JevError) as excinfo:
            jev_eval.parse_response({"message": "boom", "error_type": "bad_request"})
        assert "boom" in str(excinfo.value)

    @pytest.mark.parametrize("body", [None, [], "texto", {"answers": "no es un objeto"}])
    def test_cuerpo_con_otra_forma_levanta_jev_error(self, body: object) -> None:
        with pytest.raises(jev_eval.JevError):
            jev_eval.parse_response(body)

    def test_noul(self) -> None:
        answer = jev_eval.parse_answer(NOUL_QUESTION, {"type": "noul", "noul": 0.98})
        assert answer.invalid == ""
        assert answer.p == pytest.approx(0.98)

    @pytest.mark.parametrize("raw", [None, {}, {"noul": "alto"}, {"noul": 1.5}, {"noul": True}])
    def test_noul_invalido(self, raw: object) -> None:
        answer = jev_eval.parse_answer(NOUL_QUESTION, raw)
        assert answer.invalid
        assert answer.p is None

    def test_choice_completa(self) -> None:
        raw = {"choice": "CUP", "probabilities": {"CUP": 0.9, "USD": 0.1}, "confidence": 0.9}
        answer = jev_eval.parse_answer(CHOICE_QUESTION, raw)
        assert (answer.choice, answer.invalid) == ("CUP", "")
        assert answer.p == pytest.approx(0.9)

    def test_choice_sin_confidence_usa_la_probabilidad_de_la_elegida(self) -> None:
        raw = {"choice": "USD", "probabilities": {"CUP": 0.3, "USD": 0.7}}
        answer = jev_eval.parse_answer(CHOICE_QUESTION, raw)
        assert answer.invalid == ""
        assert answer.p == pytest.approx(0.7)

    def test_choice_sin_probabilities_pero_con_confidence_vale_para_exactitud(self) -> None:
        answer = jev_eval.parse_answer(CHOICE_QUESTION, {"choice": "CUP", "confidence": 0.6})
        assert (answer.choice, answer.invalid) == ("CUP", "")
        # Sin `probabilities` no hay probabilidad de la elegida: fuera de esa calibración.
        assert answer.p is None
        assert answer.confidence == pytest.approx(0.6)

    def test_choice_sin_probabilities_ni_confidence_es_invalida(self) -> None:
        answer = jev_eval.parse_answer(CHOICE_QUESTION, {"choice": "CUP"})
        assert answer.invalid
        assert answer.p is None

    def test_choice_fuera_de_las_opciones_es_invalida(self) -> None:
        answer = jev_eval.parse_answer(CHOICE_QUESTION, {"choice": "EUR", "confidence": 0.9})
        assert answer.invalid


# --- Corrida: caché, tope, errores, clave -------------------------------------


class TestCache:
    def test_lo_ya_respondido_no_se_vuelve_a_pedir(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache.jsonl"
        ads = [_ad("a", curated=True), _ad("b", curated=False)]
        api = _Api()
        first = _run(ads, cache, api)
        assert (first.asked, first.cached) == (2, 0)
        assert len(api.requests) == 2

        second = _run(ads, cache, api)
        assert (second.asked, second.cached) == (0, 2)
        assert len(api.requests) == 2  # ni un pedido más

    def test_hash_distinto_vuelve_a_preguntar(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache.jsonl"
        ads = [_ad("a", curated=True)]
        api = _Api()
        _run(ads, cache, api)
        other = jev_eval.Catalog(
            products={"arroz": "Arroz", "pollo": "Pollo", "aceite": "Aceite"},
            provinces=CATALOG.provinces,
        )
        again = _run(ads, cache, api, catalog=other)
        assert (again.asked, again.cached) == (1, 0)
        assert len(api.requests) == 2

    def test_una_linea_por_anuncio_sin_texto_ni_clave(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache.jsonl"
        ad = _ad("a", curated=True, text="Texto secreto del anuncio: arroz a 300")
        _run([ad], cache, _Api(cost="0.00002"))
        lines = cache.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["ingestion_id"] == "a"
        assert entry["questions_hash"] == jev_eval.questions_hash(
            jev_eval.build_questions(CATALOG, curated=True)
        )
        assert entry["answers"]["triage"]["noul"] == 0.9
        assert entry["input_tokens"] == 100
        assert entry["cost_usd"] == pytest.approx(0.00002)
        assert entry["asked_at"]
        assert "Texto secreto" not in lines[0]
        assert FAKE_KEY not in lines[0]

    def test_la_clave_viaja_en_la_cabecera(self, tmp_path: Path) -> None:
        api = _Api()
        _run([_ad("a", curated=False)], tmp_path / "c.jsonl", api)
        assert api.requests[0].headers["Authorization"] == f"Bearer {FAKE_KEY}"
        assert str(api.requests[0].url) == jev_eval.API_URL

    def test_limit_acota_los_pedidos_nuevos(self, tmp_path: Path) -> None:
        ads = [_ad(str(i), curated=False) for i in range(5)]
        api = _Api()
        summary = _run(ads, tmp_path / "c.jsonl", api, limit=2)
        assert (summary.asked, summary.unasked) == (2, 3)
        assert len(api.requests) == 2


class TestBudget:
    def test_corta_antes_de_pasarse(self, tmp_path: Path) -> None:
        # Cada llamada cuesta 0,05 y el tope es 0,12: entran dos (0,10). La
        # tercera llevaría el gasto a 0,15, así que no se hace.
        ads = [_ad(str(i), curated=False) for i in range(5)]
        api = _Api(cost="0.05")
        summary = _run(ads, tmp_path / "c.jsonl", api, max_cost=0.12)
        assert len(api.requests) == 2
        assert summary.asked == 2
        assert summary.unasked == 3
        assert summary.stopped_by_cap is True
        assert summary.cost_run == pytest.approx(0.10)
        assert summary.cost_run + summary.cost_cached <= 0.12

    def test_lo_cacheado_de_la_misma_version_cuenta_para_el_tope(self, tmp_path: Path) -> None:
        cache = tmp_path / "c.jsonl"
        api = _Api(cost="0.05")
        _run([_ad("0", curated=False)], cache, api, max_cost=0.12)
        # Ya hay 0,05 gastados en esta versión de preguntas: solo entra uno más.
        ads = [_ad(str(i), curated=False) for i in range(5)]
        summary = _run(ads, cache, api, max_cost=0.12)
        assert summary.cached == 1
        assert summary.asked == 1
        assert summary.unasked == 3
        assert summary.cost_cached == pytest.approx(0.05)
        assert len(api.requests) == 2

    def test_tope_cero_no_llama(self, tmp_path: Path) -> None:
        api = _Api()
        summary = _run([_ad("a", curated=False)], tmp_path / "c.jsonl", api, max_cost=0.0)
        assert len(api.requests) == 0
        assert summary.unasked == 1


class TestNetwork:
    def test_reintenta_un_429_y_sigue(self, tmp_path: Path) -> None:
        calls: list[int] = []
        waits: list[float] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            if len(calls) == 1:
                return httpx.Response(429, json={"message": "despacio", "error_type": "rate_limit"})
            return httpx.Response(200, json=_ok_body(request))

        with _client(handler) as client:
            result = jev_eval.ask(client, {"questions": {}}, FAKE_KEY, sleep=waits.append)
        assert len(calls) == 2
        assert len(waits) == 1
        assert result.model == "typesafe-ai/jev"

    def test_los_curados_se_preguntan_primero(self, tmp_path: Path) -> None:
        # Con 5 pedidos por minuto la corrida entera lleva horas: si se corta,
        # que lo ya respondido sean los curados, que son los pocos y los que
        # llevan las preguntas por campo.
        bodies: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            bodies.append(json.loads(request.content))
            return httpx.Response(200, json=_ok_body(request))

        ads = [_ad("a", curated=False), _ad("b", curated=True)]
        _run(ads, tmp_path / "c.jsonl", handler, limit=1)
        assert len(bodies) == 1
        assert "product" in bodies[0]["questions"]

    def test_un_429_espera_lo_que_pide_retry_after(self) -> None:
        # Forma real del gateway en el plan gratuito (2026-10-04): 5 pedidos por
        # minuto y `retry-after: 60`. Esperar 1 s, 2 s y 4 s agotaría los
        # reintentos sin que el cupo se haya repuesto.
        calls: list[int] = []
        waits: list[float] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            if len(calls) == 1:
                return httpx.Response(429, headers={"retry-after": "60"}, json={"error": {"message": "x"}})
            return httpx.Response(200, json=_ok_body(request))

        with _client(handler) as client:
            jev_eval.ask(client, {"questions": {}}, FAKE_KEY, sleep=waits.append)
        assert waits == [60.0]

    def test_un_retry_after_absurdo_se_acota(self) -> None:
        waits: list[float] = []
        calls: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            if len(calls) == 1:
                return httpx.Response(429, headers={"retry-after": "86400"}, json={})
            return httpx.Response(200, json=_ok_body(request))

        with _client(handler) as client:
            jev_eval.ask(client, {"questions": {}}, FAKE_KEY, sleep=waits.append)
        assert waits == [jev_eval.MAX_RETRY_AFTER_SECONDS]

    def test_un_retry_after_ilegible_cae_en_la_espera_creciente(self) -> None:
        waits: list[float] = []
        calls: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            if len(calls) == 1:
                return httpx.Response(429, headers={"retry-after": "pronto"}, json={})
            return httpx.Response(200, json=_ok_body(request))

        with _client(handler) as client:
            jev_eval.ask(client, {"questions": {}}, FAKE_KEY, sleep=waits.append)
        assert waits == [jev_eval.RETRY_BASE_SECONDS]

    def test_un_5xx_persistente_se_rinde_tras_tres_reintentos(self) -> None:
        calls: list[int] = []
        waits: list[float] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            return httpx.Response(503, json={"message": "caído", "error_type": "unavailable"})

        with _client(handler) as client, pytest.raises(jev_eval.JevError) as excinfo:
            jev_eval.ask(client, {"questions": {}}, FAKE_KEY, sleep=waits.append)
        assert len(calls) == 4  # el intento y tres reintentos
        assert len(waits) == 3
        assert waits == sorted(waits) and waits[0] < waits[-1]  # espera creciente
        assert excinfo.value.status == 503

    def test_un_400_no_se_reintenta(self) -> None:
        calls: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            return httpx.Response(400, json={"message": "mal pedido", "error_type": "bad_request"})

        with _client(handler) as client, pytest.raises(jev_eval.JevError):
            jev_eval.ask(client, {"questions": {}}, FAKE_KEY, sleep=lambda _s: None)
        assert len(calls) == 1

    def test_un_error_en_un_anuncio_no_aborta_la_corrida(self, tmp_path: Path) -> None:
        cache = tmp_path / "c.jsonl"

        def handler(request: httpx.Request) -> httpx.Response:
            if "ROMPE" in json.loads(request.content)["state"]:
                return httpx.Response(400, json={"message": "mal", "error_type": "bad_request"})
            return httpx.Response(200, json=_ok_body(request))

        ads = [
            _ad("a", curated=False),
            _ad("b", curated=False, text="ROMPE"),
            _ad("c", curated=False),
        ]
        summary = _run(ads, cache, handler)
        assert summary.asked == 2
        assert [ingestion_id for ingestion_id, _ in summary.errors] == ["b"]
        # El que falló no queda en el caché: la próxima corrida lo reintenta.
        assert {json.loads(line)["ingestion_id"] for line in cache.read_text().splitlines()} == {
            "a",
            "c",
        }

    def test_un_fallo_de_red_se_anota_y_sigue(self, tmp_path: Path) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("sin red")

        summary = _run([_ad("a", curated=False)], tmp_path / "c.jsonl", handler)
        assert summary.asked == 0
        assert len(summary.errors) == 1


class TestKeySecrecy:
    def test_la_clave_no_aparece_en_los_mensajes_de_error(self, tmp_path: Path) -> None:
        cache = tmp_path / "c.jsonl"

        def handler(request: httpx.Request) -> httpx.Response:
            # Un servidor descuidado que devuelve la credencial en el mensaje.
            return httpx.Response(
                401,
                json={"message": f"token {FAKE_KEY} inválido", "error_type": "authentication"},
            )

        summary = _run([_ad("a", curated=False)], cache, handler)
        assert len(summary.errors) == 1
        assert FAKE_KEY not in summary.errors[0][1]
        assert not cache.exists() or FAKE_KEY not in cache.read_text()

    def test_check_con_401_dice_que_la_clave_no_fue_aceptada(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"message": f"bad {FAKE_KEY}", "error_type": "authentication"})

        with _client(handler) as client:
            ok, message = jev_eval.run_check(client, FAKE_KEY)
        assert ok is False
        assert "no fue aceptada" in message
        assert FAKE_KEY not in message

    def test_check_con_403_muestra_el_motivo_del_gateway_y_no_culpa_a_la_clave(self) -> None:
        # Forma real del rechazo de Vercel AI Gateway (2026-10-03): la clave
        # autentica, pero el plan gratuito no da acceso al modelo. El error
        # viene anidado bajo `error`, no en la raíz como el de TypeSafe.
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                403,
                json={
                    "error": {
                        "message": "Free tier users do not have access to this model.",
                        "type": "no_providers_available",
                    }
                },
            )

        with _client(handler) as client:
            ok, message = jev_eval.run_check(client, FAKE_KEY)
        assert ok is False
        assert "Free tier users do not have access to this model." in message
        assert "no fue aceptada" not in message
        assert FAKE_KEY not in message

    def test_un_403_corta_la_corrida_con_el_motivo_del_gateway(self, tmp_path: Path) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                403,
                json={"error": {"message": "Free tier users do not have access to this model."}},
            )

        ads = [_ad("a", curated=False), _ad("b", curated=False)]
        summary = _run(ads, tmp_path / "c.jsonl", handler)
        assert len(summary.errors) == 1
        assert summary.unasked == 1
        assert summary.aborted is not None
        assert "clave" not in summary.aborted
        assert "Free tier users do not have access to this model." in summary.errors[0][1]

    def test_check_feliz_informa_costo_y_no_manda_datos_reales(self) -> None:
        api = _Api(cost="0.00001155")
        with _client(api) as client:
            ok, message = jev_eval.run_check(client, FAKE_KEY)
        assert ok is True
        assert "0,00001155" in message
        assert FAKE_KEY not in message
        sent = json.loads(api.requests[0].content)
        assert sent["state"] == jev_eval.CHECK_TEXT
        assert set(sent["questions"]) == {"triage", "product", "currency", "province", "sale_format"}

    def test_check_avisa_si_la_respuesta_no_trae_la_forma_esperada(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            body = _ok_body(request)
            body["answers"]["currency"] = {"type": "choice", "choice": "CUP"}
            return httpx.Response(200, json=body)

        with _client(handler) as client:
            ok, message = jev_eval.run_check(client, FAKE_KEY)
        assert ok is False
        assert "currency" in message

    def test_clave_del_entorno_gana_al_archivo(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("SAIMC_LLM_JEV_API_KEY=del-archivo\nOTRA=1\n", encoding="utf-8")
        assert jev_eval.load_api_key({"SAIMC_LLM_JEV_API_KEY": "del-entorno"}, env_file) == ("del-entorno")
        assert jev_eval.load_api_key({}, env_file) == "del-archivo"

    def test_la_clave_con_el_nombre_nuevo_gana_a_la_del_nombre_anterior(self, tmp_path: Path) -> None:
        """`SAIMC_VERCEL_API_KEY` es la clave del gateway; `SAIMC_LLM_JEV_API_KEY`
        es su nombre anterior y sigue valiendo. Vacía es ausente."""
        nueva = {"SAIMC_VERCEL_API_KEY": "nueva"}
        vieja = {"SAIMC_LLM_JEV_API_KEY": "vieja"}
        sin_archivo = tmp_path / "no-existe.env"
        assert jev_eval.load_api_key(nueva, sin_archivo) == "nueva"
        assert jev_eval.load_api_key(nueva | vieja, sin_archivo) == "nueva"
        assert jev_eval.load_api_key({"SAIMC_VERCEL_API_KEY": ""} | vieja, sin_archivo) == "vieja"
        env_file = tmp_path / ".env"
        env_file.write_text("SAIMC_VERCEL_API_KEY=nueva-del-archivo\n", encoding="utf-8")
        # El nombre gana antes que el origen: la nueva del archivo le gana a
        # la anterior del entorno.
        assert jev_eval.load_api_key(vieja, env_file) == "nueva-del-archivo"

    def test_sin_clave_devuelve_none(self, tmp_path: Path) -> None:
        assert jev_eval.load_api_key({}, tmp_path / "no-existe.env") is None
        vacio = tmp_path / ".env"
        vacio.write_text("SAIMC_LLM_JEV_API_KEY=\n", encoding="utf-8")
        assert jev_eval.load_api_key({}, vacio) is None


# --- Evaluación y reporte -----------------------------------------------------


def _entry(ad: Any, answers: dict[str, Any], *, cost: float = 0.00001) -> dict[str, Any]:
    questions = jev_eval.build_questions(CATALOG, curated=ad.curated)
    return {
        "ingestion_id": ad.ingestion_id,
        "questions_hash": jev_eval.questions_hash(questions),
        "model": "typesafe-ai/jev",
        "answers": answers,
        "input_tokens": 100,
        "output_tokens": 5,
        "cost_usd": cost,
        "asked_at": "2026-10-03T12:00:00+00:00",
    }


def _choice(key: str, confidence: float) -> dict[str, Any]:
    return {"type": "choice", "choice": key, "probabilities": {key: confidence}, "confidence": confidence}


def _evaluation() -> Any:
    """Cuatro anuncios con respuesta y uno sin respuesta.

    c1: curado, un solo valor por campo. Jev acierta todo.
    c2: curado, dos productos y provincia nula. Jev elige uno de los dos
        productos (acierta) y dice «hab» donde la verdad es `none` (falla).
    d1, d2: descartados. d3: descartado sin respuesta en el caché.
    """
    c1 = _ad(
        "c1",
        curated=True,
        truth={
            "product": frozenset({"arroz"}),
            "currency": frozenset({"CUP"}),
            "province": frozenset({"hab"}),
            "sale_format": frozenset({"retail"}),
        },
        channel_has_province=True,
        claude_discard=False,
        claude_fields={
            "product": frozenset({"arroz"}),
            "currency": frozenset({"CUP"}),
            "province": frozenset({"none"}),
            "sale_format": frozenset({"retail"}),
        },
    )
    c2 = _ad(
        "c2",
        curated=True,
        truth={
            "product": frozenset({"arroz", "pollo"}),
            "currency": frozenset({"CUP", "USD"}),
            "province": frozenset({"none"}),
            "sale_format": frozenset({"retail"}),
        },
        claude_discard=True,  # Claude lo habría descartado: sin filas que comparar.
    )
    d1 = _ad("d1", curated=False, discard_reason="off_focus", claude_discard=True)
    d2 = _ad("d2", curated=False, discard_reason="no_data", claude_discard=False)
    d3 = _ad("d3", curated=False)  # sin respuesta de Jev y sin propuesta
    ads = [c1, c2, d1, d2, d3]
    entries = [
        _entry(
            c1,
            {
                "triage": {"type": "noul", "noul": 0.9},
                "product": _choice("arroz", 0.9),
                "currency": _choice("CUP", 0.8),
                "province": _choice("hab", 0.7),
                "sale_format": _choice("retail", 0.6),
            },
        ),
        _entry(
            c2,
            {
                "triage": {"type": "noul", "noul": 0.4},
                "product": _choice("pollo", 0.5),
                "currency": _choice("USD", 0.5),
                "province": _choice("hab", 0.9),
                "sale_format": {"type": "choice", "choice": "retail"},  # inválida
            },
        ),
        _entry(d1, {"triage": {"type": "noul", "noul": 0.2}}),
        _entry(d2, {"triage": {"type": "noul", "noul": 0.6}}),
    ]
    cache = {(e["ingestion_id"], e["questions_hash"]): e for e in entries}
    return jev_eval.evaluate(ads, CATALOG, cache)


class TestEvaluate:
    def test_triage(self) -> None:
        triage = _evaluation().triage
        # Pares (p, y): (0,9; 1) (0,4; 1) (0,2; 0) (0,6; 0).
        # Brier = (0,01 + 0,36 + 0,04 + 0,36) / 4 = 0,1925.
        assert triage.calibration.n == 4
        assert triage.calibration.brier == pytest.approx(0.1925)
        # Aciertos con umbral 0,5: c1 (sí) y d1 (no). c2 y d2 fallan.
        assert triage.calibration.hits == 2
        assert triage.unanswered == 1

    def test_triage_por_motivo_de_descarte(self) -> None:
        groups = {g.label: g for g in _evaluation().triage.groups}
        assert set(groups) == {"curado", "off_focus", "no_data", "descartado sin motivo"}
        assert (groups["curado"].n, groups["curado"].hits) == (2, 1)
        assert (groups["off_focus"].n, groups["off_focus"].hits) == (1, 1)
        assert (groups["no_data"].n, groups["no_data"].hits) == (1, 0)
        assert groups["descartado sin motivo"].n == 0

    def test_campo_sobre_todos_y_sobre_un_solo_valor(self) -> None:
        fields = _evaluation().fields
        product = fields["product"]
        # Todos: c1 acierta con 0,9; c2 acierta con 0,5 (pollo ∈ {arroz, pollo}).
        assert (product.all.n, product.all.hits) == (2, 2)
        # Brier = ((0,9−1)² + (0,5−1)²) / 2 = (0,01 + 0,25) / 2 = 0,13.
        assert product.all.brier == pytest.approx(0.13)
        # Un solo valor: solo c1.
        assert (product.single.n, product.single.hits) == (1, 1)

    def test_none_cuenta_como_verdad_y_respuesta_invalida_queda_fuera(self) -> None:
        fields = _evaluation().fields
        # Provincia: c1 acierta; c2 dice «hab» y la verdad es `none`.
        assert (fields["province"].all.n, fields["province"].all.hits) == (2, 1)
        # Formato: la respuesta de c2 no trae probabilidad → inválida, fuera del n.
        assert fields["sale_format"].all.n == 1
        assert fields["sale_format"].invalid == 1

    def test_provincia_por_canal(self) -> None:
        by_channel = _evaluation().province_by_channel
        assert (by_channel["canal con provincia"].n, by_channel["canal con provincia"].k) == (1, 1)
        assert (by_channel["canal sin provincia"].n, by_channel["canal sin provincia"].k) == (1, 0)

    def test_acuerdo_con_claude_en_el_triage(self) -> None:
        agreement = _evaluation().agreement
        # (Claude descarta, Jev descarta): c1 (no, no) · c2 (sí, sí) · d1 (sí, sí)
        # · d2 (no, no). d3 no tiene ni respuesta ni propuesta.
        assert agreement.triage == {
            (True, True): 2,
            (True, False): 0,
            (False, True): 0,
            (False, False): 2,
        }
        assert agreement.triage_without_proposal == 0

    def test_acuerdo_con_claude_en_los_campos(self) -> None:
        agreement = _evaluation().agreement
        # Solo c1 tiene propuesta con filas. Jev dijo «hab» y Claude no propuso
        # provincia (`none`): desacuerdo. En producto coinciden.
        assert (agreement.fields["product"].k, agreement.fields["product"].n) == (1, 1)
        assert (agreement.fields["province"].k, agreement.fields["province"].n) == (0, 1)
        # c2 queda fuera: su propuesta no trae filas.
        assert agreement.fields_without_proposal == 1

    def test_costo_y_modelo(self) -> None:
        evaluation = _evaluation()
        assert evaluation.cost == pytest.approx(0.00004)
        assert evaluation.models == ("typesafe-ai/jev",)

    def test_sin_respuestas_no_revienta(self) -> None:
        evaluation = jev_eval.evaluate([_ad("a", curated=True)], CATALOG, {})
        assert evaluation.triage.calibration.n == 0
        assert jev_eval.render_report(evaluation, today="2026-10-03")


class TestReport:
    def test_el_reporte_muestra_numeros_y_limites_sin_decidir(self) -> None:
        report = jev_eval.render_report(_evaluation(), today="2026-10-03")
        assert "2026-10-03" in report
        # El título nombra el modelo que la API dijo haber usado.
        assert "typesafe-ai/jev" in report.splitlines()[0]
        assert "0,1925" in report  # Brier del triage
        for heading in ("Triage", "product", "currency", "province", "sale_format"):
            assert heading in report
        assert "top-1" in report
        assert "canal" in report.lower()
        assert "no fija umbrales" in report.lower()
        assert FAKE_KEY not in report


# --- Carga desde la base ------------------------------------------------------


def _ingestion(session: Session, **kwargs: Any) -> RawIngestion:
    values: dict[str, Any] = {
        "source_type": "inbox_text",
        "file_path": f"data/raw/{uuid.uuid4()}.txt",
        "content_hash": uuid.uuid4().hex,
        "content_type": "text",
        "original_text": "Arroz a 300",
        "processing_status": "curated",
        "captured_at": datetime(2026, 9, 1, tzinfo=UTC),
    }
    values.update(kwargs)
    ingestion = RawIngestion(**values)
    session.add(ingestion)
    session.flush()
    return ingestion


def _price(session: Session, ingestion: RawIngestion, product: Product, **kwargs: Any) -> None:
    values: dict[str, Any] = {
        "product_id": product.id,
        "price": Decimal("300"),
        "currency": "CUP",
        "source_id": ingestion.id,
        "provenance": "human",
    }
    values.update(kwargs)
    session.add(PriceHistory(**values))
    session.flush()


class TestLoad:
    @pytest.fixture
    def world(self, session: Session) -> dict[str, Any]:
        arroz = Product(name="Arroz", category="granos", unit="lb", is_pilot=True)
        azucar = Product(name="Azúcar blanca", category="x", unit="lb", is_pilot=True)
        talco = Product(name="Talco", category="x", unit="u", is_pilot=False)
        hab = Province(code="hab", name="La Habana")
        scu = Province(code="scu", name="Santiago de Cuba")
        session.add_all([arroz, azucar, talco, hab, scu])
        session.flush()
        channel = Channel(name="Canal", platform="whatsapp_channel", province_id=hab.id)
        session.add(channel)
        session.flush()
        return {
            "arroz": arroz, "azucar": azucar, "talco": talco, "hab": hab, "scu": scu,
            "channel": channel,
        }  # fmt: skip

    def test_catalogo_solo_pilotos_con_clave_estable(self, session: Session, world: dict[str, Any]) -> None:
        catalog = jev_eval.load_catalog(session)
        assert catalog.products == {"arroz": "Arroz", "azucar_blanca": "Azúcar blanca"}
        assert catalog.provinces == {"hab": "La Habana", "scu": "Santiago de Cuba"}

    def test_muestra_solo_texto_con_decision_y_texto_no_vacio(
        self, session: Session, world: dict[str, Any]
    ) -> None:
        curated = _ingestion(session)
        _price(session, curated, world["arroz"])
        discarded = _ingestion(session, processing_status="discarded", discard_reason="off_focus")
        _ingestion(session, processing_status="pending")
        _ingestion(session, content_type="image")
        _ingestion(session, processing_status="discarded", original_text="   ")
        _ingestion(session, processing_status="discarded", original_text=None)

        ads = jev_eval.load_ads(session, jev_eval.load_catalog(session))
        by_id = {ad.ingestion_id: ad for ad in ads}
        assert set(by_id) == {str(curated.id), str(discarded.id)}
        assert by_id[str(curated.id)].curated is True
        assert by_id[str(discarded.id)].curated is False
        assert by_id[str(discarded.id)].discard_reason == "off_focus"
        assert by_id[str(discarded.id)].truth == {}

    def test_la_verdad_es_el_conjunto_de_valores_de_las_filas_curadas(
        self, session: Session, world: dict[str, Any]
    ) -> None:
        ingestion = _ingestion(session, channel_id=world["channel"].id)
        _price(session, ingestion, world["arroz"], province_id=world["hab"].id, sale_format="retail")
        _price(
            session, ingestion, world["azucar"], currency="USD",
            exchange_rate=None, province_id=None, sale_format=None,
        )  # fmt: skip
        _price(session, ingestion, world["talco"], province_id=world["hab"].id, sale_format="retail")

        (ad,) = jev_eval.load_ads(session, jev_eval.load_catalog(session))
        assert ad.truth == {
            # El producto fuera del foco cae en `other`.
            "product": frozenset({"arroz", "azucar_blanca", "other"}),
            "currency": frozenset({"CUP", "USD"}),
            "province": frozenset({"hab", "none"}),
            "sale_format": frozenset({"retail", "none"}),
        }
        assert ad.channel_has_province is True

    def test_propuesta_de_claude_la_mas_reciente_exitosa(
        self, session: Session, world: dict[str, Any]
    ) -> None:
        ingestion = _ingestion(session)
        _price(session, ingestion, world["arroz"])
        base = {"ingestion_id": ingestion.id, "model": "m", "prompt_version": "v",
                "input_tokens": 1, "output_tokens": 1}  # fmt: skip
        row = {
            "product_id": str(world["arroz"].id),
            "currency": "USD",
            "province_id": str(world["scu"].id),
            "sale_format": None,
        }
        session.add_all(
            [
                LlmProposal(**base, status="ok", discard=True, rows=[],
                            created_at=datetime(2026, 9, 1, tzinfo=UTC)),
                LlmProposal(**base, status="ok", discard=False, rows=[row],
                            created_at=datetime(2026, 9, 2, tzinfo=UTC)),
                # Más nueva pero degradada: no cuenta.
                LlmProposal(**base, status="degraded", discard=None, rows=[],
                            created_at=datetime(2026, 9, 3, tzinfo=UTC)),
            ]
        )  # fmt: skip
        session.flush()

        (ad,) = jev_eval.load_ads(session, jev_eval.load_catalog(session))
        assert ad.claude_discard is False
        assert ad.claude_fields == {
            "product": frozenset({"arroz"}),
            "currency": frozenset({"USD"}),
            "province": frozenset({"scu"}),
            "sale_format": frozenset({"none"}),
        }
        assert ad.channel_has_province is False

    def test_sin_propuesta(self, session: Session, world: dict[str, Any]) -> None:
        _ingestion(session, processing_status="discarded")
        (ad,) = jev_eval.load_ads(session, jev_eval.load_catalog(session))
        assert ad.claude_discard is None
        assert ad.claude_fields == {}


# --- Fuera del camino crítico -------------------------------------------------


def test_nada_del_sistema_importa_el_script() -> None:
    """A6 es una prueba aparte: `src/saimc` no puede depender de ella."""
    src = Path(__file__).resolve().parent.parent / "src" / "saimc"
    offenders = [
        str(path) for path in src.rglob("*.py") if re.search(r"jev_eval", path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_dry_run_informa_n_por_pregunta_y_estimacion() -> None:
    ads = [_ad("a", curated=True), _ad("b", curated=False), _ad("c", curated=False)]
    estimate = jev_eval.dry_run_estimate(ads, CATALOG, cache={})
    assert estimate.n_by_question == {
        "triage": 3,
        "product": 1,
        "currency": 1,
        "province": 1,
        "sale_format": 1,
    }
    assert estimate.pending == 3
    # La estimación sale del pedido serializado: caracteres / 3,5, por el
    # factor 2,3 que se midió contra los tokens reales de la corrida de Jev.
    expected = sum(
        math.ceil(
            jev_eval.estimate_tokens(json.dumps(jev_eval.build_request(ad, CATALOG), ensure_ascii=False))
            * 2.3
        )
        for ad in ads
    )
    assert estimate.tokens == expected
    assert estimate.cost == pytest.approx(expected * 0.042 / 1_000_000)


# --- Entrada: en seco por defecto ---------------------------------------------


class TestMain:
    """Sin flags el script no gasta: la corrida real pide `--execute`."""

    @pytest.fixture
    def wired(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
        """`main` con la base, la clave, el cliente y las rutas reemplazados."""
        api = _Api()
        key_reads: list[int] = []

        def require_key() -> str:
            key_reads.append(1)
            return FAKE_KEY

        ads = [_ad("a", curated=True), _ad("b", curated=False), _ad("c", curated=False)]
        monkeypatch.setattr(jev_eval, "_load_sample", lambda: (CATALOG, ads))
        monkeypatch.setattr(jev_eval, "_require_key", require_key)
        monkeypatch.setattr(jev_eval, "_new_client", lambda: _client(api))
        monkeypatch.setattr(jev_eval, "OUTPUT_DIR", tmp_path / "jev_eval")
        monkeypatch.setattr(jev_eval, "CACHE_PATH", tmp_path / "jev_eval" / "responses.jsonl")
        monkeypatch.setattr(jev_eval, "REPORT_PATH", tmp_path / "jev_eval" / "report.md")
        monkeypatch.setattr(jev_eval.time, "sleep", lambda _seconds: None)
        return {"api": api, "key_reads": key_reads, "out": tmp_path / "jev_eval"}

    @pytest.mark.parametrize("argv", [[], ["--dry-run"], ["--limit", "2"], ["--max-cost", "5"]])
    def test_sin_execute_no_llama_ni_escribe_ni_lee_la_clave(
        self, wired: dict[str, Any], capsys: pytest.CaptureFixture[str], argv: list[str]
    ) -> None:
        jev_eval.main(argv)
        assert wired["api"].requests == []
        assert wired["key_reads"] == []
        assert not wired["out"].exists()  # ni caché ni reporte
        out = capsys.readouterr().out
        assert "triage       3" in out
        assert "product      1" in out
        assert "Dry-run" in out
        assert "--execute" in out

    def test_con_execute_llama_y_escribe_cache_y_reporte(
        self, wired: dict[str, Any], capsys: pytest.CaptureFixture[str]
    ) -> None:
        jev_eval.main(["--execute"])
        assert len(wired["api"].requests) == 3
        cache_lines = (wired["out"] / "responses.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(cache_lines) == 3
        assert (wired["out"] / "report.md").is_file()
        out = capsys.readouterr().out
        assert "Preguntados en esta corrida: 3" in out
        assert FAKE_KEY not in out

    def test_limit_y_max_cost_rigen_con_execute(self, wired: dict[str, Any]) -> None:
        jev_eval.main(["--execute", "--limit", "2"])
        assert len(wired["api"].requests) == 2
        jev_eval.main(["--execute", "--max-cost", "0"])
        assert len(wired["api"].requests) == 2  # tope en cero: ni una más

    def test_execute_y_dry_run_no_van_juntos(self, wired: dict[str, Any]) -> None:
        with pytest.raises(SystemExit):
            jev_eval.main(["--execute", "--dry-run"])
        assert wired["api"].requests == []


# --- Respuesta real y las dos señales de `choice` -----------------------------

# La forma real observada en el gateway el 2026-10-04, con un anuncio sintético:
# los campos y los valores de `answers`, `usage` y `provider_metadata` son tal
# cual llegaron. La respuesta la dio otro modelo de la misma interfaz, probado
# ese día; el campo `model` se reemplazó por el de Jev, que es el que mide el
# script. Jev devuelve la misma forma (corrida completa del 2026-10-04).
REAL_BODY: dict[str, Any] = {
    "model": "typesafe-ai/jev",
    "answers": {
        "triage": {"type": "noul", "noul": 0.85},
        "currency": {
            "type": "choice",
            "choice": "CUP",
            "confidence": 0.1974,
            "probabilities": {"CUP": 0.6558, "USD": 0.162, "MLC": 0.1822},
        },
    },
    "usage": {"input_tokens": 153, "output_tokens": 0},
    "provider_metadata": {"gateway": {"cost": "0"}},
}


class TestRealResponse:
    def test_costo_cero_del_gateway_queda_en_cero(self) -> None:
        result = jev_eval.parse_response(REAL_BODY)
        assert result.model == "typesafe-ai/jev"
        assert (result.input_tokens, result.output_tokens) == (153, 0)
        # El gateway dijo 0: no se reemplaza por una estimación.
        assert result.cost == 0.0

    def test_probabilidad_de_la_elegida_y_confidence_son_dos_senales(self) -> None:
        question = jev_eval.build_questions(CATALOG, curated=True)["currency"]
        answer = jev_eval.parse_answer(question, REAL_BODY["answers"]["currency"])
        assert (answer.choice, answer.invalid) == ("CUP", "")
        assert answer.p == pytest.approx(0.6558)  # probabilities["CUP"]
        assert answer.confidence == pytest.approx(0.1974)

    def test_triage_real(self) -> None:
        question = jev_eval.build_questions(CATALOG, curated=True)["triage"]
        assert jev_eval.parse_answer(question, REAL_BODY["answers"]["triage"]).p == pytest.approx(0.85)


def _two_signals() -> Any:
    """Tres curados, solo se mira `currency` (verdad: CUP en los tres).

    a: elige CUP (acierta), probabilidad 0,8 y confidence 0,4.
    b: elige USD (falla),   probabilidad 0,6 y confidence 0,1.
    c: elige CUP (acierta), sin `probabilities`, confidence 0,5.
    """
    truth = {"currency": frozenset({"CUP"})}
    ads = [_ad(name, curated=True, truth=truth) for name in ("a", "b", "c")]
    raw = [
        {"choice": "CUP", "probabilities": {"CUP": 0.8, "USD": 0.2}, "confidence": 0.4},
        {"choice": "USD", "probabilities": {"CUP": 0.4, "USD": 0.6}, "confidence": 0.1},
        {"choice": "CUP", "confidence": 0.5},
    ]
    entries = [_entry(ad, {"currency": answer}) for ad, answer in zip(ads, raw, strict=True)]
    cache = {(e["ingestion_id"], e["questions_hash"]): e for e in entries}
    return jev_eval.evaluate(ads, CATALOG, cache)


class TestTwoSignals:
    def test_la_calibracion_usa_la_probabilidad_de_la_elegida(self) -> None:
        currency = _two_signals().fields["currency"]
        # Pares (p, y): (0,8; 1) y (0,6; 0). Brier = (0,04 + 0,36) / 2 = 0,20.
        assert currency.all.n == 2
        assert currency.all.brier == pytest.approx(0.20)

    def test_confidence_se_calibra_aparte(self) -> None:
        currency = _two_signals().fields["currency"]
        # Pares (confidence, y): (0,4; 1) (0,1; 0) (0,5; 1).
        # Brier = (0,36 + 0,01 + 0,25) / 3 = 0,62 / 3.
        assert currency.confidence_all.n == 3
        assert currency.confidence_all.brier == pytest.approx(0.62 / 3)
        assert currency.confidence_single.n == 3

    def test_sin_probabilities_cuenta_para_exactitud_y_se_informa(self) -> None:
        currency = _two_signals().fields["currency"]
        assert (currency.accuracy.k, currency.accuracy.n) == (2, 3)
        assert (currency.accuracy_single.k, currency.accuracy_single.n) == (2, 3)
        assert currency.without_probability == 1
        assert currency.invalid == 0

    def test_el_reporte_rotula_las_dos_senales(self) -> None:
        report = jev_eval.render_report(_two_signals(), today="2026-10-04")
        assert "probabilities[choice]" in report
        assert "`confidence`" in report
        assert "no se sabe cómo" in report.lower()
        assert "0,2000" in report  # Brier por probabilidad
        assert "0,2067" in report  # Brier por confidence
        assert "sin `probabilities`" in report.lower()


# --- Mudanza del cliente al sistema (A6b-1, caso 25) --------------------------


def test_la_huella_de_las_preguntas_de_a6_no_cambio_con_la_mudanza() -> None:
    """Fijación de la mudanza, no una verdad de validación: los dos valores se
    capturaron corriendo el script ANTES de mover el cliente a
    `saimc.validation.jev_client` (2026-10-04, commit d95a712). Si cambian, el
    caché de `data/jev_eval/` deja de valer y la corrida de A6 se pagaría de
    nuevo."""
    curados = jev_eval.build_questions(CATALOG, curated=True)
    descartados = jev_eval.build_questions(CATALOG, curated=False)

    assert jev_eval.questions_hash(curados) == "08def85f649eb41c"
    assert jev_eval.questions_hash(descartados) == "884ee02b10af398b"


def test_el_script_usa_el_cliente_del_sistema() -> None:
    """Hay un solo cliente: el script importa el del sistema, no trae una copia."""
    from saimc.validation import jev_client

    assert jev_eval.ask is jev_client.ask
    assert jev_eval.parse_answer is jev_client.parse_answer
    assert jev_eval.parse_response is jev_client.parse_response
    assert jev_eval.JevError is jev_client.JevError
    assert jev_eval.API_URL == jev_client.API_URL
