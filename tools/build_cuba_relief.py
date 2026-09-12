"""Genera `src/saimc/web/static/relieve_cuba.webp`, el fondo de relieve del mapa.

No es parte del paquete que se despliega: es herramienta de build, y corre a
mano cuando haya que regenerar el asset. Existe por el mismo motivo que
`build_cuba_map.py` — reproducibilidad: una imagen de 21 KB commiteada sin
decir de dónde salió ni cómo se recortó es imposible de auditar o rehacer.

Fuentes (dos, las dos ya vienen citadas en el proyecto)
--------------------------------------------------------
1. El bounding box en grados sale del MISMO shapefile de provincias que usa
   `build_cuba_map.py` (`ne_10m_admin_1_states_provinces`, Natural Earth
   5.1.1) — no un rectángulo elegido a ojo.
2. El ráster de relieve es `NE2_HR_LC_SR_W_DR` («Natural Earth II, alta
   resolución, con cobertura de suelo, relieve sombreado, agua y drenajes»)
   de Natural Earth, la serie 10m:

    curl -L -o ne10_provincias.zip \\
      https://naciscdn.org/naturalearth/10m/cultural/ne_10m_admin_1_states_provinces.zip
    curl -L -o ne2_relieve.zip \\
      https://naciscdn.org/naturalearth/10m/raster/NE2_HR_LC_SR_W_DR.zip
    python tools/build_cuba_relief.py ne10_provincias.zip ne2_relieve.zip

**Licencia: dominio público**, las dos. Mismo motivo que `build_cuba_map.py`
—ver ese docstring—: es la fuente que el pie del mapa ya cita, así que este
fondo no suma ninguna atribución nueva.

Por qué esta variante del ráster y no la de 50m (`NE2_50M_SR_W`, ~89 MB, la
primera que se probó): recortada al bounding box de Cuba, la de 50m da
~324×103 px — maleza pixelada al escalarla a los 1000 px del `viewBox`. Esta
(10m, ~326 MB) da ~650×207 px, el doble de lineal, y a esta escala (un fondo,
no un mapa para navegar con zoom libre) ya alcanza.

Alineación
----------
El recorte usa el bounding box en grados de las 16 provincias, y el resize
final va directo del recorte en píxeles crudos del ráster (equirectangular
sin distorsionar, un píxel = 1/60° tanto en x como en y) a
`(TARGET_WIDTH, height)` del `viewBox`. Un solo `resize` no uniforme
reproduce exactamente el mismo achatado horizontal por
`stretch = cos(latitud_media)` que usa la proyección de las provincias, sin
tener que calcularlo aparte: la razón width/height del recorte en grados y la
razón width/height del `viewBox` difieren exactamente en ese `stretch`, así
que un `Image.resize(old_size, new_size)` sin mantener aspecto lo aplica
solo. Verificado dibujando las 16 rutas SVG encima del resultado: la costa
de la imagen y los bordes vectoriales coinciden al píxel (sin ese chequeo,
un desalineamiento de unos pocos píxeles sería invisible a simple vista pero
real).

No hay dependencia de GDAL ni de un lector de TIFF aparte: Pillow (ya es
dependencia del proyecto, para OCR) alcanza para abrir el `.tif`, recortar y
reescalar. `Image.MAX_IMAGE_PIXELS` se desactiva a propósito: el TIFF de
origen (21600×10800, ~700 MB descomprimido) supera el límite por defecto de
Pillow contra bombas de descompresión, y acá el archivo es de una fuente
confiable citada arriba, no un upload de un desconocido.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_cuba_map import build_paths, load_cuba  # noqa: E402

RELIEF_STEM = "NE2_HR_LC_SR_W_DR"

#: Mismo `TARGET_WIDTH` que `build_cuba_map.py`: el fondo tiene que compartir
#: viewBox con las 16 rutas para alinear sin ningún cálculo en JS.
TARGET_WIDTH = 1000

#: Del `.tfw` que trae el ráster: tamaño de píxel en grados (1/60°) y
#: coordenada del centro de su primer píxel (esquina noroeste del planeta).
#: No se leen del `.tfw` en tiempo de build para no sumarle a este script un
#: parser de otro formato más — son constantes del producto Natural Earth
#: 10m, documentadas acá en vez de en código disperso.
PIXEL_SIZE = 1 / 60
ORIGIN_LON = -180.0 + PIXEL_SIZE / 2
ORIGIN_LAT = 90.0 - PIXEL_SIZE / 2


def _province_bbox(provinces_zip: Path) -> tuple[float, float, float, float]:
    """Bounding box en grados de los mismos puntos (ya simplificados) que
    `build_cuba_map.py` proyecta para las 16 provincias."""
    from build_cuba_map import SIMPLIFY_EPSILON, simplify

    provinces = load_cuba(provinces_zip)
    simplified = []
    for _name, rings in provinces:
        kept = [sr for ring in rings if len(sr := simplify(ring, SIMPLIFY_EPSILON)) >= 4]
        simplified.append(kept)

    points = [p for rings in simplified for ring in rings for p in ring]
    min_lon = min(p[0] for p in points)
    max_lon = max(p[0] for p in points)
    min_lat = min(p[1] for p in points)
    max_lat = max(p[1] for p in points)
    return min_lon, max_lon, min_lat, max_lat


def main() -> int:
    if len(sys.argv) != 3:
        print(
            f"uso: {sys.argv[0]} <ne_10m_admin_1_states_provinces.zip> <{RELIEF_STEM}.zip>",
            file=sys.stderr,
        )
        return 1

    from PIL import Image

    # Fuente confiable (Natural Earth, citada arriba): el .tif de origen
    # excede el límite por defecto de Pillow contra bombas de descompresión.
    Image.MAX_IMAGE_PIXELS = None

    provinces_zip = Path(sys.argv[1])
    relief_zip = Path(sys.argv[2])

    min_lon, max_lon, min_lat, max_lat = _province_bbox(provinces_zip)
    _paths, target_width, target_height = build_paths(load_cuba(provinces_zip))
    assert target_width == TARGET_WIDTH

    with zipfile.ZipFile(relief_zip) as archive:
        tif_bytes = archive.read(f"{RELIEF_STEM}.tif")

    import io

    image = Image.open(io.BytesIO(tif_bytes))

    left = (min_lon - ORIGIN_LON) / PIXEL_SIZE
    right = (max_lon - ORIGIN_LON) / PIXEL_SIZE
    top = (ORIGIN_LAT - max_lat) / PIXEL_SIZE
    bottom = (ORIGIN_LAT - min_lat) / PIXEL_SIZE
    crop_box = (int(left), int(top), int(right) + 1, int(bottom) + 1)

    cropped = image.crop(crop_box)
    # Un solo resize no uniforme: ver «Alineación» en el docstring del
    # módulo para por qué esto ya aplica el achatado por `stretch` sin
    # calcularlo aparte.
    final = cropped.resize((TARGET_WIDTH, round(target_height)), Image.LANCZOS)

    destination = (
        Path(__file__).resolve().parent.parent
        / "src" / "saimc" / "web" / "static" / "relieve_cuba.webp"
    )
    final.save(destination, quality=82, method=6)
    print(f"{destination}: {final.size[0]}×{final.size[1]}px, {destination.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
