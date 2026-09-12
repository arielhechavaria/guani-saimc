"""Genera `src/saimc/web/static/cuba_provincias.geojson`, para el mapa Leaflet.

No es parte del paquete que se despliega: es herramienta de build, y corre a
mano cuando haya que regenerar el asset. Existe por el mismo motivo que
`build_cuba_map.py` —reproducibilidad— y de hecho reusa sus funciones: mismo
shapefile fuente, mismo criterio de qué anillo entra (`MIN_RING_AREA`) y
qué tan simplificado (`SIMPLIFY_EPSILON`), para que la geometría del mapa
interactivo sea consistente con la que el resto del proyecto ya conoce.

Por qué un archivo nuevo y no seguir usando `map_cuba.py`: ese módulo trae
las 16 rutas ya proyectadas a un `viewBox` SVG propio (equirectangular con
`stretch` — ver su docstring), coordenadas que no significan nada para
Leaflet, que necesita longitud/latitud reales para posicionar sobre tiles
de OpenStreetMap. `map_cuba.py` sigue existiendo por ahora en el repo pero
ya no lo usa ninguna plantilla (docs/decisions.md, 2026-09-12 — el mapa
público pasa de SVG a Leaflet).

Cada provincia es un `MultiPolygon`, no un `Polygon`: los anillos extra que
sobreviven al filtro de `MIN_RING_AREA` (cayos que si se ven a la escala del
mapa) son tierra separada, no agujeros — un `Polygon` con más de un anillo
en GeoJSON trata a los siguientes como huecos, que cortaría un agujero en
la provincia en vez de sumarle una isla.

Fuente
------
`ne_10m_admin_1_states_provinces` de Natural Earth 5.1.1 — la misma que
`build_cuba_map.py`:

    curl -L -o ne10.zip \\
      https://naciscdn.org/naturalearth/10m/cultural/ne_10m_admin_1_states_provinces.zip
    python tools/build_cuba_geojson.py ne10.zip

**Licencia: dominio público.**
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_cuba_map import SIMPLIFY_EPSILON, load_cuba, simplify  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from saimc.web.product_nav import product_slug  # noqa: E402


def main() -> int:
    if len(sys.argv) != 2:
        print(f"uso: {sys.argv[0]} <ne_10m_admin_1_states_provinces.zip>", file=sys.stderr)
        return 1

    provinces = load_cuba(Path(sys.argv[1]))
    if len(provinces) != 16:
        print(f"Se esperaban 16 provincias, salieron {len(provinces)}", file=sys.stderr)
        return 1

    features = []
    for name, rings in provinces:
        kept = [
            simplified_ring
            for ring in rings
            if len(simplified_ring := simplify(ring, SIMPLIFY_EPSILON)) >= 4
        ]
        # GeoJSON quiere el anillo cerrado (primer punto == último); el
        # simplificador de `build_cuba_map.py` no lo garantiza porque el SVG
        # lo cierra con el comando `Z`, que acá no existe.
        polygons = [
            [[*point, ] for point in (ring if ring[0] == ring[-1] else [*ring, ring[0]])]
            for ring in kept
        ]
        features.append(
            {
                "type": "Feature",
                # `slug` viaja en las properties para que el cliente empareje
                # cada polígono con su fila de datos (`map_provinces_json` en
                # `routers/public.py`) por un string exacto, en vez de
                # reimplementar `product_slug` en JS.
                "properties": {"name": name, "slug": product_slug(name)},
                "geometry": {"type": "MultiPolygon", "coordinates": [[p] for p in polygons]},
            }
        )

    geojson = {"type": "FeatureCollection", "features": features}

    destination = (
        Path(__file__).resolve().parent.parent
        / "src" / "saimc" / "web" / "static" / "cuba_provincias.geojson"
    )
    # Sin espacios: es un asset que viaja al navegador en cada carga de la
    # home pública, no un archivo para leer a mano. `ensure_ascii=False`
    # porque los nombres llevan tildes y separators compactos ahorran los
    # espacios después de `:`/`,` que `json.dumps` deja por default.
    destination.write_text(
        json.dumps(geojson, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    total_points = sum(
        len(ring) for f in features for polygon in f["geometry"]["coordinates"] for ring in polygon
    )
    print(
        f"{destination}: {len(features)} provincias, {total_points} puntos, "
        f"{destination.stat().st_size} bytes"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
