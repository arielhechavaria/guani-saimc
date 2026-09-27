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
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from saimc.extraction.engine import ExtractionProposal
from saimc.extraction.llm_assist import (
    _as_count,
    _as_positive_decimal,
    _match_catalog_name,
    _response_text,
    append_assist_run,
    classify_failure,
)
from saimc.extraction.llm_checks import RowMeta, verify_evidence
from saimc.glossary import effective_prompt_version, glossary_prompt_section
from saimc.models import (
    ContentType,
    Currency,
    DiscardReason,
    GlossaryEntry,
    Product,
    Province,
    RawIngestion,
    SaleFormat,
)
from saimc.units import PACKAGING, UNITS, canonical_packaging, canonical_unit

# Varias filas por respuesta: 1024 truncó una respuesta en el asistente por fila.
MAX_TOKENS = 4096

# Versión del prompt de sistema: cada línea del registro de llamadas y cada
# resultado del backtest la lleva, para atribuir una corrida a su prompt. La v1
# (el prompt original, sin esta constante) no la registró.
PROMPT_VERSION = "2026-09-27.1"

_CURRENCIES: tuple[str, ...] = tuple(c.value for c in Currency)
_DISCARD_REASONS: tuple[str, ...] = tuple(r.value for r in DiscardReason)
_SALE_FORMATS: tuple[str, ...] = tuple(f.value for f in SaleFormat)
# Mismo vocabulario que la CHECK `ck_prices_history_price_basis`.
_PRICE_BASES: tuple[str, ...] = ("unit", "total")


@dataclass(frozen=True)
class AdProposal:
    """Lo que el modelo propuso para un anuncio, ya validado."""

    rows: list[ExtractionProposal] = field(default_factory=list)
    # Paralela a `rows` (mismo índice): evidencia citada, moneda explícita o
    # asumida y su verificación contra el texto. Va aparte para no romper a quien
    # ya consume `rows` como `ExtractionProposal` (el backtest).
    row_meta: list[RowMeta] = field(default_factory=list)
    # `None` = sin veredicto (llamada degradada).
    discard: bool | None = None
    discard_reason: str | None = None
    degraded: bool = False
    # "credit" / "timeout" / "network" / "auth" / "other" / "no_key" /
    # "empty_text". Vacío si la llamada anduvo.
    failure: str = ""
    # Entrada «equivalente»: los tokens normales más los de caché ponderados por
    # lo que cuestan (ver `_usage_with_cache`), para que todo el que multiplica
    # `input_tokens` por la tarifa siga acertando el costo sin enterarse.
    input_tokens: int = 0
    output_tokens: int = 0
    # Los tokens de caché tal cual los informó la API. Solo para verificar que
    # el caching funciona (lectura > 0 desde la segunda llamada).
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    # Filas o campos que el modelo contestó y no resolvieron contra el catálogo.
    dropped: int = 0
    # La respuesta del modelo tal cual, también si no parseó (slice 3a: ver en
    # qué se van los tokens de salida). `None` si no hubo respuesta. El modelo
    # solo ve texto redactado, así que no trae teléfonos.
    raw_response: str | None = None
    # `PROMPT_VERSION`, o esa más el hash del glosario activo si se usó
    # alguno (slice 3c, `saimc.glossary.effective_prompt_version`). Sin
    # llamada real (`no_key`/`empty_text`) queda en la base: no hubo glosario
    # que aplicar.
    prompt_version: str = PROMPT_VERSION


def _enum_o_null(valores: Sequence[str]) -> dict[str, Any]:
    """Un `enum` de strings que además admite `null`.

    La API rechaza `{"type": ["string", "null"], "enum": [...]}` (HTTP 400,
    medido el 2026-09-25): el `enum` va con un único tipo y la nulabilidad con
    `anyOf`.
    """
    return {"anyOf": [{"type": "string", "enum": list(valores)}, {"type": "null"}]}


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
            "currency": _enum_o_null(_CURRENCIES),
            "quantity": {"type": ["number", "null"]},
            "unit": _enum_o_null(UNITS),
            "province": {"type": ["string", "null"]},
            # UN fragmento de evidencia por fila y no uno por campo (el plan dice
            # «por campo»): cuesta menos tokens de salida. Simplificación
            # deliberada, a revisar si la medición del backtest lo pide.
            "evidence": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "currency_explicit": {"type": "boolean"},
            # Slice 3d: juicio que antes solo hacía el operador. Nulables:
            # sin dato en el anuncio, el modelo se abstiene.
            "sale_format": _enum_o_null(_SALE_FORMATS),
            "price_basis": _enum_o_null(_PRICE_BASES),
            "packaging": _enum_o_null(PACKAGING),
        },
        "required": [
            "product",
            "price",
            "currency",
            "quantity",
            "unit",
            "province",
            "evidence",
            "currency_explicit",
            "sale_format",
            "price_basis",
            "packaging",
        ],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "discard": {"type": "boolean"},
            "discard_reason": _enum_o_null(_DISCARD_REASONS),
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
    "- UNA fila por oferta. Si una misma oferta está publicada en dos monedas "
    "(«43.5 o 30885», «25 USD o 17175»), es una sola fila, no una por moneda.\n"
    "- Precio en dos monedas: si la línea trae una marca de moneda explícita "
    "(«USD», «CUP», «MLC», «EUR»), tomá el precio marcado con esa moneda: en "
    "«42 usd o 28560» la fila es 42 con moneda USD. Si NO hay ninguna marca de "
    "moneda, tomá el mayor de los dos números con moneda CUP (la brecha USD:CUP ronda "
    "700:1): en «40 o 27400» la fila es 27400 con moneda CUP.\n"
    "- La cantidad y la unidad son las del lote que ese precio compra.\n"
    "- Si el lote se describe por su envase (caja, paquete, saco...) y el "
    "anuncio dice cuánto pesa o cuánto contiene, cantidad y unidad van en esa "
    "unidad de peso o volumen (por ejemplo «cajas de 40 lb» es cantidad 40, "
    "unidad lb), no 1 caja. Si el peso o volumen no está dicho, cantidad y "
    "unidad quedan en null.\n"
    "- La provincia es una de la lista o null.\n"
    "- sale_format: «retail» (minorista) o «wholesale» (mayorista). Mayorista "
    "es escala de contenedor, no vender un saco o paquete individual — eso lo "
    "ofrece cualquier minorista desde su casa. Vender por saco, por caja o por "
    "paquete entero sigue siendo «retail», aunque el título del anuncio diga "
    "«por mayor» o «mayorista» — un vendedor mayorista puede listar el precio "
    "de UN saco sin que esa fila deje de ser minorista. Ejemplo: un anuncio "
    "titulado «OFERTA ESPECIAL — POR MAYOR» que ofrece «el saco de 50 kg a "
    "$44» y también menciona que tiene «contenedores de 600 sacos» "
    "disponibles: la fila que describe es «el saco de 50 kg», así que "
    "sale_format es «retail» para esa fila (sería «wholesale» solo si la "
    "fila describiera el contenedor completo). Decidí SIEMPRE por la unidad "
    "que la fila vende, nunca por el título del anuncio. null si el anuncio "
    "no deja determinarlo.\n"
    "- price_basis: «unit» si el precio es por cada unidad de `unit` (por "
    "libra, por kg...); «total» si el precio es por el lote entero que "
    "describen cantidad y unidad. Ejemplo: «Pollo Muslo 4x10: 27300» son 4 "
    "paquetes de 10 lb en una caja: cantidad 40, unidad lb, packaging «caja», "
    "price_basis «total» y sale_format «retail». Solo importa si la cantidad "
    "no es 1; null si no está claro.\n"
    "- packaging: el envase en que se vende, uno de «paquete», «saco», «caja», "
    "«botella», «pomo» o «a granel». null si el anuncio no lo dice.\n"
    "- evidence: el fragmento del anuncio que respalda esa oferta (producto, "
    "precio y cantidad), copiado LITERALMENTE del texto, sin parafrasear ni "
    "corregir, y lo más corto posible. null si no hay fragmento que la respalde.\n"
    "- currency_explicit: true solo si el texto del anuncio dice la moneda de "
    "ese precio; false si asumiste CUP por defecto.\n"
    "- Marcá discard=true cuando un operador descartaría el anuncio: no ofrece "
    "ningún producto del catálogo, no tiene un precio utilizable, está "
    "agotado o es spam. Con discard=true, discard_reason es «off_focus» si "
    "el anuncio trae datos económicos pero de productos fuera del catálogo, "
    "o «no_data» si no trae precio, es ilegible, está agotado o es spam. "
    "Con discard=false, discard_reason es null y rows trae las ofertas."
)


def build_prompt_blocks(
    text: str,
    products: Sequence[Product],
    provinces: Sequence[Province],
    glossary: Sequence[GlossaryEntry] = (),
) -> list[dict[str, Any]]:
    """El mensaje de usuario en dos bloques: catálogos + glosario (fijos) y anuncio.

    Los catálogos van iguales en cada llamada, así que su bloque es el que se
    cachea (junto con el `system`, que lo precede): el prefijo fijo pasa de
    ~1.500 tokens a leerse al 10% desde la segunda llamada. El anuncio va al
    final y sin marca: es lo único que cambia. Cualquier byte que varíe antes
    del anuncio invalida la caché entera.

    El glosario (slice 3c) cambia con la misma frecuencia que el catálogo, no
    con cada anuncio, así que va en el mismo bloque cacheado. Sin entradas
    activas no agrega texto.
    """
    nombres_producto = ", ".join(sorted(p.name for p in products))
    nombres_provincia = ", ".join(sorted(p.name for p in provinces))
    catalogos = (
        f"Productos del catálogo: {nombres_producto}\n"
        f"Provincias: {nombres_provincia}\n"
        f"Unidades válidas: {', '.join(UNITS)}\n"
        f"Monedas válidas: {', '.join(_CURRENCIES)}\n"
        f"Envases válidos: {', '.join(PACKAGING)}\n\n"
        f"{glossary_prompt_section(glossary)}"
    )
    return [
        {"type": "text", "text": catalogos, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": f"Anuncio:\n{text}"},
    ]


def build_prompt(
    text: str,
    products: Sequence[Product],
    provinces: Sequence[Province],
    glossary: Sequence[GlossaryEntry] = (),
) -> str:
    """El mismo mensaje como un solo texto (estimaciones y pruebas)."""
    bloques = build_prompt_blocks(text, products, provinces, glossary)
    return "".join(b["text"] for b in bloques)


# Lo que cuesta cada tipo de token de entrada respecto de uno normal, con la
# caché de 5 minutos (la que se usa: el pre-cálculo llama seguido). Tarifas de
# la API, no medidas acá.
_CACHE_READ_FACTOR = 0.1
_CACHE_WRITE_FACTOR = 1.25


def _usage_with_cache(response: object) -> tuple[int, int, int, int]:
    """`(entrada equivalente, salida, leídos de caché, escritos en caché)`.

    Con caché, `usage.input_tokens` cuenta solo lo que viene DESPUÉS del último
    bloque cacheado: lo demás llega en `cache_read_input_tokens` y
    `cache_creation_input_tokens`. Sumar solo el primero subestimaría el gasto.
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        return (0, 0, 0, 0)
    normales = _as_count(getattr(usage, "input_tokens", 0))
    salida = _as_count(getattr(usage, "output_tokens", 0))
    leidos = _as_count(getattr(usage, "cache_read_input_tokens", 0))
    escritos = _as_count(getattr(usage, "cache_creation_input_tokens", 0))
    equivalente = round(
        normales + leidos * _CACHE_READ_FACTOR + escritos * _CACHE_WRITE_FACTOR
    )
    return (equivalente, salida, leidos, escritos)


def _perdido(crudo: object, resuelto: object) -> int:
    """1 si el modelo contestó algo (no `null`) que no validó; 0 si no.

    Abstenerse no es perder: es la diferencia entre honestidad e invento
    (misma distinción que `llm_assist.AssistedFields.dropped`).
    """
    return int(crudo is not None and resuelto is None)


def _en_vocabulario(crudo: object, valores: Sequence[str]) -> str | None:
    """El valor si es exactamente uno del vocabulario cerrado; si no, `None`."""
    return crudo if isinstance(crudo, str) and crudo in valores else None


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
    metas: list[RowMeta] = []
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
        sale_format = _en_vocabulario(item.get("sale_format"), _SALE_FORMATS)
        price_basis = _en_vocabulario(item.get("price_basis"), _PRICE_BASES)
        packaging_raw = item.get("packaging")
        packaging = (
            canonical_packaging(packaging_raw) if isinstance(packaging_raw, str) else None
        )
        for nombre, resuelto in (
            ("sale_format", sale_format),
            ("price_basis", price_basis),
            ("packaging", packaging),
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
                price_basis=price_basis,
                sale_format=sale_format,
                packaging=packaging,
            )
        )
        # Lo que falta o viene mal degrada a None/False: nunca levanta.
        evidence = item.get("evidence")
        metas.append(
            RowMeta(
                evidence=evidence if isinstance(evidence, str) else None,
                currency_explicit=item.get("currency_explicit") is True,
            )
        )

    discard = raw.get("discard")
    discard = discard if isinstance(discard, bool) else None
    reason = raw.get("discard_reason")
    reason = reason if discard and reason in _DISCARD_REASONS else None
    return AdProposal(
        rows=rows, row_meta=metas, discard=discard, discard_reason=reason, dropped=dropped
    )


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
    glossary: Sequence[GlossaryEntry] = (),
) -> AdProposal:
    """Una llamada por anuncio: todas las filas del foco y el veredicto.

    Nunca levanta (ver el docstring de módulo). `client` es el seam de test.
    Sin clave o con texto vacío no hay invocación y no se registra: el
    registro cuenta intentos reales — tampoco se aplica el glosario, porque
    no hubo llamada que lo use.
    """
    if not api_key:
        return AdProposal(degraded=True, failure="no_key")

    from saimc.privacy import redact_personal_data

    # La redacción va acá, en el borde de la llamada.
    redacted = redact_personal_data(text) or ""
    if not redacted.strip():
        return AdProposal(degraded=True, failure="empty_text")

    # Slice 3c: la versión efectiva ya incluye el glosario que se manda en
    # esta llamada, así que se calcula acá y viaja tanto en el registro como
    # en el resultado — sea cual sea el desenlace de la llamada.
    version = effective_prompt_version(PROMPT_VERSION, glossary)

    def _registrar(result: AdProposal) -> AdProposal:
        """Todo camino de salida con invocación pasa por acá."""
        if log_path is not None:
            append_assist_run(
                {
                    "timestamp": datetime.now(UTC).isoformat(),
                    "ingestion_id": ingestion_id,
                    "model": model,
                    "prompt_version": result.prompt_version,
                    "rows": len(result.rows),
                    "discard": result.discard,
                    "dropped": result.dropped,
                    "degraded": result.degraded,
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "cache_read_tokens": result.cache_read_tokens,
                    "cache_write_tokens": result.cache_write_tokens,
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
                AdProposal(
                    degraded=True, failure=classify_failure(error), prompt_version=version
                )
            )

    try:
        response = client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": build_prompt_blocks(redacted, products, provinces, glossary),
                }
            ],
            output_config={"format": {"type": "json_schema", "schema": response_schema()}},
            # Sonnet 5 corre *adaptive thinking* por defecto si no se lo pide
            # explícitamente desactivado, y ese razonamiento se factura como
            # output_tokens sin aparecer en `raw_response` (que solo guarda
            # el primer bloque `type: text`). Medido contra anuncios reales
            # (tarea 7 del backlog): con el default, la extracción de un
            # anuncio con varias filas agotaba `max_tokens` (4096) antes de
            # terminar el JSON y degradaba (docs/decisions.md, hallazgo del
            # 2026-09-26 sobre `62cd0e01`); desactivado, el mismo anuncio usó
            # 7-8x menos tokens de salida y el JSON salió completo. La tarea
            # es extracción con esquema cerrado, no razonamiento abierto: no
            # hay nada que "pensar" que el esquema no fuerce ya.
            thinking={"type": "disabled"},
        )
    except Exception as error:
        return _registrar(
            AdProposal(degraded=True, failure=classify_failure(error), prompt_version=version)
        )

    entrada, salida, leidos, escritos = _usage_with_cache(response)
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
                cache_read_tokens=leidos,
                cache_write_tokens=escritos,
                raw_response=raw or None,
                prompt_version=version,
            )
        )
    return _registrar(
        AdProposal(
            rows=parsed.rows,
            # Se verifica contra el texto que vio el modelo (ya redactado).
            row_meta=verify_evidence(redacted, parsed.rows, parsed.row_meta),
            discard=parsed.discard,
            discard_reason=parsed.discard_reason,
            input_tokens=entrada,
            output_tokens=salida,
            cache_read_tokens=leidos,
            cache_write_tokens=escritos,
            dropped=parsed.dropped,
            raw_response=raw or None,
            prompt_version=version,
        )
    )


# --- compartido por «Completar con IA» y el pre-cálculo (slice 3b) ----------
#
# Vivían en `web/routers/curation.py`. El pre-cálculo tiene que mandar
# EXACTAMENTE el mismo texto y escribir EXACTAMENTE la misma línea de registro
# que el botón, así que se mudaron acá en vez de copiarse.

# Los campos que el modelo propone por fila (los tres últimos, desde el 3d).
ASSIST_FIELDS = (
    "product",
    "price",
    "currency",
    "quantity",
    "unit",
    "province",
    "sale_format",
    "price_basis",
    "packaging",
)


def ad_text(
    ingestion: RawIngestion,
    raw_store_dir: Path,
    transcribe: Callable[[Path], str] | None = None,
) -> str:
    """El texto del anuncio que se le da al modelo.

    El original si es de texto; lo que sale del OCR si es una imagen sin texto
    cargado. Vacío si no hay nada que mandar. `transcribe` es el seam de test;
    por defecto `ocr.transcribe`, resuelto al llamar para que un
    `monkeypatch` del módulo `ocr` lo alcance.
    """
    if ingestion.content_type == ContentType.TEXT:
        return ingestion.original_text or ""
    if ingestion.original_text is not None:
        return ""
    if transcribe is None:
        from saimc.extraction import ocr

        transcribe = ocr.transcribe
    return transcribe(raw_store_dir / ingestion.file_path) or ""


def filled_fields(row: ExtractionProposal) -> list[str]:
    """Qué campos llenó el modelo en esta fila (los que trajo con valor).

    La moneda cuenta solo con precio: sin él el `CUP` es el default de las
    reglas y no una respuesta del modelo.
    """
    campos = ["product"]
    if row.price is not None:
        campos += ["price", "currency"]
    if row.quantity is not None:
        campos.append("quantity")
    if row.unit:
        campos.append("unit")
    if row.province is not None:
        campos.append("province")
    campos += [
        campo
        for campo in ("sale_format", "price_basis", "packaging")
        if getattr(row, campo) is not None
    ]
    return campos


def record_operator_run(
    result: AdProposal,
    *,
    ingestion_id: str,
    model: str,
    log_path: Path,
    source: str,
) -> None:
    """Anota la invocación en el registro del operador (`llm_assist_log_path`).

    Es el registro que lee `llm_assist.consumption_summary` (`/control`) y
    `llm_assist.assisted_ingestion_ids` (H0 deja fuera lo asistido, salvo las
    líneas con `source` = `llm_assist.PREFILL_SOURCE`). Su forma es la de
    `llm_assist.AssistRun`: `requested`/`answered`/`dropped` son LISTAS. El
    `dropped` de `AdProposal` es un entero y copiarlo tal cual haría que
    `read_assist_runs` fallara en `/control`, así que va aparte como
    `dropped_count`. Por eso no sirve el `log_path` de `propose_ad`, que es el
    registro aparte del backtest, con otra forma.

    No se registra lo que no fue una invocación (`no_key`, `empty_text`), igual
    que `propose_ad`: el registro cuenta intentos reales.
    """
    if result.failure in ("no_key", "empty_text"):
        return
    respondidos = sorted({campo for row in result.rows for campo in filled_fields(row)})
    append_assist_run(
        {
            "timestamp": datetime.now(UTC).isoformat(),
            "ingestion_id": ingestion_id,
            "model": model,
            "kind": "ad",
            "source": source,
            "prompt_version": PROMPT_VERSION,
            "requested": list(ASSIST_FIELDS),
            "answered": respondidos,
            "dropped": [],
            "dropped_count": result.dropped,
            "rows": len(result.rows),
            "discard": result.discard,
            "degraded": result.degraded,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "cache_read_tokens": result.cache_read_tokens,
            "cache_write_tokens": result.cache_write_tokens,
            "failure": result.failure,
        },
        log_path,
    )
