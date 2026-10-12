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
DURATION = 38.6
#: Hasta aquí llegaba la prueba aprobada. El empuje lento de la cámara se midió
#: sobre ese tramo y sigue al mismo paso; su sonido se sintetiza igual que entonces.
PROOF = 13.0

CYCLE = 3.2
PULSE = 0.4
CLAVE_START = 0.2
CLAVE_PATTERN = (0.0, 0.6, 1.2, 2.0, 2.4)

#: Lo que se lee en pantalla. Va aquí porque el tecleo suena letra a letra.
QUESTION = ("¿A cómo está", "el pollo?")
#: Lo que teclea la máquina en B5, por lo mismo: la palabra de la muesca y la
#: última línea de la celda (`{n}` es la cuenta del tablero).
MEDIAN_WORD = "mediana"
SYSTEM_LINE = "{n} por el sistema"
#: B6 · El orden en que se trazan las provincias (por su `slug` en el GeoJSON): la
#: ruta sale de La Habana, baja por la isla hasta el oriente y, al abrirse el plano,
#: termina por el occidente.
ROUTE = (
    "la-habana",
    "artemisa",
    "mayabeque",
    "matanzas",
    "cienfuegos",
    "villa-clara",
    "sancti-spiritus",
    "ciego-de-avila",
    "camaguey",
    "las-tunas",
    "holguin",
    "granma",
    "santiago-de-cuba",
    "guantanamo",
    "pinar-del-rio",
    "isla-de-la-juventud",
)
#: Lo que se lee en B6 y en el cierre.
SAMPLE_TITLE = ("{ads} anuncios", "en {days} días")
SAMPLE_SPLIT = ("{manual} a mano", "{system} por el sistema")
SAMPLE_CAPTION = ("Una muestra", "en formación,", "no un censo.")
CLOSE_TITLE = ("Una máquina lee;", "una persona", "supervisa.")
CLOSE_BUTTON = "Ver los precios de hoy"
CLOSE_SIGNATURE = "Guani, por Leira Studio"
#: Lo que tarda la máquina en teclear una letra de esos rótulos.
TYPE_STEP = 0.018

#: Datos del sitio: la foto del tablero del 11 de septiembre al 11 de octubre de
#: 2026 (`/precios?desde=2026-09-11&hasta=2026-10-11`). Van fijos: el comercial no
#: lee el sitio. Si el tablero cambia, se cambian aquí y se vuelve a producir.
DATA = {
    "date": "2026-10-11",
    "date_label": "Tablero del 11 de octubre de 2026",
    "window": ["2026-09-11", "2026-10-11"],
    "days": 30,
    "ads": 34,  # anuncios en esos 30 días
    "manual": 24,  # revisados a mano
    "system": 10,  # aceptados por el sistema
    # Dónde están: 2 de las 16 provincias tienen datos (el GeoJSON trae las 16).
    "provinces": 16,
    "by_province": [
        {"slug": "la-habana", "name": "La Habana", "ads": 27},
        {"slug": "santiago-de-cuba", "name": "Santiago de Cuba", "ads": 7},
    ],
    # La otra celda de pollo del tablero: un solo anuncio, en Santiago de Cuba.
    "far": {
        "product": "Pollo",
        "province": "Santiago de Cuba",
        "per_lb": 850,
        "unit": "CUP/lb",
        "ads": 1,
    },
    "clear": 10,  # aceptados por el sistema
    "doubtful": 24,  # los que repasa una persona
    "audited": 1,  # de los aceptados por el sistema, los que después auditó una persona
    # El anuncio de la caja (La Habana), revisado a mano. SOLO se enseña esta línea,
    # tal como se publicó: el resto de ese anuncio trae la dirección de una tienda.
    "box": {
        "line": "🐔Pollo Muslo 4x10:🤑27300 O 39",
        "key": "4x10",  # lo que hay que entender: cuatro bolsas de 10 lb
        "price": 27300,
        "pounds": 40,
        "per_lb": 683,  # 682,5: el tablero lo muestra como 683
    },
    # El anuncio que aceptó el sistema (La Habana). El texto no trae provincia:
    # la pone el grupo de origen.
    "pack": {
        "text": "Paquete de Pollo 10lb (muslo y contramuslo) - 7500cup",
        "product": "Pollo",
        "quantity": "10lb",
        "amount": "7500cup",
        "price": 7500,
        "pounds": 10,
        "per_lb": 750,
        "province": "La Habana",
    },
    # La celda del tablero donde caen los tres anuncios de pollo de La Habana.
    "cell": {
        "product": "Pollo",
        "province": "La Habana",
        "ads": 3,
        "manual": 2,
        "system": 1,
        "values": [683, 683, 750],
        "median": 683,
        "low": 683,
        "high": 750,
        "unit": "CUP/lb",
    },
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
    # B4. «Lo difícil» cae cuando el grano de la caja se abre (14,94–15,22) y «audita»
    # llega con el aro del claro (17,4). Empieza pasado el segundo 13: la prueba no se toca.
    {
        "text": "Lo claro pasa. Lo difícil lo decide una persona, que también audita a la máquina.",
        "at": 12.84,
        "rate": "+3%",
        "anchor": "difícil",
        "anchor_at": 15.15,
    },
    # B5. «libra» cae con el 750 CUP/lb que queda fijo en la clave de 22,6.
    {
        "text": "Todo a la misma medida: precio por libra, en pesos cubanos.",
        "at": 20.26,
        "rate": "+3%",
        "anchor": "libra",
        "anchor_at": 22.53,
    },
    # B6 y B7. Frase a frase, para que cada una caiga en su golpe y quede aire entre
    # todas: «Poco» cuando el plano empieza a abrirse (29,4) y «Mira» con el botón (36,0).
    {"text": "Poco y cierto.", "at": 29.15, "rate": "+8%"},
    {"text": "Es una muestra en formación, no un censo.", "at": 30.26, "rate": "+8%"},
    {"text": "Una máquina lee; una persona supervisa.", "at": 33.11, "rate": "+8%"},
    {
        "text": "Mira los precios de hoy en Guani.",
        "at": 35.9,
        "rate": "+8%",
        "anchor": "mira",
        "anchor_at": 36.0,
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


def _spaced(start: float, end: float, count: int, power: float = 1.0) -> list[float]:
    """`count` instantes de `start` a `end`. Con `power` menor que 1 se van juntando: un acelerando."""
    return [round(start + (end - start) * (i / (count - 1)) ** power, 3) for i in range(count)]


def _typing_times(start: float, end: float) -> list[float]:
    """Cuándo aparece cada letra de la pregunta. El salto de línea cuenta como una pausa."""
    text = " ".join(QUESTION)
    step = (end - start) / (len(text) - 1)
    return [round(start + i * step, 4) for i in range(len(text))]


def _review() -> dict:
    """B4 · El repaso: ciclos 4 y 5 de la clave, de 13,0 a 19,4 s.

    La persona es una yema de luz que deja trazo de lápiz. Repasa los 24 dudosos
    (el primero y el último, despacio; los demás de corrido, en dos tandas que
    cierran con la clave), abre el difícil, audita uno de los claros y saca una
    muestra del montón.
    """
    start = beat(4, 0)  # 13,0
    wake = 0.24  # lo que tarda en cerrarse un aro al paso de la yema
    first = [round(beat(4, 1) - 0.52, 3), round(beat(4, 1) - 0.02, 3)]  # 13,08 → 13,58
    run_a = _spaced(13.68, beat(4, 2), 11, 0.8)  # hasta la clave de 14,2
    run_b = _spaced(14.28, 14.6, 11, 0.85)
    hero = [14.66, 14.94]  # el difícil: otra vez despacio
    sample = _spaced(beat(5, 3), beat(5, 4), 5, 0.9)  # 18,2 → 18,6
    return {
        "start": start,
        "yema_in": round(start - 0.14, 3),
        "labels_out": [start, round(start + 0.1, 3)],  # «claro · dos lecturas coinciden» y «dudoso»
        "title": [start, 14.72],
        # Cada aro: cuándo empieza y cuándo cierra su trazo, en el orden en que se repasan.
        "rings": [first] + [[t, round(t + wake, 3)] for t in run_a + run_b] + [hero],
        # El golpe de cada grano repasado: al cerrar el aro si se traza despacio, al tocarlo si va de corrido.
        "ring_hits": [beat(4, 1), *run_a, *run_b, hero[1]],
        "ring_groups": [1, len(run_a), len(run_b), 1],  # cómo se agrupan: cada tanda cierra en un golpe
        "open": [hero[1], round(hero[1] + 0.28, 3)],  # el grano se abre en la línea real: 14,94 → 15,22
        "underline": [[beat(4, 4), round(beat(4, 4) + 0.3, 3)], [15.76, 15.92]],  # subraya «4x10», dos veces
        "morph": [round(beat(5, 0) - 0.15, 3), round(beat(5, 0) + 0.05, 3)],  # cambia a «Caja de 40 lb…»
        "reading": beat(5, 0),  # y ahí queda fija la lectura: 16,2
        "close": [16.98, 17.22],
        "audit_ring": [round(beat(5, 2) - 0.26, 3), beat(5, 2)],  # 17,14 → 17,4
        "audit_label": [beat(5, 2), 19.12],
        "audit_underline": [17.92, 18.22],  # la persona subraya su rótulo
        "discard_label_out": 17.6,
        "sample_label": [17.98, 19.3],
        "sample_underline": [18.4, 18.7],
        "sample_rings": [[t, round(t + wake, 3)] for t in sample],
        "yema_out": [18.66, 19.3],
    }


def _measure() -> dict:
    """B5 · La misma medida: ciclos 6 y 7 de la clave, de 19,4 a 25,8 s.

    La cámara entra en la fila de los claros y un grano con marca se abre en el
    anuncio de 10 lb. Sus palabras vuelan y quedan grandes, ruedan las dos cuentas
    (750 y 683 CUP/lb) y los tres granos de la celda caen en una recta. Cada cosa
    entra en un golpe de la clave y lo ya leído se retira antes de lo siguiente.
    """
    start = beat(6, 0)  # 19,4
    return {
        "start": start,
        "anticipation": round(start - 0.2, 3),  # la cámara toma impulso
        "dive": [start, round(beat(6, 1) + 0.1, 3)],  # 19,4 → 20,1: entra en la fila
        "open": [19.74, round(beat(6, 1) + 0.04, 3)],  # el grano se abre en el anuncio
        "opened": beat(6, 1),  # y ya se lee: 20,0
        "slide": [19.95, 20.95],  # la mesa se corre y la fila queda a un lado
        "marks": [20.2, 20.3, 20.4],  # la máquina subraya producto, cantidad y precio
        "type_step": TYPE_STEP,
        # Cada palabra: cuándo despega del anuncio y cuándo cae, ya grande, en su sitio.
        "words": [[beat(6, 2), 20.9], [20.74, 21.04], [20.88, 21.18]],  # 20,6 …
        "province": [21.08, beat(6, 3)],  # «La Habana» entra por el borde y cae en 21,4
        "pack_out": [21.22, 21.6],  # el anuncio, ya leído, se cierra en su grano
        "regroup": [round(beat(6, 4) - 0.22, 3), beat(6, 4)],  # 21,58 → 21,8: se arma la cuenta
        "roll_power": 2.6,  # la cuenta arranca rápido y se asienta: 1 − (1 − u)^2,6
        "roll_a": [beat(6, 4), beat(7, 0)],  # 21,8 → 22,6: 7.500 ÷ 10 lb → 750
        "rise_a": [22.7, 22.92],  # el 750 sube a la cabecera
        "pair_in": [22.62, 22.88],  # entran los dos granos con aro: la caja
        "roll_b": [22.9, beat(7, 1)],  # más breve: 22,9 → 23,2: 27.300 ÷ 40 lb → 683
        "rise_b": [23.34, 23.6],
        "line": [23.3, 23.76],  # la recta se dibuja sola
        "drops": [23.68, beat(7, 2), 23.94],  # caen los tres granos: 683, 683 y 750
        "median": 24.14,  # la muesca
        "median_typed": 24.21,  # y su palabra, tecleada
        "median_word": MEDIAN_WORD,
        "count": 24.34,  # «3 anuncios»
        "by_hand": 24.44,  # «2 a mano»
        "hand": [beat(7, 3), round(beat(7, 3) + 0.34, 3)],  # 24,6: el aro, a mano
        "by_system": 24.7,  # «1 por el sistema»
        "system_line": SYSTEM_LINE.format(n=DATA["cell"]["system"]),
        "stamp": beat(7, 4),  # 25,0: la marca cuadrada
        "yema": [24.36, 25.4],  # la persona vuelve a firmar
    }


def _island() -> dict:
    """B6 · La isla: ciclos 8 y 9 de la clave, de 25,8 a 32,2 s.

    Lo del cristal se recoge en un punto, que está en La Habana. Las provincias se
    trazan como una ruta mientras la cámara baja por la isla hasta Santiago de Cuba,
    y al final el plano se abre: dos provincias con granos y catorce en puntos.
    """
    start = beat(8, 0)  # 25,8
    piles = {p["slug"]: p["ads"] for p in DATA["by_province"]}
    trace = [26.1, 26.5, 26.68]  # La Habana y sus dos vecinas
    trace += _spaced(27.0, 27.74, 9)  # de Matanzas a Granma, por delante de la cámara
    trace += [beat(8, 4) - 0.14, 28.34]  # Santiago de Cuba (queda trazada en 28,5) y Guantánamo
    trace += [29.7, 29.9]  # el occidente, cuando el plano se abre
    return {
        "start": start,
        "gather": [start, round(start + 0.55, 3)],  # el cristal se recoge
        "pull": [start, beat(8, 1) + 0.25],  # la cámara se aleja: 25,8 → 26,65
        "sea": beat(8, 1),  # de la mesa ya no queda nada: 26,4
        "route": list(ROUTE),
        "trace": [round(t, 3) for t in trace],
        "trace_dur": 0.44,
        "near_label": [beat(8, 1), 27.02],  # «La Habana»: 26,4
        "near_drops": _spaced(26.62, beat(8, 2) - 0.02, piles["la-habana"], 0.85),  # 27 granos, hasta 27,0
        "near_full": beat(8, 2),  # 27,0: ya están los 27
        "anticipation": 26.9,
        "travel": [beat(8, 2), beat(8, 4)],  # 27,0 → 28,2: baja por la isla
        "far_label": [27.96, 29.42],  # «Santiago de Cuba»
        "far_drops": _spaced(28.14, 28.44, piles["santiago-de-cuba"]),  # 7 granos
        "far_full": 28.44,
        "pick": [28.5, 28.78],  # uno se aparta: el del pollo
        "price": [28.56, beat(9, 0)],  # la cifra rueda y queda fija en 29,0
        "far_out": [29.3, 29.5],
        "open": [beat(9, 1) - 0.2, beat(9, 2)],  # 29,4 → 30,2: se abre el plano
        "filled": beat(9, 2),  # 30,2: dos provincias con granos
        "title": beat(9, 2),  # «34 anuncios en 30 días»
        "split": 30.6,  # «24 a mano · 10 por el sistema»
        "caption": [beat(9, 3), beat(9, 4)],  # «Una muestra en formación,» (31,0) «no un censo.» (31,4)
        "date": beat(9, 4),
        "title_text": [s.format(**DATA) for s in SAMPLE_TITLE],
        "split_text": [s.format(**DATA) for s in SAMPLE_SPLIT],
        "caption_text": list(SAMPLE_CAPTION),
        "title_out": beat(10, 0),  # 32,2
        "caption_out": 33.0,  # lo último que se dice de B6 acaba en 33,0
    }


def _close() -> dict:
    """B7 · El cierre: ciclos 10 y 11 de la clave, de 32,2 a 38,6 s.

    La isla se acuesta y se vuelve horizonte; la cámara sube y entra el atardecer de
    la portada. La clave calla en el botón (36,0) y el último segundo queda quieto.
    """
    start = beat(10, 0)  # 32,2
    return {
        "start": start,
        "lie": [start, beat(10, 1) + 0.1],  # la isla se acuesta: 32,2 → 32,9
        "rise": [beat(10, 1), 34.3],  # la cámara sube: el horizonte baja
        "sky": [32.3, 34.0],  # el cielo se enciende desde el horizonte
        "sun": [beat(10, 2), 34.7],  # el sol sube con rebote desde 33,4
        "city": [[beat(10, 2), 34.3], [33.62, 34.52]],  # lejos y medio
        "palms": [beat(10, 3), 35.0],  # 34,2
        "guani": [beat(10, 4), 35.36],  # 34,6
        "stars": [32.3, 34.6],  # los granos se elevan
        "star_time": 1.15,
        "title": [beat(10, 2), beat(10, 3)],  # «Una máquina lee;» (33,4) «una persona supervisa.» (34,2)
        "title_text": list(CLOSE_TITLE),
        "button": [beat(11, 0), beat(11, 1)],  # un grano crece hasta ser el botón: 35,4 → 36,0
        "button_text": CLOSE_BUTTON,
        "sheen": [36.2, 36.95],
        "signature": beat(11, 2),  # 36,6
        "signature_text": CLOSE_SIGNATURE,
        "last_clave": beat(11, 1),  # la clave cierra aquí su frase
        "still": 37.6,  # desde aquí no se mueve nada
    }


def build() -> dict:
    """La línea de tiempo completa, lista para pasar a JSON."""
    whip = (beat(0, 4), beat(1, 0))  # 2,6 → 3,4
    guani = beat(2, 0)  # 6,6
    discard = beat(3, 0)  # 9,8
    clear = beat(3, 2)  # 11,0
    doubt = beat(3, 3)  # 11,8
    clear_step = 0.05
    review = _review()
    last_ring = review["rings"][-1][0]
    return {
        "fps": FPS,
        "duration": DURATION,
        "proof": PROOF,
        "pulse": PULSE,
        "clave": clave_hits(whip[1], _close()["last_clave"]),
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
        # Lo dudoso late hasta que la persona lo repasa: 11,8 … 14,6
        "doubt_pulses": [p for p in (round(doubt + PULSE * i, 3) for i in range(12)) if p < last_ring],
        "doubt_label": beat(3, 4),  # 12,2
        "review": review,
        "measure": _measure(),
        "island": _island(),
        "close": _close(),
    }
