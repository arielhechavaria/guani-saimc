"""Motor de reglas de extracción sobre texto crudo de anuncios.

El whitepaper (§6.1, «Rol del OCR») describe esta etapa como «extracción
asistida»: el sistema propone valores candidatos por regex y diccionarios
estáticos —nada de ML/LLM en este slice (línea 611)— y el operador humano
confirma o corrige cada campo en la cola de curaduría. Este módulo solo
produce la propuesta; la confirmación es UI y no vive acá.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from saimc.models import Currency, Product, Province

# --- provincias --------------------------------------------------------------

# Las 16 provincias de Cuba son fijas y viven como fila de datos, no como
# código (whitepaper §9.0). Pero el texto libre de un anuncio usa jerga y
# abreviaturas informales ("stgo", "habana") que no son parte del esquema:
# por eso el diccionario vive acá, en la capa de extracción, y no en
# `Province`. Claves sin tilde: el texto de entrada tampoco suele traerlas.
_PROVINCE_ALIASES: dict[str, str] = {
    "pinar del rio": "Pinar del Río",
    "pinar": "Pinar del Río",
    "pri": "Pinar del Río",
    "artemisa": "Artemisa",
    "art": "Artemisa",
    "la habana": "La Habana",
    "habana": "La Habana",
    "hab": "La Habana",
    "mayabeque": "Mayabeque",
    "may": "Mayabeque",
    "matanzas": "Matanzas",
    "mtz": "Matanzas",
    "cienfuegos": "Cienfuegos",
    "cfg": "Cienfuegos",
    "villa clara": "Villa Clara",
    "vcl": "Villa Clara",
    "sancti spiritus": "Sancti Spíritus",
    "ssp": "Sancti Spíritus",
    "ciego de avila": "Ciego de Ávila",
    "cav": "Ciego de Ávila",
    "camaguey": "Camagüey",
    "cmg": "Camagüey",
    "las tunas": "Las Tunas",
    "tunas": "Las Tunas",
    "ltu": "Las Tunas",
    "holguin": "Holguín",
    "hlg": "Holguín",
    "granma": "Granma",
    "gra": "Granma",
    "santiago de cuba": "Santiago de Cuba",
    "santiago": "Santiago de Cuba",
    "stgo": "Santiago de Cuba",
    "scu": "Santiago de Cuba",
    "guantanamo": "Guantánamo",
    "gtm": "Guantánamo",
    "isla de la juventud": "Isla de la Juventud",
    "isla de pinos": "Isla de la Juventud",
    "iju": "Isla de la Juventud",
}

# De más larga a más corta: si no, una abreviatura corta ("santiago") gana
# siempre por ser substring de la frase larga ("santiago de cuba").
_PROVINCE_ALIASES_BY_LENGTH = sorted(
    _PROVINCE_ALIASES.items(), key=lambda item: len(item[0]), reverse=True
)

# --- moneda --------------------------------------------------------------

# "mn" (moneda nacional) es jerga cubana habitual para CUP, no una moneda
# aparte.
_CURRENCY_MARKERS: dict[str, Currency] = {
    "cup": Currency.CUP,
    "mn": Currency.CUP,
    "usd": Currency.USD,
    "eur": Currency.EUR,
    "mlc": Currency.MLC,
}
_CURRENCY_RE = re.compile(r"\b(" + "|".join(_CURRENCY_MARKERS) + r")\b", re.IGNORECASE)

# --- precio y cantidad --------------------------------------------------------

# La palabra "a" antes de un número casi siempre precede al precio en este
# mercado ("a 12000"); se intenta primero por ser la pista más fuerte.
_PRICE_AFTER_A_RE = re.compile(r"\ba\s+(\d[\d.,]*)", re.IGNORECASE)
# Segunda pista: un número pegado a una marca de moneda también es precio.
_PRICE_BEFORE_CURRENCY_RE = re.compile(
    r"(\d[\d.,]*)\s*(" + "|".join(_CURRENCY_MARKERS) + r")\b", re.IGNORECASE
)

# Unidades conocidas para cantidad. "cajas"/"caja" antes que las demás no
# hace falta por backtracking de regex, pero se deja explícito por claridad.
_UNIT_WORDS = ("lb", "kg", "cajas", "caja", "docena", "litro", "unidad")
_QUANTITY_RE = re.compile(
    r"(\d[\d.,]*)\s*(" + "|".join(_UNIT_WORDS) + r")\b", re.IGNORECASE
)


@dataclass
class ExtractionProposal:
    """Candidatos propuestos para un anuncio.

    Ningún campo se da por confirmado: la cola de curaduría es quien decide
    (whitepaper §6.1). `currency` es la excepción: nunca queda sin proponer,
    porque el whitepaper manda inferir CUP cuando no hay marca explícita.
    """

    product: Product | None = None
    price: Decimal | None = None
    currency: str = Currency.CUP.value
    quantity: Decimal | None = None
    unit: str | None = None
    province: Province | None = None


def _normalize_number(raw: str) -> Decimal | None:
    """Normaliza separadores de miles/decimales a un `Decimal` sin ambigüedad.

    "12.000" en este mercado es doce mil (punto de miles), no 12 con
    decimales: por eso un punto seguido de grupos completos de tres dígitos
    se trata como separador de miles y se descarta, nunca como decimal.
    """
    cleaned = raw.strip()
    if re.fullmatch(r"\d{1,3}(\.\d{3})+", cleaned):
        cleaned = cleaned.replace(".", "")
    elif re.fullmatch(r"\d{1,3}(,\d{3})+", cleaned):
        cleaned = cleaned.replace(",", "")
    else:
        cleaned = cleaned.replace(",", ".")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _extract_price(text: str) -> Decimal | None:
    match = _PRICE_AFTER_A_RE.search(text) or _PRICE_BEFORE_CURRENCY_RE.search(text)
    if match is None:
        return None
    return _normalize_number(match.group(1))


def _extract_currency(text: str) -> str:
    match = _CURRENCY_RE.search(text)
    if match is None:
        # Sin marca explícita: el whitepaper (§6.1, línea 606) manda CUP.
        return Currency.CUP.value
    return _CURRENCY_MARKERS[match.group(1).lower()].value


def _extract_quantity(text: str) -> tuple[Decimal | None, str | None]:
    match = _QUANTITY_RE.search(text)
    if match is None:
        return None, None
    return _normalize_number(match.group(1)), match.group(2).lower()


def _strip_accents(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value)
    return "".join(char for char in decomposed if unicodedata.category(char) != "Mn")


def _match_province(text: str, provinces: Sequence[Province]) -> Province | None:
    normalized = _strip_accents(text.lower())
    canonical: str | None = None
    for alias, name in _PROVINCE_ALIASES_BY_LENGTH:
        pattern = r"\b" + re.escape(_strip_accents(alias)) + r"\b"
        if re.search(pattern, normalized):
            canonical = name
            break
    if canonical is None:
        return None
    return next((p for p in provinces if p.name == canonical), None)


def _match_product(text: str, products: Sequence[Product]) -> Product | None:
    text_lower = text.lower()
    for product in products:
        names = (product.name, *product.aliases)
        if any(name.lower() in text_lower for name in names):
            return product
    return None


def propose(
    text: str, products: Sequence[Product], provinces: Sequence[Province]
) -> ExtractionProposal:
    """Propone valores candidatos a partir del texto crudo de un anuncio.

    Reglas y diccionarios estáticos, no ML/LLM (whitepaper §6.1, línea 611):
    la propuesta es un punto de partida para que el operador confirme o
    corrija en la cola de curaduría, no un resultado final.
    """
    quantity, unit = _extract_quantity(text)
    return ExtractionProposal(
        product=_match_product(text, products),
        price=_extract_price(text),
        currency=_extract_currency(text),
        quantity=quantity,
        unit=unit,
        province=_match_province(text, provinces),
    )
