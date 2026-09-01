"""Asistente de extracción por LLM.

Cubre lo que `spec.md` §6.1 reserva para el modelo: «LLM únicamente para los
casos que las reglas no resuelven, con el resultado siempre sujeto a
confirmación humana» (`EXT-07`). Este módulo arma el pedido, hace la llamada,
valida la respuesta y registra la invocación; confirmar lo propuesto es del
operador y no pasa por acá.

Tres partes, separadas a propósito para que la que puede hacer daño se pueda
probar entera sin salir de la máquina: el núcleo puro (`missing_fields`,
`response_schema`, `parse_response`), el registro de invocaciones, y la
llamada (`propose_missing`), que degrada a «sin propuesta» ante cualquier
falla esperable.

**Nada de acá inventa vocabulario.** Un producto, una provincia o una unidad
que el modelo nombre y que no exista ya en el catálogo se descarta como si no
hubiera contestado ese campo. Es lo que convierte una alucinación en un
no-evento en vez de un valor plausible que el operador confirma de apuro,
curando una hora por día (`design.md`, D3).

La política de qué productos entran —solo los piloto, `docs/decisions.md`
2026-08-15— es del llamador, igual que en `engine.propose_all`: acá se resuelve
contra la secuencia que se recibe, sin re-filtrarla. Un solo lugar decide el
foco.
"""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from saimc.extraction.engine import ExtractionProposal
from saimc.models import Product, Province
from saimc.units import UNITS, canonical_unit

# Los cinco campos que el asistente puede llenar.
#
# `currency` queda afuera a propósito y no por olvido: §6.1 manda inferir CUP
# cuando el anuncio no la declara, así que `_extract_currency` nunca devuelve
# `None` y no hay hueco que preguntar. `product_name_guess`, `price_basis` y
# `min_purchase_quantity` también quedan afuera — los dos últimos son juicio
# explícito del operador (design de `price_basis`, D4).
ASSISTABLE_FIELDS: frozenset[str] = frozenset(
    {"product", "price", "quantity", "unit", "province"}
)

_NUMERIC_FIELDS: frozenset[str] = frozenset({"price", "quantity"})


def _normalize(value: str) -> str:
    """Minúsculas y sin tildes, para comparar contra el catálogo.

    Mismo criterio que `engine._strip_accents`: el texto de un anuncio —y la
    respuesta de un modelo que lo leyó— no siempre trae las tildes.
    """
    decomposed = unicodedata.normalize("NFD", value.strip().lower())
    return "".join(char for char in decomposed if unicodedata.category(char) != "Mn")


def missing_fields(proposal: ExtractionProposal) -> frozenset[str]:
    """Los campos que las reglas dejaron sin resolver en esta fila.

    Es exactamente lo que se le pide al modelo. Un campo que las reglas ya
    contestaron no se pregunta y no se puede pisar: acota el radio de daño a
    los huecos, que es donde está medido que las reglas callan
    (`design.md`, D1).

    `product_name_guess` no cuenta como producto resuelto — es texto para dar
    de alta algo a mano, no una fila del catálogo.
    """
    return frozenset(
        name for name in ASSISTABLE_FIELDS if getattr(proposal, name) is None
    )


@dataclass(frozen=True)
class AssistedFields:
    """Lo que sobrevivió a la validación, y lo que no.

    `dropped` se lleva aparte porque el registro de invocaciones necesita
    distinguir dos cosas que no significan lo mismo (`design.md`, D6): que el
    modelo se haya abstenido (`null`, no aparece acá) y que haya contestado
    algo que no resuelve contra el catálogo (sí aparece). Lo primero es
    honestidad; lo segundo es la señal de que está inventando.
    """

    product: Product | None = None
    price: Decimal | None = None
    quantity: Decimal | None = None
    unit: str | None = None
    province: Province | None = None
    dropped: frozenset[str] = field(default_factory=frozenset)

    @property
    def answered(self) -> frozenset[str]:
        """Campos con un valor utilizable."""
        return frozenset(
            name for name in ASSISTABLE_FIELDS if getattr(self, name) is not None
        )


def response_schema(requested: frozenset[str]) -> dict[str, Any]:
    """Esquema JSON de la respuesta, acotado a lo que se pide.

    Cada campo admite `null` porque abstenerse tiene que ser una respuesta
    válida y barata: forzar un valor es empujar al modelo a inventar
    justamente en los casos que las reglas ya no supieron resolver.

    `additionalProperties: False` para que la respuesta no traiga campos que
    después haya que ignorar a mano.
    """
    tipos: dict[str, list[str]] = {
        "product": ["string", "null"],
        "province": ["string", "null"],
        "unit": ["string", "null"],
        "price": ["number", "string", "null"],
        "quantity": ["number", "string", "null"],
    }
    campos = sorted(requested & ASSISTABLE_FIELDS)
    return {
        "type": "object",
        "properties": {name: {"type": tipos[name]} for name in campos},
        # Todo lo pedido es requerido: con `additionalProperties: False` eso
        # deja la forma completamente determinada. Abstenerse sigue siendo
        # posible —el campo viene, con `null` adentro—, que es justo lo que se
        # quiere permitir sin permitir además una respuesta a medio armar.
        "required": campos,
        "additionalProperties": False,
    }


def _as_positive_decimal(value: object) -> Decimal | None:
    """`Decimal` positivo, o `None` si el valor no sirve.

    El descarte de `bool` es explícito: en Python `True` es `int`, así que sin
    esta guarda entraría como `1`. `measure._as_float` lleva la misma guarda
    por el mismo motivo.

    Se convierte desde `str(value)` para no arrastrar la imprecisión binaria de
    un `float` — `Decimal(0.1)` no es `Decimal("0.1")`.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        return None
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite() or number <= 0:
        return None
    return number


def _match_catalog_name(
    value: object, entries: Sequence[Any], names: Any
) -> Any | None:
    """La entrada del catálogo cuyo nombre o alias coincide, o `None`.

    Comparación exacta sobre el texto normalizado, no por substring: acá el
    modelo devuelve un nombre, no un anuncio, y ser estricto es lo que hace
    que un nombre inventado se caiga en vez de pegar contra algo parecido.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    target = _normalize(value)
    for entry in entries:
        if any(_normalize(name) == target for name in names(entry)):
            return entry
    return None


def parse_response(
    raw: object,
    requested: frozenset[str],
    products: Sequence[Product],
    provinces: Sequence[Province],
) -> AssistedFields:
    """Valida la respuesta del modelo contra los catálogos vivos.

    Solo se miran los campos que se pidieron: si el modelo opina sobre uno que
    las reglas ya habían resuelto, esa opinión se ignora sin registrarla como
    descarte — no es una alucinación, es una respuesta fuera de alcance.

    Un cuerpo que no es un objeto JSON devuelve una propuesta vacía. Distinguir
    ese caso de una respuesta legítimamente vacía es tarea de quien hace la
    llamada, que es quien sabe si hubo error de transporte.
    """
    if not isinstance(raw, dict):
        return AssistedFields()

    valores: dict[str, Any] = {}
    dropped: set[str] = set()

    for name in requested & ASSISTABLE_FIELDS:
        if name not in raw:
            continue
        answer = raw[name]
        if answer is None:
            # Abstención explícita: no es un descarte.
            continue

        if name in _NUMERIC_FIELDS:
            resolved: Any = _as_positive_decimal(answer)
        elif name == "unit":
            resolved = canonical_unit(answer) if isinstance(answer, str) else None
        elif name == "product":
            resolved = _match_catalog_name(
                answer, products, lambda p: (p.name, *p.aliases)
            )
        else:  # province
            resolved = _match_catalog_name(answer, provinces, lambda p: (p.name,))

        if resolved is None:
            dropped.add(name)
        else:
            valores[name] = resolved

    return AssistedFields(**valores, dropped=frozenset(dropped))


# --- registro de invocaciones ----------------------------------------------
#
# Ninguna invocación puede ocurrir sin dejar rastro (`design.md`, D6). Es lo
# que después permite medir H0 sobre el conjunto NO asistido —el número
# comparable con todo el histórico de `docs/h0-runs.jsonl`— y lo que hace
# detectable que una respuesta del modelo se esté confirmando de apuro.
#
# Misma FORMA que `measure.append_h0_run`/`read_h0_runs`: solo-anexado,
# `sort_keys` para que el diff sea estable, lector tolerante a líneas rotas.
#
# **Distinto LUGAR, a propósito.** `h0-runs.jsonl` vive en `docs/` y va a git:
# son ~4 líneas al mes y `git log -p` es su interfaz de consulta. Este registro
# son ~600 al mes, está atado a ids de la base de producción y no significa
# nada en otro checkout, así que vive bajo `data/`, que `.gitignore` ya
# excluye por el mismo motivo que la evidencia.


@dataclass(frozen=True)
class AssistRun:
    """Una invocación registrada."""

    timestamp: str
    ingestion_id: str
    model: str
    requested: list[str]
    answered: list[str]
    dropped: list[str]
    # `True` cuando la llamada no dejó una respuesta utilizable (sin red,
    # timeout, cuerpo ilegible). Se registra igual: un intento fallido también
    # es información operativa.
    degraded: bool
    # Consumo real que reportó la API. En 0 cuando la llamada falló antes de
    # consumir, o cuando la respuesta no trajo `usage`.
    input_tokens: int
    output_tokens: int
    # Por qué falló, cuando falló: "credit", "timeout", "network", "auth" u
    # "other". Vacío en una llamada exitosa. Sin esto, quedarse sin saldo y
    # perder la red eran indistinguibles — que es justo lo que el operador
    # necesitaba saber.
    failure: str


def append_assist_run(record: dict[str, Any], path: Path) -> None:
    """Anexa el registro como una sola línea JSON.

    Nunca revienta: el registro es telemetría, no el objetivo. Si el disco no
    coopera, la asistencia igual tiene que devolver lo que consiguió — mismo
    criterio que `measure._git`.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, sort_keys=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        return


def _as_count(value: object) -> int:
    """Entero no negativo, o 0. `bool` descartado a propósito, como en
    `measure._as_int`."""
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, value)


def read_assist_runs(path: Path) -> list[AssistRun]:
    """Lee el registro en orden cronológico, salteando lo ilegible."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []

    runs: list[AssistRun] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict) or "ingestion_id" not in record:
            continue
        runs.append(
            AssistRun(
                timestamp=str(record.get("timestamp", "")),
                ingestion_id=str(record["ingestion_id"]),
                model=str(record.get("model", "")),
                requested=[str(f) for f in record.get("requested", []) or []],
                answered=[str(f) for f in record.get("answered", []) or []],
                dropped=[str(f) for f in record.get("dropped", []) or []],
                degraded=bool(record.get("degraded", False)),
                input_tokens=_as_count(record.get("input_tokens")),
                output_tokens=_as_count(record.get("output_tokens")),
                failure=str(record.get("failure", "")),
            )
        )
    return runs


def assisted_ingestion_ids(path: Path) -> set[str]:
    """Qué ingestas pasaron alguna vez por el asistente.

    Es lo que `measure.py` necesita para separar el conjunto no asistido, que
    es el que sigue siendo comparable con las corridas anteriores.
    """
    return {run.ingestion_id for run in read_assist_runs(path)}


# --- consumo y fallas ------------------------------------------------------
#
# Las tarifas por millón de tokens. **`docs/costos.md` lleva estos mismos
# números para que el operador los lea**, así que los dos lugares se mueven
# juntos: acá se calcula, allá se explica. Un modelo que no esté en la tabla
# no se tarifa y se declara aparte en vez de estimarse mal.
_RATES_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-opus-5": (5.0, 25.0),
}

# Señales para reconocer que se agotó el saldo. Es una **calibración
# razonada, no una certeza**: no se pudo verificar contra un error real de la
# API (hace falta una cuenta sin créditos para producirlo). El primer caso
# real puede exigir ajustar esto, y eso es esperable, no un error de diseño —
# mismo criterio que `ingestion.whatsapp_webhook` declaró para el payload de
# WAHA. Mientras tanto la falla cae en "other", que no miente: dice que falló
# sin afirmar por qué.
_CREDIT_SIGNALS = ("credit balance", "insufficient", "quota", "billing")


def classify_failure(error: BaseException) -> str:
    """Por qué falló la llamada: credit / timeout / network / auth / other.

    Clasifica por el mensaje y el status, no por la clase de excepción del
    SDK, porque este módulo no importa `anthropic` (es opcional) y no puede
    referirse a sus tipos.
    """
    mensaje = str(error).lower()
    status = getattr(error, "status_code", None)

    if any(signal in mensaje for signal in _CREDIT_SIGNALS):
        return "credit"
    if isinstance(error, TimeoutError) or "timeout" in mensaje or "timed out" in mensaje:
        return "timeout"
    if status in (401, 403) or "authentication" in mensaje or "api key" in mensaje:
        return "auth"
    if isinstance(error, ConnectionError) or "connection" in mensaje:
        return "network"
    return "other"


@dataclass(frozen=True)
class ConsumptionSummary:
    """Lo que `/control` muestra del asistente."""

    calls: int
    input_tokens: int
    output_tokens: int
    estimated_usd: float
    # Llamadas con un modelo que no está en la tabla de tarifas: se cuentan
    # pero no se estiman, para que el número de arriba no mienta.
    untariffed_calls: int
    # Fallas por tipo, solo las que hubo.
    failures: dict[str, int]
    # `True` cuando la ÚLTIMA llamada falló por saldo. Lo que importa es si
    # está fallando ahora, no si alguna vez falló.
    out_of_credit: bool


def consumption_summary(path: Path) -> ConsumptionSummary:
    """Resume el registro de invocaciones: cuánto se usó y cómo viene fallando."""
    runs = read_assist_runs(path)
    if not runs:
        return ConsumptionSummary(0, 0, 0, 0.0, 0, {}, False)

    entrada = sum(r.input_tokens for r in runs)
    salida = sum(r.output_tokens for r in runs)

    total = 0.0
    sin_tarifa = 0
    for run in runs:
        tarifa = _RATES_USD_PER_MTOK.get(run.model)
        if tarifa is None:
            sin_tarifa += 1
            continue
        total += (run.input_tokens / 1_000_000) * tarifa[0]
        total += (run.output_tokens / 1_000_000) * tarifa[1]

    fallas: dict[str, int] = {}
    for run in runs:
        if run.failure:
            fallas[run.failure] = fallas.get(run.failure, 0) + 1

    return ConsumptionSummary(
        calls=len(runs),
        input_tokens=entrada,
        output_tokens=salida,
        estimated_usd=total,
        untariffed_calls=sin_tarifa,
        failures=fallas,
        out_of_credit=runs[-1].failure == "credit",
    )


# --- la llamada ------------------------------------------------------------

_SYSTEM_PROMPT = (
    "Sos un asistente de extracción de datos de anuncios del mercado informal "
    "cubano. Recibís el texto de UN anuncio, transcripto por OCR y por lo "
    "tanto con ruido: renglones de la interfaz del chat, texto cortado, "
    "líneas fuera de orden.\n\n"
    "Devolvés SOLO los campos que se te piden, en el esquema dado.\n\n"
    "Reglas:\n"
    "- Si no podés determinar un campo con confianza, respondé null. "
    "Abstenerse es una respuesta correcta y preferible a adivinar.\n"
    "- Para producto y provincia respondé exactamente uno de los nombres de "
    "la lista que se te da, o null. No inventes nombres nuevos.\n"
    "- El precio es el de la oferta, no una tarifa derivada que el anuncio "
    "calcule aparte.\n"
    "- La cantidad y la unidad son las del lote que ese precio compra."
)


def _build_prompt(
    text: str,
    requested: frozenset[str],
    products: Sequence[Product],
    provinces: Sequence[Province],
) -> str:
    """El mensaje de usuario: catálogos cerrados y el texto ya redactado.

    Los catálogos van en el pedido para que el modelo elija de una lista en
    vez de generar texto libre. No reemplaza la validación de
    `parse_response` —que igual descarta lo que no resuelva—: es la primera
    de las dos guardas, no la única (`design.md`, D3).
    """
    nombres_producto = ", ".join(sorted(p.name for p in products))
    nombres_provincia = ", ".join(sorted(p.name for p in provinces))
    return (
        f"Campos a completar: {', '.join(sorted(requested))}\n\n"
        f"Productos del catálogo: {nombres_producto}\n"
        f"Provincias: {nombres_provincia}\n"
        f"Unidades válidas: {', '.join(UNITS)}\n\n"
        f"Anuncio:\n{text}"
    )


def _usage_of(response: object) -> tuple[int, int]:
    """Tokens consumidos según la API, o `(0, 0)`.

    El consumo es telemetría: que la respuesta no lo traiga no puede tumbar la
    asistencia, así que todo lo inesperado degrada a cero.
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        return (0, 0)
    return (
        _as_count(getattr(usage, "input_tokens", 0)),
        _as_count(getattr(usage, "output_tokens", 0)),
    )


def _response_text(response: object) -> str | None:
    """El primer bloque de texto de la respuesta, o `None`."""
    content = getattr(response, "content", None)
    if not content:
        return None
    for block in content:
        if getattr(block, "type", None) == "text":
            return getattr(block, "text", None)
    return None


def propose_missing(
    text: str,
    requested: frozenset[str],
    products: Sequence[Product],
    provinces: Sequence[Province],
    *,
    api_key: str | None,
    client: Any | None = None,
    model: str = "claude-sonnet-5",
    timeout: float = 30.0,
    ingestion_id: str = "",
    log_path: Path | None = None,
) -> AssistedFields:
    """Pide al modelo los campos que las reglas no resolvieron.

    Devuelve una propuesta vacía —nunca levanta— ante cualquier falla
    esperable: sin clave, sin paquete instalado, sin huecos que preguntar,
    error del cliente, timeout, cuerpo no parseable o respuesta que no
    valida. Mismo contrato que `exchange_rate.eltoque.fetch_rate` y que
    `extraction.ocr.transcribe`: el llamador trata la ausencia como un
    resultado ordinario y el operador completa a mano, igual que hoy.

    Sin reintento y sin backoff, por el mismo motivo que `eltoque` (design.md
    D3 de aquel cambio): la llamada vive dentro de una request del operador y
    sin cola —decisión cerrada 3— un reintento es solo más pared de espera.

    `client` es el seam de test. En producción se deja en `None` y se arma el
    cliente real; el import de `anthropic` es perezoso a propósito, para que
    este módulo se pueda importar y testear sin el paquete opcional
    instalado, igual que `ocr.py` hace con `rapidocr`.
    """
    gaps = requested & ASSISTABLE_FIELDS
    if not gaps or not api_key:
        # No hubo invocación: no hay nada que registrar. El registro cuenta
        # intentos reales, no configuraciones ausentes ni filas sin huecos.
        return AssistedFields()

    def _registrar(
        result: AssistedFields,
        *,
        degraded: bool,
        usage: tuple[int, int] = (0, 0),
        failure: str = "",
    ) -> AssistedFields:
        """Deja el rastro y devuelve el resultado.

        Todo camino de salida de acá para abajo pasa por esta función: es lo
        que hace cierto el requisito de que ninguna invocación ocurra sin
        registro, en vez de depender de acordarse en cada `return`.
        """
        if log_path is not None:
            append_assist_run(
                {
                    "timestamp": datetime.now(UTC).isoformat(),
                    "ingestion_id": ingestion_id,
                    "model": model,
                    "requested": sorted(gaps),
                    "answered": sorted(result.answered),
                    "dropped": sorted(result.dropped),
                    "degraded": degraded,
                    "input_tokens": usage[0],
                    "output_tokens": usage[1],
                    "failure": failure,
                },
                log_path,
            )
        return result

    if client is None:
        try:
            import anthropic

            client = anthropic.Anthropic(api_key=api_key, timeout=timeout)
        except Exception as error:
            # Paquete ausente o cliente que no se puede construir: este motor
            # no es usable en esta máquina, que es la condición de degradación.
            return _registrar(
                AssistedFields(), degraded=True, failure=classify_failure(error)
            )

    from saimc.privacy import redact_personal_data

    # La redacción va acá, en el borde de la llamada, y no solo antes de
    # guardar: es lo que hace que la exposición nueva sea cero (`design.md`,
    # D2). El archivo de evidencia nunca se adjunta.
    redacted = redact_personal_data(text) or ""

    try:
        response = client.messages.create(
            model=model,
            max_tokens=1024,
            system=_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": _build_prompt(redacted, gaps, products, provinces),
                }
            ],
            output_config={
                "format": {
                    "type": "json_schema",
                    "schema": response_schema(gaps),
                }
            },
        )
    except Exception as error:
        return _registrar(
            AssistedFields(), degraded=True, failure=classify_failure(error)
        )

    consumo = _usage_of(response)
    raw = _response_text(response)
    if not raw:
        return _registrar(
            AssistedFields(), degraded=True, usage=consumo, failure="other"
        )

    try:
        body = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return _registrar(
            AssistedFields(), degraded=True, usage=consumo, failure="other"
        )

    return _registrar(
        parse_response(body, gaps, products, provinces),
        degraded=False,
        usage=consumo,
    )
