"""Mapa de Cuba para la vista pública: geometría + cobertura, sin pintar nada.

Mismo reparto de trabajo que `matrix.py`: acá se arma la estructura y la
plantilla solo la recorre. Es el invariante 1 del ADR 001 — si el número se
calcula en Jinja, en Fase 2 hay que reescribirlo y mientras tanto no se puede
testear.

**Por qué no hay un umbral de «datos suficientes».** `spec.md` §6.7 describe el
gris del mapa como «sin datos suficientes», pero nunca dice cuántas
observaciones son suficientes, y §12.6 es explícita: los umbrales que la
especificación introdujo sin valor asignado **no se inventan**. Así que este
módulo distingue lo único que puede verificar sin inventar nada —una provincia
tiene observaciones o no las tiene— y expone el tamaño de muestra para que se
muestre al lado, que es lo que §9.0 exige desde el día 1 en lugar de un titular.
Cuando exista una medición que fije el umbral, se agrega un estado más.
"""

from __future__ import annotations

from dataclasses import dataclass

from saimc.web.map_cuba import PROVINCE_PATHS, VIEWBOX


@dataclass(frozen=True)
class MapProvince:
    """Una provincia dibujable: su forma y cuánto dato la respalda."""

    name: str
    path: str
    rows: int

    @property
    def has_data(self) -> bool:
        return self.rows > 0

    @property
    def state(self) -> str:
        """Sufijo de clase CSS. No es un juicio de precio, es cobertura."""
        return "con-dato" if self.has_data else "sin-dato"


@dataclass(frozen=True)
class MapView:
    """El mapa entero, listo para recorrer en la plantilla."""

    viewbox: str
    provinces: tuple[MapProvince, ...]

    @property
    def covered(self) -> int:
        return sum(1 for province in self.provinces if province.has_data)

    @property
    def total_rows(self) -> int:
        return sum(province.rows for province in self.provinces)


def build_map(rows_by_province: dict[str, int]) -> MapView:
    """Cruza la geometría con el conteo de observaciones por provincia.

    Recorre `PROVINCE_PATHS` y no el diccionario de conteos: las 16 provincias
    aparecen siempre, y una sin dato entra con `rows=0`. El cero **es** el dato
    —dice dónde no se está mirando—, igual que en `progress.compute_coverage`.
    Un nombre que venga en los conteos y no exista en la geometría se ignora en
    silencio acá; `tests/test_map_view.py` cubre que los 16 nombres coincidan
    con los de la tabla `provinces`.
    """
    return MapView(
        viewbox=VIEWBOX,
        provinces=tuple(
            MapProvince(name=name, path=path, rows=rows_by_province.get(name, 0))
            for name, path in sorted(PROVINCE_PATHS.items())
        ),
    )
