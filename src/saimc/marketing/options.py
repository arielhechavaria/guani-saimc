"""Opciones de una tarjeta que el operador elige en el editor (F1 de
`docs/plan-fabrica-marketing.md`): qué bloques se ven y dos textos.

Viajan en la URL (`b_<bloque>=0|1`, `subtitle`, `cta`): sin tabla ni estado.
Nada de lo que se lee acá llega a una cifra —las cifras salen de
`build_context`, que no recibe estas opciones—, y un bloque sin interruptor
nunca se apaga, lo pida quien lo pida.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from saimc.marketing.context import CardType

#: Topes en caracteres. El subtítulo cabe en dos renglones del lienzo de
#: 1080 px; la llamada, en uno dentro de su chip.
SUBTITLE_MAX = 80
CTA_MAX = 60

#: Tarjeta «Libre»: tipo de bloque → (etiqueta, tope de caracteres, una sola
#: línea). El separador no lleva texto. Es texto narrativo: las cifras de
#: precios salen solo de las tarjetas con datos (regla 1 del plan).
FREE_TYPES: dict[str, tuple[str, int, bool]] = {
    "titulo": ("Título", 80, True),
    "texto": ("Texto", 240, False),
    "chip": ("Llamada", CTA_MAX, True),
    "separador": ("Separador", 0, True),
}
FREE_MAX_BLOCKS = 8


@dataclass(frozen=True)
class FreeBlock:
    type: str
    text: str = ""


@dataclass(frozen=True)
class CardOptions:
    #: `slug` del bloque → si se ve. Incluye los bloques sin interruptor, siempre en `True`.
    show: dict[str, bool]
    subtitle: str = ""
    cta: str = ""
    #: Solo la tarjeta «Libre»: sus bloques, en orden.
    free: tuple[FreeBlock, ...] = ()

    def query_items(self) -> list[tuple[str, str]]:
        """Los pares de URL que reconstruyen estas opciones (los bloques con
        interruptor y los textos que no están vacíos)."""
        items = [(f"b_{slug}", "1" if on else "0") for slug, on in self.show.items()]
        if self.subtitle:
            items.append(("subtitle", self.subtitle))
        if self.cta:
            items.append(("cta", self.cta))
        return items


def _clean(raw: str | None, limit: int) -> str:
    """Una sola línea, sin espacios de más y recortada al tope."""
    return " ".join((raw or "").split())[:limit].rstrip()


def _free_text(kind: str, raw: str | None) -> str:
    _, limit, single = FREE_TYPES[kind]
    if limit == 0:
        return ""
    if single:
        return _clean(raw, limit)
    # Párrafo: conserva los saltos de línea, sin espacios de más.
    lineas = [" ".join(linea.split()) for linea in (raw or "").splitlines()]
    return "\n".join(lineas).strip()[:limit].rstrip()


def _free_blocks(pairs: list[tuple[str, str]]) -> tuple[FreeBlock, ...]:
    bloques = [
        FreeBlock(tipo, _free_text(tipo, texto)) for tipo, texto in pairs if tipo in FREE_TYPES
    ]
    return tuple(bloques[:FREE_MAX_BLOCKS])


def _apply_free_op(blocks: tuple[FreeBlock, ...], op: str | None) -> tuple[FreeBlock, ...]:
    """`add:<tipo>`, `up:<i>`, `down:<i>` o `del:<i>` (i desde 0). Una
    operación inválida o fuera de rango no cambia nada."""
    accion, _, arg = (op or "").partition(":")
    lista = list(blocks)
    if accion == "add":
        if arg in FREE_TYPES and len(lista) < FREE_MAX_BLOCKS:
            lista.append(FreeBlock(arg))
        return tuple(lista)
    try:
        i = int(arg)
    except ValueError:
        return blocks
    if not 0 <= i < len(lista):
        return blocks
    if accion == "del":
        del lista[i]
    elif accion == "up" and i > 0:
        lista[i - 1], lista[i] = lista[i], lista[i - 1]
    elif accion == "down" and i < len(lista) - 1:
        lista[i + 1], lista[i] = lista[i], lista[i + 1]
    return tuple(lista)


def _free_from_params(params: Mapping[str, str]) -> tuple[FreeBlock, ...]:
    getlist = getattr(params, "getlist", None)
    if getlist is None:
        return ()
    tipos, textos = getlist("free_type"), getlist("free_text")
    pares = [(t, textos[i] if i < len(textos) else "") for i, t in enumerate(tipos)]
    return _apply_free_op(_free_blocks(pares), params.get("free_op"))


def parse_options(kind: CardType, params: Mapping[str, str]) -> CardOptions:
    """Lee las opciones de `params` (los parámetros de la URL).

    Solo cuenta `b_<slug>=0` para apagar y `=1` para prender un bloque con
    interruptor; cualquier otro valor deja el por defecto. Una tarjeta sin
    bloques tampoco tiene textos: no hay dónde pintarlos."""
    if kind.free:
        return CardOptions(show={}, free=_free_from_params(params))
    show: dict[str, bool] = {}
    for block in kind.blocks:
        value = params.get(f"b_{block.slug}") if block.toggleable else None
        show[block.slug] = (value == "1") if value in ("0", "1") else block.default
    if not kind.blocks:
        return CardOptions(show=show)
    return CardOptions(
        show=show,
        subtitle=_clean(params.get("subtitle"), SUBTITLE_MAX),
        cta=_clean(params.get("cta"), CTA_MAX),
    )


def options_to_dict(options: CardOptions) -> dict[str, Any]:
    """Lo que se guarda en `marketing_post_slides.options`."""
    datos: dict[str, Any] = {
        "show": dict(options.show), "subtitle": options.subtitle, "cta": options.cta
    }
    if options.free:
        datos["free"] = [{"type": b.type, "text": b.text} for b in options.free]
    return datos


def options_from_dict(kind: CardType, data: Mapping[str, Any] | None) -> CardOptions:
    """Lee lo guardado pasando otra vez por `parse_options`: si el JSON dice
    que un bloque sin interruptor está apagado, igual se ve."""
    data = data or {}
    if kind.free:
        pares = [(str(b.get("type")), str(b.get("text") or "")) for b in data.get("free") or []]
        return CardOptions(show={}, free=_free_blocks(pares))
    params: dict[str, str] = {
        f"b_{slug}": "1" if on else "0" for slug, on in (data.get("show") or {}).items()
    }
    params["subtitle"] = str(data.get("subtitle") or "")
    params["cta"] = str(data.get("cta") or "")
    return parse_options(kind, params)
