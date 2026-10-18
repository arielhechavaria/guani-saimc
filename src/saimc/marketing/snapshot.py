"""Congela el contexto de una tarjeta en JSON y lo reconstruye (F2,
`docs/spec-slice-fabrica-publicaciones.md` §1.1).

Lo que se guarda es el resultado de `build_context` —las dataclasses de
`cards.py`—, no el HTML: así lo aprobado se vuelve a pintar igual y se puede
auditar de dónde salió cada cifra. El registro de tipos es cerrado a
propósito: un `__type__` que no está acá es un error, no un `dict` suelto que
la plantilla pintaría a medias.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from saimc.marketing.cards import (
    AnuncioRow,
    ComoSeMovioCard,
    DatoDelDiaCard,
    DeDondeSaleCard,
    ProvinceRow,
    ProvinceSlope,
)

_TYPES: dict[str, type] = {
    t.__name__: t
    for t in (
        DatoDelDiaCard, ProvinceRow, ComoSeMovioCard, ProvinceSlope, DeDondeSaleCard, AnuncioRow
    )
}

_TYPE_KEY = "__type__"


def _freeze(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        nombre = type(value).__name__
        if nombre not in _TYPES:
            raise ValueError(f"Tipo sin registrar en el snapshot: {nombre}")
        campos = {f.name: _freeze(getattr(value, f.name)) for f in dataclasses.fields(value)}
        return {_TYPE_KEY: nombre, **campos}
    if isinstance(value, dict):
        return {k: _freeze(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_freeze(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError(f"Valor que no entra en un snapshot: {type(value).__name__}")


def _thaw(value: Any) -> Any:
    if isinstance(value, dict):
        if _TYPE_KEY in value:
            nombre = value[_TYPE_KEY]
            tipo = _TYPES.get(nombre)
            if tipo is None:
                raise ValueError(f"Tipo desconocido en el snapshot: {nombre}")
            return tipo(**{k: _thaw(v) for k, v in value.items() if k != _TYPE_KEY})
        return {k: _thaw(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_thaw(v) for v in value]
    return value


def freeze_context(context: dict[str, Any]) -> dict[str, Any]:
    """`context` en JSON puro, con el tipo de cada dataclass anotado."""
    congelado: dict[str, Any] = _freeze(context)
    return congelado


def thaw_context(data: dict[str, Any]) -> dict[str, Any]:
    """Lo inverso de `freeze_context`: las mismas dataclasses de `cards.py`."""
    contexto: dict[str, Any] = _thaw(data)
    return contexto
