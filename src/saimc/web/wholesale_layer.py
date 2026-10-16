"""Capa mayorista opcional de la vista pública (2026-10-16).

El precio de referencia del sistema es el minorista
(`cells.REFERENCE_SALE_FORMAT`): toda cifra pública parte de ahí. El mayorista
no es otra serie en pie de igualdad, es una **capa** que el visitante prende a
pedido (`?capa=mayorista`) y que se dibuja junto a la cifra minorista del mismo
producto, provincia y formato, nunca adentro de ella.

Mismo reparto que `price_charts`: acá se empareja y se calcula la geometría;
la plantilla solo pinta. Las cifras de los dos lados son `Headline` armados por
`price_charts.build_headlines` con las mismas reglas —mismo `_group`, mismo
cerco robusto, misma regla de publicación de `_visible_observations`—, así que
esta capa no inventa ninguna medición: pone dos que ya existen sobre un eje
común.

**Sin umbral de muestra.** Una celda mayorista con un solo anuncio se muestra
como un valor suelto, con su `n = 1` escrito, igual que la minorista: §12.6
prohíbe fijar por intuición cuántos anuncios son «pocos». Lo único que no se
dibuja es lo que no existe: sin anuncios mayoristas en la celda, la capa dice
«sin datos al por mayor».

**El par es el mismo formato en dos escalones de volumen**, igual que en la
matriz del operador (`matrix._pair_formats`): la caja de 40 lb al por menor
contra la caja de 40 lb al por mayor. Un mayorista sin formato minorista igual
en su provincia no tiene contra qué compararse; se lista aparte, sin ratio.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from saimc.web.price_charts import AdRow, Headline

#: El único valor de `?capa=` que prende la capa en `/producto/<slug>`.
LAYER_PARAM = "mayorista"

#: Rótulo de la capa en pantalla. «Escala de contenedor» es la definición
#: operativa de mayorista (`docs/decisions.md`, 2026-08-12): vender un saco
#: suelto sigue siendo minorista.
LAYER_LABEL = "Al por mayor (escala de contenedor)"

_Key = tuple[str, str, str, str, str | None]


def wants_wholesale(capa: str | None) -> bool:
    """`True` solo con `?capa=mayorista` exacto. Cualquier otro valor —los de
    la capa del mapa de `/precios`, que esta ruta ignora, o uno tipeado mal—
    deja la página como siempre."""
    return capa == LAYER_PARAM


def headline_key(h: Headline) -> _Key:
    """La clave de celda de una cifra: la misma tupla de `price_charts._group`
    y de `build_cell_ads`."""
    return (h.product_name, h.province_name, h.currency, h.unit, h.format_label)


@dataclass(frozen=True)
class Band:
    """Geometría de una banda mínimo–máximo con su mediana, en porcentajes
    del eje común. `width_pct = 0` es un valor suelto: se dibuja como punto."""

    left_pct: float
    width_pct: float
    median_pct: float


@dataclass(frozen=True)
class WholesaleOverlay:
    """La capa mayorista de una cifra minorista: la cifra mayorista del mismo
    formato, si hay, y las dos bandas sobre un eje común."""

    retail: Headline
    wholesale: Headline | None
    retail_band: Band | None
    wholesale_band: Band | None
    #: Los anuncios detrás de la cifra mayorista (§6.5), con el mismo enlace a
    #: `/anuncio/{id}` que la minorista cuando la evidencia es pública.
    ads: tuple[AdRow, ...] = ()

    @property
    def has_data(self) -> bool:
        return self.wholesale is not None

    @property
    def ratio(self) -> Decimal | None:
        """Mediana minorista ÷ mediana mayorista: «cuánto más caro sale
        comprar suelto». Misma definición que `CellWithBar.retail_ratio`, sobre
        las medianas dentro del cerco que la página publica."""
        if self.wholesale is None or self.wholesale.median <= 0:
            return None
        return self.retail.median / self.wholesale.median

    @property
    def ratio_text(self) -> str:
        """«×1,38», con coma decimal como el resto de las cifras públicas."""
        ratio = self.ratio
        if ratio is None:
            return ""
        return f"×{ratio:.2f}".replace(".", ",")


def _axis(*cifras: Headline) -> tuple[float, float]:
    lo = float(min(c.minimum for c in cifras))
    hi = float(max(c.maximum for c in cifras))
    if lo == hi:
        # Mismo criterio que `build_range_series`: sin rango, ±10 % para que
        # el valor quede en el medio y no pegado a un borde.
        return lo * 0.9, hi * 1.1
    return lo, hi


def _band(cifra: Headline, lo: float, hi: float) -> Band:
    def pct(value: Decimal) -> float:
        return (float(value) - lo) / (hi - lo) * 100.0

    left = pct(cifra.minimum)
    return Band(
        left_pct=left,
        width_pct=pct(cifra.maximum) - left,
        median_pct=pct(cifra.median),
    )


def build_overlays(
    retail: Sequence[Headline],
    wholesale: Sequence[Headline],
    ads_by_cell: Mapping[_Key, Sequence[AdRow]] | None = None,
) -> list[WholesaleOverlay]:
    """Una capa por cifra minorista, en el mismo orden que `retail` —así la
    plantilla las lee con el índice del bloque—. Se empareja por producto,
    provincia, moneda, unidad y formato: la misma clave de celda que ya usa
    `panel_view.attach_blocks`."""
    por_clave = {headline_key(w): w for w in wholesale}
    ads_by_cell = ads_by_cell or {}
    capas: list[WholesaleOverlay] = []
    for cifra in retail:
        clave = headline_key(cifra)
        par = por_clave.get(clave)
        if par is None:
            capas.append(WholesaleOverlay(cifra, None, None, None))
            continue
        lo, hi = _axis(cifra, par)
        capas.append(
            WholesaleOverlay(
                cifra,
                par,
                _band(cifra, lo, hi),
                _band(par, lo, hi),
                ads=tuple(ads_by_cell.get(clave, ())),
            )
        )
    return capas


def unpaired_wholesale(
    retail: Sequence[Headline], wholesale: Sequence[Headline]
) -> list[Headline]:
    """Las cifras mayoristas sin minorista del mismo formato en su provincia:
    se muestran aparte y sin ratio, porque no hay referencia contra la cual
    compararlas."""
    claves = {headline_key(r) for r in retail}
    return [w for w in wholesale if headline_key(w) not in claves]
