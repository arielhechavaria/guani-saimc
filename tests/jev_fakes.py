"""Doble del gateway de Jev para las pruebas (slice A6b).

Ningún test toca la red: el cliente es un `httpx.Client` real con
`httpx.MockTransport`, que contesta desde una función. La clave es siempre una
cadena falsa.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx

FAKE_KEY = "clave-falsa-de-jev-123"

Handler = Callable[[httpx.Request], httpx.Response]


def noul(p: object) -> dict[str, Any]:
    """Una respuesta de sí/no: `p` es P(sí)."""
    return {"type": "noul", "noul": p}


def choice(key: str, probability: float | None = None, confidence: float | None = None) -> dict[str, Any]:
    """Una respuesta de elección, con las dos señales que trae Jev (o una sola)."""
    answer: dict[str, Any] = {"type": "choice", "choice": key}
    if probability is not None:
        answer["probabilities"] = {key: probability}
    if confidence is not None:
        answer["confidence"] = confidence
    return answer


#: Coincide con la fila F de la especificación: arroz en CUP, todo con
#: probabilidad alta (caso 2 de `docs/spec-slice-A6b.md`).
ANSWERS_OK: dict[str, Any] = {
    "triage": noul(0.98),
    "product": choice("arroz", 0.97, 0.96),
    "currency": choice("CUP", 0.99, 0.98),
}


def body(answers: dict[str, Any] | None = None, *, cost: str | None = "0.00003") -> dict[str, Any]:
    """Un cuerpo 200 con la forma del gateway."""
    payload: dict[str, Any] = {
        "model": "typesafe-ai/jev",
        "answers": ANSWERS_OK if answers is None else answers,
        "usage": {"input_tokens": 500, "output_tokens": 0},
    }
    if cost is not None:
        payload["provider_metadata"] = {"gateway": {"cost": cost}}
    return payload


class Api:
    """Gateway falso: guarda los pedidos y contesta desde una cola.

    Cada elemento de `responses` es una `httpx.Response`, una excepción (se
    lanza) o `None` (contesta bien). Agotada la cola, contesta bien.
    """

    def __init__(
        self,
        responses: list[Any] | None = None,
        *,
        answers: dict[str, Any] | None = None,
        cost: str | None = "0.00003",
    ) -> None:
        self.requests: list[httpx.Request] = []
        self._responses = list(responses or [])
        self._answers = answers
        self._cost = cost

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        queued = self._responses.pop(0) if self._responses else None
        if isinstance(queued, Exception):
            raise queued
        if isinstance(queued, httpx.Response):
            return queued
        return httpx.Response(200, json=body(self._answers, cost=self._cost))

    def payloads(self) -> list[dict[str, Any]]:
        """El cuerpo JSON de cada pedido recibido."""
        return [json.loads(request.content) for request in self.requests]


def client(handler: Handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))
