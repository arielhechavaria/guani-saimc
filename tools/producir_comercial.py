"""Produce el comercial «Como se escoge el arroz» (`docs/concepto-comercial-guani.md`).

Es animación renderizada cuadro a cuadro, no una grabación: la escena
(`tools/comercial/escena.html`) dibuja en un lienzo de 720×1280 el cuadro de
cada instante, un navegador sin ventana los va sacando a 30 por segundo y ffmpeg
los monta con el sonido, que se sintetiza aparte (`tools/comercial/sonido.py`).
Los tiempos de imagen y sonido salen de una sola fuente: `tools/comercial/tiempos.py`.

Hoy existen los primeros 25,8 s: gancho, reguero y primera pasada (la prueba
aprobada, hasta 13 s), el repaso (B4) y la misma medida (B5). Faltan la isla (B6)
y el cierre (B7).

Uso:

    python tools/producir_comercial.py --out /tmp/guani-comercial/comercial.mp4
    python tools/producir_comercial.py --hasta 6.6 --sin-voz          # hasta ahí, sin red
    python tools/producir_comercial.py --desde 12.5 --prefijo b45-     # un tramo, con su sonido
    python tools/producir_comercial.py --solo-cuadros --cuadros 2.0,5.0
    python tools/producir_comercial.py --solo-sonido                   # sonido, espectro e informe

Deja en la carpeta de trabajo (`--work`) el mp4, `contacto.png` (un cuadro cada
medio segundo), los cuadros sueltos que se pidan, `sonido.wav`, `espectro.png` y
`informe.json` (dónde quedó cada golpe y cada línea de locución). Con `--prefijo`
el mp4, los cuadros, la hoja de contacto y el espectro llevan ese prefijo, para
no pisar los de otra producción guardada en la misma carpeta.

No lee el sitio ni envía nada, salvo el texto de la locución al servicio de voz
de Edge TTS. Requiere `playwright` (Chromium), `imageio-ffmpeg`, `Pillow`,
`numpy` y, para la voz, `edge-tts`.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import subprocess
import tempfile
import time
from pathlib import Path

import imageio_ffmpeg
from comercial import sonido, tiempos
from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright

SCENE = Path(__file__).resolve().parent / "comercial" / "escena.html"
FF = imageio_ffmpeg.get_ffmpeg_exe()
W, H = 720, 1280
CRF = 25
AUDIO_KBPS = 96
#: Los cuadros sueltos: los momentos donde algo tiene que estar en su sitio.
STILLS = (0.5, 1.0, 2.0, 2.5, 2.9, 3.6, 5.0, 6.7, 7.8, 8.8, 10.3, 11.4, 12.8)  # la prueba
STILLS += (13.4, 14.4, 15.4, 16.4, 17.4, 18.4, 19.2, 20.0, 21.0, 22.0, 23.0, 24.0, 25.6)  # B4 y B5
SHEET_EVERY = 0.5
THUMB = (216, 384)


def open_scene(playwright, timeline: dict):
    """Abre la escena en un Chromium sin ventana y la deja lista para pedirle cuadros."""
    browser = playwright.chromium.launch(args=["--allow-file-access-from-files"])
    page = browser.new_page(viewport={"width": W, "height": H})
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.goto(SCENE.as_uri())
    info = page.evaluate("cfg => window.init(cfg)", {"timeline": timeline, "data": tiempos.DATA})
    if errors:
        raise SystemExit("La escena no arrancó: " + "; ".join(errors))
    return browser, page, info


def frame_png(page, t: float, samples: int = 0) -> bytes:
    """El cuadro del instante `t`, en PNG. `samples` fuerza cuántos subcuadros mezclar."""
    url = page.evaluate("([t, n]) => window.capture(t, n || undefined)", [t, samples])
    return base64.b64decode(url.split(",", 1)[1])


def contact_sheet(thumbs: list[tuple[float, Image.Image]], path: Path, cols: int = 9) -> None:
    """Hoja de contacto: una miniatura por cada medio segundo, con su tiempo."""
    rows = (len(thumbs) + cols - 1) // cols
    sheet = Image.new("RGB", (THUMB[0] * cols, THUMB[1] * rows), "black")
    draw = ImageDraw.Draw(sheet)
    for i, (t, thumb) in enumerate(thumbs):
        x, y = (i % cols) * THUMB[0], (i // cols) * THUMB[1]
        sheet.paste(thumb, (x, y))
        draw.rectangle((x, y, x + 44, y + 15), fill="black")
        draw.text((x + 3, y + 2), f"{t:.1f} s", fill="white")
    sheet.save(path)


def render(args: argparse.Namespace, timeline: dict, work: Path, audio: Path | None) -> dict:
    """Saca los cuadros y los monta. Devuelve duración, peso y tiempo de render."""
    fps = timeline["fps"]
    first, total = round(args.desde * fps), round(args.hasta * fps)
    stills = {round(t * fps): t for t in args.cuadros if args.desde <= t <= args.hasta}

    def still(t: float) -> Path:
        return work / f"{args.prefijo}cuadro-{t:05.2f}.png"

    every = round(SHEET_EVERY * fps)
    thumbs: list[tuple[float, Image.Image]] = []
    started = time.time()

    with sync_playwright() as playwright:
        browser, page, info = open_scene(playwright, timeline)
        print("escena:", info)
        if args.solo_cuadros:
            for index, t in sorted(stills.items()):
                still(t).write_bytes(frame_png(page, index / fps, args.subcuadros))
                print(f"cuadro {t:5.2f} s")
            browser.close()
            return {"cuadros": len(stills)}

        cmd = [FF, "-y", "-hide_banner", "-loglevel", "error", "-f", "image2pipe", "-c:v", "png"]
        cmd += ["-framerate", str(fps), "-i", "-"]
        if audio:  # un tramo lleva el trozo de sonido que le toca
            cmd += ["-ss", f"{first / fps:.4f}", "-i", str(audio)]
        cmd += ["-c:v", "libx264", "-preset", "veryslow", "-tune", "animation", "-crf", str(CRF)]
        cmd += ["-pix_fmt", "yuv420p", "-r", str(fps)]
        if audio:
            cmd += ["-c:a", "aac", "-b:a", f"{AUDIO_KBPS}k", "-ac", "2", "-shortest"]
        cmd += ["-movflags", "+faststart", str(args.out)]
        ffmpeg = subprocess.Popen(cmd, stdin=subprocess.PIPE)  # noqa: S603
        assert ffmpeg.stdin is not None  # noqa: S101
        for index in range(first, total):
            png = frame_png(page, index / fps, args.subcuadros)
            ffmpeg.stdin.write(png)
            if index in stills:
                still(stills[index]).write_bytes(png)
            if index % every == 0:
                thumb = Image.open(io.BytesIO(png)).convert("RGB").resize(THUMB, Image.LANCZOS)
                thumbs.append((index / fps, thumb))
            if index % fps == 0:
                print(
                    f"  {index / fps:4.0f} s de {args.hasta:.1f} · {time.time() - started:5.0f} s de render",
                    flush=True,
                )
        ffmpeg.stdin.close()
        if ffmpeg.wait() != 0:
            raise SystemExit("ffmpeg no pudo montar el video.")
        browser.close()

    contact_sheet(thumbs, work / f"{args.prefijo}contacto.png")
    size, seconds = args.out.stat().st_size, (total - first) / fps
    return {
        "segundos": round(seconds, 2),
        "bytes": size,
        "kbps": round(size * 8 / seconds / 1000),
        "render_s": round(time.time() - started),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hasta", type=float, default=tiempos.DURATION, help="segundos a producir")
    ap.add_argument(
        "--desde", type=float, default=0.0, help="empieza en este segundo (un tramo, con su sonido)"
    )
    ap.add_argument("--work", type=Path, default=Path(tempfile.gettempdir()) / "guani-comercial")
    ap.add_argument(
        "--prefijo", default="", help="prefijo del mp4, los cuadros, la hoja de contacto y el espectro"
    )
    ap.add_argument("--out", type=Path, help="el mp4 (por defecto, <work>/<prefijo>comercial.mp4)")
    ap.add_argument(
        "--cuadros", type=lambda s: [float(x) for x in s.split(",")], default=list(STILLS),
        help="tiempos de los cuadros sueltos, separados por comas",
    )  # fmt: skip
    ap.add_argument("--solo-cuadros", action="store_true", help="solo guarda esos cuadros, sin montar")
    ap.add_argument(
        "--subcuadros", type=int, default=0, help="fuerza los subcuadros por cuadro (0: los de la escena)"
    )
    ap.add_argument("--sin-voz", action="store_true", help="monta sin locución (no usa la red)")
    ap.add_argument("--solo-sonido", action="store_true", help="solo el sonido, su espectro y el informe")
    args = ap.parse_args()

    args.hasta = min(args.hasta, tiempos.DURATION)
    args.work.mkdir(parents=True, exist_ok=True)
    args.out = args.out or args.work / f"{args.prefijo}comercial.mp4"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    timeline = tiempos.build()

    audio, report = None, {}
    if not args.solo_cuadros:  # el sonido va siempre desde 0: un tramo toma de ahí su trozo
        audio, report = sonido.build(timeline, args.work, args.hasta, voice=not args.sin_voz)
        sonido.spectrogram(audio, args.work / f"{args.prefijo}espectro.png", report["eventos"])
        for line in report["voz"]:
            span, over = f"{line['start']:5.2f}–{line['end']:5.2f} s", line["sobre_la_cama_db"]
            print(f"voz {span} · {over:+.0f} dB sobre la cama · {line['text']}")
    if not args.solo_sonido:
        report["video"] = render(args, timeline, args.work, audio)
    report["linea_de_tiempo"] = timeline
    (args.work / "informe.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print("listo:", report.get("video", "solo el sonido"))


if __name__ == "__main__":
    main()
