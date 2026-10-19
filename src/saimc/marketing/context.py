"""El catálogo de tarjetas y el contexto que pinta cada una.

Antes vivía en `web/routers/marketing.py`; se mudó acá para que el lote diario
(`saimc.marketing.daily`) arme exactamente las mismas tarjetas que la página
`/marketing`, sin importar un router de FastAPI desde un script de systemd.
Ninguna función de este módulo inventa un cómputo: cada rama reusa el mismo
que ya usa la vista pública (`docs/decisions.md`, slice de marketing).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy.orm import Session

from saimc.cells import RetailObservation, compute_retail_observations, compute_retail_ranges
from saimc.marketing.cards import (
    build_como_se_movio,
    build_dato_del_dia,
    build_de_donde_sale,
)
from saimc.progress import PHASE0_PROVINCES, pilot_product_names
from saimc.web.card_view import build_cards
from saimc.web.price_charts import build_month_vs_month

#: La dirección que se imprime en las piezas. El sitio pasó a `guani.app`
#: (`docs/decisions.md`, 2026-10-19). Cambiarla es editar esta constante, no
#: cada plantilla.
SITE_URL = "guani.app"

#: Una semana, igual que el pilar «Dato del día»/«Cómo se movió» del calendario.
DEFAULT_WINDOW_DAYS = 7

#: «De dónde sale este precio» mira más atrás a propósito (es la tarjeta que
#: explica el método, no la del ritmo semanal): con 7 días muchas veces no
#: hay ni 4 anuncios que listar.
LOOKBACK_WINDOW_DAYS = 30


@dataclass(frozen=True)
class Block:
    """Una parte de la tarjeta que el editor puede mostrar u ocultar.

    Los bloques sin interruptor (`toggleable=False`) son los que hacen que la
    pieza diga algo —título, rango, fecha—: la URL no los apaga."""

    slug: str
    label: str
    default: bool = True
    toggleable: bool = True


@dataclass(frozen=True)
class CardType:
    slug: str
    label: str
    template: str
    width: int
    height: int
    needs_product: bool = False
    needs_province: bool = False
    #: Bloques de la plantilla (`data-block="<slug>"`). Vacía en las tarjetas
    #: de puro branding, que no tienen nada que apagar.
    blocks: tuple[Block, ...] = ()
    #: Tarjeta «Libre»: en lugar de bloques fijos, el operador arma los suyos
    #: (`options.FREE_TYPES`).
    free: bool = False
    #: La tarjeta pinta una fila por provincia (`card.rows`): el editor puede
    #: ocultar algunas (`CardOptions.hidden_provinces`).
    rows_by_province: bool = False


CARD_TYPES: tuple[CardType, ...] = (
    CardType(
        "dato-del-dia", "Dato del día", "marketing_dato_del_dia.html",
        1080, 1350, needs_product=True, rows_by_province=True,
        blocks=(
            Block("titulo", "Título y fecha de corte", toggleable=False),
            Block("rango", "Rango de precios", toggleable=False),
            Block("barra", "Barra de rango y mediana"),
            Block("mediana", "Mediana y cantidad de anuncios"),
            Block("comparacion", "Comparación entre provincias"),
            Block("muestra", "Aviso «muestra en formación»"),
            Block("fuente", "Nota de la fuente"),
        ),
    ),
    CardType(
        "como-se-movio", "Cómo se movió", "marketing_como_se_movio.html",
        1080, 1350, needs_product=True, rows_by_province=True,
        blocks=(
            Block("titulo", "Título", toggleable=False),
            Block("cambio", "Cambio por provincia", toggleable=False),
            Block("grafico", "Gráfico de líneas"),
            Block("muestra", "Aviso «muestra en formación»"),
            Block("fuente", "Nota de la fuente"),
        ),
    ),
    CardType(
        "de-donde-sale", "De dónde sale este precio", "marketing_de_donde_sale.html",
        1080, 1350, needs_product=True, needs_province=True,
        blocks=(
            Block("titulo", "Título", toggleable=False),
            Block("rango", "Rango y cantidad de anuncios", toggleable=False),
            Block("anuncios", "Tabla de anuncios"),
            Block("nota", "Nota sobre la imagen original"),
            Block("posicion", "Número de diapositiva (solo en un carrusel)"),
        ),
    ),
    CardType("libre", "Libre", "marketing_libre.html", 1080, 1350, free=True),
    # Diapositiva de la sección educativa (`saimc.marketing.lessons`): bloques
    # libres con el kicker «Aprende con Guani». Texto narrativo, sin cifras.
    CardType("leccion", "Educativo", "marketing_leccion.html", 1080, 1350, free=True),
    CardType(
        "carrusel-portada", "Carrusel · portada", "marketing_carrusel_portada.html", 1080, 1350
    ),
    CardType(
        "carrusel-consejo", "Carrusel · cómo leer los precios",
        "marketing_carrusel_consejo.html", 1080, 1350,
    ),
    CardType(
        "story-precalentamiento", "Story de precalentamiento", "marketing_story.html", 1080, 1920
    ),
    CardType(
        "portada-facebook", "Portada de Facebook", "marketing_portada_facebook.html", 1640, 624
    ),
    CardType(
        "como-funciona", "De los grupos a la cifra", "marketing_como_funciona.html", 1080, 1350
    ),
    CardType(
        "carrusel-cierre", "Carrusel · cierre", "marketing_carrusel_cierre.html", 1080, 1350
    ),
    # No es para publicar: es la imagen que ven WhatsApp, Telegram y X al
    # pegar un enlace del sitio (`og:image`). Se rasteriza una vez con
    # `tools/build_seo_assets.py` y se sirve desde `/static/seo/og-guani.png`.
    CardType("og-compartir", "Vista previa al compartir", "marketing_og_compartir.html", 1200, 630),
)

BY_SLUG: dict[str, CardType] = {t.slug: t for t in CARD_TYPES}


def build_context(
    kind: CardType,
    db: Session,
    *,
    product: str | None,
    province: str | None,
    hoy: date | None = None,
) -> dict | None:
    """El contexto para la plantilla de `kind`, o `None` si el pedido no se
    puede armar (producto fuera del foco, o sin ese dato todavía).

    Cada rama reusa el mismo cómputo que ya usa la vista pública — nunca un
    promedio propio (`docs/decisions.md`, slice de marketing).

    `hoy` es el último día que cuenta. Sin él (la página `/marketing`) las
    ventanas terminan hoy y no tienen tope superior, como siempre. Con él (el
    lote diario pasa el día anterior) las ventanas terminan ahí y se les pone
    tope: lo que se cargó después no entra en la cifra."""
    products = pilot_product_names(db)
    if kind.needs_product and product not in products:
        return None

    tope = hoy
    hoy = hoy or date.today()

    if kind.slug == "dato-del-dia":
        if product is None:
            return None
        desde = hoy - timedelta(days=DEFAULT_WINDOW_DAYS - 1)
        report = compute_retail_ranges(db, days=DEFAULT_WINDOW_DAYS, hoy=hoy, hasta=tope)
        ranges = [
            r for r in report.ranges
            if r.province_name in PHASE0_PROVINCES and not r.unconverted
        ]
        cards = build_cards(ranges)
        dato = build_dato_del_dia(
            cards, product_name=product, province_names=PHASE0_PROVINCES,
            week_from=desde, week_to=hoy,
        )
        return {"card": dato}

    if kind.slug == "como-se-movio":
        if product is None:
            return None
        ventana_desde = hoy - timedelta(days=DEFAULT_WINDOW_DAYS)
        actual = compute_retail_observations(
            db, days=DEFAULT_WINDOW_DAYS, hoy=hoy, hasta=tope
        ).observations
        previa_completa = compute_retail_observations(
            db, days=2 * DEFAULT_WINDOW_DAYS, hoy=hoy, hasta=tope
        ).observations
        previa = [o for o in previa_completa if o.day < ventana_desde]

        def _en_foco(o: RetailObservation) -> bool:
            return o.product_name == product and o.province_name in PHASE0_PROVINCES

        slopes = build_month_vs_month(
            [o for o in actual if _en_foco(o)], [o for o in previa if _en_foco(o)],
            window_days=DEFAULT_WINDOW_DAYS, hoy=hoy,
        )
        movio = build_como_se_movio(slopes, product_name=product, province_names=PHASE0_PROVINCES)
        if movio is None:
            return None
        return {"card": movio}

    if kind.slug == "de-donde-sale":
        if product is None:
            return None
        if province not in PHASE0_PROVINCES:
            return None
        if province is None:
            return None
        observaciones = compute_retail_observations(
            db, days=LOOKBACK_WINDOW_DAYS, hoy=hoy, hasta=tope
        ).observations
        donde = build_de_donde_sale(observaciones, product_name=product, province_name=province)
        if donde is None:
            return None
        return {"card": donde}

    if kind.slug in ("story-precalentamiento", "portada-facebook"):
        return {"products": products}

    # Las de puro branding (portada, consejo, cómo funciona, cierre) no
    # necesitan nada más que el sitio, que ya se agrega al renderizar.
    return {}
