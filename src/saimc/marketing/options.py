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


@dataclass(frozen=True)
class CardOptions:
    #: `slug` del bloque → si se ve. Incluye los bloques sin interruptor, siempre en `True`.
    show: dict[str, bool]
    subtitle: str = ""
    cta: str = ""

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


def parse_options(kind: CardType, params: Mapping[str, str]) -> CardOptions:
    """Lee las opciones de `params` (los parámetros de la URL).

    Solo cuenta `b_<slug>=0` para apagar y `=1` para prender un bloque con
    interruptor; cualquier otro valor deja el por defecto. Una tarjeta sin
    bloques tampoco tiene textos: no hay dónde pintarlos."""
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
    return {"show": dict(options.show), "subtitle": options.subtitle, "cta": options.cta}


def options_from_dict(kind: CardType, data: Mapping[str, Any] | None) -> CardOptions:
    """Lee lo guardado pasando otra vez por `parse_options`: si el JSON dice
    que un bloque sin interruptor está apagado, igual se ve."""
    data = data or {}
    params: dict[str, str] = {
        f"b_{slug}": "1" if on else "0" for slug, on in (data.get("show") or {}).items()
    }
    params["subtitle"] = str(data.get("subtitle") or "")
    params["cta"] = str(data.get("cta") or "")
    return parse_options(kind, params)
