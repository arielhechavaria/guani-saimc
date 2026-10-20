"""El pie compartido de la capa pública y las páginas de texto a las que apunta
(2026-10-05, `docs/decisions.md`).

Mismo reparto que `landing_view`: acá se decide **qué enlaces existen**; la
plantilla (`_public_footer.html`) solo los pinta. La regla que este módulo
sostiene es una: *un enlace del pie existe si y solo si su página existe*. Por
eso la lista de páginas (`SITE_PAGES`) es la misma de la que el router saca sus
rutas, y «Contacto» entra a la columna con la misma condición con la que su
ruta deja de dar 404 (`SiteFooter.has_contact`).

El pie lo usan la landing y estas páginas. **No** el tablero, el mapa ni
explorar: ahí la ausencia de pie es una decisión registrada (2026-09-21).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from saimc.config import Settings
from saimc.web.landing_view import FooterLink, FooterLinks, build_footer_links, http_url
from saimc.web.product_nav import HOME_PATH

#: Ruta de la página de contacto. Existe solo con un correo o una red puestos.
CONTACT_PATH = "/contacto"
CONTACT_TEMPLATE = "public_page_contacto.html"

#: Ruta de la página de apoyo. Existe solo con un enlace de donación
#: (`SAIMC_DONATE_URL`) o con el QR puesto por el operador.
DONATE_PATH = "/apoyar"
DONATE_TEMPLATE = "public_page_apoyar.html"
#: Los QR para pagar desde cuentas cubanas (Transfermóvil/EnZona) son imágenes
#: que pone el operador en `static/`, una por tarjeta; cada una se muestra solo
#: si su archivo existe.
DONATE_QR_DIR = Path(__file__).parent / "static"


@dataclass(frozen=True)
class DonateQr:
    """Un QR de pago: cómo se llama la tarjeta y de dónde se sirve."""

    label: str
    src: str


#: (rótulo, nombre del archivo en `static/`), en el orden en que se dibujan.
_DONATE_QR_FILES: tuple[tuple[str, str], ...] = (
    ("Tarjeta Clásica", "apoyar-qr-clasica.jpg"),
    ("Tarjeta BPA", "apoyar-qr-bpa.jpg"),
)


def _donate_qrs() -> tuple[DonateQr, ...]:
    return tuple(
        DonateQr(label, f"/static/{name}")
        for label, name in _DONATE_QR_FILES
        if (DONATE_QR_DIR / name).is_file()
    )


@dataclass(frozen=True)
class SitePage:
    """Una página de texto: su ruta, su plantilla y cómo la nombra el pie."""

    path: str
    template: str
    label: str
    #: Columna del pie en la que va: «Guani» o «Legal».
    column: str


#: Las páginas que existen siempre, en el orden en que el pie las lista. El
#: router registra una ruta por cada una: agregar una fila acá es lo único que
#: hace falta para que la página exista y quede enlazada.
SITE_PAGES: tuple[SitePage, ...] = (
    SitePage("/quienes-somos", "public_page_quienes_somos.html", "Quiénes somos", "Guani"),
    SitePage("/metodologia", "public_page_metodologia.html", "Cómo calculamos los precios", "Guani"),
    SitePage("/preguntas", "public_page_preguntas.html", "Preguntas frecuentes", "Guani"),
    SitePage("/terminos", "public_page_terminos.html", "Términos y condiciones", "Legal"),
    SitePage("/privacidad", "public_page_privacidad.html", "Política de privacidad", "Legal"),
    SitePage("/uso-de-datos", "public_page_uso_de_datos.html", "Uso de los datos", "Legal"),
)

#: Columna «Precios»: las tres vistas de `/precios` y la descarga. `vista` va
#: explícita para que la cookie no decida por el visitante (mismo motivo que en
#: `product_nav._query`).
_PRICE_LINKS: tuple[FooterLink, ...] = (
    FooterLink("Tablero de hoy", f"{HOME_PATH}?vista=tablero"),
    FooterLink("Mapa por provincia", f"{HOME_PATH}?vista=mapa"),
    FooterLink("Explorar y comparar", f"{HOME_PATH}?vista=explorar"),
    FooterLink("Descargar datos (CSV)", "/datos.csv"),
)


@dataclass(frozen=True)
class FooterColumn:
    title: str
    links: tuple[FooterLink, ...]


@dataclass(frozen=True)
class SiteFooter:
    """Todo lo que el pie dibuja y no es texto fijo."""

    #: Precios, Guani y Legal, en ese orden. Siempre las tres.
    columns: tuple[FooterColumn, ...]
    #: Redes y correo configurados. Vacío es un estado soportado: la columna
    #: «Síguenos» no se dibuja.
    contact: FooterLinks
    year: int
    home_href: str = HOME_PATH
    #: Enlace externo de donación ya validado, o `None`.
    donate_url: str | None = None
    #: Los QR cuyo archivo existe, o vacío.
    donate_qrs: tuple[DonateQr, ...] = ()

    @property
    def has_contact(self) -> bool:
        """Si hay a dónde escribir. Decide a la vez la columna «Síguenos», el
        enlace «Contacto» y si `/contacto` responde."""
        return self.contact.any


    @property
    def has_donate(self) -> bool:
        """Si hay cómo apoyar. Decide a la vez `/apoyar`, su enlace del pie y
        el bloque de la landing."""
        return self.donate_url is not None or bool(self.donate_qrs)


def _page_links(column: str) -> tuple[FooterLink, ...]:
    return tuple(FooterLink(page.label, page.path) for page in SITE_PAGES if page.column == column)


def build_site_footer(settings: Settings, *, year: int) -> SiteFooter:
    """El pie para esta configuración. `year` llega de afuera para que el
    copyright se pueda probar sin depender del reloj."""
    contact = build_footer_links(settings)
    guani = _page_links("Guani")
    if contact.any:
        guani = (*guani, FooterLink("Contacto", CONTACT_PATH))
    donate_url = http_url(settings.donate_url)
    donate_qrs = _donate_qrs()
    if donate_url is not None or donate_qrs:
        guani = (*guani, FooterLink("Apoyar", DONATE_PATH))
    return SiteFooter(
        columns=(
            FooterColumn("Precios", _PRICE_LINKS),
            FooterColumn("Guani", guani),
            FooterColumn("Legal", _page_links("Legal")),
        ),
        contact=contact,
        year=year,
        donate_url=donate_url,
        donate_qrs=donate_qrs,
    )
