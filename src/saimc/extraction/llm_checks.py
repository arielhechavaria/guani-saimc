"""Chequeos puros sobre lo que propuso el modelo (sin I/O).

Dos señales de calidad que NO vienen del modelo sino de código
(`docs/plan-asistente-llm-cola-completa.md`, slice 2):

- **Evidencia verificada.** El modelo cita el fragmento del anuncio que sustenta
  cada oferta; acá se comprueba que el fragmento exista de verdad en el texto
  (`verify_evidence`). Un fragmento que no aparece hace sospechosa a la fila.
- **Acuerdo con las reglas.** Cada fila del modelo se compara campo a campo
  contra la fila de las reglas que le corresponde (`compare_with_rules`), con el
  MISMO emparejado que usa H0 (`measure._match_rows_to_candidates`).

Ninguna de las dos decide nada: son señales que el backtest mide contra lo
curado a mano (`llm_backtest`) antes de que alguien les dé peso.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from types import SimpleNamespace

from saimc.extraction import measure
from saimc.extraction.engine import ExtractionProposal, _normalize_number
from saimc.extraction.llm_assist import _normalize
from saimc.units import canonical_unit

COINCIDE = "coincide"
DIFIERE = "difiere"
SIN_REGLA = "sin_regla"


@dataclass(frozen=True)
class RowMeta:
    """Lo que el modelo dijo de UNA fila además de sus valores, y su verificación.

    Va en paralelo a `AdProposal.rows` (mismo índice). `evidence_found` y
    `price_in_evidence` los llena `verify_evidence`; antes de eso quedan en su
    valor por defecto.
    """

    # Fragmento literal del anuncio que sustenta la oferta (uno por fila, no por
    # campo). `None` si el modelo no lo dio o lo dio inválido.
    evidence: str | None = None
    # `True` solo si el texto del anuncio dice la moneda de ese precio; `False`
    # si el modelo asumió CUP por defecto (o no contestó).
    currency_explicit: bool = False
    evidence_found: bool = False
    # `None` si la fila no tiene precio: no hay nada que buscar.
    price_in_evidence: bool | None = None


def _squash(value: str) -> str:
    """Minúsculas, sin tildes y con los blancos colapsados a un espacio."""
    return " ".join(_normalize(value).split())


# Número con miles separados por espacio ("30 885") o cualquier corrida de
# dígitos con puntos y comas ("30.885", "43,5"). Las dos formas se prueban por
# separado para que "25 30885" no fabrique un número que el anuncio no dice.
_SPACED_NUMBER_RE = re.compile(r"\d{1,3}(?: \d{3})+(?:[.,]\d+)?")
_PLAIN_NUMBER_RE = re.compile(r"\d[\d.,]*")


def _price_in_fragment(fragment: str, price: Decimal) -> bool:
    """¿Algún número del fragmento vale exactamente `price`?

    Se reusa `engine._normalize_number`, que ya entiende los formatos del
    mercado (punto o coma de miles, coma decimal): así «30.885», «30 885» y
    «30885» son el mismo precio y «885» no lo es.
    """
    for regex in (_SPACED_NUMBER_RE, _PLAIN_NUMBER_RE):
        for match in regex.finditer(fragment):
            if _normalize_number(match.group(0)) == price:
                return True
    return False


def verify_evidence(
    text: str,
    rows: Sequence[ExtractionProposal],
    rows_meta: Sequence[RowMeta],
) -> list[RowMeta]:
    """Comprueba cada fragmento citado contra el texto del anuncio.

    `text` es el que vio el modelo (ya redactado). El fragmento cuenta como
    hallado si, normalizado, es subcadena del texto normalizado. `rows` aporta
    el precio de cada fila (`rows_meta` va en paralelo, mismo índice).
    """
    haystack = _squash(text)
    verified: list[RowMeta] = []
    for row, meta in zip(rows, rows_meta, strict=True):
        fragment = _squash(meta.evidence) if meta.evidence else ""
        found = bool(fragment) and fragment in haystack
        price_in = None if row.price is None else _price_in_fragment(fragment, row.price)
        verified.append(replace(meta, evidence_found=found, price_in_evidence=price_in))
    return verified


@dataclass(frozen=True)
class RowAgreement:
    """Acuerdo de una fila del modelo con la fila de las reglas que le toca.

    Cada campo es `coincide`, `difiere` o `sin_regla` (las reglas no produjeron
    valor, o ninguna fila de las reglas le corresponde). `status` es el resumen
    de los cinco campos de H0: `difiere` si alguno difiere; `coincide` si hay
    al menos uno comparado y todos coinciden; `sin_regla` si no hubo nada con
    qué comparar.

    Los tres del slice 3d van por campo y NO entran en `status`: es la señal que
    el slice 2 midió contra la extracción completa de H0, que no los incluye, y
    sumarlos cambiaría lo medido. `sale_format` y `packaging` son siempre
    `sin_regla`: las reglas nunca los proponen.

    `province` (A5b) también va aparte, y ni siquiera entra en `fields()`: la
    lee solo el motor de validación (`saimc.validation`, carril 1). Meterla en
    `status` o en `fields()` cambiaría el orden de la cola, las pistas del
    formulario y lo que escribe el backtest, y nada de eso debía moverse.
    """

    product: str
    price: str
    currency: str
    quantity: str
    unit: str
    status: str
    price_basis: str = SIN_REGLA
    sale_format: str = SIN_REGLA
    packaging: str = SIN_REGLA
    province: str = SIN_REGLA

    def fields(self) -> dict[str, str]:
        return {
            "product": self.product,
            "price": self.price,
            "currency": self.currency,
            "quantity": self.quantity,
            "unit": self.unit,
            "price_basis": self.price_basis,
            "sale_format": self.sale_format,
            "packaging": self.packaging,
        }


def _compare(model: object, rule: object) -> str:
    if rule is None:
        return SIN_REGLA
    return COINCIDE if model == rule else DIFIERE


def _agreement(model: ExtractionProposal, rule: ExtractionProposal | None) -> RowAgreement:
    if rule is None:
        fields = [SIN_REGLA] * 5
    else:
        fields = [
            _compare(
                None if model.product is None else model.product.id,
                None if rule.product is None else rule.product.id,
            ),
            _compare(model.price, rule.price),
            # Las reglas siempre proponen moneda (CUP por defecto): sin precio no
            # dicen nada de la moneda.
            _compare(model.currency, rule.currency if rule.price is not None else None),
            _compare(model.quantity, rule.quantity),
            _compare(canonical_unit(model.unit), canonical_unit(rule.unit) if rule.unit else None),
        ]
    if DIFIERE in fields:
        status = DIFIERE
    elif COINCIDE in fields:
        status = COINCIDE
    else:
        status = SIN_REGLA
    producto, precio, moneda, cantidad, unidad = fields
    return RowAgreement(
        product=producto,
        price=precio,
        currency=moneda,
        quantity=cantidad,
        unit=unidad,
        status=status,
        # Las reglas solo proponen la base cuando el anuncio la declara.
        price_basis=_compare(model.price_basis, rule.price_basis if rule else None),
        # Sin fila de las reglas, o sin provincia en ella, no hay con qué comparar.
        province=_compare(
            None if model.province is None else model.province.id,
            None if rule is None or rule.province is None else rule.province.id,
        ),
    )


def match_rule_rows(
    model_rows: Sequence[ExtractionProposal],
    rule_proposals: Sequence[ExtractionProposal],
) -> list[ExtractionProposal | None]:
    """La fila de las reglas que le toca a cada fila del modelo (o `None`).

    El emparejado es el de H0 (`measure._match_rows_to_candidates`): las filas
    del modelo hacen de «confirmadas» y las de las reglas de «candidatas», así
    que se aplican las mismas reglas de producto, mejor ajuste y líneas libres.
    Lo que H0 devuelve como propuesta pelada (no hay línea de las reglas que le
    toque) es `None`. Misma longitud y orden que `model_rows`.
    """
    # `_match_rows_to_candidates` solo lee estos atributos de la fila «confirmada».
    stand_ins = [
        SimpleNamespace(
            product_id=None if row.product is None else row.product.id,
            price=row.price,
            currency=row.currency,
            quantity=row.quantity,
            unit=row.unit,
            province_id=None if row.province is None else row.province.id,
        )
        for row in model_rows
    ]
    pairs = measure._match_rows_to_candidates(
        stand_ins,  # type: ignore[arg-type]  # duck typing sobre PriceHistory
        list(rule_proposals),
    )
    real = {id(r) for r in rule_proposals}
    return [matched if id(matched) in real else None for _, matched in pairs]


def compare_with_rules(
    model_rows: Sequence[ExtractionProposal],
    rule_proposals: Sequence[ExtractionProposal],
) -> list[RowAgreement]:
    """Compara cada fila del modelo con su fila de las reglas, campo a campo.

    El emparejado es `match_rule_rows`; lo que no tiene fila de las reglas es
    `sin_regla`.
    """
    return [
        _agreement(model, matched)
        for model, matched in zip(
            model_rows, match_rule_rows(model_rows, rule_proposals), strict=True
        )
    ]


def highlight_segments(text: str, fragments: Sequence[str]) -> list[tuple[str, bool]]:
    """Parte `text` en tramos `(texto, resaltado)` para pintar las citas.

    Cada fragmento se busca como subcadena EXACTA del texto mostrado (la
    primera aparición): el que no aparece literal se omite en silencio, porque
    resaltar de menos es inofensivo y resaltar de más miente. Los tramos que se
    solapan o se tocan se unen. Concatenados, los tramos reconstruyen `text`
    sin perder ni duplicar nada; el escapado HTML es de quien los renderiza.
    """
    spans: list[tuple[int, int]] = []
    for fragment in fragments:
        if not fragment.strip():
            continue
        start = text.find(fragment)
        if start >= 0:
            spans.append((start, start + len(fragment)))
    spans.sort()

    merged: list[list[int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])

    segments: list[tuple[str, bool]] = []
    cursor = 0
    for start, end in merged:
        if start > cursor:
            segments.append((text[cursor:start], False))
        segments.append((text[start:end], True))
        cursor = end
    if cursor < len(text) or not segments:
        segments.append((text[cursor:], False))
    return segments
