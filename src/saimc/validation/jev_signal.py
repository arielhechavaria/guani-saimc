"""Jev como señal en sombra: preguntar, guardar y anotar (slice A6b-1).

`validation.jev_client` sabe hablar con Jev y no toca la base. Este módulo es
el que une a Jev con el sistema:

- **Preguntar** (`prefill_pass`): una pasada dentro del pre-cálculo, antes del
  bucle de Claude, sobre los textos pendientes que todavía no tienen señal con
  la huella de preguntas vigente. Una llamada por anuncio con las tres
  preguntas, alta de la fila en `jev_signals` y `commit`.
- **Anotar** (`annotate`): al curar, al editar y al revalidar, `persist` busca
  la señal vigente del anuncio y suma un bloque `jev` al `validation_detail`
  de cada fila. Es código puro.

**Es sombra: solo anota.** Nada de acá cambia `validation_status`, el carril
ni `provenance`, y el motor (`engine`, `rules`, `types`, `fence`) no importa
este módulo. Al guardar no se llama a nadie: un anuncio que el operador decide
antes de que pase el temporizador queda «sin señal», que es un estado
soportado. Ningún umbral se fija acá; eso es de G1.

**Un fallo de Jev nunca le cuesta nada al resto.** La pasada lo anota en el
registro y sigue o corta, y el pre-cálculo termina con el código de salida de
siempre.

Lo que sale hacia el proveedor: el texto redactado, las tres preguntas y la
clave en la cabecera. No sale el id de la ingesta, el canal, el emisor ni la
provincia. El texto del anuncio nunca va al registro.

Contrato: `docs/spec-slice-A6b.md`.
"""

from __future__ import annotations

import logging
import math
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

from saimc.config import Settings
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
    sistema se comporta exactamente como antes de A6b.
    """
    return bool(settings.llm_jev_enabled and (settings.llm_jev_api_key or "").strip())


def warn_if_enabled_without_key(settings: Settings) -> None:
    """Aviso al arrancar: interruptor encendido y sin clave.

    No es un error (la ausencia de la clave es un estado soportado), pero sin
    el aviso el operador creería que la señal se está juntando.
    """
    if settings.llm_jev_enabled and not is_active(settings):
        logger.warning(
            "jev.sin_clave SAIMC_LLM_JEV_ENABLED está encendido y falta "
            "SAIMC_LLM_JEV_API_KEY: no se llama a Jev ni se anota nada"
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


def select_pending(session: Session, questions_hash: str, limit: int | None = None) -> list[RawIngestion]:
    """Textos pendientes sin señal de la huella vigente, más viejo primero.

    No mira el foco: el triage se midió sobre todos los textos, en foco o no.
    Las imágenes quedan afuera (su texto es el del OCR; eso es de C2). Un texto
    con señal de una huella vieja SÍ entra: se le pregunta de nuevo y queda con
    dos filas.
    """
    has_current = exists().where(
        JevSignal.ingestion_id == RawIngestion.id,
        JevSignal.questions_hash == questions_hash,
    )
    stmt = (
        select(RawIngestion)
        .where(
            RawIngestion.processing_status == ProcessingStatus.PENDING,
            RawIngestion.content_type == ContentType.TEXT,
            ~has_current,
        )
        .order_by(RawIngestion.captured_at.asc(), RawIngestion.id)
    )
    # El texto vacío se filtra en Python, igual que el foco en el pre-cálculo:
    # así `limit` cuenta candidatos de verdad y no filas de la tabla.
    pending = [
        ingestion for ingestion in session.execute(stmt).scalars() if (ingestion.original_text or "").strip()
    ]
    return pending if limit is None else pending[:limit]


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


def annotate(reading: Reading, rows: Sequence[AnnotatedRow]) -> list[dict[str, Any]]:
    """El bloque `jev` de cada fila guardada de un anuncio, en el mismo orden.

    Función pura. Jev contesta por anuncio («el producto principal») y un
    anuncio puede tener varias filas: **coincide si su elección es el valor de
    alguna fila**. Un producto que no es piloto vale `other`.

    El triage no lleva `agrees`: decir sí o no pide un umbral, y los umbrales
    son de G1.
    """
    by_id = {product_id: key for key, product_id in reading.product_keys.items()}
    products = [by_id.get(str(row.product_id), jev_client.OTHER_KEY) for row in rows]
    # Una fila sin moneda es en pesos: es lo mismo que se le pide a Jev.
    currencies = [str(row.currency or Currency.CUP.value) for row in rows]
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
            "Jev: el interruptor está encendido pero falta SAIMC_LLM_JEV_API_KEY. "
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
        questions = options.questions
        tokens = sum(
            jev_client.request_tokens(jev_client.build_request(c.original_text or "", questions))
            for c in candidates
        )
        out(
            f"Jev (señal en sombra): {_plural(len(candidates), 'texto', 'textos')} por preguntar, "
            f"costo estimado {jev_client.tokens_cost(tokens):.6f} USD "
            f"(tope por corrida: {max_cost} USD)."
        )
        return None

    # Sin cliente de afuera (el caso real) se abre uno propio y se cierra al
    # terminar, pase lo que pase: para eso el `try/finally`.
    http = client or httpx.Client(timeout=settings.llm_jev_timeout)
    try:
        result = run_pass(
            session,
            candidates,
            options,
            client=http,
            api_key=settings.llm_jev_api_key or "",
            timeout=settings.llm_jev_timeout,
            max_cost=max_cost,
            sleep=sleep,
        )
    finally:
        if client is None:
            http.close()
    out(
        f"Jev (señal en sombra): {_plural(result.asked, 'preguntado', 'preguntados')} de "
        f"{result.pending}, {_plural(result.failed, 'fallo', 'fallos')}, "
        f"{_plural(result.invalid, 'respuesta ilegible', 'respuestas ilegibles')}, "
        f"costo {result.cost_usd:.6f} USD."
    )
    if result.stopped:
        out(
            f"Jev: la pasada se cortó — {STOP_LABELS[result.stopped]}"
            + (f" ({result.stop_detail})" if result.stop_detail else "")
            + ". El resto del pre-cálculo sigue igual."
        )
    return result


# --- /control -----------------------------------------------------------------

STATE_ON = "on"
STATE_OFF = "off"
STATE_NO_KEY = "no_key"

_STATE_LABELS = {STATE_ON: "encendida", STATE_OFF: "apagada", STATE_NO_KEY: "sin clave"}


@dataclass(frozen=True)
class ControlSummary:
    """Lo único de Jev que va a `/control`, que es público: el estado, conteos
    y gasto. Ningún texto, canal ni respuesta."""

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
