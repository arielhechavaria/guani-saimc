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
from saimc.units import PACKAGING, canonical_unit

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
# El marcador puede venir PEGADO al número ("90.00cup", "4.45USD", "30100mn"):
# 29 de los 132 anuncios curados al 2026-08-21 lo escriben así. Por eso el
# borde izquierdo no es `\b` sino "no venir precedido de una letra": entre el
# "5" de "4.45" y la "U" de "USD" no hay frontera de palabra, y con `\b` este
# regex no veía una moneda que `_PRICE_BEFORE_CURRENCY_RE` —cuyo `\s*` admite
# cero espacios— sí usaba para ubicar el precio. Los dos regex discrepaban
# sobre el mismo texto y la moneda caía al default CUP: un error de ~700x.
# El lookbehind sigue excluyendo el marcador incrustado en una palabra
# ("column" termina en "mn"), que es lo que `\b` protegía.
_CURRENCY_RE = re.compile(
    r"(?<![a-záéíóúñ])(" + "|".join(_CURRENCY_MARKERS) + r")\b", re.IGNORECASE
)

# --- precio y cantidad --------------------------------------------------------

# Un número de precio, con el espacio admitido como separador de miles.
#
# Los canales del operador escriben "7 000 CUP", "27 400 CUP", "24 000"
# constantemente. Sin los grupos de tres detrás del espacio, el patrón viejo
# —`(\d[\d.,]*)`— enganchaba solo el pedazo pegado a la marca de moneda: "7 000
# CUP" daba `Decimal("000")` = **0**, y "27 400 CUP" daba 400. Un precio de cero
# es peor que ninguno: el operador ve un número plausible en vez de un campo
# vacío que le pide atención.
#
# El grupo tiene que ser de EXACTAMENTE tres dígitos (`\d{3}(?!\d)`): "30 8850"
# no es treinta mil ochocientos cincuenta, son dos números distintos. Se admite
# el espacio duro (U+00A0) porque WhatsApp y el OCR lo emiten indistinguible del
# normal.
#
# Esto vale solo para los patrones ANCLADOS a una marca de precio (moneda, "$",
# "a "), donde el espacio es inequívocamente separador de miles. El número
# pelado (`_BARE_NUMBER_RE`) queda deliberadamente afuera: sin ancla, "Km 34
# 500" es ambiguo y pegar los grupos inventaría precios (2026-09-09).
_PRICE_NUMBER = r"\d[\d.,]*(?:[  ]\d{3}(?!\d))*"

# La palabra "a" antes de un número casi siempre precede al precio en este
# mercado ("a 12000"); se intenta primero por ser la pista más fuerte.
_PRICE_AFTER_A_RE = re.compile(r"\ba\s+(" + _PRICE_NUMBER + r")", re.IGNORECASE)
# Segunda pista: un número pegado a una marca de moneda también es precio.
_PRICE_BEFORE_CURRENCY_RE = re.compile(
    r"(" + _PRICE_NUMBER + r")\s*(" + "|".join(_CURRENCY_MARKERS) + r")\b",
    re.IGNORECASE,
)
# Tercera pista: el símbolo "$" también marca precio, muy común en capturas de
# tiendas informales. No implica moneda USD —en el mercado cubano "$" suele
# usarse para CUP también— por eso esto solo ubica el número, la moneda la
# sigue decidiendo `_extract_currency` por separado.
_PRICE_DOLLAR_RE = re.compile(r"\$\s*(" + _PRICE_NUMBER + r")")
# El mismo símbolo escrito DESPUÉS del número ("850$", "21900$"), forma
# habitual de las publicaciones de Facebook/Marketplace que el operador
# captura como imagen. `_PRICE_DOLLAR_RE` solo mira adelante, así que estas
# líneas quedaban sin precio aunque el OCR hubiera transcripto el número
# perfectamente (docs/decisions.md, 2026-08-31).
_PRICE_DOLLAR_SUFFIX_RE = re.compile(r"(" + _PRICE_NUMBER + r")\s*\$")
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
#
# El segundo número no puede ir seguido de una palabra: «3 o 4 lb», «2 o 3
# personas» son alternativas de cantidad, no un precio doble. `(?![\d.,])`
# impide que el backtracking recorte «30 personas» a «3». Solo espacio
# horizontal: un salto de línea separa renglones, no califica al número.
_DUAL_PRICE_TWO_CURRENCIES_RE = re.compile(
    r"(" + _PRICE_NUMBER + r")\s*[oO]\s*(" + _PRICE_NUMBER + r")"
    r"(?![\d.,]|[ \t ]*[^\W\d_])"
)

# Unidades conocidas para cantidad: sólo las que `units.canonical_unit()` sabe
# resolver. Reconocer una que no canonice sería peor que no reconocerla, porque
# dejaría una magnitud huérfana —cantidad con `unit=None`— después de que
# `curation.py` canonice la salida. `ml` y `l` entran acá porque ya son
# canónicas en `units.UNITS`: es el "cambio aparte" que anticipaba el docstring
# de ese módulo.
_UNIT_WORDS = (
    "lb",
    "kg",
    "cajas",
    "caja",
    "docena",
    "litros",
    "litro",
    "ml",
    "l",
    "unidad",
)

# Magnitudes que el mercado escribe pero `units.UNITS` no almacena ("500 g",
# "12 u", "250 cc"). NO entran en `_QUANTITY_RE` por el motivo de arriba, pero
# sí tienen que impedir que su número se lea como precio: ése es el otro rol
# que la tupla única cumplía sin decirlo, y confundirlos es lo que hacía que
# "Vinagre 500 ml ➔ 220" propusiera 500.
_NON_CANONICAL_MAGNITUDE_WORDS = ("gr", "g", "oz", "cc", "lt", "unidades", "uds", "u")


def _alternation(words: Sequence[str]) -> str:
    """Alternancia de regex con las formas largas primero.

    El backtracking ya resolvería "lb" contra una alternancia que empiece por
    "l", pero ordenar explícitamente evita depender de eso al agregar formas
    que son prefijo de otras ("l"/"lb", "g"/"gr", "u"/"uds").
    """
    return "|".join(sorted(words, key=len, reverse=True))


_QUANTITY_RE = re.compile(
    r"(\d[\d.,]*)\s*(" + _alternation(_UNIT_WORDS) + r")\b", re.IGNORECASE
)

# "x libra", "por libra", "la libra", "/ lb" (y las mismas con kilo): el anuncio
# cotiza el precio por unidad de peso sin decir cuántas. El operador cura esas
# filas como `quantity=1, unit=lb` — medido contra la cola el 2026-09-03, 4
# filas así ("Azúcar x libra (470)", "Picadillo de pollo x libra (580)").
_PER_WEIGHT_UNIT_RE = re.compile(
    r"(?:\bx\s*|\bpor\s+|\bla\s+|/\s*)(libra|lb|kilo|kg)s?\b", re.IGNORECASE
)
# Si la línea nombra un envase de bulto, ese "por libra" es el precio derivado
# de un saco/paca/caja, no la unidad de venta al menudeo: no se propone (1, lb).
_BULK_WORD_RE = re.compile(
    r"\b(caja|cajas|cajon|cajones|saco|sacos|paca|pacas|paquete|paquetes|contenedor)\b",
    re.IGNORECASE,
)

# --- número pelado, último recurso ----------------------------------------
#
# Un número sin ninguna marca ("Papas prefritas 1kg 2500", "Picadillo x libras
# 550"). Hasta el 2026-08-31 se descartaba a propósito por ambiguo; se propone
# por decisión explícita del operador, que prefiere revisarlo a que el motor se
# abstenga (docs/decisions.md). Sigue siendo el ÚLTIMO tramo de
# `_extract_price`: cualquier pista real le gana.
#
# Los bordes `(?<![\d.,:+])` / `(?![\d.,:])` son las guardas que lo hacen
# usable sobre texto de OCR: sin ellas la hora de la captura ("7:00") y el
# teléfono del anunciante ("+53 5 4727826") entran como precio.
_BARE_NUMBER_RE = re.compile(r"(?<![\d.,:+])(\d[\d.,]*)(?![\d.,:])")

# Más de 6 dígitos es un teléfono, no un precio de este mercado.
_BARE_NUMBER_MAX_DIGITS = 6

# Un número pegado a su unidad es una MAGNITUD, no un precio: dimensiona el
# envase ("500 ml", "12 u") o el lote ("40 lb"). La guarda cubre más formas que
# `_QUANTITY_RE` a propósito — acá alcanza con reconocer que el número está
# tomado, aunque la unidad no sea almacenable.
#
# Sin esto el daño no era que faltara la cantidad sino que sobraba un precio:
# `_bare_price_candidate` se queda con el número MAYOR de la línea, así que en
# "Vinagre de claro 500 ml ➔ 220" el volumen le ganaba al precio (medido contra
# la cola el 2026-09-07; docs/decisions.md).
_BARE_NUMBER_UNIT_TAIL_RE = re.compile(
    r"(" + _alternation(_UNIT_WORDS + _NON_CANONICAL_MAGNITUDE_WORDS) + r")\b",
    re.IGNORECASE,
)


def _bare_price_candidate(text: str) -> Decimal | None:
    """El mayor número suelto que puede ser un precio, o `None`.

    El mayor y no el primero: en estas capturas el precio convive con
    cantidades chicas ("1kg 2500", "5 unidade: 850") y es el número grande.
    Se midió también "el último de la línea" contra el corpus real y da
    exactamente el mismo resultado —en estos anuncios el precio es a la vez
    el mayor y el último—, así que se elige el criterio que no depende de
    dónde corte el renglón el OCR.
    """
    mejor: Decimal | None = None
    for match in _BARE_NUMBER_RE.finditer(text):
        crudo = match.group(1)
        if len(re.sub(r"\D", "", crudo)) > _BARE_NUMBER_MAX_DIGITS:
            continue
        cola = text[match.end(1) : match.end(1) + 12].lstrip()
        if _BARE_NUMBER_UNIT_TAIL_RE.match(cola):
            continue
        valor = _normalize_number(crudo)
        if valor is None or valor <= 0:
            continue
        if mejor is None or valor > mejor:
            mejor = valor
    return mejor

# --- precio escalonado ---------------------------------------------------

# Palabras que cuentan BULTOS, no magnitudes: "10 cajas" no dice cuánto pesa
# nada hasta saber qué trae una caja. Se separan del resto de `_UNIT_WORDS`
# porque el mínimo de compra tiene que quedar expresado en la unidad canónica
# del lote — es el mismo motivo por el que `prices_history` lo documenta así
# («un mínimo dicho en pacas no sirve»).
_LOT_WORDS = frozenset({"caja", "cajas", "saco", "sacos", "paquete", "paquetes"})

# Un escalón de precio por volumen: "Más de 10 cajas 28", "A partir de 100
# cajas a 27,5", "Desde 500 lb 480".
#
# El precio final puede venir PELADO, sin "a" ni marca de moneda, y por eso
# `_extract_price` no lo ve cuando mira la línea aislada: un número suelto es
# ambiguo en general y descartarlo es lo correcto. Deja de serlo dentro de un
# escalón, donde la frase "más de N" ya declaró qué es cada número. Ese
# contexto es justamente lo que la lectura línea-por-línea no tiene.
_TIER_RE = re.compile(
    r"\b(?:m[áa]s\s+de|a\s+partir\s+de|desde)\s+(\d[\d.,]*)\s*([a-záéíóúñ]+)?"
    r"[^\d]*?(\d[\d.,]*)[.\s]*$",
    re.IGNORECASE,
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
    # Forma singular canónica: el plural ("alas", "alitas" — como se anuncia
    # el corte de verdad, 2026-08-20) lo cubre `_plural_pattern()`
    # automáticamente, sin listarlo a mano — ver esa función más abajo.
    "ala",
    "alita",
    "corazon",
    "molida",
    "falda",
    "lomo",
    "costilla",
    "contramuslo",
    "rabadilla",
    "menudencia",
    # Derivados industriales. Los de arriba son cortes del mismo animal; estos
    # son otro producto que solo nombra al bien fresco como sabor. La distancia
    # de precio es de otro orden: un caldito de pollo vale ~50 CUP la unidad y
    # un pollo ~950 CUP la libra, así que un solo caldito mal atribuido corre
    # la mediana de la celda entera.
    "caldito",
    "cubito",
    "consome",
    "sopa",
    "sazon",
    "pastilla",
    "pastillita",
    "saborizante",
    # Artículos de higiene y puericultura. No son ni un corte ni un derivado
    # del bien: son otro producto que comparte la palabra. Caso real que llegó
    # a la base: "Aceite para bebé 100 ML" se cargó como el producto "Aceite",
    # que es aceite de cocina y producto piloto.
    "bebe",
)


def _plural_pattern(word: str) -> str:
    """Patrón que acepta `word` en singular o en plural español.

    Regla simple —vocal final suma "s" ("pechuga" -> "pechugas"), consonante
    final suma "es" ("corazon" -> "corazones")— suficiente para el
    vocabulario cerrado de este módulo: se verificó a mano que reproduce el
    plural real de cada palabra de `_PRODUCT_DISQUALIFYING_MODIFIERS` y de
    cada alias de producto en uso (docs/decisions.md, 2026-08-21). No cubre
    excepciones fonéticas (z -> c, tildes que se pierden al pluralizar)
    porque ninguna palabra de ese vocabulario las tiene — una que sí las
    tenga exige revisar esta función, no forzarla con más casos especiales.

    Antes de esto, cada modificador que alguien anunciaba en plural
    ("higados", "pechugas") no coincidía con su forma singular en la lista y
    colaba como el producto genérico — el mismo motivo por el que
    "ala"/"alas" y "alita"/"alitas" se venían listando dos veces a mano.
    """
    escaped = re.escape(word)
    return escaped + ("s?" if word[-1] in "aeiou" else "(?:es)?")


_DISQUALIFYING_MODIFIER_RE = re.compile(
    r"\b(" + "|".join(_plural_pattern(m) for m in _PRODUCT_DISQUALIFYING_MODIFIERS) + r")\b",
    re.IGNORECASE,
)

# ¿Esta línea nombra un envase? Es lo que distingue una línea de FORMATO de un
# renglón cualquiera que traiga números: "Caja de 40 lb: 27 400 CUP" declara un
# formato; "$23,500.Sale 427.27Lb" (ruido de OCR) y "Calle 20 entre 100 y 101"
# (una dirección) no, aunque los tres tengan cifras que el motor lee como precio
# y hasta como cantidad.
#
# Se construye del mismo vocabulario cerrado que usa el resto del sistema
# (`units.PACKAGING`), no de una lista paralela: un envase nuevo entra ahí una
# vez y esta regla lo reconoce sola. El plural sale de `_plural_pattern`, igual
# que en los modificadores y los alias de producto.
_PACKAGING_WORD_RE = re.compile(
    r"\b("
    + "|".join(
        r"\s+".join(_plural_pattern(palabra) for palabra in envase.split())
        for envase in PACKAGING
    )
    + r")\b",
    re.IGNORECASE,
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
    # El año de la captura y, si ahí cae en el futuro (post de diciembre
    # capturado en enero), el anterior. Un año donde la fecha no existe se
    # salta: "29 de febrero" puede existir en uno y no en el otro, y "31 de
    # febrero" en ninguno.
    day = int(day_month.group(1))
    for year in (captured_date.year, captured_date.year - 1):
        try:
            resolved = date(year, month, day)
        except ValueError:
            continue
        if resolved <= captured_date:
            return resolved
    return None


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
    # Solo lo llena la lectura de bloque de `propose_all` (ver
    # `_tiered_proposals`), y siempre en la misma unidad que `unit`, igual que
    # la columna homónima de `prices_history`. Nunca se adivina: un escalón
    # cuyo mínimo no se puede expresar en esa unidad no se propone.
    min_purchase_quantity: Decimal | None = None
    # `"unit"` / `"total"`, y solo cuando el anuncio lo DECLARA y la aritmética
    # lo confirma (ver `_declared_price_basis`). Apartamiento consciente de D4
    # del design de `price_basis`, que fijaba no preproponerlo nunca por ser
    # juicio del operador: el motivo está en `docs/decisions.md`, 2026-08-25.
    # Sigue siendo una propuesta — la cola la muestra preseleccionada y el
    # operador confirma o corrige, igual que con todo lo demás (§6.1).
    price_basis: str | None = None
    # `"retail"` / `"wholesale"` y un envase de `saimc.units.PACKAGING`. Las
    # reglas NUNCA los llenan (ver `_tiered_proposals`): solo los propone el
    # modelo (`llm_propose_ad`, slice 3d, docs/decisions.md 2026-09-25). Viven
    # acá y no en `RowMeta` porque son valores de la fila, igual que el resto,
    # y el formulario, el guardado de la propuesta y el backtest ya consumen
    # la fila como `ExtractionProposal`.
    sale_format: str | None = None
    packaging: str | None = None


#: Pistas del tipo «Sale 515 Cup Lb» / «Sale $281.82 Lb» / «Sale $1080 Unidad»:
#: el anunciante escribe, además del precio de la oferta, a cuánto le sale al
#: comprador cada unidad. Medido el 2026-08-25 sobre los 150 anuncios de texto
#: curados: 18 lo dicen con esta forma.
#:
#: `cup` opcional en el medio porque aparece de las dos maneras en el mismo
#: canal. La unidad se canonicaliza después, no acá: este patrón la captura
#: cruda y `canonical_unit` decide si es una que el catálogo conoce.
_DECLARED_UNIT_PRICE_RE = re.compile(
    r"\bsale\s*\$?\s*([\d.,]+)\s*(?:cup\s*)?([a-záéíóúñ]+)",
    re.IGNORECASE,
)

#: Tolerancia relativa al comparar el precio declarado contra las dos lecturas.
#: No es un umbral de los que §12.6 prohíbe —no decide si un dato es válido ni
#: si se publica—: absorbe el redondeo del propio anunciante, que escribe
#: «281.82» donde la división exacta da 281,8181… El 1% es holgado para eso y
#: sigue siendo dos órdenes de magnitud menor que la distancia entre las dos
#: lecturas posibles (que difieren por el factor `quantity`, nunca menor a 2
#: cuando esta regla corre).
_DECLARED_PRICE_TOLERANCE = Decimal("0.01")

#: Envases que nombran un lote cotizado como tal. Sale «a granel» del
#: vocabulario de `saimc.units.PACKAGING`: nombra la ausencia de envase, y su
#: precio es justamente el unitario. Se deriva del vocabulario en vez de
#: repetirlo para que un envase nuevo entre acá solo.
_LOTE_PACKAGING = "|".join(envase for envase in PACKAGING if envase != "a granel")

#: «cajas de 40lb», «el saco a 500», «24800 la caja»: el envase nombrado como
#: la cosa que se cotiza. Las dos formas son las que aparecen en los anuncios
#: reales — el envase antes de la cantidad, o el artículo junto al precio.
_PACKAGING_MARKER_RE = re.compile(
    rf"\b(?:(?:{_LOTE_PACKAGING})s?\s+de\b"
    rf"|(?:el|la|los|las)\s+(?:{_LOTE_PACKAGING})s?\b)",
    re.IGNORECASE,
)

#: «la libra», «x lb», «cada unidad»: el anuncio declara que el precio es
#: unitario. Veta la lectura de lote aunque el envase esté nombrado en la misma
#: línea — el caso real de «Paca de 30 paquetes, 21900 la libra». El artículo o
#: la preposición son obligatorios: «40lb» es una cantidad, no una declaración.
_UNIT_MARKER_RE = re.compile(
    r"\b(?:la|el|los|las|por|x|cada)\s+"
    r"(?:libras?|lbs?|kilos?|kg|unidades?|litros?|ml|docenas?)\b",
    re.IGNORECASE,
)


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
    # El espacio como separador de miles ("7 000", "27 400"), incluido el duro
    # (U+00A0). Se saca antes de mirar el punto y la coma porque los patrones de
    # precio solo capturan grupos de tres bien formados: lo que llega acá con
    # espacios ya está validado como un único número (2026-09-09).
    cleaned = cleaned.replace(" ", "").replace(" ", "")
    if re.fullmatch(r"\d{1,3}(\.\d{3})+", cleaned):
        cleaned = cleaned.replace(".", "")
    elif re.fullmatch(r"\d{1,3}(,\d{3})+", cleaned):
        cleaned = cleaned.replace(",", "")
    elif re.fullmatch(r"\d{1,3}(,\d{3})+\.\d+", cleaned):
        # Los dos separadores ("25,750.00"): el de la derecha es el decimal.
        cleaned = cleaned.replace(",", "")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+,\d+", cleaned):
        cleaned = cleaned.replace(".", "").replace(",", ".")
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
        # Marca de moneda escrita después del número: sigue siendo una pista
        # explícita, así que va antes que el número pelado.
        or _PRICE_DOLLAR_SUFFIX_RE.search(text)
    )
    if match is not None:
        return _normalize_number(match.group(1))
    # Sin ninguna pista: el número pelado, que el operador revisa.
    return _bare_price_candidate(text)


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
    if match is not None:
        return _normalize_number(match.group(1)), match.group(2).lower()

    # Sin "N unidad" explícito: ¿cotiza "por libra" / "por kilo"? Se asume una
    # unidad, que es lo que confirma el operador. La guarda de envase evita
    # aplicarlo a un anuncio de saco donde "la libra" es el precio unitario
    # derivado del bulto, no la unidad de venta.
    per_weight = _PER_WEIGHT_UNIT_RE.search(text)
    if per_weight is not None and _BULK_WORD_RE.search(_strip_accents(text)) is None:
        word = per_weight.group(1).lower()
        return Decimal("1"), "kg" if word in ("kilo", "kg") else "lb"

    return None, None


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


def _name_pattern(name_normalized: str) -> re.Pattern[str]:
    """Patrón que reconoce `name_normalized` en el texto tolerando plural en
    cada palabra que lo compone ("saco de azucar" reconoce también "sacos de
    azúcar").

    Antes de esto la comparación era substring literal (`in`): un alias
    escrito en singular no reconocía la forma en que el vendedor realmente
    anuncia un mayorista, en plural — caso real encontrado en la cola de
    producción el 2026-08-21 ("Sacos de azúcar 25 kg..." quedaba oculto
    porque el alias es "saco de azucar"). `\\b` en los extremos, ausente
    antes, es necesario para que el sufijo plural opcional de cada palabra no
    se coma texto de la palabra siguiente.
    """
    palabras = name_normalized.split()
    cuerpo = r"\s+".join(_plural_pattern(palabra) for palabra in palabras)
    return re.compile(r"\b" + cuerpo + r"\b")


def _match_product(text: str, products: Sequence[Product]) -> Product | None:
    # Sin tilde: el texto de OCR/tipeo no siempre las trae, y cada variante
    # sin normalizar terminaba dando de alta un producto duplicado (ver
    # docs/decisions.md, limpieza de duplicados 2026-08-08).
    text_normalized = _strip_accents(text.lower())
    has_modifier = bool(_DISQUALIFYING_MODIFIER_RE.search(text_normalized))
    for product in products:
        for name in (product.name, *product.aliases):
            # Los alias son JSON sin validar editado a mano: uno vacío compila
            # a `\b\b` y matchea cualquier línea, uno nulo no tiene `.lower()`.
            if not isinstance(name, str) or not name.strip():
                continue
            name_normalized = _strip_accents(name.lower())
            if not _name_pattern(name_normalized).search(text_normalized):
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


# Sin producto del foco, "agotado" y venta mayorista — las tres formas de
# ruido que la medición contra 159 descartes reales de producción encontró
# (docs/decisions.md, 2026-08-22). Un fuzzy-match por similitud de texto se
# probó y se descartó: 0 grupos de texto casi-idéntico entre esos descartes,
# el ruido es estructural, no repetido palabra por palabra.
_OUT_OF_STOCK_RE = re.compile(r"agotad[oa]|⛔", re.IGNORECASE)

# "Caja" es unidad canónica válida (`saimc.units.UNITS`) y no dispara esta
# señal: solo las formas que en los anuncios reales medidos marcaban venta al
# por mayor, nunca consumo minorista.
_WHOLESALE_RE = re.compile(r"contenedor|mayorista|por mayor|cajones", re.IGNORECASE)


def probable_discard_reasons(text: str, products: Sequence[Product]) -> list[str]:
    """Señales de que este anuncio probablemente no aporta un precio útil.

    Sugerencia, no veredicto: la cola de curaduría la muestra como una marca
    que el operador confirma con un clic o ignora — nunca auto-descarta
    (spec.md §6.1: el operador siempre confirma o corrige). Devuelve una
    razón por señal disparada, en orden fijo, para que el badge sea legible;
    lista vacía cuando ninguna aplica.
    """
    reasons: list[str] = []
    if not mentions_any_product(text, products):
        reasons.append("no menciona ningún producto del foco")
    if _OUT_OF_STOCK_RE.search(text):
        reasons.append("parece indicar agotado")
    if _WHOLESALE_RE.search(text):
        reasons.append("parece venta mayorista/por volumen")
    return reasons


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
    # Las mismas pistas de precio EXPLÍCITAS que `_extract_price`: una que
    # falte acá deja una línea con precio y sin nombre, y `propose_all` la
    # descarta. El número suelto queda afuera a propósito: sin nombre, esa
    # línea sin producto se descarta, y es lo que frena a la dirección («Km
    # 34») y a la URL de ser ofertas (docs/decisions.md, 2026-09-28).
    markers = [
        match.start()
        for match in (
            _PRICE_AFTER_A_RE.search(line),
            _PRICE_BEFORE_CURRENCY_RE.search(line),
            _PRICE_DOLLAR_RE.search(line),
            _PRICE_DOLLAR_SUFFIX_RE.search(line),
            _DUAL_PRICE_TWO_CURRENCIES_RE.search(line),
            _QUANTITY_RE.search(line),
        )
        if match is not None
    ]
    if not markers:
        return None
    guess = line[: min(markers)].strip(" -•\t")
    guess = _LIST_MARKER_RE.sub("", guess).strip(" -•\t")
    return guess or None


def _declared_price_basis(
    text: str, *, price: Decimal | None, quantity: Decimal | None, unit: str | None
) -> str | None:
    """Lee «Sale X <unidad>» y decide qué cubre el precio, o `None`.

    La pregunta que resuelve —¿el precio es de una unidad o del lote entero?—
    NO se puede inferir de la cantidad. Está medido: «Picadillo de pollo
    americano 40lb a 525» tiene cantidad 40 y el precio es **por libra**,
    mientras «Cajas de Picadillo 50Lb $25,750» tiene cantidad 50 y el precio
    es **del lote**. Mismo producto, misma unidad, cantidades parecidas, y
    lecturas opuestas (docs/decisions.md, 2026-08-25).

    Lo que sí se puede leer es cuando el anuncio lo dice. Con el número
    declarado en la mano hay dos lecturas posibles del precio de la fila, y se
    elige la que coincide:

        precio / cantidad == declarado  → el precio cubre el lote  ("total")
        precio            == declarado  → el precio ya es unitario ("unit")

    Si no coincide ninguna, devuelve `None` **a propósito**: significa que el
    anuncio dice algo que esta regla no entiende, y proponer ahí sería
    exactamente el "adivinar" que la regla existe para evitar. Lo mismo si la
    pista viene en otra unidad que la fila: convertir acá mezclaría dos
    lecturas distintas y un error de cualquiera enmascararía al otro.
    """
    if price is None or quantity is None or quantity == 1 or unit is None:
        return None

    match = _DECLARED_UNIT_PRICE_RE.search(text)
    if match is None:
        return None

    # Las comas de millar se quitan antes: `_normalize_number` no las acepta
    # junto a un punto decimal, y el canal mayorista escribe «1,986.36».
    declared = _normalize_number(re.sub(r",(?=\d{3}\b)", "", match.group(1)))
    if declared is None or declared <= 0:
        return None

    if canonical_unit(match.group(2)) != unit:
        return None

    def coincide(candidate: Decimal) -> bool:
        return abs(candidate - declared) <= declared * _DECLARED_PRICE_TOLERANCE

    if coincide(price / quantity):
        return "total"
    if coincide(price):
        return "unit"
    return None


def _packaging_price_basis(
    text: str, *, price: Decimal | None, quantity: Decimal | None
) -> str | None:
    """«cajas de 40lb a 12000» → `'total'`: el precio es del lote.

    Segunda regla de `price_basis`, subordinada a `_declared_price_basis` y
    más laxa que ella. Nace del caso del 2026-09-05: «caja de pollo de 40 lb a
    24800» se curó como «por libra» y la caja terminó valiendo 992.000 CUP en
    la matriz. El anuncio lo decía —«la caja»—, pero ninguna regla leía ese
    marcador, y la aritmética no podía ayudar: ni 620 (24800/40) ni 992.000
    (24800×40) aparecen en el texto, así que el conjunto de números del anuncio
    es idéntico se declare `'unit'` o `'total'`. Solo queda la evidencia
    léxica.

    **Es un apartamiento de D4 mayor que el de `_declared_price_basis`**, y
    conviene tenerlo presente al juzgar un falso positivo: allá el número
    declarado verifica la lectura antes de proponer nada, acá no hay
    verificación aritmética posible. Se acepta porque el operador confirma cada
    fila en la cola —la propuesta nunca se persiste sola— y porque el silencio
    ya demostró costar más: nadie revisa la fila que nadie señala.

    **El marcador de unidad siempre gana**, y el de `_declared_price_basis`
    también aunque su aritmética no haya cerrado: si el anuncio habla de la
    libra, dice algo sobre el precio unitario que esta regla no puede
    contradecir. Es el caso real de «Paca de 30 paquetes, 21900 la libra»,
    donde el esqueleto es el mismo que el de arriba y la lectura es la opuesta.
    """
    if price is None or quantity is None or quantity == 1:
        return None
    if _UNIT_MARKER_RE.search(text) or _DECLARED_UNIT_PRICE_RE.search(text):
        return None
    return "total" if _PACKAGING_MARKER_RE.search(text) else None


def propose(
    text: str,
    products: Sequence[Product],
    provinces: Sequence[Province],
    *,
    channel_province: Province | None = None,
) -> ExtractionProposal:
    """Propone valores candidatos a partir del texto crudo de un anuncio.

    Reglas y diccionarios estáticos, no ML/LLM (spec.md §6.1, «Tecnología»):
    la propuesta es un punto de partida para que el operador confirme o
    corrija en la cola de curaduría, no un resultado final.

    `channel_province` es la provincia fija del canal que originó el anuncio,
    cuando lo originó un canal automatizado con esa provincia cargada. Pisa lo
    que el texto diga — "el canal siempre manda" (docs/decisions.md,
    2026-08-17) — porque el operador la conoce de antemano y un mensaje puede
    no mencionarla o mencionar otra por error. `None` es el caso normal de una
    fuente sin canal (bandeja, pegado) o de un canal sin provincia cargada, y
    ahí la provincia sigue saliendo del texto.
    """
    quantity, unit = _extract_quantity(text)
    product = _match_product(text, products)
    price = _extract_price(text)
    return ExtractionProposal(
        product=product,
        product_name_guess=_guess_product_phrase(text) if product is None else None,
        price=price,
        currency=_extract_currency(text),
        quantity=quantity,
        unit=unit,
        province=channel_province or _match_province(text, provinces),
        # La aritmética primero y el marcador de envase como repliegue: donde
        # el anuncio declara un precio por unidad y el número lo confirma, esa
        # lectura manda aunque el envase esté nombrado en la misma línea.
        price_basis=_declared_price_basis(text, price=price, quantity=quantity, unit=unit)
        or _packaging_price_basis(text, price=price, quantity=quantity),
    )


def _lot_size(lines: Sequence[str]) -> tuple[Decimal | None, str | None]:
    """Cuánto trae un bulto, según la primera línea que lo diga en unidad real.

    "CAJA DE 40 lb" dimensiona el lote; "Más de 10 cajas" no dice nada del
    tamaño. Por eso se ignoran las unidades de bulto (`_LOT_WORDS`): sirven
    para contar, no para medir.
    """
    for line in lines:
        quantity, unit = _extract_quantity(line)
        if quantity is not None and unit is not None and unit not in _LOT_WORDS:
            return quantity, unit
    return None, None


def _block_header(lines: Sequence[str]) -> str | None:
    """El renglón que nombra el producto en un anuncio de bloque.

    Es la primera línea sin precio y con letras: en esta forma de anuncio el
    encabezado ES el producto. Hace falta porque `_guess_product_phrase`
    adivina a partir del texto que precede a un precio, y el encabezado
    justamente no tiene ninguno — sin esto, un producto que todavía no está
    en el catálogo dejaría al bloque sin nada que mostrar.
    """
    for line in lines:
        if _extract_price(line) is not None:
            continue
        if any(char.isalpha() for char in line):
            return line
    return None


def _tier_minimum(
    count: Decimal, word: str | None, lot_quantity: Decimal | None, lot_unit: str | None
) -> Decimal | None:
    """El mínimo del escalón expresado en la unidad del lote, o `None`.

    `None` cuando la conversión no se puede hacer con lo que dice el anuncio:
    un mínimo inventado es peor que ausente, porque `min_purchase_quantity`
    se compara entre anuncios sin tabla de conversión que lo salve después.
    """
    if word is None:
        return None
    word = word.lower()
    if word in _LOT_WORDS:
        # "100 cajas" solo se vuelve una magnitud si el anuncio dijo antes
        # cuánto trae una caja.
        if lot_quantity is None:
            return None
        return count * lot_quantity
    if word in _UNIT_WORDS:
        # Ya viene medido. Si el lote se declaró en otra unidad, no se
        # convierte acá: mezclar kg con lb sin tabla es exactamente el error
        # que este retorno evita.
        if lot_unit is not None and word != lot_unit:
            return None
        return count
    return None


def _tiered_proposals(
    text: str,
    products: Sequence[Product],
    provinces: Sequence[Province],
    *,
    channel_province: Province | None = None,
) -> list[ExtractionProposal] | None:
    """Lee el anuncio como un BLOQUE cuando publica precio por volumen.

    Forma real medida el 2026-08-20 ("ALITAS DE POLLO / CAJA DE 40 lb / La
    caja 28,5 usd o 25650 cup. / Más de 10 cajas 28 / Más de 100 cajas
    27,5."): el producto lo declara el encabezado, el tamaño del lote otra
    línea, y las de precio heredan las dos. Ninguna línea sola está completa,
    así que `propose_all` línea-por-línea sacaba una fila con producto sin
    precio y otra que proponía dar de alta un producto llamado «La caja».

    `None` —y no una lista vacía— cuando el anuncio no tiene esta forma: es
    la señal de "acá no aplico, seguí con la lectura normal".

    Se proponen dos filas, no tres: el techo del anuncio y su mejor precio
    mayorista (decisión del operador, 2026-08-20). El escalón intermedio
    describe la misma oferta sin agregar un extremo.

    Lo que NO se prellena es tan deliberado como lo que sí: `sale_format` y
    `packaging` son juicio explícito del operador (design.md D4), y nada acá
    los deriva de texto suelto. Desde el slice 3d el modelo sí los propone
    (`llm_propose_ad`), siempre como propuesta que el operador confirma; las
    reglas siguen sin tocarlos (docs/decisions.md, 2026-09-25).
    """
    lines = [stripped for raw in text.splitlines() if (stripped := raw.strip())]

    tiers = []
    plain = []
    for line in lines:
        match = _TIER_RE.search(line)
        if match is not None:
            tiers.append(match)
        else:
            plain.append(line)
    if not tiers:
        return None

    # La herencia del encabezado solo es cierta si todo el anuncio habla de un
    # único producto. Con dos, atribuir el escalón a uno sería inventar.
    matched = [p for p in (_match_product(line, products) for line in lines) if p is not None]
    if any(p is not matched[0] for p in matched):
        return None

    # Sin match no se abandona la lectura de bloque: el anuncio sigue teniendo
    # esta forma y lo único que falta es el alta del producto. Se propone el
    # encabezado como nombre candidato, igual que hace la lectura por línea
    # con `product_name_guess`, y el operador decide.
    product = matched[0] if matched else None
    guess = _block_header(plain) if product is None else None
    if product is None and guess is None:
        return None

    con_precio = [line for line in plain if _extract_price(line) is not None]

    # El catálogo de tienda no es un anuncio escalonado, aunque traiga un
    # «Más de N» que active esta lectura.
    #
    # La premisa del bloque es que NINGUNA línea sola está completa: el
    # encabezado declara el producto y las de precio heredan (forma medida el
    # 2026-08-20, «ALITAS DE POLLO / CAJA DE 40 lb / La caja 28,5 usd o 25650»).
    # Cuando dos o más líneas con precio nombran el producto, esa premisa es
    # falsa: cada una es una oferta completa —un formato distinto del mismo
    # producto— y la lectura línea por línea las saca todas, mientras el bloque
    # colapsa el anuncio a dos filas y descarta el resto.
    #
    # Caso testigo (medido 2026-09-10): un catálogo con «Arroz grano dorado
    # 100lb. 43,5 o 30885» y «Arroz grano dorado 1 kg. 0,97 o 688,7» se leía
    # como bloque y proponía UNA fila con precio 34 —el kilómetro de
    # «📍 Tienda San José Carretera Central Km 34», que era la primera línea
    # con cifras—, sin leer nunca las dos que traían la respuesta. 30 de las 50
    # filas con precio equivocado del corpus salían de este camino.
    if product is not None:
        propias = [
            line for line in con_precio if _match_product(line, products) is product
        ]
        if len(propias) > 1:
            return None

    base_line = con_precio[0] if con_precio else None
    if base_line is None:
        return None
    base_price = _extract_price(base_line)

    currency = (
        _extract_currency_explicit(base_line)
        or _extract_currency_explicit(text)
        or Currency.CUP.value
    )
    lot_quantity, lot_unit = _lot_size(plain)
    province = channel_province or _match_province(text, provinces)

    def fila(price: Decimal | None, minimum: Decimal | None) -> ExtractionProposal:
        return ExtractionProposal(
            product=product,
            product_name_guess=guess,
            price=price,
            currency=currency,
            quantity=lot_quantity,
            unit=lot_unit,
            province=province,
            min_purchase_quantity=minimum,
        )

    candidatos: list[tuple[Decimal, Decimal]] = []
    for match in tiers:
        count = _normalize_number(match.group(1))
        price = _normalize_number(match.group(3))
        if count is None or price is None:
            continue
        minimum = _tier_minimum(count, match.group(2), lot_quantity, lot_unit)
        if minimum is None:
            # Un escalón sin mínimo expresable es una segunda fila con otro
            # precio y ninguna explicación de por qué difiere: peor que no
            # proponerla.
            continue
        candidatos.append((price, minimum))

    if not candidatos:
        return [fila(base_price, None)]

    mejor_precio, mejor_minimo = min(candidatos, key=lambda par: par[0])
    return [fila(base_price, None), fila(mejor_precio, mejor_minimo)]


def propose_all(
    text: str,
    products: Sequence[Product],
    provinces: Sequence[Province],
    *,
    channel_province: Province | None = None,
) -> list[ExtractionProposal]:
    """Una propuesta por línea, para anuncios que ofrecen varios productos a la vez.

    Patrón real observado (venta mayorista/TCP): cada oferta en su propio
    renglón, con su propio precio. `propose()` solo ve una propuesta por
    anuncio porque opera sobre el texto entero; esto lo hace línea por línea
    y descarta las que no aportan ni producto ni precio.

    Provincia y moneda caen al texto completo cuando la línea puntual no trae
    marca propia — son datos que suelen valer para todo el anuncio, no para
    un renglón en particular (ver `_extract_currency_explicit`).

    `channel_province` corta esa cadena por arriba: cuando el anuncio vino de
    un canal con provincia fija cargada, esa gana sobre la línea Y sobre el
    texto completo (ver `propose`).

    El anuncio de un solo producto con precio por volumen se lee antes y
    aparte (`_tiered_proposals`): ahí las líneas no son ofertas autónomas
    sino escalones que heredan producto y lote del encabezado.
    """
    tiered = _tiered_proposals(
        text, products, provinces, channel_province=channel_province
    )
    if tiered is not None:
        return tiered

    whole_text_province = channel_province or _match_province(text, provinces)
    whole_text_currency = _extract_currency_explicit(text)

    # Producto que las líneas de formato pueden heredar del encabezado. La
    # condición es la MISMA que ya usa `_tiered_proposals`: solo si todo el
    # anuncio habla de un único producto, porque con dos, atribuir un formato a
    # uno de ellos sería inventar.
    mencionados = [
        p
        for p in (_match_product(raw.strip(), products) for raw in text.splitlines())
        if p is not None
    ]
    producto_unico = (
        mencionados[0]
        if mencionados and all(p is mencionados[0] for p in mencionados)
        else None
    )

    proposals: list[ExtractionProposal] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        price = _extract_price(line)
        product = _match_product(line, products)
        quantity, unit = _extract_quantity(line)

        # Línea de formato del price-list mayorista: declara envase y precio
        # ("Caja de 40 lb: 27 400 CUP") pero no repite el producto, que está en
        # el encabezado. `_tiered_proposals` ya heredaba en la forma de
        # escalones por volumen; esta forma no los tiene y caía a la lectura
        # línea por línea, dejando el candidato con el precio correcto sin
        # producto — y `_match_rows_to_candidates`, que empareja por
        # `product_id`, lo descartaba (2026-09-09).
        #
        # Nombrar un envase es la condición, no un detalle: es lo que distingue
        # una línea de formato de un renglón cualquiera con cifras. Pedir solo
        # "precio y cantidad" resultó demasiado laxo — convertía en oferta tanto
        # una dirección ("Calle 20 entre 100 y 101", cuyo 101 se lee como
        # precio) como el ruido de OCR ("$23,500.Sale 427.27Lb", que además
        # produce una cantidad de 427.27 lb).
        if (
            product is None
            and price is not None
            and _PACKAGING_WORD_RE.search(_strip_accents(line.lower())) is not None
        ):
            product = producto_unico

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

        currency = (
            _extract_currency_explicit(line) or whole_text_currency or Currency.CUP.value
        )
        # `channel_province` va primero a propósito: es override, no fallback
        # ("el canal siempre manda"), así que también le gana a la provincia
        # que la línea puntual mencione.
        province = (
            channel_province or _match_province(line, provinces) or whole_text_province
        )
        # Mismas dos reglas que `propose()`, pero acotadas a ESTA línea: cada
        # renglón es una oferta propia, con su propio precio y su propia
        # declaración si la trae. Buscar contra el texto completo del anuncio
        # arriesgaría que la «Sale X unidad» de una línea confirme por
        # casualidad la aritmética de otra (docs/plan-asistente-llm-cola-
        # completa.md, pendiente suelto: "las reglas de la base del precio no
        # llegan a la cola").
        basis = _declared_price_basis(
            line, price=price, quantity=quantity, unit=unit
        ) or _packaging_price_basis(line, price=price, quantity=quantity)

        proposals.append(
            ExtractionProposal(
                product=product,
                product_name_guess=guess,
                price=price,
                currency=currency,
                quantity=quantity,
                unit=unit,
                province=province,
                price_basis=basis,
            )
        )
    return proposals
