"""Lo que la landing (`GET /`, 2026-10-05) muestra y no es texto fijo.

Dos cosas, las dos armadas acá para que la plantilla solo pinte y para poder
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

No consulta la base: recibe las observaciones que ya trae
`routers/public._visible_observations`, las mismas que alimentan el tablero.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

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
