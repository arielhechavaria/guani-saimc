"""Propuesta de UN anuncio completo por LLM: todas las filas y el veredicto de descarte.

Hermana de `llm_assist.propose_missing`, con otra pregunta. Aquella completa
huecos de una fila que las reglas ya armaron, y por eso hace una llamada por
fila sin decirle al modelo de cuál pregunta: en las listas de precios el
modelo se abstiene, y son N llamadas por anuncio. Esta hace UNA llamada por
anuncio y pide todas las filas del foco más si el operador descartaría el
anuncio (`docs/plan-asistente-llm-cola-completa.md`, slices 1 y 2).

Mismo contrato que `propose_missing`:

- **Nunca levanta.** Sin clave, sin paquete, error del cliente, timeout, cuerpo
  ilegible o truncado: devuelve un `AdProposal` con `degraded=True` y el motivo
  en `failure`. Quien llama lo trata como un resultado ordinario.
- **La redacción de teléfonos va en el borde de la llamada.**
- **Nada de acá inventa vocabulario.** Un producto, provincia o unidad que no
  resuelva contra el catálogo cerrado se descarta (la fila entera si es el
  producto; solo el campo en los demás casos). Se cuenta en `dropped`: es la
  señal de que el modelo está inventando.
- `import anthropic` perezoso y `client` como seam de test.

El registro de invocaciones es un archivo APARTE del del asistente del
operador: `assisted_ingestion_ids` saca anuncios de la medición de H0 y el
backtest no puede hacerlo.

Las filas salen como `ExtractionProposal` para que `measure.py` las empareje y
puntúe con las mismas funciones que a las reglas.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from saimc.extraction.engine import ExtractionProposal
from saimc.extraction.llm_assist import (
    _as_positive_decimal,
    _match_catalog_name,
    _response_text,
    _usage_of,
    append_assist_run,
    classify_failure,
)
from saimc.models import Currency, DiscardReason, Product, Province
from saimc.units import UNITS, canonical_unit

# Varias filas por respuesta: 1024 truncó una respuesta en el asistente por fila.
MAX_TOKENS = 4096

_CURRENCIES: tuple[str, ...] = tuple(c.value for c in Currency)
_DISCARD_REASONS: tuple[str, ...] = tuple(r.value for r in DiscardReason)


@dataclass(frozen=True)
class AdProposal:
    """Lo que el modelo propuso para un anuncio, ya validado."""

    rows: list[ExtractionProposal] = field(default_factory=list)
    # `None` = sin veredicto (llamada degradada).
    discard: bool | None = None
    discard_reason: str | None = None
    degraded: bool = False
    # "credit" / "timeout" / "network" / "auth" / "other" / "no_key" /
    # "empty_text". Vacío si la llamada anduvo.
    failure: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    # Filas o campos que el modelo contestó y no resolvieron contra el catálogo.
    dropped: int = 0


def response_schema() -> dict[str, Any]:
    """Esquema JSON de la respuesta.

    Todo campo de una fila admite `null`: abstenerse es una respuesta válida y
    preferible a adivinar. Todo es requerido y `additionalProperties: False`
    para que la forma quede completamente determinada.
    """
    fila = {
        "type": "object",
        "properties": {
            "product": {"type": ["string", "null"]},
            "price": {"type": ["number", "null"]},
            "currency": {"type": ["string", "null"], "enum": [*_CURRENCIES, None]},
            "quantity": {"type": ["number", "null"]},
            "unit": {"type": ["string", "null"], "enum": [*UNITS, None]},
            "province": {"type": ["string", "null"]},
        },
        "required": ["product", "price", "currency", "quantity", "unit", "province"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "discard": {"type": "boolean"},
            "discard_reason": {
                "type": ["string", "null"],
                "enum": [*_DISCARD_REASONS, None],
            },
            "rows": {"type": "array", "items": fila},
        },
        "required": ["discard", "discard_reason", "rows"],
        "additionalProperties": False,
    }


_SYSTEM_PROMPT = (
    "Sos un asistente de extracción de datos de anuncios del mercado informal "
    "cubano. Recibís el texto de UN anuncio, a veces transcripto por OCR y por "
    "lo tanto con ruido: renglones de la interfaz del chat, texto cortado, "
    "líneas fuera de orden.\n\n"
    "Tu tarea: devolver TODAS las ofertas del anuncio que sean de productos "
    "del catálogo que se te da (una fila por oferta; una lista de precios "
    "tiene varias filas), y decir si el anuncio se descarta.\n\n"
    "Reglas:\n"
    "- Devolvé solo productos del catálogo, con exactamente uno de los nombres "
    "de la lista. Las ofertas de productos que no están en el catálogo no se "
    "devuelven. No inventes nombres.\n"
    "- Si no podés determinar un campo con confianza, respondé null. "
    "Abstenerse es una respuesta correcta y preferible a adivinar.\n"
    "- El precio es el de la oferta, no una tarifa derivada que el anuncio "
    "calcule aparte (por ejemplo «sale a X la libra»).\n"
    "- La cantidad y la unidad son las del lote que ese precio compra.\n"
    "- La provincia es una de la lista o null.\n"
    "- Marcá discard=true cuando un operador descartaría el anuncio: no ofrece "
    "ningún producto del catálogo, no tiene un precio utilizable, está "
    "agotado o es spam. Con discard=true, discard_reason es «off_focus» si "
    "el anuncio trae datos económicos pero de productos fuera del catálogo, "
    "o «no_data» si no trae precio, es ilegible, está agotado o es spam. "
    "Con discard=false, discard_reason es null y rows trae las ofertas."
)


def build_prompt(
    text: str, products: Sequence[Product], provinces: Sequence[Province]
) -> str:
    """El mensaje de usuario: catálogos cerrados y el texto ya redactado."""
    nombres_producto = ", ".join(sorted(p.name for p in products))
    nombres_provincia = ", ".join(sorted(p.name for p in provinces))
    return (
        f"Productos del catálogo: {nombres_producto}\n"
        f"Provincias: {nombres_provincia}\n"
        f"Unidades válidas: {', '.join(UNITS)}\n"
        f"Monedas válidas: {', '.join(_CURRENCIES)}\n\n"
        f"Anuncio:\n{text}"
    )


def _perdido(crudo: object, resuelto: object) -> int:
    """1 si el modelo contestó algo (no `null`) que no validó; 0 si no.

    Abstenerse no es perder: es la diferencia entre honestidad e invento
    (misma distinción que `llm_assist.AssistedFields.dropped`).
    """
    return int(crudo is not None and resuelto is None)


def parse_response(
    raw: object, products: Sequence[Product], provinces: Sequence[Province]
) -> AdProposal | None:
    """Valida la respuesta contra los catálogos vivos.

    `None` si el cuerpo no es un objeto: no hay nada que rescatar y quien llama
    lo registra como falla, no como una respuesta legítimamente vacía.
    """
    if not isinstance(raw, dict):
        return None

    dropped = 0
    rows: list[ExtractionProposal] = []
    raw_rows = raw.get("rows")
    for item in raw_rows if isinstance(raw_rows, list) else []:
        if not isinstance(item, dict):
            dropped += 1
            continue
        product = _match_catalog_name(
            item.get("product"), products, lambda p: (p.name, *p.aliases)
        )
        if product is None:
            dropped += 1  # fila de un producto que no existe en el foco
            continue

        price = _as_positive_decimal(item.get("price"))
        quantity = _as_positive_decimal(item.get("quantity"))
        unit_raw = item.get("unit")
        unit = canonical_unit(unit_raw) if isinstance(unit_raw, str) else None
        province = _match_catalog_name(
            item.get("province"), provinces, lambda p: (p.name,)
        )
        currency_raw = item.get("currency")
        currency = (
            currency_raw.upper()
            if isinstance(currency_raw, str) and currency_raw.upper() in _CURRENCIES
            else None
        )
        for nombre, resuelto in (
            ("price", price),
            ("quantity", quantity),
            ("unit", unit),
            ("province", province),
            ("currency", currency),
        ):
            dropped += _perdido(item.get(nombre), resuelto)
        # Sin moneda válida cae a CUP, igual que las reglas (spec.md §6.1).
        currency = currency or Currency.CUP.value

        rows.append(
            ExtractionProposal(
                product=product,
                price=price,
                currency=currency,
                quantity=quantity if isinstance(quantity, Decimal) else None,
                unit=unit,
                province=province,
            )
        )

    discard = raw.get("discard")
    discard = discard if isinstance(discard, bool) else None
    reason = raw.get("discard_reason")
    reason = reason if discard and reason in _DISCARD_REASONS else None
    return AdProposal(rows=rows, discard=discard, discard_reason=reason, dropped=dropped)


def propose_ad(
    text: str,
    products: Sequence[Product],
    provinces: Sequence[Province],
    *,
    api_key: str | None,
    client: Any | None = None,
    model: str = "claude-sonnet-5",
    timeout: float = 120.0,
    ingestion_id: str = "",
    log_path: Path | None = None,
) -> AdProposal:
    """Una llamada por anuncio: todas las filas del foco y el veredicto.

    Nunca levanta (ver el docstring de módulo). `client` es el seam de test.
    Sin clave o con texto vacío no hay invocación y no se registra: el
    registro cuenta intentos reales.
    """
    if not api_key:
        return AdProposal(degraded=True, failure="no_key")

    from saimc.privacy import redact_personal_data

    # La redacción va acá, en el borde de la llamada.
    redacted = redact_personal_data(text) or ""
    if not redacted.strip():
        return AdProposal(degraded=True, failure="empty_text")

    def _registrar(result: AdProposal) -> AdProposal:
        """Todo camino de salida con invocación pasa por acá."""
        if log_path is not None:
            append_assist_run(
                {
                    "timestamp": datetime.now(UTC).isoformat(),
                    "ingestion_id": ingestion_id,
                    "model": model,
                    "rows": len(result.rows),
                    "discard": result.discard,
                    "dropped": result.dropped,
                    "degraded": result.degraded,
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "failure": result.failure,
                },
                log_path,
            )
        return result

    if client is None:
        try:
            import anthropic

            client = anthropic.Anthropic(api_key=api_key, timeout=timeout)
        except Exception as error:
            return _registrar(
                AdProposal(degraded=True, failure=classify_failure(error))
            )

    try:
        response = client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=_SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": build_prompt(redacted, products, provinces)}
            ],
            output_config={"format": {"type": "json_schema", "schema": response_schema()}},
        )
    except Exception as error:
        return _registrar(AdProposal(degraded=True, failure=classify_failure(error)))

    entrada, salida = _usage_of(response)
    raw = _response_text(response)
    body: object = None
    if raw:
        try:
            body = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            body = None

    parsed = parse_response(body, products, provinces)
    if parsed is None:
        return _registrar(
            AdProposal(
                degraded=True,
                failure="other",
                input_tokens=entrada,
                output_tokens=salida,
            )
        )
    return _registrar(
        AdProposal(
            rows=parsed.rows,
            discard=parsed.discard,
            discard_reason=parsed.discard_reason,
            input_tokens=entrada,
            output_tokens=salida,
            dropped=parsed.dropped,
        )
    )
