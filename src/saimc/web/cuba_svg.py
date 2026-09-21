"""Mapa SVG de las 16 provincias para el panel derecho de «Explorar» (2026-09-21).

Sale de la MISMA geometría que ya sirve el mapa Leaflet de la home
(`static/cuba_provincias.geojson`, lon/lat de Natural Earth), proyectada una vez a
coordenadas de un `viewBox` fijo. Sin tiles, sin Leaflet y sin una segunda
fuente de geometría que se desincronice: una miniatura de contexto —teñida por
precio, con clic para filtrar— pesa unos 14 KB de HTML y no pide nada a la red.

La proyección es equirectangular corregida por la latitud media: un grado de
longitud mide menos que uno de latitud, y sin esa corrección Cuba sale más
larga de lo que es. Los límites se leen de la propia geometría, así que si
`tools/build_cuba_geojson.py` la regenera, esto la sigue.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from itertools import pairwise
from pathlib import Path

_GEOJSON = Path(__file__).resolve().parent / "static" / "cuba_provincias.geojson"


@dataclass(frozen=True)
class MapPath:
    slug: str
    name: str
    #: Todos los anillos de la provincia (islas y cayos incluidos) en un solo
    #: `d`, a un decimal.
    d: str
    #: Centro de su polígono más grande, para anclar rótulos.
    cx: float
    cy: float


@dataclass(frozen=True)
class CubaMap:
    width: float
    height: float
    provinces: tuple[MapPath, ...]

    @property
    def viewbox(self) -> str:
        return f"0 0 {self.width:g} {self.height:g}"


def _polygons(geometry: dict) -> list[list[list[list[float]]]]:
    return geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]


def _centroid(ring: list[list[float]]) -> tuple[float, float, float]:
    """(área con signo, x, y) del centroide de un anillo, por la fórmula del
    polígono."""
    area = cx = cy = 0.0
    for (x0, y0), (x1, y1) in pairwise(ring):
        cruz = x0 * y1 - x1 * y0
        area += cruz
        cx += (x0 + x1) * cruz
        cy += (y0 + y1) * cruz
    area /= 2
    return (area, cx / (6 * area), cy / (6 * area)) if area else (0.0, ring[0][0], ring[0][1])


@lru_cache(maxsize=8)
def build_cuba_map(width: float = 340.0) -> CubaMap:
    """Las 16 provincias en un lienzo de `width` de ancho.

    Se calcula una sola vez por ancho: la geometría no cambia con los filtros.
    """
    features = json.loads(_GEOJSON.read_text(encoding="utf-8"))["features"]
    puntos = [
        p for f in features for poligono in _polygons(f["geometry"]) for anillo in poligono for p in anillo
    ]
    lon0, lon1 = min(p[0] for p in puntos), max(p[0] for p in puntos)
    lat0, lat1 = min(p[1] for p in puntos), max(p[1] for p in puntos)
    corregido = math.cos(math.radians((lat0 + lat1) / 2))
    k = width / ((lon1 - lon0) * corregido)

    def proyectar(lon: float, lat: float) -> tuple[float, float]:
        return (lon - lon0) * corregido * k, (lat1 - lat) * k

    provincias = []
    for f in features:
        trazos = []
        mayor = (0.0, 0.0, 0.0)
        for poligono in _polygons(f["geometry"]):
            for anillo in poligono:
                pts = [proyectar(lon, lat) for lon, lat in anillo]
                trazos.append("M" + "L".join(f"{x:.1f},{y:.1f}" for x, y in pts) + "Z")
            area, x, y = _centroid([list(proyectar(lon, lat)) for lon, lat in poligono[0]])
            if abs(area) > abs(mayor[0]):
                mayor = (area, x, y)
        provincias.append(
            MapPath(
                slug=f["properties"]["slug"],
                name=f["properties"]["name"],
                d="".join(trazos),
                cx=round(mayor[1], 1),
                cy=round(mayor[2], 1),
            )
        )
    return CubaMap(width=float(width), height=round((lat1 - lat0) * k, 1), provinces=tuple(provincias))
