"""Motor de reglas de extracción sobre texto crudo de anuncios.

La especificación (§6.1, «Rol del OCR») describe esta etapa como «extracción
asistida»: el sistema propone valores candidatos por regex y diccionarios
estáticos —nada de ML/LLM en este slice (§6.1, «Tecnología»)— y el operador humano
confirma o corrige cada campo en la cola de curaduría. Este módulo solo
produce la propuesta; la confirmación es UI y no vive acá.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from saimc.models import Currency, Product, Province

# --- provincias --------------------------------------------------------------

# Las 16 provincias de Cuba son fijas y viven como fila de datos, no como
# código (spec.md §9.0). Pero el texto libre de un anuncio usa jerga y
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
# Cuarta pista, la más específica: precio en dos monedas para el mismo ítem
# ("43.5 o 30885" = 43.5 USD, o —al tipo de cambio propio del vendedor—
# 30885 CUP). Patrón real de los canales de La Habana que publican catálogo
# diario completo así, sin ningún otro marcador (Groupej Morales Surl,
# verificado 2026-08-17 contra 96 mensajes reales del canal). Los DOS
# números se capturan porque el orden NO es confiable: el mismo canal, mismo
# producto, escribe unos días "USD o CUP" y otros "CUP o USD" ("30885 o
# 43.5" — CUP primero, visto en un mensaje real distinto del mismo canal).
# `_extract_price` se queda con el mayor de los dos, no con una posición fija
# — ver esa función para el motivo (la escala, no el orden, es lo que
# distingue USD de CUP en este mercado). El separador "o"/"O" tolera el
# punto suelto que a veces queda pegado al número de la izquierda ("0,40. O
# 280") porque `_normalize_number` ya sabe recortarlo (`rstrip(".,")`).
_DUAL_PRICE_TWO_CURRENCIES_RE = re.compile(r"(\d[\d.,]*)\s*[oO]\s*(\d[\d.,]*)")

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
    # Derivados industriales. Los de arriba son cortes del mismo animal; estos
    # son otro producto que solo nombra al bien fresco como sabor. La distancia
    # de precio es de otro orden: un caldito de pollo vale ~50 CUP la unidad y
    # un pollo ~950 CUP la libra, así que un solo caldito mal atribuido corre
    # la mediana de la celda entera.
    "caldito",
    "calditos",
    "cubito",
    "cubitos",
    "consome",
    "sopa",
    "sopas",
    "sazon",
    "pastilla",
    "pastillas",
    "pastillita",
    "pastillitas",
    "saborizante",
    # Artículos de higiene y puericultura. No son ni un corte ni un derivado
    # del bien: son otro producto que comparte la palabra. Caso real que llegó
    # a la base: "Aceite para bebé 100 ML" se cargó como el producto "Aceite",
    # que es aceite de cocina y producto piloto.
    "bebe",
)
_DISQUALIFYING_MODIFIER_RE = re.compile(
    r"\b(" + "|".join(_PRODUCT_DISQUALIFYING_MODIFIERS) + r")\b", re.IGNORECASE
)


# --- canal origen -------------------------------------------------------------

# Patrón habitual de redes tipo Facebook/Marketplace: "Nombre - Ayer a las
# H:MM" / "Nombre - D de mes a las H:MM" (docs/backlog.md). "a las?" tolera
# el "a la" (singular) que a veces deja el OCR.
_SOURCE_REFERENCE_RE = re.compile(
    r"^(?P<name>.+?)\s*-\s*"
    r"(?P<when>ayer|hoy|\d{1,2}\s+de\s+[a-záéíóúñ]+)"
    r"\s+a\s+las?\s+\d{1,2}:\d{2}",
    re.IGNORECASE,
)

# Cuántas líneas iniciales se revisan buscando el patrón. Contra los
# anuncios reales capturados hasta ahora (docs/decisions.md, 2026-08-08) el
# patrón aparece siempre en la línea 1 (índice 1): la línea 0 suele ser el
# nombre de la página/grupo o ruido de OCR, no de quien publica. 5 da margen
# sin arriesgar matchear un " - " suelto en medio del cuerpo del anuncio.
_SOURCE_REFERENCE_SEARCH_LINES = 5


def _match_source_line(text: str) -> re.Match[str] | None:
    """Escanea las primeras líneas del anuncio buscando `_SOURCE_REFERENCE_RE`.

    Compartido por `guess_source_reference` y `guess_published_date`: los dos
    leen el mismo patrón de fecha de redes, uno para el nombre (`name`) y el
    otro para el token de fecha (`when`) — un solo escaneo, no dos regex.
    """
    lines = text.splitlines()[:_SOURCE_REFERENCE_SEARCH_LINES] if text else []
    for line in lines:
        match = _SOURCE_REFERENCE_RE.match(line.strip())
        if match is not None:
            return match
    return None


def guess_source_reference(text: str) -> str | None:
    """Adivina quién publicó el anuncio a partir del patrón de fecha de redes.

    Heurística de recorte, no NLP — sirve para prellenar `source_reference`
    en la cola de curaduría; el operador la revisa y corrige (`source_reference`
    sigue siendo 100% manual en cuanto a lo que termina guardado).
    """
    match = _match_source_line(text)
    if match is None:
        return None
    name = match.group("name").strip(" -•\t")
    return name or None


# Nombres de mes sin tilde (el texto de OCR/tipeo tampoco suele traerla, ver
# `_strip_accents`) → número de mes. Solo los que puede producir el patrón
# `\d{1,2}\s+de\s+[a-záéíóúñ]+`; cualquier otra palabra en esa posición
# ("trueno") no es un mes real y `guess_published_date` debe degradar a
# `None`, no inventar una fecha.
_MONTHS: dict[str, int] = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}


def guess_published_date(text: str, captured_at: datetime) -> date | None:
    """Adivina el día de publicación del anuncio a partir del mismo patrón de
    fecha de redes que usa `guess_source_reference` (design.md, D3/D4).

    Heurística de sugerencia, no un valor confirmado — la cola de curaduría
    la prellena y el operador la revisa o corrige, igual que
    `guess_source_reference`/`product_name_guess`. Nunca devuelve una fecha
    posterior a `captured_at.date()`: un token relativo ("ayer", "D de mes")
    solo tiene sentido mirando hacia el pasado desde el momento de la
    captura.
    """
    match = _match_source_line(text)
    if match is None:
        return None

    when = match.group("when").lower()
    captured_date = captured_at.date()

    if when == "hoy":
        return captured_date
    if when == "ayer":
        return captured_date - timedelta(days=1)

    day_month = re.match(r"(\d{1,2})\s+de\s+([a-záéíóúñ]+)", when, re.IGNORECASE)
    if day_month is None:
        return None
    month = _MONTHS.get(_strip_accents(day_month.group(2).lower()))
    if month is None:
        return None
    try:
        resolved = date(captured_date.year, month, int(day_month.group(1)))
    except ValueError:
        # Día inválido para ese mes ("31 de febrero"): no hay fecha que adivinar.
        return None

    if resolved > captured_date:
        # Post de diciembre capturado en enero: con el año de `captured_at`
        # la fecha cae en el futuro — es del año anterior.
        resolved = resolved.replace(year=resolved.year - 1)
    return resolved


@dataclass
class ExtractionProposal:
    """Candidatos propuestos para un anuncio.

    Ningún campo se da por confirmado: la cola de curaduría es quien decide
    (spec.md §6.1). `currency` es la excepción: nunca queda sin proponer,
    porque la especificación manda inferir CUP cuando no hay marca explícita.
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
    # El precio en dos monedas gana con prioridad, pero solo cuando la línea
    # no trae ya una marca de moneda explícita ("25 Usd o 17175"): ahí el
    # número de la izquierda y su moneda ya son una pareja consistente por
    # `_PRICE_BEFORE_CURRENCY_RE` más abajo, y forzar el lado CUP sin también
    # forzar la moneda dejaría precio y moneda contradiciéndose entre sí.
    # Ese caso queda deliberadamente fuera de este slice (docs/decisions.md,
    # 2026-08-17).
    dual_match = _DUAL_PRICE_TWO_CURRENCIES_RE.search(text)
    if dual_match is not None and _extract_currency_explicit(text) is None:
        lado_a = _normalize_number(dual_match.group(1))
        lado_b = _normalize_number(dual_match.group(2))
        # El mayor de los dos es el CUP, sin importar de qué lado del "o"
        # cayó: la brecha de escala entre USD y CUP en este mercado (~700:1,
        # medida contra el propio canal) es varios órdenes de magnitud más
        # ancha que cualquier variación real de precio entre dos vendedores.
        # Asumir una posición fija ("el de la derecha") se probó falso contra
        # datos reales del mismo canal: unos días escribe "USD o CUP" y otros
        # "CUP o USD" para el mismo producto (ver comentario del regex).
        candidatos = [v for v in (lado_a, lado_b) if v is not None]
        if candidatos:
            return max(candidatos)

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
    # Sin marca explícita: la especificación (§6.1, Etapa 2: «moneda: CUP (inferida)») manda CUP.
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
    # El guion bajo es un carácter de palabra para `\w`, así que un hashtag
    # como "#Santiago_de_Cuba" es una sola palabra larga para regex y nunca
    # calza contra el alias "santiago de cuba", que lleva espacios. Se
    # normaliza a espacio antes de matchear (caso real: canal comercial de
    # Santiago que abre cada anuncio con ese hashtag).
    normalized = _strip_accents(text.lower()).replace("_", " ")
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
    # Sin tilde: el texto de OCR/tipeo no siempre las trae, y cada variante
    # sin normalizar terminaba dando de alta un producto duplicado (ver
    # docs/decisions.md, limpieza de duplicados 2026-08-08).
    text_normalized = _strip_accents(text.lower())
    has_modifier = bool(_DISQUALIFYING_MODIFIER_RE.search(text_normalized))
    for product in products:
        for name in (product.name, *product.aliases):
            name_normalized = _strip_accents(name.lower())
            if name_normalized not in text_normalized:
                continue
            if has_modifier and not _DISQUALIFYING_MODIFIER_RE.search(name_normalized):
                # Hay un corte/parte en el texto ("hígado de pollo") pero el
                # nombre que matcheó ("pollo") no lo incluye: es un match
                # genérico de un producto más específico, no confiamos en él.
                continue
            return product
    return None


def mentions_any_product(text: str, products: Sequence[Product]) -> bool:
    """¿Algún renglón de este anuncio habla de un producto de `products`?

    Señal barata de relevancia para ordenar la cola de curaduría por foco
    (`docs/decisions.md`, 2026-08-18): responde la pregunta sin construir la
    propuesta completa —sin precio, cantidad, moneda ni provincia—, que es lo
    único que hace falta para decidir qué anuncio mirar primero.

    Línea por línea, igual que `propose_all`, y no sobre el texto entero: el
    `has_modifier` de `_match_product` se calcula sobre lo que recibe, así que
    un "hígado de pollo" en un renglón descartaría también el arroz limpio del
    renglón siguiente. Mirar cada línea por separado es exactamente lo que la
    cola termina mostrando, así que la señal no puede prometer más ni menos
    que eso.
    """
    return any(_match_product(line, products) is not None for line in text.splitlines())


# Viñeta de lista ("4)", "4.", o un número suelto) que el OCR deja pegada
# al principio de una oferta ("4) Azúcar blanca a 22500"). Sin recortarla,
# terminaba siendo parte del nombre del producto nuevo dado de alta —causa
# real de duplicados en la base, ver docs/decisions.md.
_LIST_MARKER_RE = re.compile(r"^\d{1,2}[).:]?\s+")


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
    guess = _LIST_MARKER_RE.sub("", guess).strip(" -•\t")
    return guess or None


def propose(
    text: str, products: Sequence[Product], provinces: Sequence[Province]
) -> ExtractionProposal:
    """Propone valores candidatos a partir del texto crudo de un anuncio.

    Reglas y diccionarios estáticos, no ML/LLM (spec.md §6.1, «Tecnología»):
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
