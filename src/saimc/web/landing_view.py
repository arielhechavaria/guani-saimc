"""Lo que la landing (`GET /`, 2026-10-05) muestra y no es texto fijo.

Tres cosas, las tres armadas acá para que la plantilla solo pinte y para poder
probarlas sin parsear HTML (mismo reparto que `card_view` y `watchlist_view`):

- **La tarjeta «Así se lee un precio».** Un ejemplo real: el producto piloto
  con más anuncios en la ventana por defecto, en su provincia con más anuncios.
  Sigue las reglas de `card_view` (§9.0): el titular es el rango, la mediana va
  adentro y nunca sola, el número de anuncios queda a la vista y la tarjeta va
  rotulada «muestra en formación». Si no hay ninguna celda con rango, no hay
  tarjeta: la plantilla dibuja la ilustración sin cifras, nunca un número de
  relleno.
- **Los enlaces de contacto y redes del pie**, que salen de la configuración y
  solo se dibujan si están puestos.
- **El recorrido de «Así trabajo»** (2026-10-06): cinco capturas del sistema y,
  si existe el archivo, un video. Qué se dibuja depende de qué hay en
  `static/landing/`, y eso se mira acá (`build_landing_flow`), no en la
  plantilla.

No consulta la base: recibe las observaciones que ya trae
`routers/public._visible_observations`, las mismas que alimentan el tablero.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from saimc.cells import RetailObservation
from saimc.config import Settings
from saimc.web.card_view import SAMPLE_FORMING, format_price

#: Dónde empieza y cuánto mide la barra del rango dentro de su pista, en
#: porcentaje. Deja aire a los dos lados para que el punto del mínimo y el del
#: máximo no queden cortados contra el borde de la tarjeta.
BAR_LEFT = 10.0
BAR_WIDTH = 80.0

#: Filas en las que se reparten los puntos para que dos anuncios de precio
#: parecido no se tapen del todo. Solo presentación: la posición horizontal es
#: la que dice el precio.
DOT_ROWS = 4


@dataclass(frozen=True)
class LandingDot:
    """Un anuncio: dónde cae entre el mínimo y el máximo."""

    #: Porcentaje desde la izquierda de la pista.
    left: float
    #: Fila (0 a `DOT_ROWS - 1`), para separar puntos vecinos.
    row: int


@dataclass(frozen=True)
class LandingPrice:
    """Un producto en una provincia, con su rango y la muestra que lo respalda."""

    product_name: str
    province_name: str
    currency: str
    unit: str
    count: int
    minimum: Decimal
    median: Decimal
    maximum: Decimal
    dots: tuple[LandingDot, ...]
    median_left: float
    bar_left: float = BAR_LEFT
    bar_width: float = BAR_WIDTH

    @property
    def minimum_text(self) -> str:
        return format_price(self.minimum)

    @property
    def median_text(self) -> str:
        return format_price(self.median)

    @property
    def maximum_text(self) -> str:
        return format_price(self.maximum)

    @property
    def state(self) -> str:
        """El rótulo de §9.0. Hoy es el mismo para toda celda (`card_view`)."""
        return SAMPLE_FORMING


def _position(value: Decimal, minimum: Decimal, maximum: Decimal) -> float:
    """El precio como porcentaje de la pista. El llamador garantiza
    `minimum < maximum`."""
    share = float((value - minimum) / (maximum - minimum))
    return round(BAR_LEFT + BAR_WIDTH * share, 2)


def build_landing_price(observations: Sequence[RetailObservation]) -> LandingPrice | None:
    """La celda de ejemplo de la landing, o `None` si ninguna sirve.

    Se elige el producto con más anuncios (sumando sus provincias) y, dentro de
    él, la provincia con más anuncios. Una celda solo sirve si tiene rango de
    verdad —mínimo distinto del máximo—: con un solo anuncio, o con varios al
    mismo precio, no hay dispersión que enseñar a leer, y dibujar una barra
    sugeriría una variación que nadie observó. Si el producto más anunciado no
    tiene ninguna celda así, se pasa al siguiente.

    Descarta lo mismo que `card_view.build_cards`: filas sin provincia y filas
    sin convertir a CUP. Moneda y unidad entran a la clave para no fundir en un
    rango dos cifras que no son comparables. Los empates se resuelven por
    nombre, para que la tarjeta no cambie de una carga a otra.
    """
    cells: dict[tuple[str, str, str, str], list[Decimal]] = defaultdict(list)
    for o in observations:
        if o.province_name is None or o.unconverted:
            continue
        cells[(o.product_name, o.currency, o.unit, o.province_name)].append(o.unit_price)

    per_product: dict[tuple[str, str, str], int] = defaultdict(int)
    for (product, currency, unit, _province), prices in cells.items():
        per_product[(product, currency, unit)] += len(prices)

    with_range = [key for key, prices in cells.items() if min(prices) < max(prices)]
    if not with_range:
        return None
    product, currency, unit, province = min(
        with_range,
        key=lambda k: (-per_product[k[:3]], k[0], -len(cells[k]), k[3], k[1], k[2]),
    )

    prices = sorted(cells[(product, currency, unit, province)])
    minimum, maximum = prices[0], prices[-1]
    median = statistics.median(prices)
    return LandingPrice(
        product_name=product,
        province_name=province,
        currency=currency,
        unit=unit,
        count=len(prices),
        minimum=minimum,
        median=median,
        maximum=maximum,
        dots=tuple(
            LandingDot(left=_position(price, minimum, maximum), row=index % DOT_ROWS)
            for index, price in enumerate(prices)
        ),
        median_left=_position(median, minimum, maximum),
    )


@dataclass(frozen=True)
class FooterLink:
    label: str
    href: str


@dataclass(frozen=True)
class FooterLinks:
    """Contacto y redes del pie. Vacío es un estado soportado: la plantilla no
    dibuja la columna que no tiene nada."""

    email: str | None
    social: tuple[FooterLink, ...]

    @property
    def any(self) -> bool:
        return self.email is not None or bool(self.social)


def _http_url(raw: str | None) -> str | None:
    """La URL recortada si es `http(s)://…`, o `None`.

    El valor viene del `.env` del operador, no de un visitante, pero termina en
    un `href` de la página pública: se acepta solo lo que es un enlace web, así
    un valor a medio escribir no publica un `href` roto ni un esquema raro.
    """
    value = (raw or "").strip()
    if value.startswith(("https://", "http://")) and " " not in value:
        return value
    return None


def _email(raw: str | None) -> str | None:
    """El correo recortado si tiene forma de correo, o `None`. No valida que
    exista: solo evita armar un `mailto:` con algo que no lo es."""
    value = (raw or "").strip()
    local, at, domain = value.partition("@")
    allowed = all(c.isalnum() or c in "._+-@" for c in value)
    if at and local and "." in domain and allowed and value.count("@") == 1:
        return value
    return None


def build_footer_links(settings: Settings) -> FooterLinks:
    """Los enlaces configurados, en orden fijo: Facebook, WhatsApp, Telegram."""
    candidates = (
        ("Facebook", settings.link_facebook),
        ("WhatsApp", settings.link_whatsapp),
        ("Telegram", settings.link_telegram),
    )
    social = tuple(
        FooterLink(label=label, href=href)
        for label, raw in candidates
        if (href := _http_url(raw)) is not None
    )
    return FooterLinks(email=_email(settings.contact_email), social=social)


# ── El recorrido de «Así trabajo» ────────────────────────────────────────────

#: Dónde viven las capturas y el video. Se resuelve con `Path(__file__)`, como
#: el montaje de `/static` en `app.py`, para no depender del cwd. Es una
#: variable de módulo que `build_landing_flow` lee al llamarse (no un valor por
#: defecto del parámetro) para que un test pueda apuntarla a un `tmp_path`.
LANDING_STATIC_DIR = Path(__file__).parent / "static" / "landing"
LANDING_STATIC_URL = "/static/landing"

#: Medida de las cinco capturas, en píxeles. Va a los atributos `width` y
#: `height` de cada `<img>` para que el navegador reserve el hueco antes de que
#: la imagen llegue. Si se vuelven a tomar con otra medida, se cambia acá: un
#: test compara estos números con los archivos.
SHOT_WIDTH = 460
SHOT_HEIGHT = 958

VIDEO_NAME = "guani-flujo.mp4"
VIDEO_POSTER_NAME = "guani-flujo-poster.webp"

#: Un paso del recorrido: (archivo, texto del paso, `alt` de la captura). El
#: texto es el del prototipo aprobado; el `alt` dice qué se ve en la imagen, que
#: no es lo mismo. La captura 1 es una recreación y su `alt` lo dice. El de la
#: 5 no cita el título del tablero a propósito: un test de la landing usa ese
#: título para comprobar que `/` no es el tablero.
_FLOW: tuple[tuple[str, str, str], ...] = (
    (
        "flujo-1.webp",
        "Un emprendedor publica su oferta en un grupo o canal.",
        "Recreación de un anuncio real en un grupo de ventas: un globo de chat con el texto de la oferta.",
    ),
    (
        "flujo-2.webp",
        "El anuncio llega a Guani tal cual se escribió.",
        "El mismo anuncio en la herramienta de Guani, en la tarjeta «Evidencia», con su texto intacto.",
    ),
    (
        "flujo-3.webp",
        "Saco el producto, el precio y la provincia.",
        "Los campos extraídos: producto Pollo, precio 40.00 USD, cantidad 40 lb, provincia La Habana.",
    ),
    (
        "flujo-4.webp",
        "Una persona lo revisa y lo corrige si hace falta.",
        "Las preguntas que responde la persona que verifica el anuncio, con el botón «Guardar corrección».",
    ),
    (
        "flujo-5.webp",
        "Lo ves en el tablero como rango, mediana y número de anuncios.",
        "El tablero público de precios de Guani, donde el anuncio ya cuenta dentro de un rango.",
    ),
)


@dataclass(frozen=True)
class FlowStep:
    """Un paso de la lista. Existe con o sin capturas."""

    number: int
    text: str


@dataclass(frozen=True)
class FlowShot:
    """Una captura del pase, lista para un `<img>`."""

    src: str
    alt: str
    width: int = SHOT_WIDTH
    height: int = SHOT_HEIGHT


@dataclass(frozen=True)
class FlowVideo:
    src: str
    #: `None` si no hay ni portada propia ni captura 5: el `<video>` va sin
    #: `poster` antes que con una URL que da 404.
    poster: str | None
    #: Peso del archivo, ya en texto («3,4 MB»). No hay duración: saberla pide
    #: leer el contenedor del video, y eso sería una dependencia nueva.
    size_text: str


@dataclass(frozen=True)
class LandingFlow:
    steps: tuple[FlowStep, ...]
    #: Las cinco capturas, o ninguna: si falta una sola, la plantilla vuelve a
    #: las tres tarjetas ilustradas en vez de dejar un hueco en el pase.
    shots: tuple[FlowShot, ...]
    video: FlowVideo | None


def _file_size(directory: Path, name: str) -> tuple[int, int] | None:
    """(tamaño, mtime) del archivo, o `None` si no existe o está vacío. Un
    archivo de cero bytes (una copia cortada) cuenta como ausente. Es un
    `stat`, sin abrir nada."""
    try:
        info = (directory / name).stat()
    except OSError:
        return None
    if info.st_size == 0:
        return None
    return info.st_size, int(info.st_mtime)


def _versioned_url(directory: Path, name: str) -> str | None:
    """La URL del archivo con `?v=<mtime>`, o `None` si no está.

    Mismo cache-busting que `css_version()` en `app.py`: el navegador puede
    guardar la captura todo lo que quiera y, si se vuelve a tomar, la URL
    cambia sola.
    """
    found = _file_size(directory, name)
    if found is None:
        return None
    return f"{LANDING_STATIC_URL}/{name}?v={found[1]}"


def _megabytes(size: int) -> str:
    """El peso en MB con un decimal y coma. MB de un millón de bytes, que es
    como lo cuenta un plan de datos. Nunca «0,0»: lo mínimo que se anuncia es
    0,1, para no decirle a nadie que un video no pesa."""
    return f"{max(size / 1_000_000, 0.1):.1f}".replace(".", ",") + " MB"


def build_landing_flow(directory: Path | None = None) -> LandingFlow:
    """Qué dibuja «Así trabajo» según lo que hay en disco.

    Se llama una vez por pedido y cuesta siete `stat` como mucho. No se guarda
    entre pedidos a propósito, igual que la versión del CSS: así copiar el
    video al servidor (o volver a tomar una captura) no pide reiniciar nada.
    """
    directory = LANDING_STATIC_DIR if directory is None else directory
    steps = tuple(FlowStep(number=n, text=text) for n, (_, text, _) in enumerate(_FLOW, start=1))

    urls = [_versioned_url(directory, name) for name, _, _ in _FLOW]
    shots: tuple[FlowShot, ...] = ()
    if all(urls):
        shots = tuple(
            FlowShot(src=url, alt=alt)
            for url, (_, _, alt) in zip(urls, _FLOW, strict=True)
            if url is not None
        )

    video: FlowVideo | None = None
    found = _file_size(directory, VIDEO_NAME)
    if found is not None:
        size, mtime = found
        video = FlowVideo(
            src=f"{LANDING_STATIC_URL}/{VIDEO_NAME}?v={mtime}",
            poster=_versioned_url(directory, VIDEO_POSTER_NAME) or urls[-1],
            size_text=_megabytes(size),
        )
    return LandingFlow(steps=steps, shots=shots, video=video)
