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
# Tercera pista: el símbolo "$" también marca precio, muy común en capturas de
# tiendas informales. No implica moneda USD —en el mercado cubano "$" suele
# usarse para CUP también— por eso esto solo ubica el número, la moneda la
# sigue decidiendo `_extract_currency` por separado.
_PRICE_DOLLAR_RE = re.compile(r"\$\s*(\d[\d.,]*)")

# Unidades conocidas para cantidad. "cajas"/"caja" antes que las demás no
# hace falta por backtracking de regex, pero se deja explícito por claridad.
_UNIT_WORDS = ("lb", "kg", "cajas", "caja", "docena", "litro", "unidad")
_QUANTITY_RE = re.compile(
    r"(\d[\d.,]*)\s*(" + "|".join(_UNIT_WORDS) + r")\b", re.IGNORECASE
)

# --- producto --------------------------------------------------------------

# Cortes o partes que hacen de un producto genérico algo distinto ("hígado de
# pollo" no es "Pollo"). Distinguir esto en general es un problema de NLP que
# no resolvemos acá: esta lista curada es la aproximación barata — si
# aparece cualquiera de estas palabras, no confiamos en el match genérico y
# en cambio se propone dar de alta un producto nuevo (ver `propose_all` /
# `product_name_guess`). Sin tilde: se compara contra texto normalizado.
_PRODUCT_DISQUALIFYING_MODIFIERS = (
    "higado",
    "picadillo",
    "muslo",
    "pechuga",
    "molleja",
    "ala",
    "corazon",
    "molida",
    "falda",
    "lomo",
    "costilla",
    "contramuslo",
    "rabadilla",
    "menudencia",
    "menudencias",
)
_DISQUALIFYING_MODIFIER_RE = re.compile(
    r"\b(" + "|".join(_PRODUCT_DISQUALIFYING_MODIFIERS) + r")\b", re.IGNORECASE
)


# --- canal origen -------------------------------------------------------------

# Patrón habitual de redes tipo Facebook/Marketplace al principio del texto:
# "Nombre - Ayer a las H:MM" / "Nombre - D de mes a las H:MM" (docs/backlog.md).
# Solo se busca en la primera línea: el patrón real observado siempre encabeza
# el texto, buscar en todo el cuerpo arriesgaría matchear un " - " que no
# tiene nada que ver con quien publicó.
_SOURCE_REFERENCE_RE = re.compile(
    r"^(?P<name>.+?)\s*-\s*"
    r"(?:ayer|hoy|\d{1,2}\s+de\s+[a-záéíóúñ]+)"
    r"\s+a\s+las\s+\d{1,2}:\d{2}",
    re.IGNORECASE,
)


def guess_source_reference(text: str) -> str | None:
    """Adivina quién publicó el anuncio a partir del patrón de fecha de redes.

    Heurística de recorte, no NLP — sirve para prellenar `source_reference`
    en la cola de curaduría; el operador la revisa y corrige (`source_reference`
    sigue siendo 100% manual en cuanto a lo que termina guardado).
    """
    first_line = text.splitlines()[0].strip() if text else ""
    match = _SOURCE_REFERENCE_RE.match(first_line)
    if match is None:
        return None
    name = match.group("name").strip(" -•\t")
    return name or None


@dataclass
class ExtractionProposal:
    """Candidatos propuestos para un anuncio.

    Ningún campo se da por confirmado: la cola de curaduría es quien decide
    (whitepaper §6.1). `currency` es la excepción: nunca queda sin proponer,
    porque el whitepaper manda inferir CUP cuando no hay marca explícita.
    """

    product: Product | None = None
    # Nombre candidato para dar de alta un producto nuevo, solo cuando
    # `product` es None por un modificador que descalifica el match genérico
    # (ver `_PRODUCT_DISQUALIFYING_MODIFIERS`). Heurística, no un nombre
    # confirmado: el operador lo revisa antes de crear nada.
    product_name_guess: str | None = None
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

    `rstrip(".,")` porque el OCR pega puntuación del layout justo después del
    número ("$23,500.Sale" — el "." es de la oración, no del precio); sin
    esto, ese "." final rompe el fullmatch de separador de miles.
    """
    cleaned = raw.strip().rstrip(".,")
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
    match = (
        _PRICE_AFTER_A_RE.search(text)
        or _PRICE_BEFORE_CURRENCY_RE.search(text)
        or _PRICE_DOLLAR_RE.search(text)
    )
    if match is None:
        return None
    return _normalize_number(match.group(1))


def _extract_currency_explicit(text: str) -> str | None:
    """Moneda con marca explícita en el texto, o `None` si no hay ninguna.

    Separado de `_extract_currency` para que `propose_all` pueda distinguir
    "esta línea no dice moneda" de "esta línea dice CUP": una línea sin marca
    propia debe caer a lo que diga el resto del anuncio antes de caer al
    default, no ir directo al default como si el anuncio no dijera nada.
    """
    match = _CURRENCY_RE.search(text)
    if match is None:
        return None
    return _CURRENCY_MARKERS[match.group(1).lower()].value


def _extract_currency(text: str) -> str:
    # Sin marca explícita: el whitepaper (§6.1, línea 606) manda CUP.
    return _extract_currency_explicit(text) or Currency.CUP.value


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
    has_modifier = bool(_DISQUALIFYING_MODIFIER_RE.search(_strip_accents(text_lower)))
    for product in products:
        for name in (product.name, *product.aliases):
            name_lower = name.lower()
            if name_lower not in text_lower:
                continue
            if has_modifier and not _DISQUALIFYING_MODIFIER_RE.search(
                _strip_accents(name_lower)
            ):
                # Hay un corte/parte en el texto ("hígado de pollo") pero el
                # nombre que matcheó ("pollo") no lo incluye: es un match
                # genérico de un producto más específico, no confiamos en él.
                continue
            return product
    return None


def _guess_product_phrase(line: str) -> str | None:
    """Frase candidata a nombre de producto: el texto antes de la primera
    pista de cantidad o precio en la línea.

    Heurística de recorte, no NLP — sirve para prellenar el alta de producto
    nuevo cuando no hay match de catálogo, no para decidir nada por su
    cuenta. El operador la revisa y corrige en la cola de curaduría.
    """
    markers = [
        match.start()
        for match in (
            _PRICE_AFTER_A_RE.search(line),
            _PRICE_BEFORE_CURRENCY_RE.search(line),
            _PRICE_DOLLAR_RE.search(line),
            _QUANTITY_RE.search(line),
        )
        if match is not None
    ]
    if not markers:
        return None
    guess = line[: min(markers)].strip(" -•\t")
    return guess or None


def propose(
    text: str, products: Sequence[Product], provinces: Sequence[Province]
) -> ExtractionProposal:
    """Propone valores candidatos a partir del texto crudo de un anuncio.

    Reglas y diccionarios estáticos, no ML/LLM (whitepaper §6.1, línea 611):
    la propuesta es un punto de partida para que el operador confirme o
    corrija en la cola de curaduría, no un resultado final.
    """
    quantity, unit = _extract_quantity(text)
    product = _match_product(text, products)
    return ExtractionProposal(
        product=product,
        product_name_guess=_guess_product_phrase(text) if product is None else None,
        price=_extract_price(text),
        currency=_extract_currency(text),
        quantity=quantity,
        unit=unit,
        province=_match_province(text, provinces),
    )


def propose_all(
    text: str, products: Sequence[Product], provinces: Sequence[Province]
) -> list[ExtractionProposal]:
    """Una propuesta por línea, para anuncios que ofrecen varios productos a la vez.

    Patrón real observado (venta mayorista/TCP): cada oferta en su propio
    renglón, con su propio precio. `propose()` solo ve una propuesta por
    anuncio porque opera sobre el texto entero; esto lo hace línea por línea
    y descarta las que no aportan ni producto ni precio.

    Provincia y moneda caen al texto completo cuando la línea puntual no trae
    marca propia — son datos que suelen valer para todo el anuncio, no para
    un renglón en particular (ver `_extract_currency_explicit`).
    """
    whole_text_province = _match_province(text, provinces)
    whole_text_currency = _extract_currency_explicit(text)

    proposals: list[ExtractionProposal] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        price = _extract_price(line)
        product = _match_product(line, products)
        guess = _guess_product_phrase(line) if product is None else None

        if product is None and (price is None or guess is None):
            # Sin producto identificado: si además no hay precio, la línea no
            # aporta nada (texto de relleno, encabezado, etc.). Si hay precio
            # pero no hay ni siquiera un nombre candidato para revisar (el
            # precio matcheó pegado al principio de la línea — una fecha mal
            # leída como "a 8/4/2026", o el nombre del producto vive en otra
            # línea del mismo anuncio), la tarjeta quedaría con precio pero
            # cero pista de qué es: peor que una fila en blanco, porque
            # aparenta que el motor entendió algo. Se descarta.
            continue

        quantity, unit = _extract_quantity(line)
        currency = (
            _extract_currency_explicit(line) or whole_text_currency or Currency.CUP.value
        )
        province = _match_province(line, provinces) or whole_text_province

        proposals.append(
            ExtractionProposal(
                product=product,
                product_name_guess=guess,
                price=price,
                currency=currency,
                quantity=quantity,
                unit=unit,
                province=province,
            )
        )
    return proposals
