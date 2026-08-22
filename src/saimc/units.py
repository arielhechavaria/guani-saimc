"""Vocabulario canónico de unidades para `prices_history.unit`.

Este módulo es hermano de `cells.py` y `privacy.py`, no vive bajo
`extraction/`: el extractor no es dueño del vocabulario de almacenamiento.
`engine.py:_UNIT_WORDS` es una superficie de *reconocimiento* — formas
flexionadas tal como aparecen en anuncios reales, calibradas para el recall
de `_QUANTITY_RE` — mientras que `UNITS` acá es *canónico*: `l` y no
`litro`, `caja` y no `cajas`, e incluye `ml`, que el reconocimiento
deliberadamente no tiene todavía (soporte de "100 ML" en el extractor es un
cambio aparte). Unificarlos forzaría flexiones a los valores almacenados o
un cambio de recall del extractor dentro de un slice de esquema. Por eso
`curation.py` llama `canonical_unit()` sobre la salida del extractor, pero
`engine.py` no importa este módulo y no lo toca este cambio (design.md, D1).

No hay CHECK de base de datos sobre `unit`: el vocabulario cerrado se aplica
acá, en código de aplicación, así que ampliarlo nunca exige una migración.
"""

from decimal import Decimal

UNITS: tuple[str, ...] = ("lb", "kg", "l", "ml", "unidad", "docena", "caja")

# Formas que produce el extractor u operadores y no son canónicas.
_ALIASES = {"litro": "l", "litros": "l", "cajas": "caja"}


def canonical_unit(raw: str | None) -> str | None:
    """Unidad canónica, o `None` si no está en el vocabulario. Nunca inventa."""
    if not raw:
        return None
    value = raw.strip().lower()
    value = _ALIASES.get(value, value)
    return value if value in UNITS else None


# Tamaño de cada unidad expresado en la unidad base de su magnitud —gramo para
# masa, mililitro para volumen—, en vez de una tabla de pares origen/destino:
# así sumar una unidad es una línea y no N entradas nuevas.
#
# **Estos números son definiciones, no estimaciones.** La libra internacional
# son 0,45359237 kg exactos por el acuerdo de 1959, y el prefijo «kilo» es 10³
# por el SI. `spec.md` §12.6 —«ningún umbral debe inventarse»— gobierna
# parámetros de juicio que deciden qué se publica (N mínimo por celda, ventana
# de validez de la tasa, criterios de fuente confiable) y que la especificación
# manda calibrar contra datos reales. Una equivalencia entre unidades no es
# ninguna de esas cosas: no se calibra, no gobierna nada y no admite otro
# valor. Tratarla como umbral fue lo que dejó afuera de la vista minorista al
# 65% de las filas de productos bandera (decisions.md, 2026-08-22).
_MAGNITUDES: dict[str, tuple[str, Decimal]] = {
    "lb": ("mass", Decimal("453.59237")),
    "kg": ("mass", Decimal("1000")),
    "ml": ("volume", Decimal("1")),
    "l": ("volume", Decimal("1000")),
}

# `unidad`, `docena` y `caja` quedan deliberadamente fuera de `_MAGNITUDES`.
# `caja` no dice cuánto contiene, así que no es convertible ni en principio.
# `docena` sí lo sería —doce unidades es una definición—, pero ninguna fila del
# corpus usa esas dos unidades todavía: agregar la equivalencia sería
# vocabulario sin dato que lo respalde.


def convert_price_per_unit(
    price: Decimal, *, frm: str | None, to: str | None
) -> Decimal | None:
    """Convierte un precio **por unidad** de `frm` a `to`. Nunca inventa.

    Devuelve `None` —misma convención que `canonical_unit()`— cuando la
    conversión no está definida: unidad fuera del vocabulario, unidad sin
    magnitud (`caja`), o magnitudes distintas (`kg` a `l` exigiría la densidad
    del producto, que no tenemos).

    Ojo con la dirección: lo que se convierte es un **precio por unidad**, no
    una cantidad, así que el factor va al revés que la intuición. Si el kilo
    cuesta 1000, la libra no cuesta 2204,62 sino 453,59 — una libra es *menos*
    producto, así que cuesta *menos*. De ahí que el factor sea
    `tamaño(destino) / tamaño(origen)` y no su inversa.
    """
    if frm is None or to is None:
        return None
    if frm == to:
        # Vale también para las unidades sin magnitud: convertir algo a sí
        # mismo no necesita saber cuánto pesa.
        return price if frm in UNITS else None

    source = _MAGNITUDES.get(frm)
    target = _MAGNITUDES.get(to)
    if source is None or target is None:
        return None
    if source[0] != target[0]:
        return None

    return price * target[1] / source[1]
