"""Asistente de extracción por LLM — la mitad pura, sin red.

Cubre lo que `spec.md` §6.1 reserva para el modelo: «LLM únicamente para los
casos que las reglas no resuelven, con el resultado siempre sujeto a
confirmación humana» (`EXT-07`). Este módulo arma el pedido y valida la
respuesta; hacer la llamada es otra mitad, y confirmar lo propuesto es del
operador.

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

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from saimc.extraction.engine import ExtractionProposal
from saimc.models import Product, Province
from saimc.units import canonical_unit

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
    return {
        "type": "object",
        "properties": {
            name: {"type": tipos[name]}
            for name in sorted(requested & ASSISTABLE_FIELDS)
        },
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
