"""Jev como señal en sombra: preguntar, guardar y anotar (slices A6b-1 y A6b-2).

Corre `python -m saimc.validation.jev_signal` (el relleno; por defecto, en seco).

`validation.jev_client` sabe hablar con Jev y no toca la base. Este módulo es
el que une a Jev con el sistema:

- **Preguntar** (`prefill_pass`): una pasada dentro del pre-cálculo, antes del
  bucle de Claude, sobre los textos pendientes que todavía no tienen señal con
  la huella de preguntas vigente. Una llamada por anuncio con las tres
  preguntas, alta de la fila en `jev_signals` y `commit`.
- **Anotar** (`annotate`): al curar, al editar y al revalidar, `persist` busca
  la señal vigente del anuncio y suma un bloque `jev` al `validation_detail`
  de cada fila. Es código puro.
- **Adaptar** (`to_external_signals`, A6b-2): convierte la respuesta en
  `ExternalSignal` para `AdContext.signals`. Es código puro y solo lo usa el
  informe de sombra (`validation.shadow`), que corre el motor con y sin Jev y
  compara; nada de eso se guarda.
- **Rellenar** (`run`, la línea de comandos, A6b-2): la misma pasada, a mano,
  sobre los pendientes o sobre los textos ya decididos (`--decided`), para que
  el informe tenga n desde el primer día.

**En el carril 1 es sombra: solo anota.** Nada de acá cambia
`validation_status` ni `provenance`, y el motor (`engine`, `rules`, `types`,
`fence`) no importa este módulo. **En el carril 0 decide** desde el slice
jev-decisor (`docs/spec-slice-jev-decisor.md`), solo con
`SAIMC_LANE0_JEV_ENABLED`: `validation.lane0` lee el triage de la señal
vigente y se lo pasa al motor como `AdContext.triage_probability`.

Al guardar no se llama a nadie: un anuncio que el operador decide
antes de que pase el temporizador queda «sin señal», que es un estado
soportado. Ningún umbral se fija acá: el de descarte vive en
`validation.lane0.JEV_DISCARD_BELOW`.

**Un fallo de Jev nunca le cuesta nada al resto.** La pasada lo anota en el
registro y sigue o corta, y el pre-cálculo termina con el código de salida de
siempre.

Lo que sale hacia el proveedor: el texto redactado, las tres preguntas y la
clave en la cabecera. No sale el id de la ingesta, el canal, el emisor ni la
provincia. El texto del anuncio nunca va al registro.

Contrato: `docs/spec-slice-A6b.md`.
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol

import httpx
from sqlalchemy import exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from saimc.config import Settings, get_settings
from saimc.db import get_session_factory
from saimc.models import (
    ContentType,
    Currency,
    JevSignal,
    PriceHistory,
    ProcessingStatus,
    Product,
    RawIngestion,
)
from saimc.progress import pilot_products
from saimc.validation import jev_client
from saimc.validation.jev_client import Answer, ApiResult, JevError
from saimc.validation.types import ExternalSignal

logger = logging.getLogger(__name__)

SOURCE_PREFILL = "prefill"
SOURCE_BACKFILL = "backfill"

# Por qué se cortó una pasada. Son los valores de `PrefillRun.jev_stopped`.
STOP_AUTH = "auth"
STOP_FORBIDDEN = "forbidden"
STOP_RATE_LIMIT = "rate_limit"
STOP_MAX_COST = "max_cost"
STOP_FAILURES = "failures"

#: Cada corte, en palabras del operador (salida del pre-cálculo y `/control`).
STOP_LABELS: dict[str, str] = {
    STOP_AUTH: "la clave fue rechazada",
    STOP_FORBIDDEN: "el gateway denegó el acceso",
    STOP_RATE_LIMIT: "se agotó el cupo de pedidos",
    STOP_MAX_COST: "se llegó al tope de gasto",
    STOP_FAILURES: "tres fallos seguidos",
}

#: Códigos HTTP que no son un problema de ESTE anuncio: todos los demás
#: fallarían igual, así que la pasada corta en vez de seguir gastando pedidos.
_STOP_BY_STATUS: dict[int | None, str] = {
    401: STOP_AUTH,
    403: STOP_FORBIDDEN,
    429: STOP_RATE_LIMIT,
}

#: Un reintento por anuncio (red, 5xx, tiempo agotado y 429). La prueba fuera
#: de línea usaba tres: ahí no había nadie más esperando el turno.
PASS_RETRIES = 1
#: Fallos seguidos que cortan la pasada: algo está caído y no es un anuncio.
MAX_CONSECUTIVE_FAILURES = 3

_EIGHT_PLACES = Decimal("0.00000001")


# --- Configuración ------------------------------------------------------------


def is_active(settings: Settings) -> bool:
    """El único predicado de «Jev activo»: interruptor encendido Y clave no vacía.

    Gobierna las llamadas y la anotación. Apagado, o encendido sin clave, el
    sistema se comporta exactamente como antes de A6b. La clave es la de Vercel
    AI Gateway, con cualquiera de sus dos nombres (`Settings.gateway_api_key`).
    """
    api_key, _ = settings.gateway_api_key()
    return bool(settings.llm_jev_enabled and api_key)


def warn_if_enabled_without_key(settings: Settings) -> None:
    """Aviso al arrancar: interruptor encendido y sin clave.

    No es un error (la ausencia de la clave es un estado soportado), pero sin
    el aviso el operador creería que la señal se está juntando.
    """
    if settings.llm_jev_enabled and not is_active(settings):
        logger.warning(
            "jev.sin_clave SAIMC_LLM_JEV_ENABLED está encendido y falta "
            "SAIMC_VERCEL_API_KEY: no se llama a Jev ni se anota nada"
        )


# --- Opciones y huella --------------------------------------------------------


# `frozen=True` hace al objeto inmutable (asignar un atributo levanta), como un
# `readonly class` de PHP 8.2. Los diccionarios de adentro siguen siendo
# mutables: es una convención de uso, no un candado.
@dataclass(frozen=True)
class Options:
    """Las opciones de producto que se le ofrecen a Jev, con su clave estable."""

    #: Clave -> nombre del piloto. Es lo que viaja en el pedido.
    products: dict[str, str]
    #: Clave -> id del producto, como texto. No viaja: se guarda con la señal
    #: para saber qué producto era cada opción cuando se preguntó.
    product_keys: dict[str, str]

    # `@property` hace que se lea como un atributo (`options.questions`) aunque
    # se calcule al pedirlo: un getter sin paréntesis.
    @property
    def questions(self) -> dict[str, dict[str, Any]]:
        return jev_client.build_questions(self.products)

    @property
    def questions_hash(self) -> str:
        """La huella vigente: cambia si cambia un piloto, una instrucción o el modelo."""
        return jev_client.questions_hash(self.questions)


def options_from_products(products: Sequence[Product]) -> Options:
    """Arma las opciones desde los productos piloto ya leídos.

    Levanta `ValueError` si dos pilotos dan la misma clave o si alguno pisa la
    opción reservada `other`: con claves repetidas no se sabría qué eligió Jev.
    """
    names: dict[str, str] = {}
    keys: dict[str, str] = {}
    for product in products:
        key = jev_client.slugify(product.name)
        if key in names or key == jev_client.OTHER_KEY:
            raise ValueError(f"clave de producto repetida o reservada: {key!r}")
        names[key] = product.name
        keys[key] = str(product.id)
    return Options(products=names, product_keys=keys)


def load_options(session: Session) -> Options:
    """Las opciones vigentes: los pilotos del catálogo (`products.is_pilot`)."""
    return options_from_products(pilot_products(session))


# --- Selección y alta ---------------------------------------------------------


def _select_texts(
    session: Session, questions_hash: str, statuses: Sequence[ProcessingStatus], limit: int | None
) -> list[RawIngestion]:
    """Textos en alguno de esos estados sin señal de la huella vigente, más
    viejo primero. Lo comparten la pasada del pre-cálculo y el relleno."""
    has_current = exists().where(
        JevSignal.ingestion_id == RawIngestion.id,
        JevSignal.questions_hash == questions_hash,
    )
    stmt = (
        select(RawIngestion)
        .where(
            RawIngestion.processing_status.in_(statuses),
            RawIngestion.content_type == ContentType.TEXT,
            ~has_current,
        )
        .order_by(RawIngestion.captured_at.asc(), RawIngestion.id)
    )
    # El texto vacío se filtra en Python, igual que el foco en el pre-cálculo:
    # así `limit` cuenta candidatos de verdad y no filas de la tabla.
    texts = [
        ingestion for ingestion in session.execute(stmt).scalars() if (ingestion.original_text or "").strip()
    ]
    return texts if limit is None else texts[:limit]


def select_pending(session: Session, questions_hash: str, limit: int | None = None) -> list[RawIngestion]:
    """Textos pendientes sin señal de la huella vigente, más viejo primero.

    No mira el foco: el triage se midió sobre todos los textos, en foco o no.
    Las imágenes quedan afuera (su texto es el del OCR; eso es de C2). Un texto
    con señal de una huella vieja SÍ entra: se le pregunta de nuevo y queda con
    dos filas.
    """
    return _select_texts(session, questions_hash, (ProcessingStatus.PENDING,), limit)


def select_decided(session: Session, questions_hash: str, limit: int | None = None) -> list[RawIngestion]:
    """Textos ya curados o descartados sin señal de la huella vigente (A6b-2).

    Es lo que pregunta el relleno con `--decided`: los anuncios que el operador
    decidió antes de que Jev se encendiera, o antes de que pasara el
    temporizador. Mismas reglas que `select_pending` para todo lo demás.
    """
    return _select_texts(
        session, questions_hash, (ProcessingStatus.CURATED, ProcessingStatus.DISCARDED), limit
    )


def _cost(result: ApiResult) -> float:
    """El costo de una respuesta, o su estimación si el gateway mandó algo raro
    (negativo, infinito): un número así rompería el tope de la corrida."""
    if math.isfinite(result.cost) and result.cost >= 0:
        return result.cost
    return jev_client.tokens_cost(result.input_tokens)


def save_signal(
    session: Session,
    *,
    ingestion_id: uuid.UUID,
    result: ApiResult,
    options: Options,
    source: str = SOURCE_PREFILL,
) -> JevSignal | None:
    """Da de alta la señal. `None` si ya había una con esa huella. No hace `commit`.

    Dos procesos sobre el mismo anuncio: el segundo choca con el `UNIQUE`. Se
    ataja con un savepoint (`begin_nested`) porque en PostgreSQL un error deja
    abortada toda la transacción; así solo se deshace este alta.
    """
    signal = JevSignal(
        ingestion_id=ingestion_id,
        model=(result.model or jev_client.MODEL)[:64],
        questions_hash=options.questions_hash,
        answers=result.answers,
        product_keys=dict(options.product_keys),
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        # `Decimal(str(...))` y no `Decimal(float)`: el segundo arrastra el
        # error binario del float (0.00003 → 0.0000299999…).
        cost_usd=Decimal(str(_cost(result))).quantize(_EIGHT_PLACES),
        source=source,
    )
    try:
        with session.begin_nested():
            session.add(signal)
            session.flush()
    except IntegrityError:
        return None
    return signal


def current_signal(session: Session, ingestion_id: uuid.UUID, questions_hash: str) -> JevSignal | None:
    """La señal vigente de un anuncio: la fila de esa huella, si existe."""
    return session.execute(
        select(JevSignal).where(
            JevSignal.ingestion_id == ingestion_id,
            JevSignal.questions_hash == questions_hash,
        )
    ).scalar_one_or_none()


# --- Lectura y anotación (código puro) ----------------------------------------


@dataclass(frozen=True)
class Reading:
    """Una señal ya leída: las tres respuestas más de dónde salieron."""

    signal_id: str
    model: str
    questions_hash: str
    asked_at: datetime
    source: str
    triage: Answer
    product: Answer
    currency: Answer
    #: Clave -> id del producto, como estaba al preguntar.
    product_keys: Mapping[str, str]


def _read_answers(answers: object, product_keys: object) -> tuple[Answer, Answer, Answer, dict[str, str]]:
    """Lee las tres respuestas. Nunca levanta: lo ilegible queda `invalid`.

    Las opciones de producto salen de `product_keys` (las que se ofrecieron al
    preguntar), no del catálogo de hoy: la lectura depende solo de la fila.
    """
    raw = answers if isinstance(answers, dict) else {}
    keys = {str(k): str(v) for k, v in product_keys.items()} if isinstance(product_keys, dict) else {}
    return (
        jev_client.parse_answer(jev_client.triage_question(()), raw.get(jev_client.TRIAGE)),
        jev_client.parse_answer(jev_client.product_question(keys), raw.get(jev_client.PRODUCT)),
        jev_client.parse_answer(jev_client.currency_question(), raw.get(jev_client.CURRENCY)),
        keys,
    )


def read_signal(signal: JevSignal) -> Reading:
    """La lectura de una fila de `jev_signals`. Función pura."""
    triage, product, currency, keys = _read_answers(signal.answers, signal.product_keys)
    asked_at = signal.created_at
    if asked_at.tzinfo is None:
        # SQLite devuelve las fechas sin zona; en la base son UTC.
        asked_at = asked_at.replace(tzinfo=UTC)
    return Reading(
        signal_id=str(signal.id),
        model=signal.model,
        questions_hash=signal.questions_hash,
        asked_at=asked_at,
        source=signal.source,
        triage=triage,
        product=product,
        currency=currency,
        product_keys=keys,
    )


# `Protocol` es tipado estructural: cualquier objeto que tenga estos dos
# atributos sirve, sin heredar de nada (una interfaz de PHP que no hace falta
# declarar con `implements`). Así `annotate` no depende de `PriceHistory`.
class AnnotatedRow(Protocol):
    @property
    def product_id(self) -> uuid.UUID | None: ...

    @property
    def currency(self) -> str | None: ...


def _invalid(answer: Answer) -> dict[str, Any]:
    return {"status": "invalid", "reason": answer.invalid or "sin respuesta"}


def _choice_entry(answer: Answer, row_value: str, ad_values: frozenset[str]) -> dict[str, Any]:
    if answer.invalid or answer.choice is None:
        return _invalid(answer)
    return {
        "status": "ok",
        "answer": answer.choice,
        # Las dos señales, cada una en su lugar; si vino una sola, la otra va
        # en `null`. Sobre cuál se fija un umbral lo decide G1.
        "probability": answer.p,
        "confidence": answer.confidence,
        # El valor de ESTA fila; el acuerdo es por anuncio. Con los dos se lee
        # también el acuerdo por fila (`answer == row_value`).
        "row_value": row_value,
        "agrees": answer.choice in ad_values,
    }


def _row_keys(reading: Reading, rows: Sequence[AnnotatedRow]) -> tuple[list[str], list[str]]:
    """El valor de cada fila con el vocabulario de Jev: la clave de su producto
    y su moneda. Lo comparten la anotación y el adaptador, para que «coincide»
    sea lo mismo en los dos caminos."""
    by_id = {product_id: key for key, product_id in reading.product_keys.items()}
    products = [by_id.get(str(row.product_id), jev_client.OTHER_KEY) for row in rows]
    # Una fila sin moneda es en pesos: es lo mismo que se le pide a Jev.
    currencies = [str(row.currency or Currency.CUP.value) for row in rows]
    return products, currencies


def annotate(reading: Reading, rows: Sequence[AnnotatedRow]) -> list[dict[str, Any]]:
    """El bloque `jev` de cada fila guardada de un anuncio, en el mismo orden.

    Función pura. Jev contesta por anuncio («el producto principal») y un
    anuncio puede tener varias filas: **coincide si su elección es el valor de
    alguna fila**. Un producto que no es piloto vale `other`.

    El triage no lleva `agrees`: decir sí o no pide un umbral, y los umbrales
    son de G1.
    """
    products, currencies = _row_keys(reading, rows)
    ad_products, ad_currencies = frozenset(products), frozenset(currencies)

    triage = (
        {"status": "ok", "probability": reading.triage.p}
        if reading.triage.p is not None and not reading.triage.invalid
        else _invalid(reading.triage)
    )
    return [
        {
            "signal_id": reading.signal_id,
            "model": reading.model,
            "questions_hash": reading.questions_hash,
            "asked_at": reading.asked_at.isoformat(),
            "source": reading.source,
            # Un diccionario por fila: cada bloque se guarda en su propia fila.
            "triage": dict(triage),
            "product": _choice_entry(reading.product, product, ad_products),
            "currency": _choice_entry(reading.currency, currency, ad_currencies),
        }
        for product, currency in zip(products, currencies, strict=True)
    ]


# --- Adaptador a `ExternalSignal` (código puro, A6b-2) ------------------------

#: Cuál de las dos probabilidades de una respuesta de elección viaja en la
#: señal. Se llaman igual que las claves del bloque `jev` de `annotate`.
USE_PROBABILITY = "probability"  # `probabilities[choice]`
USE_CONFIDENCE = "confidence"  # el campo `confidence`
PROBABILITY_SOURCES = (USE_PROBABILITY, USE_CONFIDENCE)


def _product_value(reading: Reading, choice: str) -> object:
    """Lo que el motor compara contra el id del producto de la fila: el id del
    piloto elegido, o la clave tal cual si es `other` (no tiene id)."""
    raw = reading.product_keys.get(choice)
    if raw is None:
        return choice
    try:
        return uuid.UUID(raw)
    except ValueError:
        return raw


def _choice_signal(
    field: str,
    answer: Answer,
    keys: Sequence[str],
    values: Sequence[object],
    disagreement: object,
    use: str,
) -> ExternalSignal | None:
    """La señal de UNA pregunta de elección, o `None` si no hay nada que decir.

    `keys` es el valor de cada fila con el vocabulario de Jev y `values`, el
    que el motor lee de esa misma fila.
    """
    if answer.invalid or answer.choice is None:
        return None
    probability = answer.p if use == USE_PROBABILITY else answer.confidence
    if probability is None:
        # Vino una sola de las dos (caso 14) y se pidió la otra. Para el motor
        # una señal sin probabilidad está mal formada: es «sin señal».
        return None
    if answer.choice in keys:
        # Coincide con alguna fila: apunta a la primera, con el valor de ESA
        # fila, que es contra lo que el motor compara (y por eso da acuerdo).
        index = keys.index(answer.choice)
        value = values[index]
    else:
        index, value = 0, disagreement
    if value is None:
        # Un acuerdo sobre una fila sin valor (sin producto y Jev dijo
        # `other`). Un acuerdo no suma nada y el motor pide un valor.
        return None
    return ExternalSignal(row_index=index, field=field, value=value, probability=probability)


def to_external_signals(
    reading: Reading, rows: Sequence[AnnotatedRow], *, probability: str = USE_PROBABILITY
) -> tuple[ExternalSignal, ...]:
    """Convierte la respuesta de Jev en señales para `AdContext.signals`.

    Función pura, y solo para el informe de sombra: lo que se persiste nunca
    pasa por acá. `rows` van en el orden de las filas del anuncio, porque
    `row_index` es ese índice.

    El contrato del motor es por fila y Jev contesta por anuncio
    (`docs/spec-slice-A6b.md`, punto 4), así que:

    - sale **una señal por pregunta** (`product`, `currency`), no una por fila:
      si la elección coincide con alguna fila apunta a la primera que coincide;
      si no, a la fila 0;
    - `probability` dice cuál de las dos probabilidades usar (`USE_*`); el
      informe corre con las dos;
    - una respuesta `invalid` no produce señal, y **el triage nunca**: es del
      anuncio y del carril 0, no de un campo.
    """
    if probability not in PROBABILITY_SOURCES:
        raise ValueError(f"probability tiene que ser una de {PROBABILITY_SOURCES}: {probability!r}")
    if not rows:
        return ()
    products, currencies = _row_keys(reading, rows)
    product_choice = reading.product.choice or ""
    signals = (
        _choice_signal(
            jev_client.PRODUCT,
            reading.product,
            products,
            [row.product_id for row in rows],
            _product_value(reading, product_choice),
            probability,
        ),
        _choice_signal(
            jev_client.CURRENCY,
            reading.currency,
            currencies,
            [row.currency for row in rows],
            reading.currency.choice,
            probability,
        ),
    )
    return tuple(signal for signal in signals if signal is not None)


# --- La pasada ----------------------------------------------------------------


@dataclass
class PassResult:
    """Qué hizo una pasada."""

    #: Textos que había para preguntar.
    pending: int = 0
    #: Respuestas recibidas (y guardadas).
    asked: int = 0
    #: Anuncios que quedaron sin respuesta: se reintentan en la corrida siguiente.
    failed: int = 0
    #: Preguntas (no anuncios) cuya respuesta se guardó pero no se pudo leer.
    invalid: int = 0
    cost_usd: float = 0.0
    #: Por qué se cortó (`STOP_*`); vacío si llegó al final.
    stopped: str = ""
    #: El motivo que mandó el gateway, con la clave ya quitada.
    stop_detail: str = ""


def _failure_kind(status: int | None) -> str:
    if status is None:
        return "network"
    if status == 200:
        return "bad_response"
    if status >= 500:
        return "server"
    return _STOP_BY_STATUS.get(status, "http")


def run_pass(
    session: Session,
    candidates: Sequence[RawIngestion],
    options: Options,
    *,
    client: httpx.Client,
    api_key: str,
    timeout: float,
    max_cost: float,
    source: str = SOURCE_PREFILL,
    sleep: Callable[[float], None] = time.sleep,
) -> PassResult:
    """Un anuncio a la vez: pedido, alta de la fila y `commit`.

    El tope se revisa antes de cada llamada: lo gastado más lo que se espera
    de la siguiente (la estimación por caracteres o la llamada más cara vista,
    lo que sea mayor). Un fallo no deja fila, así que no hay nada que limpiar.
    """
    questions = options.questions
    result = PassResult(pending=len(candidates))
    highest_cost = 0.0
    streak = 0
    for ingestion in candidates:
        payload = jev_client.build_request(ingestion.original_text or "", questions)
        expected = max(jev_client.tokens_cost(jev_client.request_tokens(payload)), highest_cost)
        if result.cost_usd + expected > max_cost:
            result.stopped = STOP_MAX_COST
            break
        try:
            answer = jev_client.ask(
                client, payload, api_key, sleep=sleep, timeout=timeout, max_retries=PASS_RETRIES
            )
        except JevError as exc:
            result.failed += 1
            streak += 1
            stop = _STOP_BY_STATUS.get(exc.status, "")
            # El motivo del gateway solo se conserva en los cortes, que es
            # donde el operador lo necesita. Por si el servidor repite lo que
            # se le mandó, el texto del anuncio se saca: nunca va al registro.
            detail = str(exc).replace(payload["state"], "[texto]") if payload["state"] else str(exc)
            logger.warning(
                "jev.fallo tipo=%s http=%s ingestion_id=%s%s",
                _failure_kind(exc.status),
                exc.status,
                ingestion.id,
                f" detalle={detail}" if stop else "",
            )
            if stop:
                result.stopped, result.stop_detail = stop, detail
                break
            if streak >= MAX_CONSECUTIVE_FAILURES:
                result.stopped = STOP_FAILURES
                break
            continue

        streak = 0
        cost = _cost(answer)
        result.asked += 1
        result.cost_usd += cost
        highest_cost = max(highest_cost, cost)
        triage, product, currency, _ = _read_answers(answer.answers, options.product_keys)
        result.invalid += sum(1 for reading in (triage, product, currency) if reading.invalid)
        save_signal(session, ingestion_id=ingestion.id, result=answer, options=options, source=source)
        # Commit por anuncio: cortar la corrida no pierde lo ya pagado.
        session.commit()
    return result


def _plural(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


def prefill_pass(
    session: Session,
    settings: Settings,
    *,
    execute: bool,
    limit: int | None = None,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
    out: Callable[[str], None] = print,
) -> PassResult | None:
    """La pasada de Jev del pre-cálculo. `None` si no se llamó a nada.

    - Interruptor apagado: no hace ni dice nada.
    - Encendido y sin clave: avisa en la salida y no llama.
    - En seco (`execute=False`): informa cuántos textos preguntaría y el costo
      estimado, sin red.
    - Con `execute`: pregunta hasta el tope de `SAIMC_LLM_JEV_MAX_COST`.

    `client` y `sleep` son parámetros para que los tests no usen la red ni
    esperen de verdad.
    """
    if not settings.llm_jev_enabled:
        return None
    if not is_active(settings):
        out(
            "Jev: el interruptor está encendido pero falta SAIMC_VERCEL_API_KEY. "
            "No se llama ni se anota nada."
        )
        return None
    options = load_options(session)
    if not options.products:
        out("Jev: no hay productos piloto en el catálogo; no hay nada que preguntar.")
        return None
    candidates = select_pending(session, options.questions_hash, limit)
    max_cost = settings.llm_jev_max_cost

    if not execute:
        out(
            f"Jev (señal en sombra): {_plural(len(candidates), 'texto', 'textos')} por preguntar, "
            f"costo estimado {_estimated_cost(candidates, options):.6f} USD "
            f"(tope por corrida: {max_cost} USD)."
        )
        return None

    result = _ask_all(session, candidates, options, settings, max_cost=max_cost, client=client, sleep=sleep)
    out(f"Jev (señal en sombra): {_result_summary(result)}")
    if result.stopped:
        out(f"{_stop_summary(result)}. El resto del pre-cálculo sigue igual.")
    return result


def _estimated_cost(candidates: Sequence[RawIngestion], options: Options) -> float:
    """Lo que costaría preguntarlos, estimado por caracteres. Sin red."""
    questions = options.questions
    tokens = sum(
        jev_client.request_tokens(jev_client.build_request(c.original_text or "", questions))
        for c in candidates
    )
    return jev_client.tokens_cost(tokens)


def _ask_all(
    session: Session,
    candidates: Sequence[RawIngestion],
    options: Options,
    settings: Settings,
    *,
    max_cost: float,
    source: str = SOURCE_PREFILL,
    client: httpx.Client | None,
    sleep: Callable[[float], None],
) -> PassResult:
    """`run_pass` con el cliente HTTP resuelto."""
    # Sin cliente de afuera (el caso real) se abre uno propio y se cierra al
    # terminar, pase lo que pase: para eso el `try/finally`.
    http = client or httpx.Client(timeout=settings.llm_jev_timeout)
    try:
        return run_pass(
            session,
            candidates,
            options,
            client=http,
            api_key=settings.gateway_api_key()[0] or "",
            timeout=settings.llm_jev_timeout,
            max_cost=max_cost,
            source=source,
            sleep=sleep,
        )
    finally:
        if client is None:
            http.close()


def _result_summary(result: PassResult) -> str:
    return (
        f"{_plural(result.asked, 'preguntado', 'preguntados')} de "
        f"{result.pending}, {_plural(result.failed, 'fallo', 'fallos')}, "
        f"{_plural(result.invalid, 'respuesta ilegible', 'respuestas ilegibles')}, "
        f"costo {result.cost_usd:.6f} USD."
    )


def _stop_summary(result: PassResult) -> str:
    """Por qué se cortó, sin punto final: quien llama dice qué sigue."""
    return f"Jev: la pasada se cortó — {STOP_LABELS[result.stopped]}" + (
        f" ({result.stop_detail})" if result.stop_detail else ""
    )


# --- /control -----------------------------------------------------------------

STATE_ON = "on"
STATE_OFF = "off"
STATE_NO_KEY = "no_key"

_STATE_LABELS = {STATE_ON: "encendida", STATE_OFF: "apagada", STATE_NO_KEY: "sin clave"}


@dataclass(frozen=True)
class ControlSummary:
    """Lo único de Jev que va a `/control`: el estado, conteos y gasto. Ningún
    texto, canal ni respuesta (criterio de cuando `/control` era público; hoy
    pide operador)."""

    state: str
    #: Textos con señal de la huella vigente.
    current_texts: int
    #: Filas en `jev_signals`, de cualquier huella.
    total_rows: int
    #: Filas de `prices_history` con el bloque `jev` en su detalle.
    annotated_rows: int
    cost_total: float
    cost_week: float

    @property
    def state_label(self) -> str:
        return _STATE_LABELS[self.state]


def control_summary(session: Session, settings: Settings, *, now: datetime | None = None) -> ControlSummary:
    """Los números de la sección «Jev (señal en sombra)». Solo lee la base."""
    state = STATE_OFF
    if settings.llm_jev_enabled:
        state = STATE_ON if is_active(settings) else STATE_NO_KEY

    try:
        current_hash: str | None = load_options(session).questions_hash
    except ValueError:
        # Dos pilotos con la misma clave: no hay huella vigente que contar.
        current_hash = None
    current_texts = 0
    if current_hash is not None:
        current_texts = session.execute(
            select(func.count()).select_from(JevSignal).where(JevSignal.questions_hash == current_hash)
        ).scalar_one()

    since = (now or datetime.now(UTC)) - timedelta(days=7)
    total_rows, cost_total = session.execute(
        select(func.count(), func.coalesce(func.sum(JevSignal.cost_usd), 0)).select_from(JevSignal)
    ).one()
    cost_week = session.execute(
        select(func.coalesce(func.sum(JevSignal.cost_usd), 0)).where(JevSignal.created_at >= since)
    ).scalar_one()
    # `as_string()` y no la clave pelada: en SQLite el acceso a una clave que
    # no existe se compara distinto según el tipo, y como texto da NULL en los
    # dos motores (`->>` en PostgreSQL, `json_extract` en SQLite).
    annotated_rows = session.execute(
        select(func.count())
        .select_from(PriceHistory)
        .where(PriceHistory.validation_detail["jev"].as_string().is_not(None))
    ).scalar_one()
    return ControlSummary(
        state=state,
        current_texts=current_texts,
        total_rows=total_rows,
        annotated_rows=annotated_rows,
        cost_total=float(cost_total),
        cost_week=float(cost_week),
    )


# --- Relleno: `python -m saimc.validation.jev_signal` (A6b-2) -----------------


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("tiene que ser un entero mayor que cero")
    return number


def _cost_cap(value: str) -> float:
    number = float(value)
    # `nan` e `inf` pasan `float()` sin levantar: se rechazan a mano.
    if not math.isfinite(number) or number < 0:
        raise argparse.ArgumentTypeError("tiene que ser un número de USD mayor o igual que cero")
    return number


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m saimc.validation.jev_signal",
        description=(
            "Relleno de la señal de Jev (en sombra): le pregunta a Jev por los textos que "
            "todavía no tienen señal con las preguntas vigentes. Por defecto en seco: dice "
            "cuántos preguntaría y cuánto costaría, sin llamar a nadie."
        ),
    )
    parser.add_argument(
        "--decided",
        action="store_true",
        help=(
            "Textos ya curados o descartados, en vez de los pendientes. "
            "Las filas quedan con source = backfill."
        ),
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Pregunta de verdad y guarda las respuestas en jev_signals. Sin esto es en seco. Gasta API.",
    )
    parser.add_argument("--limit", type=_positive_int, default=None, help="Máximo de textos.")
    parser.add_argument(
        "--max-cost",
        type=_cost_cap,
        default=None,
        help="Tope de gasto de esta corrida, en USD. Por defecto, SAIMC_LLM_JEV_MAX_COST.",
    )
    return parser.parse_args(argv)


def _inactive_reason(settings: Settings) -> str:
    if not settings.llm_jev_enabled:
        return "SAIMC_LLM_JEV_ENABLED está apagado"
    return "el interruptor está encendido pero falta SAIMC_VERCEL_API_KEY"


def run(
    args: argparse.Namespace,
    session: Session,
    settings: Settings,
    *,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
    out: Callable[[str], None] = print,
) -> int:
    """Corre el relleno. Devuelve el código de salida.

    - Sin `--decided`: los pendientes, lo mismo que la pasada del pre-cálculo
      (`source = prefill`). Con `--decided`: los ya curados o descartados
      (`source = backfill`).
    - En seco no hace falta la clave: solo lee la base y estima.
    - Con `--execute` y Jev inactivo no llama a nadie, lo dice y sale con 1.
    - Una pasada que se corta (tope, clave rechazada, fallos) sale con 0, igual
      que en el pre-cálculo: lo ya preguntado quedó guardado y el motivo va en
      la salida. Correrlo de nuevo sigue por donde quedó.
    """
    try:
        options = load_options(session)
    except ValueError as exc:
        out(f"Jev: no se pueden armar las opciones de producto ({exc}).")
        return 1
    if not options.products:
        out("Jev: no hay productos piloto en el catálogo; no hay nada que preguntar.")
        return 0
    select_texts = select_decided if args.decided else select_pending
    candidates = select_texts(session, options.questions_hash, args.limit)
    max_cost = settings.llm_jev_max_cost if args.max_cost is None else args.max_cost
    which = "ya decididos (curados o descartados)" if args.decided else "pendientes"

    if not args.execute:
        estimated = _estimated_cost(candidates, options)
        out("EN SECO: no se llama a Jev ni se escribe nada (con --execute se pregunta).")
        out(
            f"Jev (relleno, textos {which}): {_plural(len(candidates), 'texto', 'textos')} "
            f"sin señal vigente por preguntar, costo estimado {estimated:.6f} USD "
            f"(tope de esta corrida: {max_cost} USD)."
        )
        if estimated > max_cost:
            out(
                "El costo estimado supera el tope: la corrida se cortaría antes de terminar. "
                "Se puede subir con --max-cost o correr varias veces."
            )
        if not is_active(settings):
            out(f"Aviso: con --execute no correría — {_inactive_reason(settings)}.")
        return 0

    if not is_active(settings):
        out(f"No se puede correr: {_inactive_reason(settings)}. No se llamó a Jev ni se escribió nada.")
        return 1
    result = _ask_all(
        session,
        candidates,
        options,
        settings,
        max_cost=max_cost,
        source=SOURCE_BACKFILL if args.decided else SOURCE_PREFILL,
        client=client,
        sleep=sleep,
    )
    out(f"Jev (relleno, textos {which}): {_result_summary(result)}")
    if result.stopped:
        out(f"{_stop_summary(result)}. Lo ya preguntado quedó guardado; al correrlo de nuevo sigue.")
    return 0


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    # Sin `session_scope`: la pasada hace `commit` por anuncio y en seco no hay
    # nada que guardar. El `rollback` final deja a la vista que no queda nada.
    session = get_session_factory()()
    try:
        code = run(args, session, get_settings())
    finally:
        session.rollback()
        session.close()
    sys.exit(code)


if __name__ == "__main__":
    main()
