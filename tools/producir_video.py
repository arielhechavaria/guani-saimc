# ruff: noqa: E501  (plantillas HTML/CSS en línea: partirlas las hace ilegibles)
"""Produce el video promocional de Guani (`guani-flujo.mp4`) de la landing.

Sigue `docs/guion-video-promocional.md`: doce escenas, 720×1280, voz de Edge TTS
y subtítulo quemado. Son cuadros fijos con fundidos, no una grabación: pesan poco
y no hace falta autoplay ni JavaScript.

Las capturas se toman del sitio real, **solo con `GET`**, y el script se detiene
si en el texto visible aparece un id de anuncio, un teléfono o un enlace de grupo
(las mismas guardas de `tools/grabar_entradas.py`).

Uso (con el servidor corriendo):

    python tools/producir_video.py --ad <uuid-del-anuncio-de-texto>
    python tools/producir_video.py --ad <uuid> --base http://127.0.0.1:8000 --work /tmp/video

Salida: `src/saimc/web/static/landing/guani-flujo.mp4` y `guani-flujo-poster.webp`.
Requiere `playwright` (Chromium), `imageio-ffmpeg`, `Pillow` y `edge-tts`.
"""

from __future__ import annotations

import argparse
import asyncio
import html
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import imageio_ffmpeg
from PIL import Image
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from grabar_entradas import forbidden_in

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "saimc" / "web" / "static"
OUT_DIR = STATIC / "landing"
FONTS = (STATIC / "fonts").as_uri()

FF = imageio_ffmpeg.get_ffmpeg_exe()
W, H = 720, 1280
GAP = 0.45  # silencio tras cada frase
FADE = 0.2
VOICE = "es-CU-ManuelNeural"
VOICE_RATE = "-4%"
CRF = 30
AUDIO_KBPS = 32
#: Tope de peso: el guion lo fija en ~1 MB (720×1280, 74 s).
MAX_BYTES = 1_100_000

#: Producto y provincia de la celda del tablero que se enseña (escenas 8 y 9).
CELL_PRODUCT = "Pollo"

# (rótulo grande, locución). La imagen de cada escena sale de `visual()`.
SCENES: list[tuple[str, str, str]] = [
    ("", "Precios reales del mercado informal cubano.", "¿Cuánto cuesta algo hoy en tu provincia?"),
    (
        "PASO 1",
        "Alguien publica su oferta",
        "Todo empieza cuando un emprendedor publica su oferta en un grupo o canal de WhatsApp.",
    ),
    (
        "PASO 2",
        "Escucha: llega solo, tal cual se escribió",
        "El anuncio le llega a Guani solo, tal cual se escribió. Nadie tiene que escribirle nada.",
    ),
    (
        "PASO 3",
        "Entiende: producto, precio, cantidad, provincia",
        "Un asistente saca el producto, el precio, la cantidad y la provincia.",
    ),
    (
        "PASO 3",
        "Misma unidad, misma moneda",
        "Y lo lleva todo a la misma unidad y a pesos cubanos, con la tasa informal de elTOQUE.",
    ),
    (
        "PASO 4",
        "Filtra: lo que no es un precio, se aparta",
        "Lo que no es un anuncio de precio se aparta solo, y cada semana una persona revisa una muestra.",
    ),
    (
        "PASO 4",
        "Lo claro, el sistema. Lo difícil, una persona.",
        "Lo claro lo acepta el sistema y una persona lo audita después; lo difícil lo decide una persona.",
    ),
    (
        "PASO 5",
        "Te lo enseña con su cuenta",
        "En el tablero ves el rango, la mediana y cuántos anuncios hay detrás de cada precio.",
    ),
    (
        "PASO 5",
        "A mano o por el sistema: siempre a la vista",
        "Al lado va cuántos revisó una persona y cuántos aceptó el sistema.",
    ),
    (
        "",
        "Por qué puedes fiarte",
        "Guani no te pide fe: cada cifra enseña sus anuncios, va provincia por provincia y un precio "
        "disparatado no manda.",
    ),
    (
        "",
        "Para quién es",
        "Sirve si compras o vendes, si revendes o decides, y si investigas o informas.",
    ),
    (
        "",
        "Ver los precios de hoy",
        "Es una muestra en formación, no un censo. Mira los precios de hoy en Guani.",
    ),
]

AVATAR = """<svg viewBox="0 0 80 80" width="{s}" height="{s}">
<circle cx="40" cy="40" r="39" fill="#ffffff" stroke="#c4cfcf" stroke-width="2"/>
<clipPath id="g{s}"><circle cx="40" cy="40" r="37"/></clipPath>
<g clip-path="url(#g{s})" transform="translate(40,40) scale(.8) translate(-40,-40)">
<ellipse cx="40" cy="30" rx="34" ry="34" fill="#191c1c"/><circle cx="40" cy="34" r="27" fill="#6f7979"/>
<rect x="13" y="20" width="54" height="8" rx="4" fill="#191c1c"/><path d="M52,22 L57,3 L62,22 Z" fill="#c4cfcf"/>
<ellipse cx="33" cy="36" rx="3" ry="2.3" fill="#ffffff"/><ellipse cx="47" cy="36" rx="3" ry="2.3" fill="#ffffff"/>
<path d="M33,48 Q40,52 47,48" stroke="#ffffff" stroke-width="2.4" fill="none" stroke-linecap="round"/>
<ellipse cx="40" cy="72" rx="40" ry="20" fill="#8a9494"/></g></svg>"""

CSS = f"""
@font-face{{font-family:Alice;src:url({FONTS}/alice.woff2)}}
@font-face{{font-family:Roboto;src:url({FONTS}/roboto.woff2);font-weight:100 900}}
*{{box-sizing:border-box}}
body{{margin:0;width:{W}px;height:{H}px;background:#05668d;color:#fff;font-family:Roboto,sans-serif;
  display:flex;flex-direction:column;overflow:hidden}}
body.dusk{{background:linear-gradient(180deg,#0b2c43 0%,#2f3a80 28%,#c8507a 58%,#f08a3c 80%,#ffd072 100%)}}
.top{{display:flex;align-items:center;gap:14px;padding:26px 36px 0;height:96px;flex:none}}
.brand{{font-family:Alice,serif;font-size:38px;flex-grow:1}}
.step{{font-size:22px;font-weight:700;letter-spacing:.1em;padding:6px 16px;border-radius:100px;
  background:#cce8e9;color:#041f21}}
.title{{flex:none;height:128px;padding:18px 44px 0;text-align:center;font-family:Alice,serif;
  font-size:46px;line-height:1.1;display:flex;align-items:center;justify-content:center}}
.stage{{flex-grow:1;display:flex;align-items:center;justify-content:center;min-height:0;position:relative}}
.cap{{flex:none;height:280px;padding:0 44px 118px;display:flex;align-items:center;justify-content:center;
  text-align:center;font-size:35px;line-height:1.28;font-weight:500}}
.phone{{height:800px;width:auto;border-radius:34px;border:10px solid #191c1c;background:#191c1c;
  box-shadow:0 18px 44px rgba(5,32,48,.45)}}
.card{{background:#fff;color:#191c1c;border-radius:26px;padding:26px 30px;box-shadow:0 14px 36px rgba(5,32,48,.35)}}
.glass{{background:rgba(255,255,255,.14);border:2px solid rgba(255,255,255,.3);border-radius:26px;padding:26px 30px}}
.chat{{width:520px;background:#e8f1f6;border-radius:34px;border:10px solid #191c1c;overflow:hidden;color:#191c1c;
  box-shadow:0 18px 44px rgba(5,32,48,.45)}}
.chat .bar{{display:flex;align-items:center;gap:12px;padding:16px 18px;background:#05668d;color:#fff}}
.chat .av{{width:46px;height:46px;border-radius:50%;background:#cce8e9;color:#05668d;font-weight:700;
  display:flex;align-items:center;justify-content:center;font-size:20px}}
.chat .t{{font-size:21px;font-weight:700}}.chat .s{{font-size:15px;opacity:.85}}
.chat .day{{margin:22px auto 8px;width:max-content;padding:4px 14px;border-radius:100px;background:#fff;
  font-size:14px;color:#3f4949}}
.chat .m{{margin:12px 18px 150px;padding:12px 14px 9px;border-radius:4px 18px 18px 18px;background:#fff;
  font-size:23px;line-height:1.4;white-space:pre-line;box-shadow:0 1px 2px rgba(5,32,48,.12);
  border:3px solid #05668d;max-width:90%}}
.chat .who{{font-size:16px;font-weight:700;color:#05668d;margin-bottom:3px}}
.pill{{display:inline-block;padding:6px 18px;border-radius:100px;background:#cce8e9;color:#041f21;
  font-weight:700;font-size:24px}}
.lane{{display:flex;align-items:center;gap:20px;padding:22px 26px;border-radius:24px;margin:16px 0;width:620px;
  background:rgba(255,255,255,.08);border:2px solid rgba(255,255,255,.18);opacity:.45;font-size:30px;
  line-height:1.2}}
.lane.on{{opacity:1;background:#fff;color:#191c1c;border-color:#fff;box-shadow:0 14px 36px rgba(5,32,48,.35)}}
.lane b{{display:block;font-size:34px}}
.lane .n{{flex:none;width:64px;height:64px;border-radius:50%;background:#cce8e9;color:#041f21;font-weight:700;
  font-size:32px;display:flex;align-items:center;justify-content:center}}
.fields{{display:grid;grid-template-columns:1fr 1fr;gap:16px;width:620px}}
.field{{background:#fff;color:#191c1c;border-radius:22px;padding:18px 22px}}
.field small{{display:block;font-size:20px;color:#3f4949;letter-spacing:.06em;text-transform:uppercase}}
.field b{{font-size:38px}}
.arrow{{font-size:60px;text-align:center;line-height:1;margin:8px 0}}
.result{{width:620px;background:#cce8e9;color:#041f21;border-radius:24px;padding:22px 26px;font-size:34px;
  text-align:center;font-weight:700}}
.trio{{display:flex;flex-direction:column;gap:20px;width:620px}}
.trio .card b{{display:block;font-size:36px;margin-bottom:4px}}
.trio .card span{{font-size:27px;color:#3f4949;line-height:1.3}}
.cta{{background:#ffd072;color:#08202f;font-weight:700;font-size:42px;padding:26px 54px;border-radius:100px;
  box-shadow:0 14px 36px rgba(0,0,0,.35)}}
.sig{{font-size:28px;opacity:.92;margin-top:22px;text-align:center}}
.sun{{position:absolute;width:560px;height:560px;border-radius:50%;
  background:radial-gradient(circle,#ffd072 0 38%,rgba(255,208,114,.35) 39% 60%,rgba(255,208,114,0) 72%)}}
.city{{position:absolute;left:0;right:0;bottom:0;height:300px}}
"""

CITY = """<svg class="city" viewBox="0 0 720 300" preserveAspectRatio="none">
<path d="M0 300V190h40v-30h30v30h50v-70h26v70h40v-40h60v40h30v-120l34-24 34 24v120h60v-50h50v50h60v-80h40v80h60v-30h46v30h54v70z" fill="#3b3f86" opacity=".7"/>
<path d="M0 300V230h60v-40h40v40h70v-60h30v60h50v-90h22v90h40v-24h70v24h50v-70h30v70h60v-34h60v34h70v66z" fill="#1b3159"/>
<path d="M0 300V262h90v-20h60v20h120v-28h70v28h120v-18h110v18h50v38z" fill="#0a2233"/></svg>"""


def page_html(body: str, *, dusk: bool = False) -> str:
    cls = ' class="dusk"' if dusk else ""
    return f'<!doctype html><html lang="es"><meta charset="utf-8"><style>{CSS}</style><body{cls}>{body}</body></html>'


def frame(step: str, title: str, caption: str, stage: str, *, dusk: bool = False) -> str:
    pill = f'<div class="step">{html.escape(step)}</div>' if step else ""
    body = (
        f'<div class="top">{AVATAR.format(s=56)}<div class="brand">Guani</div>{pill}</div>'
        f'<div class="title">{html.escape(title)}</div>'
        f'<div class="stage">{stage}</div>'
        f'<div class="cap">{html.escape(caption)}</div>'
    )
    return page_html(body, dusk=dusk)


def sunset(title: str, caption: str, extra: str = "") -> str:
    """Portada y cierre: el atardecer de la landing, con Guani sobre el sol."""
    stage = (
        '<div class="sun" style="top:20px"></div>'
        f'<div style="position:relative;margin-top:-60px">{AVATAR.format(s=300)}</div>'
        f"{CITY}{extra}"
    )
    body = (
        f'<div class="title" style="color:#fff;height:170px;padding-top:70px;font-size:56px">{html.escape(title)}</div>'
        f'<div class="stage">{stage}</div>'
        f'<div class="cap" style="position:relative;z-index:2;text-shadow:0 2px 12px rgba(8,32,47,.6)">'
        f"{html.escape(caption)}</div>"
    )
    return page_html(body, dusk=True)


def clean(text: str) -> str:
    return html.escape(" ".join(text.split()))


# ── Capturas ────────────────────────────────────────────────────────────────


def capture(base: str, ad_id: str, work: Path) -> dict:
    """Toma la página pública del anuncio y la celda del tablero. Solo `GET`."""
    shots: dict = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(
            viewport={"width": 390, "height": 812}, device_scale_factor=2, reduced_motion="reduce"
        )

        def guard(scene: str) -> None:
            found = forbidden_in(page.inner_text("body"))
            if found:
                raise SystemExit(f"[{scene}] la página muestra {', '.join(found)}: no se produce nada.")

        page.goto(f"{base}/anuncio/{ad_id}", wait_until="networkidle")
        guard("anuncio")
        shots["ad_text"] = page.locator("pre.ph-evidence-text").inner_text().strip()
        card = page.locator(".ph-evidence-card")
        card.screenshot(path=str(work / "anuncio.png"))
        shots["ad_card"] = work / "anuncio.png"

        page.goto(f"{base}/precios", wait_until="networkidle")
        guard("tablero")
        cell = page.locator("a.ph-fila").filter(has_text=CELL_PRODUCT).first
        cell.scroll_into_view_if_needed()
        cell.screenshot(path=str(work / "tablero.png"))
        shots["cell"] = work / "tablero.png"
        line = cell.get_by_text(re.compile(r"\d+ anuncios? · .*(mano|sistema)")).first
        shots["line_text"] = line.inner_text().strip()
        browser.close()
    return shots


def parse_ad(text: str) -> dict:
    """Producto, cantidad y precio del anuncio, solo para la tarjeta de la escena 5."""
    price = re.search(r"(\d[\d.\s]*)\s*(cup|usd|mlc)", text, re.I)
    qty = re.search(r"(\d+(?:[.,]\d+)?)\s*(lb|kg|l)\b", text, re.I)
    if not (price and qty):
        raise SystemExit("No encuentro precio y cantidad en el anuncio; elige otro con `--ad`.")
    amount = float(price.group(1).replace(" ", "").replace(".", ""))
    quantity = float(qty.group(1).replace(",", "."))
    return {
        "amount": amount,
        "currency": price.group(2).upper(),
        "qty": quantity,
        "unit": qty.group(2).lower(),
        "per_unit": amount / quantity,
    }


def img_tag(path: Path, cls: str = "phone", style: str = "") -> str:
    return f'<img class="{cls}" style="{style}" src="{path.as_uri()}">'


# ── Escenas ─────────────────────────────────────────────────────────────────


def build_frames(shots: dict, ad: dict) -> list[str]:
    pages: list[str] = []
    text = shots["ad_text"]
    n = len(SCENES)
    for i, (step, title, caption) in enumerate(SCENES):
        if i == 0:
            pages.append(sunset(title, caption))
            continue
        if i == n - 1:
            extra = (
                '<div style="position:absolute;top:560px;left:0;right:0;z-index:2;display:flex;flex-direction:column;'
                'align-items:center"><div class="cta">Ver los precios de hoy</div>'
                '<div class="sig">Guani, por Leira Studio<br>Una muestra en formación, no un censo.</div></div>'
            )
            pages.append(sunset("", caption, extra))
            continue
        if i == 1:
            stage = (
                '<div class="chat"><div class="bar"><div class="av">G</div><div><div class="t">Grupo de ventas</div>'
                '<div class="s">emprendedores</div></div></div><div class="day">Hoy</div>'
                f'<div class="m"><div class="who">Vendedor</div>{clean(text)}</div></div>'
            )
        elif i == 2:
            stage = (
                f'<div style="display:flex;flex-direction:column;align-items:center;gap:6px;width:620px">'
                f'<div class="card" style="width:100%;font-size:30px;border:3px solid #05668d">{clean(text)}</div>'
                '<div class="arrow">↓</div>'
                f'<div class="glass" style="display:flex;align-items:center;gap:22px;width:100%;font-size:34px">'
                f'{AVATAR.format(s=96)}<div><b>Guani</b><br><span style="font-size:26px;opacity:.9">'
                "recibe el anuncio sin cambiarlo</span></div></div></div>"
            )
        elif i == 3:
            stage = img_tag(shots["ad_card"], "card", "width:600px;height:auto;padding:0")
        elif i == 4:
            per = ad["per_unit"]
            per_txt = f"{per:,.0f}".replace(",", ".")
            amount_txt = f"{ad['amount']:,.0f}".replace(",", ".")
            qty_txt = f"{ad['qty']:g}"
            stage = (
                '<div style="display:flex;flex-direction:column;align-items:center">'
                '<div class="fields">'
                f'<div class="field"><small>Producto</small><b>{html.escape(CELL_PRODUCT)}</b></div>'
                f'<div class="field"><small>Precio</small><b>{amount_txt} {ad["currency"]}</b></div>'
                f'<div class="field"><small>Cantidad</small><b>{qty_txt} {ad["unit"]}</b></div>'
                '<div class="field"><small>Provincia</small><b>La Habana</b></div></div>'
                '<div class="arrow">↓</div>'
                f'<div class="result">{per_txt} {ad["currency"]} por {ad["unit"]}</div></div>'
            )
        elif i in (5, 6):
            on = {5: (True, False, False), 6: (False, True, True)}[i]
            lanes = (
                ("0", "Aparta lo que no es un precio", "«¿alguien sabe dónde…?»"),
                ("1", "Lo claro lo acepta el sistema", "y una persona lo audita después · con freno"),
                ("P", "Lo difícil lo decide una persona", "dudas, imágenes, casos raros"),
            )
            stage = (
                '<div style="display:flex;flex-direction:column;align-items:center">'
                + "".join(
                    f'<div class="lane{" on" if o else ""}"><div class="n">{n_}</div><div><b>{t}</b>{s}</div></div>'
                    for o, (n_, t, s) in zip(on, lanes, strict=True)
                )
                + "</div>"
            )
        elif i == 7:
            stage = img_tag(shots["cell"], "card", "width:600px;height:auto;padding:0")
        elif i == 8:
            # «3 anuncios · 2 a mano · 1 por el sistema»: se parte en tres cifras grandes.
            parts = [x.strip() for x in shots["line_text"].split("·")]
            chips = "".join(
                f'<div class="field" style="text-align:center"><b style="font-size:44px">{html.escape(x)}</b></div>'
                for x in parts
            )
            stage = (
                f'<div class="card" style="width:640px"><div style="font-size:34px;margin-bottom:18px">'
                f"<b>{html.escape(CELL_PRODUCT)}</b> · La Habana</div>"
                f'<div style="display:flex;flex-direction:column;gap:14px;background:#cce8e9;border-radius:22px;'
                f'padding:18px">{chips}</div></div>'
            )
        elif i == 9:
            cards = (
                ("Cada cifra enseña sus anuncios", "Al lado de cada precio, cuántos anuncios tiene detrás."),
                ("Provincia por provincia", "Nunca se mezclan en una sola cifra."),
                ("Un precio disparatado no manda", "Un valor fuera de lo normal no mueve la mediana."),
            )
            stage = (
                '<div class="trio">'
                + "".join(f'<div class="card"><b>{t}</b><span>{s}</span></div>' for t, s in cards)
                + "</div>"
            )
        else:
            cards = (
                ("Si compras o vendes", "¿Cuánto cuesta esto hoy en mi provincia?"),
                ("Si revendes o decides", "Qué se mueve y dónde."),
                ("Si investigas o informas", "Los datos en CSV, con su metodología."),
            )
            stage = (
                '<div class="trio">'
                + "".join(f'<div class="card"><b>{t}</b><span>{s}</span></div>' for t, s in cards)
                + "</div>"
            )
        pages.append(frame(step, title, caption, stage))
    return pages


# ── Voz y montaje ───────────────────────────────────────────────────────────


def duration(path: Path) -> float:
    err = subprocess.run([FF, "-i", str(path)], capture_output=True, text=True).stderr  # noqa: S603
    h, m, s = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err).groups()  # type: ignore[union-attr]
    return int(h) * 3600 + int(m) * 60 + float(s)


def speak(work: Path) -> list[Path]:
    try:
        import edge_tts
    except ImportError:
        raise SystemExit("Falta `edge-tts` (pip install edge-tts).") from None

    async def run() -> None:
        for i, (_, _, caption) in enumerate(SCENES, start=1):
            await edge_tts.Communicate(caption, VOICE, rate=VOICE_RATE).save(str(work / f"voz-{i:02d}.mp3"))

    asyncio.run(run())
    return [work / f"voz-{i:02d}.mp3" for i in range(1, len(SCENES) + 1)]


def render_frames(pages: list[str], work: Path) -> list[Path]:
    paths = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": W, "height": H})
        for i, doc in enumerate(pages, start=1):
            src = work / f"cuadro-{i:02d}.html"
            src.write_text(doc, encoding="utf-8")
            page.goto(src.as_uri(), wait_until="networkidle")
            page.evaluate("document.fonts.ready")
            out = work / f"cuadro-{i:02d}.png"
            page.screenshot(path=str(out))
            paths.append(out)
        browser.close()
    return paths


def build_video(frames: list[Path], voices: list[Path], out: Path) -> float:
    lengths = [duration(v) + GAP for v in voices]
    cmd = [FF, "-y", "-hide_banner", "-loglevel", "error"]
    for f, d in zip(frames, lengths, strict=True):
        cmd += ["-loop", "1", "-framerate", "12", "-t", f"{d:.3f}", "-i", str(f)]
    for v in voices:
        cmd += ["-i", str(v)]
    n = len(frames)
    parts = [
        f"[{i}:v]format=yuv420p,fade=t=in:st=0:d={FADE},fade=t=out:st={d - FADE:.3f}:d={FADE}[v{i}]"
        for i, d in enumerate(lengths)
    ]
    parts += [f"[{n + i}:a]aresample=44100,apad=pad_dur={GAP}[a{i}]" for i in range(n)]
    parts.append("".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[v]")
    parts.append("".join(f"[a{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1[a]")
    cmd += [
        "-filter_complex", ";".join(parts), "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "slow", "-tune", "stillimage", "-crf", str(CRF),
        "-g", "240", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", f"{AUDIO_KBPS}k", "-ac", "1",
        "-movflags", "+faststart", "-shortest", str(out),
    ]  # fmt: skip
    subprocess.run(cmd, check=True)  # noqa: S603
    return sum(lengths)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ad", required=True, help="id de un anuncio de texto con página pública limpia")
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument(
        "--work",
        default=str(Path(tempfile.gettempdir()) / "guani-video"),
        help="carpeta de trabajo (capturas, voz, cuadros)",
    )
    args = ap.parse_args()

    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    shots = capture(args.base, args.ad, work)
    ad = parse_ad(shots["ad_text"])
    pages = build_frames(shots, ad)
    frames = render_frames(pages, work)
    voices = speak(work)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "guani-flujo.mp4"
    total = build_video(frames, voices, out)
    Image.open(frames[0]).convert("RGB").save(
        OUT_DIR / "guani-flujo-poster.webp", "WEBP", quality=78, method=6
    )
    size = out.stat().st_size
    print(f"video: {out} · {size} bytes · {total:.1f} s")
    if size > MAX_BYTES:
        raise SystemExit(f"Pesa {size} bytes y el tope es {MAX_BYTES}: sube CRF o baja el audio.")


if __name__ == "__main__":
    main()
