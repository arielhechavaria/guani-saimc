"""Graba los clips y las capturas de «Entra por donde quieras» (landing, `/`).

Un clip corto y una captura por vista pública: el tablero, el mapa y Explorar.
Cada clip se graba con Playwright sobre el sitio real, **solo lectura** (todo
son `GET`: no se envía ningún formulario ni se llama a ninguna API de pago), y
se recomprime con ffmpeg a un mp4 mudo y liviano. La captura es el último
cuadro de la escena y va de `poster`: es lo que ve quien no baja el clip.

Antes de grabar, cada escena comprueba que la página no muestra nada de lo que
la landing no puede enseñar: ni ids de anuncio, ni emisores, ni teléfonos, ni
nombres de canal. Si algo así aparece, el script se detiene sin escribir nada.

Uso (con el servidor corriendo; el puerto por defecto es el de desarrollo):

    python tools/grabar_entradas.py                  # las tres vistas
    python tools/grabar_entradas.py mapa             # solo una
    python tools/grabar_entradas.py --base http://127.0.0.1:8000

Salida: `src/saimc/web/static/landing/entradas-<vista>.mp4` y `.webp`.
Requiere los extras de marketing (`playwright`, Chromium) e `imageio-ffmpeg`.
"""

from __future__ import annotations

import argparse
import io
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from PIL import Image

#: Medida de la ventana de grabación: un teléfono, que es lo que usa el
#: visitante. El clip se reduce después; la captura se toma al doble de
#: densidad y se reduce a `POSTER_WIDTH`.
VIEWPORT = {"width": 390, "height": 812}
CLIP_WIDTH = 360
CLIP_SECONDS = 6.0
CLIP_MIN_SECONDS = 4.5
CLIP_FPS = 15
CLIP_CRF = 28
POSTER_WIDTH = 480
POSTER_QUALITY = 70

OUT_DIR = Path(__file__).resolve().parents[1] / "src" / "saimc" / "web" / "static" / "landing"

#: Lo que no puede verse en ninguna de las escenas. Es una guarda, no un
#: filtro: si alguna sale, hay que arreglar la página o la escena, no esconderla.
_FORBIDDEN = (
    ("un id de anuncio", re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")),
    # Móvil cubano de ocho dígitos (empieza en 5), con o sin +53, o cualquier
    # número internacional con «+». Una fecha o un precio no encajan.
    (
        "un teléfono",
        re.compile(r"(?<![\d.,-])(?:\+\s?53[\s-]?)?5\d{3}[\s-]?\d{4}(?![\d.,])|\+\d[\d\s-]{8,}\d"),
    ),
    ("un identificador de WhatsApp", re.compile(r"@(?:c|g|s)\.us|@newsletter|wa\.me/", re.I)),
    ("un enlace de invitación", re.compile(r"chat\.whatsapp\.com|t\.me/", re.I)),
)


def forbidden_in(text: str) -> list[str]:
    """Qué cosas prohibidas aparecen en el texto de la página (vacío si ninguna)."""
    return [name for name, pattern in _FORBIDDEN if pattern.search(text)]


def _assert_clean(page, scene: str) -> None:
    """Corta la grabación si la página visible muestra algo prohibido."""
    # Solo el texto que se ve: un id dentro de un `href` no sale en el clip.
    found = forbidden_in(page.inner_text("body"))
    if found:
        raise SystemExit(f"[{scene}] la página muestra {', '.join(found)}: no se graba.")


# ── Escenas ─────────────────────────────────────────────────────────────────
# Cada una recibe la página ya cargada en su vista y hace lo que haría un
# visitante. Las pausas son parte del guion: el ojo necesita ver el antes y el
# después de cada toque.


def _pause(page, ms: int) -> None:
    page.wait_for_timeout(ms)


def scene_tablero(page, mark: Callable[[], None]) -> None:
    """La lista con la línea de procedencia de cada celda y el desglose abierto."""
    _pause(page, 700)
    first = page.locator("a.ph-fila").first
    first.scroll_into_view_if_needed()
    _pause(page, 900)
    first.click()
    page.wait_for_load_state("networkidle")
    panel = page.locator("details.ph-confianza").first
    panel.scroll_into_view_if_needed()
    _pause(page, 600)
    panel.locator("summary").click()
    _pause(page, 1200)


#: El relleno de una provincia sin datos en el mapa (`map_view`). Las demás
#: —las que tienen anuncios— se pintan con otro color.
_EMPTY_FILL = "#e3e9ea"


def scene_mapa(page, mark: Callable[[], None]) -> None:
    """Tocar una provincia con datos. Tocar una vacía («Sin datos todavía, 0
    observaciones») no enseña nada, así que se elige entre las pintadas."""
    page.wait_for_selector("path.leaflet-interactive")
    _pause(page, 1000)
    paths = page.locator("path.leaflet-interactive")
    with_data = []
    for i in range(paths.count()):
        box = paths.nth(i).bounding_box()
        if box and paths.nth(i).get_attribute("fill") != _EMPTY_FILL:
            with_data.append((box["width"] * box["height"], i))
    if not with_data:
        raise SystemExit("[mapa] ninguna provincia tiene datos: no se graba.")
    target = paths.nth(max(with_data)[1])
    mark()
    _pause(page, 700)
    target.click(force=True)
    _pause(page, 2400)


def scene_explorar(page, mark: Callable[[], None]) -> None:
    """Abrir una fila de provincia."""
    # El estado vacío de Explorar («Elegí un producto…») no entra al clip: se
    # elige el producto antes de que empiece lo que se ve.
    # El producto con más anuncios: el que mejor muestra una fila abierta.
    pills = page.locator("a.ph-ex-pill")
    counts = [int(pills.nth(i).locator("small").inner_text()) for i in range(pills.count())]
    pills.nth(counts.index(max(counts))).click()
    page.wait_for_load_state("networkidle")
    mark()
    _pause(page, 1200)
    page.locator("button.ph-ex-chev").first.click()
    _pause(page, 1400)


SCENES: dict[str, tuple[str, Callable]] = {
    "tablero": ("/precios?vista=tablero", scene_tablero),
    "mapa": ("/precios?vista=mapa", scene_mapa),
    "explorar": ("/precios?vista=explorar", scene_explorar),
}


# ── Grabación y recompresión ────────────────────────────────────────────────


def _ffmpeg() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def _to_mp4(webm: Path, start: float, out: Path) -> None:
    """Recorta, reduce y comprime. Mudo, sin metadatos, con `faststart`."""
    subprocess.run(  # noqa: S603 — argumentos fijos, ffmpeg propio
        [
            _ffmpeg(),
            "-y",
            "-loglevel",
            "error",
            "-ss",
            f"{start:.2f}",
            "-t",
            f"{CLIP_SECONDS}",
            "-i",
            str(webm),
            "-vf",
            f"fps={CLIP_FPS},scale={CLIP_WIDTH}:-2:flags=lanczos",
            "-c:v",
            "libx264",
            "-preset",
            "veryslow",
            "-crf",
            str(CLIP_CRF),
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-an",
            "-map_metadata",
            "-1",
            str(out),
        ],
        check=True,
    )


def _poster(png: bytes, out: Path) -> None:
    img = Image.open(io.BytesIO(png)).convert("RGB")
    img = img.resize((POSTER_WIDTH, round(img.height * POSTER_WIDTH / img.width)), Image.LANCZOS)
    img.save(out, "WEBP", quality=POSTER_QUALITY, method=6)


def record(base: str, name: str, out_dir: Path) -> None:
    from playwright.sync_api import sync_playwright

    path, scene = SCENES[name]
    with tempfile.TemporaryDirectory() as tmp, sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(
            viewport=VIEWPORT,
            device_scale_factor=2,
            record_video_dir=tmp,
            record_video_size=VIEWPORT,
            locale="es",
        )
        page = ctx.new_page()
        t0 = time.monotonic()
        page.goto(base + path)
        page.wait_for_load_state("networkidle")
        _assert_clean(page, name)
        started = [time.monotonic() - t0]

        def mark() -> None:
            """Desde acá empieza lo que se ve en el clip."""
            started[0] = time.monotonic() - t0

        scene(page, mark)
        # El final se sostiene hasta completar el mínimo: el clip no termina
        # justo cuando pasa lo que hay que ver.
        held = time.monotonic() - t0 - started[0]
        if held < CLIP_MIN_SECONDS:
            page.wait_for_timeout(int((CLIP_MIN_SECONDS - held) * 1000))
        _assert_clean(page, name)
        start = started[0]
        poster = page.screenshot()
        ctx.close()  # cierra y vuelca el video
        browser.close()
        webm = next(Path(tmp).glob("*.webm"))
        out_dir.mkdir(parents=True, exist_ok=True)
        _to_mp4(webm, start, out_dir / f"entradas-{name}.mp4")
        _poster(poster, out_dir / f"entradas-{name}.webp")
    for ext in ("mp4", "webp"):
        f = out_dir / f"entradas-{name}.{ext}"
        print(f"{f.name}: {f.stat().st_size / 1000:.0f} KB")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("vistas", nargs="*", choices=list(SCENES))
    parser.add_argument("--base", default="http://127.0.0.1:8773")
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    args = parser.parse_args()
    for name in args.vistas or SCENES:
        record(args.base, name, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
