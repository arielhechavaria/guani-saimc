"""Cliente elTOQUE TRMI (`spec.md` §6.3 E, design.md D1/D3).

Separa transporte (`fetch_rate`, hace la llamada HTTP) de parseo puro
(`parse_rate`, sin red) — el seam que permite probar la política de
degradación sin levantar un socket y probar el parser sin un cliente HTTP.

Hallazgos verificados en el sondeo del 2026-08-13 (Engram
`discovery/eltoque-trmi-api`), cada uno con su consecuencia acá:

- El histórico funciona, pero un solo día calendario por llamada. Un rango
  >= 24h responde con el string plano `El intervalo de tiempo debe ser menor
  a 24 horas` — no JSON, HTTP 200. `parse_rate` no debe reventar con eso.
- El campo `date` de la respuesta MIENTE: siempre devuelve la fecha de la
  consulta, no la del dato. Por eso `parse_rate` no lo expone: la firma sólo
  puede devolver un `Decimal`, la fecha persistida la decide el llamador con
  la fecha que PIDIÓ (D3).
- El euro se llama `ECU` en el JSON, no `EUR`.
- Un día sin dato responde HTTP 200 con `"tasas": {}` — la ausencia se
  detecta en el payload, nunca en el status code.
"""

import json
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from saimc.models import Currency

_TRMI_URL = "https://tasas.eltoque.com/v1/trmi"

# Mapeo entre el enum del modelo y la clave real del JSON de elTOQUE. CUP no
# aparece: nunca se resuelve tasa para moneda nacional.
_ELTOQUE_KEYS = {
    Currency.USD: "USD",
    Currency.EUR: "ECU",
    Currency.MLC: "MLC",
}


def parse_rate(body: str, key: str) -> Decimal | None:
    """Extrae la tasa de `key` del cuerpo de la respuesta de elTOQUE.

    Puro, sin red: recibe el texto crudo de la respuesta HTTP y devuelve la
    tasa o `None`. Nunca levanta excepción — cualquier forma inesperada del
    cuerpo (texto plano, JSON sin la clave, `tasas` vacío, valor no numérico)
    degrada a `None`, nunca a un error.
    """
    try:
        payload: Any = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return None

    if not isinstance(payload, dict):
        return None

    tasas = payload.get("tasas")
    if not isinstance(tasas, dict) or not tasas:
        return None

    valor = tasas.get(key)
    if valor is None:
        return None

    try:
        tasa = Decimal(str(valor))
    except InvalidOperation:
        return None

    if tasa <= 0:
        return None

    return tasa


class ElToqueUnavailable(Exception):
    """La API no respondió con un 200: límite de peticiones (429), otro
    status o falla de red. No dice nada sobre si el día tiene dato."""


def fetch_rate_strict(
    key: str,
    day: date,
    *,
    api_key: str,
    timeout: float,
    transport: httpx.BaseTransport | None = None,
) -> Decimal | None:
    """Como `fetch_rate`, pero separa las dos ausencias: `None` es un 200 sin
    tasa para `key` (el día no tiene dato), y `ElToqueUnavailable` es que la
    API no respondió. El backfill necesita la diferencia: medido el
    2026-10-18, elTOQUE corta con 429 tras una ráfaga y leerlo como día sin
    dato dejaba filas sin sanear por una razón falsa.
    """
    date_from = f"{day} 00:00:00"
    date_to = f"{day} 23:59:59"

    try:
        with httpx.Client(transport=transport, timeout=timeout) as client:
            response = client.get(
                _TRMI_URL,
                params={"date_from": date_from, "date_to": date_to},
                headers={"Authorization": f"Bearer {api_key}"},
            )
    except httpx.HTTPError as exc:
        raise ElToqueUnavailable(type(exc).__name__) from exc

    if response.status_code != 200:
        raise ElToqueUnavailable(f"HTTP {response.status_code}")

    return parse_rate(response.text, key)


def fetch_rate(
    key: str,
    day: date,
    *,
    api_key: str | None,
    timeout: float,
    transport: httpx.BaseTransport | None = None,
) -> Decimal | None:
    """Pide la tasa de `key` para el día `day` (un solo día calendario).

    Nunca levanta: falta de `api_key`, cualquier error de `httpx` (timeout,
    conexión) o un status distinto de 200 degradan a `None`. Sin retry, sin
    backoff (design.md, D3) — el llamador decide qué hacer con la ausencia.

    `transport` es el seam de test (design.md, D1): en producción se deja en
    `None` y `httpx.Client` arma el transporte real.
    """
    if not api_key:
        return None

    try:
        return fetch_rate_strict(
            key, day, api_key=api_key, timeout=timeout, transport=transport
        )
    except ElToqueUnavailable:
        return None
