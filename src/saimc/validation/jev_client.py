"""Cliente de Jev: el pedido, la respuesta y la red (slice A6b).

Jev (TypeSafe AI, vía Vercel AI Gateway) contesta preguntas cerradas con una
probabilidad. Este módulo arma el pedido, hace la llamada y lee la respuesta.
**No toca la base**: qué anuncios se preguntan y dónde se guarda lo que
contesta es de `validation.jev_signal`.

Nació en la prueba fuera de línea de A6 y se mudó acá sin cambios de
comportamiento, para que el sistema y esa prueba usen el mismo cliente. Lo
único nuevo es que `ask` recibe el tiempo límite y la cantidad de reintentos.

Las preguntas son tres, siempre las mismas y en una sola llamada: `triage`
(sí/no), `product` y `currency` (elección entre las opciones del catálogo).
Cada respuesta de elección trae dos señales, que se guardan y se miden por
separado: la probabilidad de la opción elegida (`probabilities[choice]`) y
`confidence`, que no es lo mismo y cuyo cálculo el proveedor no documenta.

La URL y el modelo son constantes y no configuración del despliegue:
cambiarlos cambia la huella de las preguntas y pide medir de nuevo.

Privacidad: el texto sale redactado (`saimc.privacy`) en el borde del pedido,
y la clave se quita de todo mensaje de error antes de que salga de acá.

Contrato: `docs/spec-slice-A6b.md`.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
import unicodedata
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from saimc.models import Currency
from saimc.privacy import redact_personal_data

API_URL = "https://ai-gateway.vercel.sh/typesafe/v1/systemone"
MODEL = "typesafe-ai/jev"

#: Precio de referencia de la entrada, en USD por millón de tokens. Solo se usa
#: para estimar: el costo real de cada llamada lo informa el gateway.
INPUT_PRICE_PER_MTOK = 0.042
#: Caracteres por token para la estimación (el español suele rendir cerca de 4).
CHARS_PER_TOKEN = 3.5
#: Lo que el pedido real pesa por encima de sus caracteres / 3,5. Medido el
#: 2026-10-04 sobre 1.409 pedidos a Jev: 751.842 tokens cobrados contra 327.015
#: estimados. El gateway cuenta más que el texto que se le manda; sin este factor
#: la estimación en seco quedó 2,3 veces corta.
REQUEST_OVERHEAD_FACTOR = 2.3

#: Reintentos de un 429, un 5xx o un fallo de red: el intento y tres más. Es el
#: valor por defecto de `ask`; la pasada del pre-cálculo pide uno solo.
MAX_RETRIES = 3
#: Espera antes del primer reintento; se duplica en cada uno (1 s, 2 s, 4 s).
RETRY_BASE_SECONDS = 1.0
#: Tope a lo que se le cree a un `Retry-After`. El gateway pide 60 s cuando se
#: agota el cupo por minuto del plan gratuito (medido el 2026-10-04).
MAX_RETRY_AFTER_SECONDS = 120.0

TRIAGE = "triage"
PRODUCT = "product"
CURRENCY = "currency"
#: Producto fuera del foco. Una fila curada puede caer acá: hay filas de
#: productos que no son piloto.
OTHER_KEY = "other"

CURRENCY_LABELS: dict[str, str] = {
    Currency.CUP.value: "Peso cubano (CUP); también cuando el anuncio no dice la moneda",
    Currency.USD.value: "Dólar estadounidense (USD)",
    Currency.EUR.value: "Euro (EUR)",
    Currency.MLC.value: "Moneda libremente convertible (MLC)",
}


def slugify(name: str) -> str:
    """«Azúcar blanca» → `azucar_blanca`: la clave estable de un producto.

    `products` no tiene código ni slug; el nombre es único (`idx_products_name`).
    """
    # NFD separa la letra de su tilde; se descartan las marcas (categoría Mn).
    plain = "".join(ch for ch in unicodedata.normalize("NFD", name) if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9]+", "_", plain.lower()).strip("_")


# --- Pedido -------------------------------------------------------------------


def triage_question(product_names: Iterable[str]) -> dict[str, Any]:
    """«¿Ofrece un piloto con precio?», de sí o no.

    Pregunta lo mismo que el `discard` del asistente
    (`llm_propose_ad._SYSTEM_PROMPT`), al revés: sí = el operador lo curaría.
    """
    pilots = ", ".join(product_names)
    return {
        "type": "noul",
        "instructions": (
            "¿Este anuncio del mercado informal cubano ofrece a la venta, con un precio "
            f"utilizable, al menos uno de estos productos: {pilots}? Respondé que no si "
            "no ofrece ninguno de esos productos, si no trae precio, si está agotado o "
            "si es spam."
        ),
    }


def product_question(products: Mapping[str, str]) -> dict[str, Any]:
    """«¿Qué producto ofrece?». `products` es clave → nombre de los pilotos."""
    return {
        "type": "choice",
        "instructions": (
            "¿Qué producto de la lista ofrece el anuncio? Si ofrece más de uno, elegí el principal."
        ),
        "criteria": {**products, OTHER_KEY: "Ningún producto de la lista"},
    }


def currency_question() -> dict[str, Any]:
    return {
        "type": "choice",
        "instructions": (
            "¿En qué moneda está el precio del producto que ofrece el anuncio? Si el anuncio "
            "no dice la moneda, es peso cubano."
        ),
        "criteria": dict(CURRENCY_LABELS),
    }


def build_questions(products: Mapping[str, str]) -> dict[str, dict[str, Any]]:
    """Las tres preguntas que el sistema le hace a todo texto.

    Antes de la decisión del operador no se sabe si el anuncio se va a curar o
    a descartar, así que van las tres siempre. Provincia y formato de venta no
    se preguntan (`docs/spec-slice-A6b.md`, «Las preguntas»).

    Los pilotos se ordenan por clave antes de armar nada: el triage los nombra
    en una frase, y sin esto el mismo catálogo leído en otro orden daría otra
    huella y se volvería a preguntar (y a pagar) todo.
    """
    ordered = dict(sorted(products.items()))
    return {
        TRIAGE: triage_question(ordered.values()),
        PRODUCT: product_question(ordered),
        CURRENCY: currency_question(),
    }


def questions_hash(questions: Mapping[str, Any]) -> str:
    """Huella de las preguntas (y del modelo): la versión de una respuesta.

    Si cambia una instrucción o una opción, cambia la huella y lo ya respondido
    deja de valer. `sort_keys` la vuelve independiente del orden de los
    diccionarios.
    """
    canonical = json.dumps({"model": MODEL, "questions": questions}, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def build_request(text: str, questions: Mapping[str, Any]) -> dict[str, Any]:
    """El cuerpo del pedido. La redacción de teléfonos va acá, en el borde:
    aunque el texto ya venga redactado de la ingesta, nada sale sin pasar por
    `redact_personal_data`. No lleva id de ingesta, canal, emisor ni provincia."""
    return {
        "model": MODEL,
        "state": redact_personal_data(text) or "",
        "questions": dict(questions),
    }


def estimate_tokens(text: str) -> int:
    """Estimación de tokens: caracteres / 3,5, redondeado hacia arriba."""
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def tokens_cost(input_tokens: int) -> float:
    """Costo en USD de esos tokens de entrada al precio de referencia."""
    return input_tokens * INPUT_PRICE_PER_MTOK / 1_000_000


def request_tokens_from_text(serialized: str) -> int:
    """Tokens que se espera que cobre un pedido ya serializado."""
    return math.ceil(estimate_tokens(serialized) * REQUEST_OVERHEAD_FACTOR)


def request_tokens(payload: Mapping[str, Any]) -> int:
    """Tokens que se espera que cobre un pedido."""
    return request_tokens_from_text(json.dumps(payload, ensure_ascii=False))


# --- Respuesta ----------------------------------------------------------------


class JevError(Exception):
    """Un pedido que no dio respuesta usable. `status` es `None` sin respuesta HTTP."""

    def __init__(self, status: int | None, message: str) -> None:
        super().__init__(message)
        self.status = status


# `frozen=True` hace al objeto inmutable (asignar un atributo levanta), como un
# `readonly class` de PHP 8.2.
@dataclass(frozen=True)
class ApiResult:
    """Una respuesta 200 con forma reconocible. `answers` va tal cual llegó."""

    model: str
    answers: dict[str, Any]
    input_tokens: int
    output_tokens: int
    cost: float


@dataclass(frozen=True)
class Answer:
    """Una respuesta ya leída. `invalid` trae el motivo si no se pudo usar."""

    #: `noul`: P(sí). `choice`: `probabilities[choice]`, la probabilidad que el
    #: modelo le da a la opción que eligió. `None` si no vino `probabilities`.
    p: float | None = None
    choice: str | None = None
    #: Solo `choice`: el campo `confidence`, tal cual. NO es la probabilidad de
    #: la elegida (respuesta real: 0,1974 contra 0,6558) y el proveedor no
    #: documenta cómo la calcula. Se mide aparte.
    confidence: float | None = None
    invalid: str = ""


def _probability(value: object) -> float | None:
    """Un número entre 0 y 1, o `None`. `bool` queda afuera a propósito: en
    Python `True` es un `int`, y un `true` de JSON no es una probabilidad."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if 0.0 <= value <= 1.0 else None


def _count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def parse_response(body: object) -> ApiResult:
    """Lee el cuerpo de un 200. Levanta `JevError` si no trae `answers`.

    Es tolerante con todo lo demás: sin `usage` cuenta 0 tokens y sin el costo
    del gateway lo estima con los tokens.
    """
    if not isinstance(body, dict):
        raise JevError(None, "la respuesta no es un objeto JSON")
    answers = body.get("answers")
    if not isinstance(answers, dict):
        detail = body.get("message") or body.get("error_type") or "sin `answers`"
        raise JevError(None, f"respuesta sin `answers`: {detail}")

    usage = body.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    input_tokens = _count(usage.get("input_tokens"))

    metadata = body.get("provider_metadata")
    gateway = metadata.get("gateway") if isinstance(metadata, dict) else None
    raw_cost = gateway.get("cost") if isinstance(gateway, dict) else None
    try:
        # El gateway manda el costo como texto («0.00001155»).
        cost = float(raw_cost) if raw_cost is not None else tokens_cost(input_tokens)
    except (TypeError, ValueError):
        cost = tokens_cost(input_tokens)

    model = body.get("model")
    return ApiResult(
        model=model if isinstance(model, str) else "",
        answers=answers,
        input_tokens=input_tokens,
        output_tokens=_count(usage.get("output_tokens")),
        cost=cost,
    )


def parse_answer(question: Mapping[str, Any], raw: object) -> Answer:
    """Lee la respuesta a UNA pregunta. Nunca levanta: lo ilegible es `invalid`."""
    if not isinstance(raw, dict):
        return Answer(invalid="sin respuesta")

    if question["type"] == "noul":
        p = _probability(raw.get("noul"))
        return Answer(p=p) if p is not None else Answer(invalid="`noul` ausente o fuera de 0–1")

    choice = raw.get("choice")
    if not isinstance(choice, str) or choice not in question["criteria"]:
        return Answer(invalid="`choice` ausente o fuera de las opciones")
    probabilities = raw.get("probabilities")
    p = _probability(probabilities.get(choice)) if isinstance(probabilities, dict) else None
    confidence = _probability(raw.get("confidence"))
    if p is None and confidence is None:
        return Answer(choice=choice, invalid="sin `confidence` ni `probabilities` de la elegida")
    # Con una sola de las dos la respuesta vale: cuenta para la exactitud y
    # para la calibración de la señal que sí trajo.
    return Answer(p=p, choice=choice, confidence=confidence)


# --- Red ----------------------------------------------------------------------


def _scrub(message: str, api_key: str) -> str:
    """Saca la clave de un mensaje, por si el servidor la devuelve en el error."""
    return message.replace(api_key, "[clave]") if api_key else message


def _error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        # TypeSafe manda `message`/`error_type` en la raíz; el gateway de Vercel
        # anida su propio rechazo bajo `error`, con `type` en vez de `error_type`.
        nested = body.get("error")
        if isinstance(nested, dict):
            body = {"error_type": nested.get("type"), "message": nested.get("message")}
        parts = [str(body[k]) for k in ("error_type", "message") if body.get(k)]
        if parts:
            return f"HTTP {response.status_code}: " + " — ".join(parts)
    return f"HTTP {response.status_code}"


def ask(
    client: httpx.Client,
    payload: Mapping[str, Any],
    api_key: str,
    *,
    sleep: Callable[[float], None] = time.sleep,
    timeout: float | None = None,
    max_retries: int = MAX_RETRIES,
) -> ApiResult:
    """Hace un pedido. Reintenta 429, 5xx y fallos de red con espera creciente.

    `sleep` es un parámetro para que los tests no esperen de verdad. `timeout`
    son los segundos por intento; sin valor rige el del cliente. `max_retries`
    son los reintentos después del primer intento. Levanta `JevError`, con la
    clave ya quitada del mensaje.
    """
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    # En httpx, `timeout=None` quiere decir «sin límite», no «el del cliente»:
    # por eso el argumento solo se pasa cuando hay un valor.
    extra: dict[str, Any] = {} if timeout is None else {"timeout": timeout}
    error = JevError(None, "sin intentos")
    for attempt in range(max_retries + 1):
        wait = RETRY_BASE_SECONDS * 2**attempt
        try:
            response = client.post(API_URL, headers=headers, json=payload, **extra)
        except httpx.HTTPError as exc:
            error = JevError(None, _scrub(f"fallo de red: {type(exc).__name__}: {exc}", api_key))
        else:
            if response.status_code == 200:
                try:
                    return parse_response(response.json())
                except ValueError as exc:  # el cuerpo no es JSON
                    raise JevError(200, "respuesta 200 que no es JSON") from exc
                except JevError as exc:
                    raise JevError(200, _scrub(str(exc), api_key)) from exc
            error = JevError(response.status_code, _scrub(_error_message(response), api_key))
            if response.status_code != 429 and response.status_code < 500:
                raise error  # un 4xx no mejora reintentando
            if response.status_code == 429:
                # El cupo es por minuto: la espera creciente (1, 2, 4 s) se
                # agotaría antes de que se reponga. Se espera lo que pide el
                # servidor, acotado por si manda un valor absurdo.
                try:
                    asked = float(response.headers.get("retry-after", ""))
                except ValueError:
                    pass  # falta o no es un número de segundos: espera creciente
                else:
                    wait = min(max(asked, 0.0), MAX_RETRY_AFTER_SECONDS)
        if attempt < max_retries:
            sleep(wait)
    raise error
