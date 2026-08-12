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
