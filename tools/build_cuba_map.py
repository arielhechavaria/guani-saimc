"""Genera `src/saimc/web/map_cuba.py` a partir de Natural Earth.

No es parte del paquete que se despliega: es herramienta de build, y corre a
mano cuando haya que regenerar la geometría. Existe para que el asset sea
**reproducible** — un archivo con 16 rutas SVG commiteado sin decir de dónde
salió ni cómo se simplificó es imposible de auditar o rehacer (mismo criterio
con el que se vendorizó htmx, ver ADR 001).

Fuente
------
`ne_10m_admin_1_states_provinces` de Natural Earth, versión **5.1.1**:

    curl -L -o ne10.zip \\
      https://naciscdn.org/naturalearth/10m/cultural/ne_10m_admin_1_states_provinces.zip
    python tools/build_cuba_map.py ne10.zip

**Licencia: dominio público.** Es la razón por la que se eligió esta fuente y no
geoBoundaries, que trae las mismas 16 unidades pero deriva de OpenStreetMap y es
ODbL — atribución obligatoria y share-alike sobre la base de datos. Para algo que
se va a publicar, dominio público es una atadura menos (`docs/backlog.md`).

Uso
---
Lee el shapefile con un parser mínimo de stdlib (no hay dependencia de GDAL,
pyshp ni geopandas: la conexión del entorno es lenta y una dependencia de 30 MB
para leer 16 polígonos una vez no se paga). Filtra Cuba, descarta cayos
irrelevantes, simplifica con Douglas-Peucker, proyecta y emite el módulo.
"""

from __future__ import annotations

import math
import struct
import sys
import zipfile
from pathlib import Path

STEM = "ne_10m_admin_1_states_provinces"

# Natural Earth 5.1.1 todavía usa el nombre anterior a la reorganización
# político-administrativa de 2011, cuando la antigua provincia de La Habana se
# partió en Artemisa y Mayabeque y «Ciudad de La Habana» pasó a llamarse
# simplemente «La Habana». La base usa el nombre vigente; el resto de los 15
# coincide carácter por carácter.
NAME_FIXES = {"Ciudad de la Habana": "La Habana"}

# Umbral de área (en grados²) por debajo del cual un anillo es un cayo que a
# escala de página no se ve. El anillo mayor de cada provincia se conserva
# siempre, pase lo que pase: sin esa excepción, Isla de la Juventud —que es una
# isla y nada más— podría quedar sin geometría.
MIN_RING_AREA = 0.001

# Tolerancia de Douglas-Peucker, en grados. Cuba abarca ~10,5° de longitud, así
# que a 1000 px de ancho un píxel son ~0,0105°: con 0,008 el error queda por
# debajo del píxel y es visualmente indistinguible del original. Medido: baja de
# 5.377 puntos a 1.629.
SIMPLIFY_EPSILON = 0.008

# Ancho del `viewBox`. El alto sale de la relación de aspecto real.
TARGET_WIDTH = 1000.0


def read_dbf(data: bytes) -> list[dict[str, str]]:
    """Lector mínimo de dBASE III. Solo campos de texto, que es todo lo que se usa."""
    n_records, header_len, record_len = struct.unpack("<IHH", data[4:12])
    fields: list[tuple[str, int]] = []
    pos = 32
    while data[pos] != 0x0D:  # 0x0D cierra los descriptores de campo
        descriptor = data[pos : pos + 32]
        name = descriptor[:11].split(b"\x00")[0].decode("ascii")
        fields.append((name, descriptor[16]))
        pos += 32

    rows = []
    for i in range(n_records):
        record = data[header_len + i * record_len :][:record_len]
        offset = 1  # el primer byte marca si el registro está borrado
        row = {}
        for name, length in fields:
            raw = record[offset : offset + length]
            # El `.cpg` del dataset declara UTF-8, y los campos se rellenan con
            # NUL además de con espacios.
            row[name] = raw.decode("utf-8", "replace").strip(" \x00")
            offset += length
        rows.append(row)
    return rows


def read_shp(data: bytes) -> list[list[list[tuple[float, float]]]]:
    """Devuelve, por registro, sus anillos; cada anillo es una lista de (lon, lat)."""
    shapes: list[list[list[tuple[float, float]]]] = []
    pos = 100  # cabecera fija del .shp
    while pos < len(data):
        _record_number, content_len = struct.unpack(">II", data[pos : pos + 8])
        pos += 8
        content = data[pos : pos + content_len * 2]  # la longitud viene en words
        pos += content_len * 2

        (shape_type,) = struct.unpack("<i", content[:4])
        if shape_type != 5:  # 5 = Polygon. 0 = Null, y no hay otros en este dataset.
            shapes.append([])
            continue

        n_parts, n_points = struct.unpack("<ii", content[36:44])
        parts = struct.unpack(f"<{n_parts}i", content[44 : 44 + n_parts * 4])
        base = 44 + n_parts * 4
        flat = struct.unpack(f"<{n_points * 2}d", content[base : base + n_points * 16])
        points = list(zip(flat[0::2], flat[1::2]))

        rings = []
        for index, start in enumerate(parts):
            stop = parts[index + 1] if index + 1 < n_parts else n_points
            rings.append(points[start:stop])
        shapes.append(rings)
    return shapes


def ring_area(ring: list[tuple[float, float]]) -> float:
    """Área por la fórmula del cordón (shoelace), sin signo."""
    total = 0.0
    for i in range(len(ring)):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % len(ring)]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2


def simplify(points: list[tuple[float, float]], epsilon: float) -> list[tuple[float, float]]:
    """Douglas-Peucker con pila explícita.

    Iterativo y no recursivo a propósito: el anillo más grande de Camagüey trae
    718 puntos y el peor caso de la versión recursiva es O(n) de profundidad,
    o sea a tiro del límite de recursión de Python por un dato de entrada.
    """
    if len(points) < 3:
        return points

    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]

    while stack:
        first, last = stack.pop()
        if last <= first + 1:
            continue
        x1, y1 = points[first]
        x2, y2 = points[last]
        dx, dy = x2 - x1, y2 - y1
        norm = math.hypot(dx, dy)

        farthest, max_distance = first, 0.0
        for i in range(first + 1, last):
            px, py = points[i]
            if norm:
                distance = abs(dy * px - dx * py + x2 * y1 - y2 * x1) / norm
            else:  # extremos coincidentes: cae a distancia al punto
                distance = math.hypot(px - x1, py - y1)
            if distance > max_distance:
                farthest, max_distance = i, distance

        if max_distance > epsilon:
            keep[farthest] = True
            stack.append((first, farthest))
            stack.append((farthest, last))

    return [point for point, keeper in zip(points, keep) if keeper]


def load_cuba(zip_path: Path) -> list[tuple[str, list[list[tuple[float, float]]]]]:
    with zipfile.ZipFile(zip_path) as archive:
        rows = read_dbf(archive.read(f"{STEM}.dbf"))
        shapes = read_shp(archive.read(f"{STEM}.shp"))

    provinces = []
    for row, rings in zip(rows, shapes):
        if row.get("adm0_a3") != "CUB":
            continue
        name = NAME_FIXES.get(row["name"], row["name"])
        biggest = max(ring_area(ring) for ring in rings)
        kept = [
            ring
            for ring in rings
            if ring_area(ring) >= MIN_RING_AREA or ring_area(ring) == biggest
        ]
        provinces.append((name, kept))
    return sorted(provinces)


def build_paths(
    provinces: list[tuple[str, list[list[tuple[float, float]]]]],
) -> tuple[dict[str, str], float, float]:
    """Simplifica, proyecta y escala. Devuelve las rutas y el tamaño del viewBox."""
    simplified = []
    for name, rings in provinces:
        # Un anillo que queda con menos de 4 puntos ya no encierra área: es
        # ruido de la simplificación, no un polígono.
        kept = [
            simplified_ring
            for ring in rings
            if len(simplified_ring := simplify(ring, SIMPLIFY_EPSILON)) >= 4
        ]
        simplified.append((name, kept))

    all_points = [point for _name, rings in simplified for ring in rings for point in ring]
    mid_latitude = (min(p[1] for p in all_points) + max(p[1] for p in all_points)) / 2
    # Equirectangular con paralelo estándar en el centro del país. Para los 3,5°
    # de latitud que abarca Cuba, la diferencia contra Mercator es invisible, y
    # esto no necesita ninguna biblioteca de proyecciones.
    stretch = math.cos(math.radians(mid_latitude))

    def project(point: tuple[float, float]) -> tuple[float, float]:
        longitude, latitude = point
        return longitude * stretch, -latitude

    projected = [(name, [[project(p) for p in ring] for ring in rings]) for name, rings in simplified]
    flat = [point for _name, rings in projected for ring in rings for point in ring]
    min_x = min(p[0] for p in flat)
    max_x = max(p[0] for p in flat)
    min_y = min(p[1] for p in flat)
    max_y = max(p[1] for p in flat)
    scale = TARGET_WIDTH / (max_x - min_x)
    height = round((max_y - min_y) * scale, 1)

    paths = {}
    for name, rings in projected:
        chunks = []
        for ring in rings:
            coordinates = [
                f"{round((x - min_x) * scale, 1):g},{round((y - min_y) * scale, 1):g}"
                for x, y in ring
            ]
            chunks.append("M" + "L".join(coordinates) + "Z")
        paths[name] = "".join(chunks)
    return paths, TARGET_WIDTH, height


TEMPLATE = '''"""Geometría de las 16 provincias de Cuba como rutas SVG.

GENERADO por `tools/build_cuba_map.py` — no editar a mano.

Fuente: Natural Earth `ne_10m_admin_1_states_provinces` 5.1.1, **dominio
público**. Simplificado con Douglas-Peucker a {epsilon}° (sub-pixel a {width:g} px de
ancho) y proyectado en equirectangular con paralelo estándar en el centro del
país. Motivo de la elección de fuente y de SVG inline en vez de un motor de
mapas: `docs/decisions/001-stack-python-unico.md`.

Las claves son los nombres de `provinces.name`, para que la vista pueda cruzar
geometría con dato sin una tabla de traducción en el medio.
"""

VIEWBOX = "0 0 {width:g} {height:g}"

PROVINCE_PATHS: dict[str, str] = {{
{entries}
}}
'''


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2

    provinces = load_cuba(Path(sys.argv[1]))
    if len(provinces) != 16:
        print(f"Se esperaban 16 provincias, salieron {len(provinces)}", file=sys.stderr)
        return 1

    paths, width, height = build_paths(provinces)
    entries = "\n".join(f'    {name!r}: "{path}",' for name, path in sorted(paths.items()))
    module = TEMPLATE.format(
        epsilon=SIMPLIFY_EPSILON, width=width, height=height, entries=entries
    )

    destination = Path(__file__).resolve().parent.parent / "src" / "saimc" / "web" / "map_cuba.py"
    destination.write_text(module, encoding="utf-8")
    total = sum(path.count(",") for path in paths.values())
    print(f"{destination}: {len(paths)} provincias, {total} puntos, viewBox 0 0 {width:g} {height:g}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
