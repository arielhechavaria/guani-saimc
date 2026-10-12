"""Línea de tiempo del comercial «Como se escoge el arroz».

Es la única fuente de los tiempos. `producir_comercial.py` se la pasa a la
escena (`escena.html`) y `sonido.py` la lee para poner cada golpe: si un golpe
se mueve, se mueve aquí y cambia a la vez en la imagen y en el sonido.

Todo cuelga de una clave de son 3-2. Un ciclo dura 3,2 s (dos compases a 150
pulsos por minuto) y sus cinco golpes caen en 0; 0,6; 1,2; 2,0 y 2,4 s. La clave
arranca en 0,2 s, para que la primera cápsula tenga de dónde caer.
"""

from __future__ import annotations

FPS = 30
DURATION = 13.0

CYCLE = 3.2
PULSE = 0.4
CLAVE_START = 0.2
CLAVE_PATTERN = (0.0, 0.6, 1.2, 2.0, 2.4)

#: Lo que se lee en pantalla. Va aquí porque el tecleo suena letra a letra.
QUESTION = ("¿A cómo está", "el pollo?")

#: Datos del sitio el 2026-10-11 (solo `GET`). En la producción completa se
#: vuelven a leer; para la prueba van fijos.
DATA = {
    "date": "2026-10-11",
    "clear": 10,  # aceptados por el sistema
    "doubtful": 24,  # los que repasa una persona
    "grains": 330,  # granos en la mesa; el montón de lo apartado no lleva cifra
    # Fragmentos reales de tres anuncios de pollo.
    "heroes": ["7500cup", "27300 O 39", "a $8500"],
    # Trozos literales de otros anuncios públicos (`/anuncio/…`), para la avalancha.
    # Solo precios, cantidades y envases: nada que identifique a nadie.
    "fragments": [
        "1lb - 800cup",
        "a 630",
        "4850 CUP",
        "25900 o 37",
        "30 USD",
        "Caja de 50 lb",
        "4200 o 6",
        "Saco de 25 kg",
        "21000 o 28",
        "52500 CUP",
        "3500 o 5.11",
        "560 o 0.8",
        "17500 o 25",
        "37 USD",
        "75 USD",
        "9720",
        "26000",
        "Mayonesa 520gr:",
        "Más de 50 cajas:",
        "500 ML:",
        "1 Lt -",
        "3 kg:",
        "25 kg:",
        "1350",
    ],
}

#: La locución. `anchor` es la palabra que tiene que caer en `anchor_at`; sin
#: ancla, la línea empieza en `at`. La primera va un poco más ligera para que
#: termine justo antes de que caiga Guani (6,6 s): su golpe le pone el punto.
VOICE = (
    {
        "text": "En Cuba, los precios andan regados en miles de mensajes de WhatsApp.",
        "at": 2.44,
        "rate": "+8%",
    },
    {
        "text": "Guani los lee como se escoge el arroz: aparta lo que no es un precio.",
        "at": 7.2,
        "rate": "+3%",
        "anchor": "aparta",
        "anchor_at": 9.74,
    },
)
VOICE_NAME = "es-CU-ManuelNeural"
#: Aire mínimo entre dos líneas de locución.
VOICE_GAP = 0.3


def beat(cycle: int, index: int) -> float:
    """El golpe `index` (0 a 4) del ciclo `cycle` de la clave, en segundos."""
    return round(CLAVE_START + CYCLE * cycle + CLAVE_PATTERN[index], 3)


def clave_hits(start: float, end: float) -> list[float]:
    """Todos los golpes de la clave entre `start` y `end` (incluidos)."""
    hits: list[float] = []
    cycle = 0
    while CLAVE_START + CYCLE * cycle <= end:
        hits += [beat(cycle, i) for i in range(len(CLAVE_PATTERN))]
        cycle += 1
    return [h for h in hits if start - 1e-6 <= h <= end + 1e-6]


def _typing_times(start: float, end: float) -> list[float]:
    """Cuándo aparece cada letra de la pregunta. El salto de línea cuenta como una pausa."""
    text = " ".join(QUESTION)
    step = (end - start) / (len(text) - 1)
    return [round(start + i * step, 4) for i in range(len(text))]


def build() -> dict:
    """La línea de tiempo completa, lista para pasar a JSON."""
    whip = (beat(0, 4), beat(1, 0))  # 2,6 → 3,4
    guani = beat(2, 0)  # 6,6
    discard = beat(3, 0)  # 9,8
    clear = beat(3, 2)  # 11,0
    doubt = beat(3, 3)  # 11,8
    clear_step = 0.05
    return {
        "fps": FPS,
        "duration": DURATION,
        "pulse": PULSE,
        "clave": clave_hits(whip[1], DURATION),
        # Gancho
        "fade_in": 0.2,
        "capsules": [beat(0, 0), beat(0, 1), beat(0, 2)],  # 0,2 / 0,8 / 1,4
        "question": list(QUESTION),
        "typing": _typing_times(0.45, 1.55),
        "poster": 2.0,
        "avalanche": beat(0, 3),  # 2,2
        "anticipation": 2.45,
        "whip": list(whip),
        # Reguero. Primero se mira el arroz; cada rótulo entra con la locución.
        "captions": [beat(1, 2), beat(1, 3)],  # 4,6 y 5,4
        "captions_out": [6.15, 6.9],  # el de arriba le deja el sitio a Guani
        "stragglers": [3.8, beat(1, 1), 4.45, 5.0, beat(1, 3), beat(1, 4)],
        # Primera pasada
        "guani": guani,
        "scan_a": [beat(2, 1), beat(2, 3)],  # 7,2 → 8,6
        "scan_b": [beat(2, 2), beat(2, 4)],  # 7,8 → 9,0
        "title_out": 9.25,
        "discard": [discard, discard + 1.0],
        "discard_label": beat(3, 1),  # 10,4
        "clear": clear,
        "clear_step": clear_step,
        "clear_times": [round(clear + clear_step * i, 3) for i in range(DATA["clear"])],
        "clear_mark": 0.24,  # lo que tarda cada uno en llegar a la fila y quedar marcado
        "clear_label": clear + 0.2,
        "doubt": doubt,
        "doubt_pulses": [round(doubt + PULSE * i, 3) for i in range(4)],  # 11,8 … 13,0
        "doubt_label": beat(3, 4),  # 12,2
    }
