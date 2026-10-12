"""Sonido del comercial: sin música, todo sintetizado aquí y mezclado con la voz.

Cada capa sale de la línea de tiempo (`tiempos.py`), la misma que mueve la
imagen: un golpe que se ve y un golpe que se oye son el mismo número.

Las capas, por orden de entrada:

- un golpe de madera por cada cápsula del gancho, y un tecleo muy suave;
- la avalancha: arroz que cae (ruido en granos, filtrado), que crece y gotea;
- el soplido del tirón de cámara;
- el mar (ruido marrón con oleaje lento), muy bajo, y la clave de son 3-2;
- el golpe grave de Guani al caer y los granos que saltan;
- dos barridos fríos, uno por cada lectura;
- el arroz que resbala al apartar lo que no es un precio;
- un tic «cuadrado» por cada anuncio que la máquina da por claro;
- un latido cálido para lo dudoso.

Hasta ahí llega la prueba aprobada (`_proof`), que se sintetiza igual que
entonces. Lo que sigue (`_after`) usa otra semilla, para no mover nada de lo
anterior:

- la persona (B4): el roce del lápiz en cada trazo y un golpe grave y suave por
  cada grano repasado, por tandas que cierran en la clave;
- la cámara que entra en la fila, y la máquina (B5): tics cuadrados al subrayar
  y al caer cada dato, y un tecleo muy tenue;
- las dos cuentas: un tictac que sube con los números y una nota cuando la cifra
  queda fija;
- el cierre con los dos trazos: el roce del aro a mano y el tic de la marca cuadrada.

No hay scipy: los filtros son de fase cero por transformada de Fourier y los
granos se reparten con una convolución. La voz es de Edge TTS (el texto de la
locución viaja al servicio de voz de Microsoft, sin cuenta ni costo).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import wave
from pathlib import Path

import imageio_ffmpeg
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from comercial import tiempos

SR = 44100
FF = imageio_ffmpeg.get_ffmpeg_exe()
SEED = 20261011
#: Dónde el mar de la prueba le da el relevo al que sigue. Acaba un poco antes del
#: segundo 13: justo ahí el mar de la prueba trae el chasquido de su propio filtro
#: (en la prueba lo tapaba el fundido de salida).
SEAM = (tiempos.PROOF - 0.4, tiempos.PROOF - 0.1)


# ── Piezas ──────────────────────────────────────────────────────────────────


def _n(seconds: float) -> int:
    return max(1, round(seconds * SR))


def _time(seconds: float) -> np.ndarray:
    return np.arange(_n(seconds)) / SR


def band(x: np.ndarray, lo: float | None = None, hi: float | None = None, order: int = 3) -> np.ndarray:
    """Filtro de fase cero por FFT, con la caída de un Butterworth de `order`."""
    spec = np.fft.rfft(x)
    freq = np.fft.rfftfreq(len(x), 1 / SR)
    gain = np.ones_like(freq)
    if lo:
        gain *= 1 / np.sqrt(1 + (lo / np.maximum(freq, 1e-6)) ** (2 * order))
    if hi:
        gain *= 1 / np.sqrt(1 + (freq / hi) ** (2 * order))
    return np.fft.irfft(spec * gain, len(x))


def convolve(x: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    """Convolución por FFT; devuelve el mismo largo que `x`."""
    size = len(x) + len(kernel) - 1
    out = np.fft.irfft(np.fft.rfft(x, size) * np.fft.rfft(kernel, size), size)
    return out[: len(x)]


def ramp(t: np.ndarray, points: list[tuple[float, float]]) -> np.ndarray:
    """Envolvente por tramos rectos: `points` son pares (segundo, valor)."""
    xs, ys = zip(*points, strict=True)
    return np.interp(t, xs, ys)


def wood(freqs: list[float], taus: list[float], amps: list[float], click: float, rng) -> np.ndarray:
    """Un golpe de madera: parciales que se apagan rápido y un chasquido al principio."""
    t = _time(max(taus) * 7)
    out = sum(
        a * np.sin(2 * np.pi * f * t) * np.exp(-t / tau) for f, tau, a in zip(freqs, taus, amps, strict=True)
    )
    burst = rng.standard_normal(len(t)) * np.exp(-t / 0.0012)
    out = out + click * band(burst, 1500, 9000)
    out[: _n(0.0006)] *= np.linspace(0, 1, _n(0.0006))
    return out


def thump(f0: float, f1: float, seconds: float, rng, noise: float = 0.3) -> np.ndarray:
    """Un golpe grave: un seno que baja de tono y un poco de ruido sordo."""
    t = _time(seconds)
    freq = f1 + (f0 - f1) * np.exp(-t / (seconds * 0.22))
    body = np.sin(2 * np.pi * np.cumsum(freq) / SR) * np.exp(-t / (seconds * 0.3))
    dull = band(rng.standard_normal(len(t)), None, 520) * np.exp(-t / 0.02)
    out = body + noise * dull / (np.abs(dull).max() + 1e-9)
    out[: _n(0.002)] *= np.linspace(0, 1, _n(0.002))
    return out


def sweep(seconds: float, f0: float, f1: float, width: float, rng) -> np.ndarray:
    """Ruido de banda estrecha cuyo centro viaja de `f0` a `f1`."""
    t = _time(seconds)
    carrier = np.sin(2 * np.pi * np.cumsum(np.geomspace(f0, f1, len(t))) / SR)
    slow = band(rng.standard_normal(len(t)), None, width)
    return carrier * slow / (np.abs(slow).max() + 1e-9)


def rice(total: float, density: np.ndarray, lo: float, hi: float, rng) -> np.ndarray:
    """Arroz que cae o resbala: miles de granos de ruido, tantos por segundo como diga `density`."""
    n = _n(total)
    hits = (rng.random(n) < density[:n] / SR) * rng.lognormal(0.0, 0.6, n)
    t = _time(0.007)
    grain = band(rng.standard_normal(len(t)), lo, hi) * np.exp(-t / 0.0016)
    return convolve(hits, grain / (np.abs(grain).max() + 1e-9))


def brown(seconds: float, rng) -> np.ndarray:
    x = np.cumsum(rng.standard_normal(_n(seconds)))
    x = band(x, 45, None)
    return x / (np.abs(x).max() + 1e-9)


def square_tick(freq: float, seconds: float) -> np.ndarray:
    """Un tic «cuadrado»: onda cuadrada de banda limitada, muy corta. El sonido de la máquina."""
    t = _time(seconds)
    harmonics = range(1, 12, 2)
    out = sum(np.sin(2 * np.pi * freq * k * t) / k for k in harmonics if freq * k < SR / 2.2)
    return out * np.exp(-t / (seconds * 0.3))


def scratch(seconds: float, rng, lo: float = 1700, hi: float = 6500) -> np.ndarray:
    """El roce de un lápiz: ruido de papel que sigue a la mano (arranca, corre y frena)."""
    t = _time(seconds + 0.03)
    u = np.clip(t / seconds, 0, 1)
    speed = 0.25 + 4.5 * u * (1 - u)  # la derivada de cómo avanza un trazo en la escena
    tooth = band(rng.standard_normal(len(t)), 30, 260)  # el diente del papel: el roce no es parejo
    tooth = 0.6 + 0.4 * tooth / (np.abs(tooth).max() + 1e-9)
    out = band(rng.standard_normal(len(t)), lo, hi) * speed * tooth
    out *= np.minimum(t / 0.008, 1) * np.clip((seconds + 0.03 - t) / 0.03, 0, 1)
    return out / (np.abs(out).max() + 1e-9)


def tap(freq: float, rng) -> np.ndarray:
    """El golpe grave y suave de la yema sobre un grano.

    La fundamental (unos 150 Hz) no sale por el altavoz de un teléfono: lo que se
    oye ahí son sus parciales de 2× y 3× y el toque de la piel.
    """
    t = _time(0.24)
    out = sum(
        amp * np.sin(2 * np.pi * freq * k * t) * np.exp(-t / tau)
        for k, amp, tau in ((1, 1.0, 0.05), (2, 0.6, 0.034), (3, 0.34, 0.022), (4.2, 0.12, 0.012))
    )
    skin = band(rng.standard_normal(len(t)), 300, 1800) * np.exp(-t / 0.005)
    out = out * np.minimum(t / 0.004, 1) + 0.3 * skin / (np.abs(skin).max() + 1e-9)
    return out / (np.abs(out).max() + 1e-9)


def chime(freq: float) -> np.ndarray:
    """El acento de una cifra que queda fija: una nota limpia y corta, fría como lo de la máquina."""
    t = _time(0.55)
    out = sum(
        amp * np.sin(2 * np.pi * freq * k * t) * np.exp(-t / tau)
        for k, amp, tau in ((1, 1.0, 0.12), (2, 0.4, 0.07), (3.01, 0.16, 0.04))
    )
    return out * np.minimum(t / 0.002, 1)


class Mix:
    """Una mezcla estéreo donde se va dejando cada sonido en su segundo."""

    def __init__(self, seconds: float) -> None:
        self.data = np.zeros((_n(seconds), 2))

    def add(self, sound: np.ndarray, at: float, gain: float = 1.0, pan: float = 0.0) -> None:
        """`pan` va de -1 (izquierda) a 1 (derecha), a potencia constante."""
        start = round(at * SR)
        if start < 0:
            sound, start = sound[-start:], 0
        end = min(len(self.data), start + len(sound))
        if end <= start:
            return
        angle = (pan + 1) * np.pi / 4
        chunk = sound[: end - start] * gain
        if chunk.ndim == 1:
            self.data[start:end, 0] += chunk * np.cos(angle) * np.sqrt(2)
            self.data[start:end, 1] += chunk * np.sin(angle) * np.sqrt(2)
        else:
            self.data[start:end] += chunk


def _pan_move(sound: np.ndarray, a: float, b: float) -> np.ndarray:
    """Un sonido mono que viaja de `a` a `b` en el estéreo."""
    angle = (np.linspace(a, b, len(sound)) + 1) * np.pi / 4
    return np.stack([sound * np.cos(angle), sound * np.sin(angle)], axis=1) * np.sqrt(2)


def _level(x: np.ndarray, rms: float) -> np.ndarray:
    return x * (rms / (np.sqrt(np.mean(x**2)) + 1e-9))


# ── La voz ──────────────────────────────────────────────────────────────────


def _speak(text: str, rate: str, work: Path) -> tuple[np.ndarray, list[dict]]:
    """Una línea de locución con Edge TTS. Se guarda: no vuelve a pedirse si ya está."""
    key = hashlib.sha1(f"{tiempos.VOICE_NAME}|{rate}|{text}".encode()).hexdigest()[:10]  # noqa: S324
    mp3, meta = work / f"voz-{key}.mp3", work / f"voz-{key}.json"
    if not (mp3.exists() and meta.exists()):
        try:
            import edge_tts
        except ImportError:
            raise SystemExit("Falta `edge-tts` (pip install edge-tts), o usa --sin-voz.") from None

        async def run() -> list[dict]:
            talk = edge_tts.Communicate(text, tiempos.VOICE_NAME, rate=rate, boundary="WordBoundary")
            words, audio = [], bytearray()
            async for chunk in talk.stream():
                if chunk["type"] == "audio":
                    audio += chunk["data"]
                elif chunk["type"] == "WordBoundary":
                    words.append(
                        {"word": chunk["text"], "at": chunk["offset"] / 1e7, "dur": chunk["duration"] / 1e7}
                    )
            mp3.write_bytes(bytes(audio))
            return words

        meta.write_text(json.dumps(asyncio.run(run()), ensure_ascii=False), encoding="utf-8")
    raw = subprocess.run(  # noqa: S603
        [FF, "-v", "error", "-i", str(mp3), "-f", "f32le", "-ac", "1", "-ar", str(SR), "-"],
        capture_output=True,
        check=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.float32).astype(np.float64), json.loads(
        meta.read_text(encoding="utf-8")
    )


def place_voice(work: Path, duration: float) -> list[dict]:
    """Dónde va cada línea de locución. Respeta el ancla de cada una y el aire entre líneas."""
    lines, free = [], 0.0
    for spec in tiempos.VOICE:
        audio, words = _speak(spec["text"], spec["rate"], work)
        loud = np.flatnonzero(np.abs(audio) > 0.02)
        first, last = loud[0] / SR, loud[-1] / SR  # dónde empieza y acaba de verdad la voz
        at = spec["at"]
        if "anchor" in spec:
            hit = next((w for w in words if w["word"].lower().startswith(spec["anchor"])), None)
            if hit:
                at = spec["anchor_at"] - hit["at"]
        at = max(at, free - first)  # nunca pisa a la línea anterior
        if at + last > duration - 0.1:  # no cabe en el tramo que se produce: se queda fuera
            continue
        free = at + last + tiempos.VOICE_GAP
        lines.append(
            {
                "text": spec["text"],
                "at": round(at, 3),
                "start": round(at + first, 3),
                "end": round(at + last, 3),
                "audio": audio,
                "words": [{"word": w["word"], "at": round(at + w["at"], 3)} for w in words],
            }
        )
    return lines


# ── La mezcla ───────────────────────────────────────────────────────────────


def _proof(timeline: dict, duration: float, rng, mark) -> tuple[Mix, Mix, dict]:
    """Gancho, reguero y primera pasada: los 13 s de la prueba aprobada. No se toca.

    Devuelve los efectos, la cama y lo que hace falta para seguir después: el mar
    por separado, la clave y el latido.
    """
    t = _time(duration)
    fx, bed = Mix(duration), Mix(duration)
    sea = np.zeros((len(t), 2))

    # Gancho: un golpe de madera por cápsula y el tecleo de la pregunta.
    for i, at in enumerate(timeline["capsules"]):
        k = (0.94, 1.0, 1.07)[i % 3]
        hit = wood([640 * k, 1210 * k, 1930 * k], [0.05, 0.028, 0.016], [1, 0.55, 0.3], 0.5, rng)
        fx.add(hit, at, 0.42, (-0.25, 0.25, -0.1)[i % 3])
        fx.add(thump(170 * k, 105 * k, 0.11, rng, 0.2), at, 0.4)
        mark("cápsula", at)
    text = " ".join(timeline["question"])
    for ch, at in zip(text, timeline["typing"], strict=True):
        if ch == " ":
            continue
        tick = wood([2150, 3900], [0.004, 0.002], [1, 0.5], 1.2, rng)
        fx.add(tick, at, 0.03 * rng.uniform(0.7, 1.15), rng.uniform(-0.2, 0.2))

    # La avalancha: arroz que cae, cada vez más, y luego gotea.
    a0, (w0, w1) = timeline["avalanche"], timeline["whip"]
    pour = ramp(
        t,
        [
            (a0 - 0.05, 0),
            (a0 + 0.08, 500),
            (w0, 2500),
            (w0 + 0.12, 1700),
            (w0 + 0.45, 520),
            (w1, 200),
            (w1 + 0.6, 50),
            (w1 + 1.3, 0),
        ],
    )
    for side in (-0.75, 0.75):
        fx.add(rice(duration, pour, 2600, 9500, rng), 0, 0.085, side)
    hiss = band(rng.standard_normal(len(t)), 5200, 11500) * ramp(
        t, [(a0, 0), (w0, 1), (w0 + 0.1, 0.8), (w0 + 0.4, 0.2), (w1 + 0.2, 0.06), (w1 + 0.9, 0)]
    )
    fx.add(hiss / (np.abs(hiss).max() + 1e-9), 0, 0.07)
    mark("avalancha", a0)

    # El tirón de cámara: toma aire y sopla.
    pre = timeline["anticipation"]
    swell = sweep(w0 - pre + 0.03, 500, 2300, 700, rng) * np.linspace(0, 1, _n(w0 - pre + 0.03)) ** 2.2
    fx.add(swell, pre, 0.2)
    whoosh = sweep(w1 - w0 + 0.25, 2100, 210, 520, rng)
    shape = ramp(
        _time(w1 - w0 + 0.25), [(0, 0), (0.05, 1), (0.16, 0.85), (w1 - w0, 0.12), (w1 - w0 + 0.25, 0)]
    )
    fx.add(_pan_move(whoosh * shape, -0.5, 0.5), w0, 0.3)
    fx.add(thump(82, 40, 0.5, rng, 0.05), w0, 0.3)
    mark("tirón", w0)

    # El reguero: el mar, muy bajo, y la clave.
    sea_in = ramp(t, [(w1 - 0.5, 0), (w1 + 0.9, 1), (duration, 1)])
    swells = 0.6 + 0.4 * np.sin(2 * np.pi * t / 5.3 + 0.8)
    for ch in (0, 1):
        low = band(brown(duration, rng), 60, 520)
        foam = band(rng.standard_normal(len(t)), 1400, 5200)
        wave_ = _level(low, 1.0) * swells + 0.35 * _level(foam, 1.0) * swells**3
        sea[:, ch] = _level(wave_ * sea_in, 0.02)
        bed.data[:, ch] += sea[:, ch]
    clave = wood([2390, 4810, 7150], [0.034, 0.014, 0.007], [1, 0.34, 0.13], 0.35, rng)
    for at in timeline["clave"]:
        if at > duration - 0.08:
            continue
        accent = 1.12 if abs((at - tiempos.CLAVE_START) % tiempos.CYCLE) < 1e-3 else 1.0
        bed.add(clave, at, 0.16 * accent, 0.14)
        mark("clave", at)
    drop = wood([3250, 5900], [0.007, 0.004], [1, 0.4], 0.9, rng)
    for i, at in enumerate(timeline["stragglers"]):
        fx.add(drop, at, 0.09, -0.5 if i % 2 else 0.5)

    # Guani: el aire de la caída, el golpe y los granos que saltan.
    gu = timeline["guani"]
    fall = sweep(0.34, 2900, 520, 600, rng) * np.linspace(0, 1, _n(0.34)) ** 2.4
    fx.add(fall, gu - 0.34, 0.2)
    fx.add(thump(118, 44, 0.52, rng, 0.35), gu, 0.62)
    fx.add(thump(96, 50, 0.22, rng, 0.2), gu + 0.23, 0.26)
    jump = ramp(t, [(gu, 0), (gu + 0.03, 900), (gu + 0.3, 160), (gu + 0.6, 0)])
    fx.add(rice(duration, jump, 2200, 8000, rng), 0, 0.1)
    mark("Guani", gu)

    # Las dos lecturas: un barrido frío cada una, que cruza el estéreo como la luz cruza la mesa.
    for (a, b), (f0, f1), (p0, p1) in (
        (timeline["scan_a"], (880, 1320), (-0.7, 0.7)),
        (timeline["scan_b"], (1175, 1760), (0.7, -0.7)),
    ):
        tt = _time(b - a)
        tone = np.sin(2 * np.pi * np.cumsum(np.geomspace(f0, f1, len(tt))) / SR)
        tone += 0.4 * np.sin(2 * np.pi * np.cumsum(np.geomspace(f0 * 2, f1 * 2, len(tt))) / SR)
        air = sweep(b - a, 3000, 6400, 380, rng)
        edge = ramp(tt, [(0, 0), (0.05, 1), (b - a - 0.12, 1), (b - a, 0)])
        fx.add(_pan_move((0.028 * tone + 0.05 * air) * edge, p0, p1), a, 1.0)
        reads = ramp(t, [(a, 0), (a + 0.02, 140), (b, 140), (b + 0.02, 0)])
        fx.add(rice(duration, reads, 6000, 11000, rng), 0, 0.022)
        mark("lectura", a)

    # Lo que no es un precio resbala al montón.
    d0, d1 = timeline["discard"]
    slide = ramp(t, [(d0 - 0.03, 0), (d0 + 0.12, 1300), (d0 + 0.55, 2300), (d1 - 0.1, 900), (d1 + 0.12, 0)])
    for side in (-0.5, 0.5):
        fx.add(rice(duration, slide, 1500, 6200, rng), 0, 0.06, side)
    drag = band(brown(duration, rng), 90, 340) * ramp(t, [(d0, 0), (d0 + 0.5, 1), (d1, 0.5), (d1 + 0.15, 0)])
    fx.add(drag / (np.abs(drag).max() + 1e-9), 0, 0.1)
    fx.add(thump(92, 58, 0.16, rng, 0.25), d1 + 0.02, 0.3)
    mark("apartar", d0)

    # Lo claro: un aviso al encenderse y un tic cuadrado al quedar marcado.
    lit = wood([2093, 4186], [0.03, 0.012], [1, 0.3], 0.1, rng)
    for i, at in enumerate(timeline["clear_times"]):
        fx.add(lit, at, 0.035, 0.4)
        last = i == len(timeline["clear_times"]) - 1
        fx.add(square_tick(1760 if last else 1568, 0.03), at + timeline["clear_mark"], 0.05, 0.45)
        mark("claro", at)

    # Lo dudoso: un latido cálido, del lado de la mesa donde quedó. Los parciales
    # de 294 a 588 Hz están para el altavoz de un teléfono, que no da los graves.
    tb = _time(0.5)
    beat = sum(
        amp * np.sin(2 * np.pi * freq * tb)
        for freq, amp in ((98, 1.0), (147, 0.6), (294, 0.45), (392, 0.3), (588, 0.12))
    )
    beat = beat * np.minimum(tb / 0.012, 1) * np.exp(-tb / 0.13)
    for at in timeline["doubt_pulses"]:
        if at > duration - 0.1:
            continue
        fx.add(beat, at, 0.24, -0.4)
        fx.add(beat, at + 0.15, 0.1, -0.4)
        mark("latido", at)
    return fx, bed, {"sea": sea, "clave": clave, "beat": beat}


def _review_sound(timeline: dict, fx: Mix, rng, mark) -> None:
    """B4 · El repaso. La persona suena a lápiz y a madera: grave y cálida."""
    review = timeline["review"]
    side = -0.42  # los dudosos quedaron a la izquierda de la mesa
    rings, hits = review["rings"], review["ring_hits"]
    fx.add(tap(150, rng), rings[0][0], 0.1, side)  # la yema se posa
    mark("yema", rings[0][0])
    # Cada aro: el roce mientras se traza y el golpe del grano repasado. Los golpes van
    # por tandas que suben de tono y cierran con la clave: no veinticuatro iguales en fila.
    index = 0
    for size in review["ring_groups"]:
        for k in range(size):
            (t0, t1), hit = rings[index], hits[index]
            slow, climb = size == 1, (k + 1) / size  # el primero y el difícil se trazan despacio
            fx.add(scratch(t1 - t0, rng), t0, 0.15 if slow else 0.04, side + rng.uniform(-0.12, 0.12))
            gain = 0.34 if slow else 0.1 + 0.11 * climb + (0.07 if k == size - 1 else 0)
            pitch = (158 if slow else 146 + 40 * climb) * rng.uniform(0.985, 1.015)
            fx.add(tap(pitch, rng), hit, gain, side + rng.uniform(-0.1, 0.1))
            mark("aro", hit)
            index += 1

    # El difícil se abre en su línea real; la persona subraya dos veces y deja su lectura.
    o0, o1 = review["open"]
    fx.add(sweep(o1 - o0 + 0.08, 420, 1500, 480, rng) * np.hanning(_n(o1 - o0 + 0.08)), o0, 0.1, -0.2)
    fx.add(wood([520, 1180], [0.03, 0.014], [1, 0.4], 0.25, rng), o1 - 0.02, 0.1)
    mark("se abre", o0)
    for a, b in review["underline"]:
        fx.add(scratch(b - a, rng, 2200, 7500), a, 0.15, -0.05)
        mark("subraya", a)
    flip = band(rng.standard_normal(_n(0.12)), 900, 4500) * np.exp(-_time(0.12) / 0.03)
    fx.add(flip / (np.abs(flip).max() + 1e-9), review["morph"][0] + 0.03, 0.06)
    # la lectura queda fija: la madera de las cápsulas del gancho, y el golpe de la persona
    fx.add(wood([640, 1210, 1930], [0.05, 0.028, 0.016], [1, 0.55, 0.3], 0.4, rng), review["reading"], 0.2)
    fx.add(tap(165, rng), review["reading"], 0.16)
    mark("lectura", review["reading"])
    c0, c1 = review["close"]
    fx.add(sweep(c1 - c0 + 0.06, 1300, 460, 420, rng) * np.hanning(_n(c1 - c0 + 0.06)), c0, 0.08, -0.2)
    fx.add(tap(172, rng), c1, 0.15, side)

    # Audita a la máquina: el aro, su golpe y un tic cuadrado que lo acusa.
    a0, a1 = review["audit_ring"]
    fx.add(scratch(a1 - a0, rng), a0, 0.14, 0.42)
    fx.add(tap(176, rng), a1, 0.26, 0.42)
    fx.add(square_tick(1568, 0.03), a1 + 0.02, 0.04, 0.45)
    mark("audita", a1)
    # Subraya sus dos rótulos y saca un puñado del montón: golpes más sordos, uno por grano.
    for key, pan in (("audit_underline", 0.35), ("sample_underline", 0.0)):
        a, b = review[key]
        fx.add(scratch(b - a, rng), a, 0.06, pan)
    for i, (t0, t1) in enumerate(review["sample_rings"]):
        fx.add(scratch(t1 - t0, rng), t0, 0.04, -0.25 + 0.12 * i)
        fx.add(tap(124 + 7 * i, rng), t0, 0.13 + 0.02 * i, -0.25 + 0.12 * i)
        mark("muestra", t0)


def _measure_sound(timeline: dict, fx: Mix, rng, mark) -> list[float]:
    """B5 · La misma medida. La máquina suena fría y cuadrada; las cuentas, a contador.

    Devuelve los instantes en que una cifra queda fija, para acentuar ahí la clave.
    """
    measure = timeline["measure"]
    # La cámara toma impulso y entra en la fila: el gesto del tirón del principio, más corto.
    pre, (d0, d1) = measure["anticipation"], measure["dive"]
    fx.add(
        sweep(d0 - pre + 0.03, 420, 1700, 600, rng) * np.linspace(0, 1, _n(d0 - pre + 0.03)) ** 2.2, pre, 0.11
    )
    shape = ramp(_time(d1 - d0 + 0.2), [(0, 0), (0.06, 1), (0.2, 0.8), (d1 - d0, 0.1), (d1 - d0 + 0.2, 0)])
    fx.add(_pan_move(sweep(d1 - d0 + 0.2, 1900, 240, 480, rng) * shape, -0.3, 0.45), d0, 0.2)
    fx.add(thump(80, 42, 0.42, rng, 0.05), d0, 0.2)
    mark("entra", d0)
    # El anuncio se abre: sube y cae como las cápsulas del gancho.
    o0, o1 = measure["open"]
    fx.add(sweep(o1 - o0, 480, 1500, 460, rng) * np.hanning(_n(o1 - o0)), o0, 0.08, 0.1)
    fx.add(
        wood([640, 1210, 1930], [0.05, 0.028, 0.016], [1, 0.55, 0.3], 0.5, rng), measure["opened"], 0.24, 0.1
    )
    fx.add(thump(170, 105, 0.11, rng, 0.2), measure["opened"], 0.22)
    mark("anuncio", measure["opened"])

    # La máquina subraya tres palabras (tres tics que suben) y teclea las etiquetas, muy tenue.
    for i, at in enumerate(measure["marks"]):
        fx.add(square_tick((1568, 1760, 1976)[i], 0.03), at, 0.035, 0.1)
    key = wood([2600, 4700], [0.003, 0.0016], [1, 0.5], 1.0, rng)

    def typed(text: str, at: float, pan: float) -> None:
        for i, ch in enumerate(text):
            if ch != " ":
                fx.add(key, at + i * measure["type_step"], 0.011 * rng.uniform(0.7, 1.15), pan)

    for name, at in zip(measure["tag_names"], measure["tags"], strict=True):
        typed(name, at, -0.2)
    # Cada palabra vuela a su etiqueta (un soplo) y cae (un tic). «La Habana» viene de fuera, por la derecha.
    flights = [(a, b, 0.15, -0.1) for a, b in measure["words"]] + [(*measure["province"], 0.85, 0.0)]
    for i, (a, b, pan0, pan1) in enumerate(flights):
        air = sweep(b - a, 700, 1500, 380, rng) * np.hanning(_n(b - a))
        fx.add(_pan_move(air, pan0, pan1), a, 0.05 if i < 3 else 0.075)
        fx.add(square_tick((1319, 1397, 1568, 1760)[i], 0.03), b, 0.06, pan1)
        fx.add(wood([1900, 3600], [0.008, 0.004], [1, 0.4], 0.6, rng), b, 0.045, pan1)
        mark("dato", b)
    # El anuncio se cierra en su grano y se arma la cuenta: dos soplos tenues.
    for (a, b), (f0, f1) in ((measure["pack_out"], (1300, 500)), (measure["regroup"], (600, 1700))):
        fx.add(sweep(b - a, f0, f1, 460, rng) * np.hanning(_n(b - a)), a, 0.05)

    # Las dos cuentas: un tictac que sube con los números y una nota cuando la cifra queda fija.
    fixed = []
    for (a, b), steps, note in ((measure["roll_a"], 22, 1568.0), (measure["roll_b"], 12, 1396.9)):
        last = -1.0
        for k in range(1, steps):
            part = k / steps  # el tic suena cuando la cuenta pasa por esa fracción de su cifra
            at = a + (b - a) * (1 - (1 - part) ** (1 / measure["roll_power"]))
            if at - last < 0.022:
                continue
            last = at
            fx.add(
                wood([950 * 2**part, 2300 * 2**part], [0.007, 0.0035], [1, 0.45], 0.7, rng),
                at,
                0.045 + 0.05 * part,
            )
        fx.add(wood([1900, 4600], [0.007, 0.0035], [1, 0.45], 0.7, rng), b, 0.1)
        fx.add(chime(note), b, 0.085)
        fx.add(tap(196, rng), b, 0.1)
        fixed.append(b)
        mark("tictac", a)
        mark("cifra fija", b)
    q0, q1 = measure["pair_in"]  # entran los dos granos de la caja, del lado de la persona
    fx.add(_pan_move(sweep(q1 - q0, 500, 1300, 420, rng) * np.hanning(_n(q1 - q0)), -0.85, -0.3), q0, 0.05)
    for k in range(2):
        fx.add(tap(160 + 18 * k, rng), q1 - 0.04 + 0.05 * k, 0.1, -0.45)

    # La recta se dibuja sola: un barrido frío, de izquierda a derecha, como las lecturas.
    l0, l1 = measure["line"]
    tt = _time(l1 - l0)
    tone = np.sin(2 * np.pi * np.cumsum(np.geomspace(660, 990, len(tt))) / SR)
    tone += 0.4 * np.sin(2 * np.pi * np.cumsum(np.geomspace(1320, 1980, len(tt))) / SR)
    edge = ramp(tt, [(0, 0), (0.05, 1), (l1 - l0 - 0.1, 1), (l1 - l0, 0)])
    fx.add(_pan_move((0.022 * tone + 0.04 * sweep(l1 - l0, 2600, 5600, 380, rng)) * edge, -0.6, 0.6), l0, 1.0)
    mark("recta", l0)
    # Caen los tres granos: los dos de la persona (graves, cálidos) y el de la máquina (con su tic).
    drops = measure["drops"]
    fx.add(tap(168, rng), drops[0], 0.26, -0.3)
    fx.add(tap(188, rng), drops[1], 0.3, -0.3)
    fx.add(tap(210, rng), drops[2], 0.22, 0.35)
    fx.add(square_tick(1568, 0.03), drops[2], 0.07, 0.4)
    for at in drops:
        mark("cae", at)
    fx.add(square_tick(1760, 0.03), measure["median"], 0.045, -0.2)  # la muesca de la mediana
    typed(measure["median_word"], measure["median_typed"], -0.2)

    # El cierre, con los dos trazos: la persona firma su aro y la máquina estampa su marca.
    h0, h1 = measure["hand"]
    fx.add(scratch(h1 - h0, rng), h0, 0.16, -0.3)
    fx.add(tap(172, rng), h1, 0.2, -0.3)
    mark("a mano", h0)
    typed(measure["system_line"], measure["by_system"], -0.1)
    fx.add(square_tick(1760, 0.035), measure["stamp"], 0.075, -0.25)
    fx.add(wood([2093, 4186], [0.03, 0.012], [1, 0.3], 0.1, rng), measure["stamp"], 0.05, -0.25)
    fx.add(thump(170, 105, 0.11, rng, 0.2), measure["stamp"], 0.2)
    mark("sello", measure["stamp"])
    return fixed


def _after(timeline: dict, duration: float, fx0: Mix, bed0: Mix, kit: dict, mark) -> tuple[Mix, Mix]:
    """Lo que sigue a la prueba: el repaso (B4) y la misma medida (B5).

    Usa otra semilla, para que alargar el comercial no cambie ni una muestra de
    lo ya aprobado. Lo único que se toca de la prueba es su final (de `SEAM[0]`
    en adelante), donde su mar, que acababa ahí, se funde con el que sigue.
    """
    rng = np.random.default_rng(SEED + 1)
    fx, bed = Mix(duration), Mix(duration)
    n, t = len(fx0.data), _time(duration)
    fx.data[:n], bed.data[:n] = fx0.data, bed0.data

    # El mar sigue, con el mismo oleaje y el mismo nivel. El de la prueba se funde con
    # otro igual, a potencia constante (el ruido de uno y el del otro no se parecen).
    left = np.clip((SEAM[1] - t) / (SEAM[1] - SEAM[0]), 0, 1)  # 1 hasta que empieza el relevo; 0 cuando acaba
    old, new = np.sin(left * np.pi / 2), np.where(left >= 1, 0.0, np.cos(left * np.pi / 2))
    bed.data[:n] -= kit["sea"] * (1 - old[:n, None])
    swells = 0.6 + 0.4 * np.sin(2 * np.pi * t / 5.3 + 0.8)
    swing = np.sqrt(swells**2 + 0.35**2 * swells**6)  # cuánto sube y baja el oleaje
    calm = slice(_n(5.0), _n(SEAM[0]))  # un tramo de la prueba con el mar ya asentado
    for ch in (0, 1):
        # El ruido marrón se filtra «en redondo» y chasquea donde el final se junta con
        # el principio: se genera un segundo de más por cada lado y se recorta.
        low = band(brown(duration + 2.0, rng), 60, 520)[SR : SR + len(t)]
        foam = band(rng.standard_normal(len(t) + 2 * SR), 1400, 5200)[SR : SR + len(t)]
        wave_ = _level(low, 1.0) * swells + 0.35 * _level(foam, 1.0) * swells**3
        level = np.sqrt(
            np.mean((kit["sea"][calm, ch] / swing[calm]) ** 2) / np.mean((wave_[n:] / swing[n:]) ** 2)
        )
        bed.data[:, ch] += wave_ * new * level

    _review_sound(timeline, fx, rng, mark)
    fixed = _measure_sound(timeline, fx, rng, mark)

    # La clave no se detiene. Donde una cifra queda fija, suena más.
    head = n / SR
    for at in timeline["clave"]:
        if at <= head - 0.08 or at > duration - 0.08:
            continue
        accent = 1.12 if abs((at - tiempos.CLAVE_START) % tiempos.CYCLE) < 1e-3 else 1.0
        if any(abs(at - f) < 1e-3 for f in fixed):
            accent = 1.3
        bed.add(kit["clave"], at, 0.16 * accent, 0.14)
        mark("clave", at)
    # Lo dudoso sigue latiendo, cada vez menos, hasta que la persona repasa el último.
    starts = [t0 for t0, _ in timeline["review"]["rings"]]
    for at in timeline["doubt_pulses"]:
        if at <= head - 0.1 or at > duration - 0.1:
            continue
        waiting = sum(t0 > at for t0 in starts) / len(starts)
        if waiting > 0:
            fx.add(
                kit["beat"], at, 0.2 * waiting, -0.4
            )  # un poco más bajo que en la prueba: ahora hay voz encima
            fx.add(kit["beat"], at + 0.15, 0.08 * waiting, -0.4)
            mark("latido", at)
    return fx, bed


def build(timeline: dict, work: Path, duration: float, voice: bool = True) -> tuple[Path, dict]:
    """Sintetiza y mezcla todo. Devuelve el WAV y un informe con lo que quedó dónde."""
    rng = np.random.default_rng(SEED)
    events: list[tuple[str, float]] = []

    def mark(name: str, at: float) -> None:
        events.append((name, round(at, 3)))

    head = min(duration, tiempos.PROOF)
    fx, bed, kit = _proof(timeline, head, rng, mark)

    # Un poco de sala para la madera y los golpes.
    ir_t = _time(0.38)
    room = np.stack(
        [band(rng.standard_normal(len(ir_t)), 200, 5200) * np.exp(-ir_t / 0.075) for _ in range(2)], axis=1
    )
    room /= np.sqrt((room**2).sum(axis=0))
    if duration > head:
        fx, bed = _after(timeline, duration, fx, bed, kit, mark)
    dry = fx.data + bed.data
    wet = np.stack([convolve(dry[:, ch], room[:, ch]) for ch in (0, 1)], axis=1)
    mix = dry + 0.16 * wet

    report: dict = {"eventos": events, "voz": []}
    if voice:
        lines = place_voice(work, duration)
        talk = Mix(duration)
        for line in lines:
            clean = band(line["audio"], 85, None)
            talk.add(_level(clean, 0.1), line["at"], 1.0)
            report["voz"].append({k: line[k] for k in ("text", "at", "start", "end", "words")})
        # La cama baja mientras alguien habla.
        follow = band(np.abs(talk.data[:, 0]), None, 6)
        # La referencia es la voz de la prueba: su cama baja lo mismo que entonces, haya o no más líneas.
        loudest = follow[: _n(head)].max()
        duck = 1 - 0.6 * np.clip(follow / ((loudest if loudest > 1e-6 else follow.max()) + 1e-9) * 2.6, 0, 1)
        mix = mix * duck[:, None]
        for line, row in zip(lines, report["voz"], strict=True):  # la voz, ¿cuánto queda por encima?
            a, b = round(line["start"] * SR), round(line["end"] * SR)
            over = np.sqrt(np.mean(talk.data[a:b] ** 2)) / (np.sqrt(np.mean(mix[a:b] ** 2)) + 1e-9)
            row["sobre_la_cama_db"] = round(float(20 * np.log10(over)), 1)
            worst = min(
                np.sqrt(np.mean(talk.data[i : i + SR // 2] ** 2))
                / (np.sqrt(np.mean(mix[i : i + SR // 2] ** 2)) + 1e-9)
                for i in range(a, max(a + 1, b - SR // 2), SR // 10)
            )
            row["peor_medio_segundo_db"] = round(float(20 * np.log10(worst)), 1)
        mix = mix + talk.data

    # Limitador suave, nivel y fundidos.
    mix = np.tanh(mix * 1.5) / np.tanh(1.5)
    # 0,8 y no más: al pasar por AAC los picos crecen, y con 0,89 llegaban a saturar.
    mix *= 0.8 / (np.abs(mix).max() + 1e-9)
    edge_in, edge_out = _n(0.02), _n(0.3)
    mix[:edge_in] *= np.linspace(0, 1, edge_in)[:, None]
    mix[-edge_out:] *= np.linspace(1, 0, edge_out)[:, None] ** 1.5
    path = work / "sonido.wav"
    with wave.open(str(path), "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(SR)
        out.writeframes((mix * 32767).astype("<i2").tobytes())
    report["pico"] = round(float(np.abs(mix).max()), 3)
    report["rms"] = round(float(np.sqrt(np.mean(mix**2))), 4)
    return path, report


# ── El espectro, para comprobar a ojo ───────────────────────────────────────

_HEAT = (
    (0, 0, 4),
    (40, 11, 84),
    (101, 21, 110),
    (159, 42, 99),
    (212, 72, 66),
    (245, 125, 21),
    (250, 193, 39),
    (252, 255, 164),
)


def spectrogram(audio: Path, path: Path, events: list[tuple[str, float]]) -> None:
    """Dibuja el espectro del sonido con una marca en cada golpe previsto.

    Frecuencia en escala logarítmica (60 Hz a 16 kHz) y una raya arriba por cada
    evento de la línea de tiempo: si el golpe suena donde debe, la raya cae
    justo encima de su mancha.
    """
    with wave.open(str(audio)) as src:
        raw = np.frombuffer(src.readframes(src.getnframes()), dtype="<i2").reshape(-1, 2)
    mono = raw.mean(axis=1) / 32768
    size, hop = 2048, 256
    frames = 1 + (len(mono) - size) // hop
    idx = np.arange(size)[None, :] + hop * np.arange(frames)[:, None]
    mag = np.abs(np.fft.rfft(mono[idx] * np.hanning(size), axis=1))
    db = 20 * np.log10(mag / (size / 4) + 1e-7)
    width, height, left, top = 1500, 440, 58, 120
    freqs = np.geomspace(60, 16000, height)
    rows = np.clip(np.round(freqs * size / SR).astype(int), 0, mag.shape[1] - 1)
    cols = np.clip((np.arange(width) * frames / width).astype(int), 0, frames - 1)
    level = np.clip((db[cols][:, rows].T[::-1] + 88) / 80, 0, 1)
    heat = np.array(_HEAT, dtype=float)
    pos = level * (len(heat) - 1)
    lo = np.floor(pos).astype(int).clip(0, len(heat) - 2)
    rgb = heat[lo] + (heat[lo + 1] - heat[lo]) * (pos - lo)[..., None]
    img = Image.new("RGB", (left + width + 16, top + height + 30), (12, 14, 18))
    img.paste(Image.fromarray(rgb.astype("uint8")), (left, top))
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=11)  # la de tamaño fijo no trae acentos
    seconds = len(mono) / SR
    for sec in range(int(seconds) + 1):
        x = left + sec / seconds * width
        draw.line((x, top + height, x, top + height + 5), fill="white")
        draw.text((x - 8, top + height + 9), f"{sec} s", fill="white", font=font)
    for hz in (100, 200, 500, 1000, 2000, 5000, 10000):
        y = top + height - np.log(hz / 60) / np.log(16000 / 60) * height
        draw.line((left - 5, y, left, y), fill="white")
        draw.text((4, y - 6), f"{hz} Hz" if hz < 1000 else f"{hz // 1000} kHz", fill="white", font=font)
    lanes: dict[str, int] = {}
    for name, at in events:
        lane = lanes.setdefault(name, len(lanes))
        x, y = left + at / seconds * width, 5 + 7 * (lane % 16)
        draw.line((x, y, x, top - 2), fill=(255, 208, 114))
        if at == min(t for n, t in events if n == name):
            draw.text((x + 3, y - 5), name, fill=(204, 232, 233), font=font)
    img.save(path)
