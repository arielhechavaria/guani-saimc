"""Mosaico de cobertura del tablero (prototipo aprobado, 2026-09-26).

Las 16 provincias como casillas de un cartograma que recorre Cuba de oeste a
este en dos filas. Es la cobertura hecha visible: el día del lanzamiento la
noticia es «2 de 16», y en vez de esconderla en «Sobre los datos» el tablero la
dibuja —llenas las que tienen dato, punteadas las que siguen en recolección— y
la ofrece como puente hacia el mapa.

No calcula nada: el estado de cada casilla es el de `MapProvince` (publicada,
recogiendo datos, sin datos), la misma fuente del mapa y de la viñeta
«N de 16 provincias tienen datos». Así las tres nunca se contradicen.

El cartograma no es geografía: es una rejilla. Cada casilla vale lo mismo aunque
Pinar del Río sea más grande que Artemisa, y eso es deliberado, porque lo que
se cuenta es cuántas provincias hay con dato y no cuánto territorio cubren.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from saimc.web.map_view import MapProvince
from saimc.web.product_nav import product_slug

#: slug → (columna, fila, abreviatura). Oeste a este; la fila 1 es el sur
#: (Isla de la Juventud y las provincias que quedan «debajo» de la fila 0).
_CASILLAS: dict[str, tuple[int, int, str]] = {
    "pinar-del-rio": (0, 0, "PRI"),
    "artemisa": (1, 0, "ART"),
    "la-habana": (2, 0, "HAB"),
    "mayabeque": (3, 0, "MAY"),
    "matanzas": (4, 0, "MTZ"),
    "villa-clara": (5, 0, "VCL"),
    "sancti-spiritus": (6, 0, "SSP"),
    "ciego-de-avila": (7, 0, "CAV"),
    "camaguey": (8, 0, "CMG"),
    "las-tunas": (9, 0, "LTU"),
    "holguin": (10, 0, "HOL"),
    "isla-de-la-juventud": (1, 1, "IJV"),
    "cienfuegos": (5, 1, "CFG"),
    "granma": (9, 1, "GRM"),
    "santiago-de-cuba": (10, 1, "SCU"),
    "guantanamo": (11, 1, "GTM"),
}

#: Un nombre fuera del cartograma cae acá, de izquierda a derecha.
_FILA_EXTRA = 2

#: Slot de las provincias sin color propio en el resto de la página.
_SLOT_NEUTRO = 9


@dataclass(frozen=True)
class CoverageTile:
    name: str
    abbr: str
    col: int
    row: int
    #: Clave de `map_view.STATE_LABELS`: «publicada», «en-ingesta» o «sin-datos».
    state: str
    #: «La Habana: Recogiendo datos, todavía sin publicar»: lo que dice el
    #: `title` de la casilla y lo que lee un lector de pantalla.
    label: str
    #: Tono de la provincia en el resto del tablero (`.ph-prov-N`), o el neutro.
    slot: int


def build_coverage(
    provinces: Sequence[MapProvince], slots: Mapping[str, int]
) -> tuple[CoverageTile, ...]:
    """Las casillas, de oeste a este y fila por fila. `slots` es el mapa
    nombre → tono que ya usa el resto de la página."""
    conocidas: list[CoverageTile] = []
    extra: list[CoverageTile] = []
    for provincia in provinces:
        posicion = _CASILLAS.get(product_slug(provincia.name))
        if posicion is None:
            col, fila, abrev = len(extra), _FILA_EXTRA, provincia.name[:3].upper()
            destino = extra
        else:
            col, fila, abrev = posicion
            destino = conocidas
        destino.append(
            CoverageTile(
                name=provincia.name,
                abbr=abrev,
                col=col,
                row=fila,
                state=provincia.state,
                label=f"{provincia.name}: {provincia.label}",
                slot=slots.get(provincia.name, _SLOT_NEUTRO),
            )
        )
    return tuple(sorted([*conocidas, *extra], key=lambda c: (c.row, c.col)))
