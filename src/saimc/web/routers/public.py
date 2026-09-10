"""Vista pública. La primera ruta del proyecto que no es del operador.

`spec.md` §9 pone «Visualización: Prototipo de mapa de calor simple y tarjetas
de producto» dentro del alcance de la **Fase 0** (`decisions.md`, 2026-08-22).
Hoy `GET /` tiene: mapa de cobertura por provincia (§9.0), tarjetas de rango
minorista con muestra visible, gráfico de evolución, y filtros por producto,
provincia y ventana de días vía query string (`?producto=`, `?provincia=`,
`?dias=`, 2026-08-26). El coloreado del mapa por precio y los semáforos siguen
bloqueados por el umbral sin calibrar (§12.6).

Sin `require_operator` a propósito: es la capa pública. Y sin identificador de
usuario ni dato de contacto en ninguna respuesta — `CLAUDE.md` fija que el
identificador de usuario origen nunca se expone acá.
"""

import csv
import io
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from datetime import date

from saimc.cells import compute_retail_observations, compute_retail_ranges
from saimc.models import ContentType, PriceHistory, ProcessingStatus, Province, RawIngestion
from saimc.progress import pilot_product_names
from saimc.web.card_view import build_cards
from saimc.web.deps import get_db
from saimc.web.map_view import build_map
from saimc.web.panel_view import build_product_panels
from saimc.web.product_nav import (
    build_product_chips,
    build_province_nav,
    build_window_nav,
    download_href,
    product_slug,
    resolve_days,
    resolve_product,
    resolve_products,
    resolve_province,
)
from saimc.web.series_view import build_series

router = APIRouter()

# La ventana de tarjetas y series la elige el visitante con `?dias=` entre
# 7/30/90 (`product_nav.WINDOW_CHOICES`); el default y su motivo viven en
# `product_nav.DEFAULT_WINDOW_DAYS`.


def _rows_by_province(session: Session) -> dict[str, int]:
    """Observaciones por provincia, sobre todo el histórico.

    Sin ventana temporal: con 225 filas en dos meses, recortar a los últimos N
    días dejaría el mapa casi vacío y no diría nada más honesto. La ventana se
    vuelve una pregunta real cuando exista volumen que la justifique, y ahí hay
    que elegir el N con una medición, no de memoria.
    """
    return {
        name: count
        for name, count in session.execute(
            select(Province.name, func.count())
            .join(PriceHistory, PriceHistory.province_id == Province.id)
            .group_by(Province.name)
        )
    }


def _all_province_names(session: Session) -> list[str]:
    """Los 16 nombres de provincia, para resolver `?provincia=` contra un
    conjunto estable: una provincia elegida que hoy no tiene dato piloto sigue
    resolviéndose y muestra su aviso, en vez de ignorarse en silencio."""
    return list(session.execute(select(Province.name)).scalars())


def _published_provinces(session: Session) -> frozenset[str]:
    """Las provincias que alguien decidió publicar (`spec.md` §9.0).

    Sale de la base y no de una constante: §9.0 fija como restricción vinculante
    que «la incorporación de una provincia debe ser configuración, no
    desarrollo». Un `frozenset` de nombres en código obligaría a un deploy para
    publicar una provincia, que es exactamente lo que la sección prohíbe.
    """
    return frozenset(
        session.execute(
            select(Province.name).where(Province.is_published.is_(True))
        ).scalars()
    )


# Ventana efectivamente sin límite para la señal de frescura: no interesa un
# corte temporal, sino el día del anuncio piloto más reciente de toda la serie.
_FRESHNESS_WINDOW_DAYS = 36500


def _latest_price_day(session: Session, pilot: frozenset[str]) -> date | None:
    """El día del anuncio piloto más reciente que la vista podría mostrar,
    sin ventana. Señal de antigüedad, no veredicto (§12.6): informa hasta
    cuándo llega el dato, no marca nada como «atrasado»."""
    report = compute_retail_observations(session, days=_FRESHNESS_WINDOW_DAYS)
    days = [
        o.day
        for o in report.observations
        if o.product_name in pilot and not o.unconverted
    ]
    return max(days) if days else None


def _visible_observations(
    session: Session,
    *,
    pilot: frozenset[str],
    products: frozenset[str] | None,
    province: str | None,
    window: int,
) -> list:
    """Las observaciones minoristas que la vista pública muestra: piloto,
    convertidas a CUP, dentro de la ventana y de los filtros activos.

    Una sola definición para que la página (tarjetas y gráfico) y la descarga
    CSV no puedan divergir: lo que se baja es exactamente lo que se ve.
    `unconverted` queda afuera igual que en `build_cards` / `build_series`.
    `products` vacío o `None` no filtra: se muestran todos los piloto.
    """
    report = compute_retail_observations(session, days=window)
    obs = [
        o
        for o in report.observations
        if o.product_name in pilot and not o.unconverted
    ]
    if products:
        obs = [o for o in obs if o.product_name in products]
    if province is not None:
        obs = [o for o in obs if o.province_name == province]
    return obs


def _render_home(
    request: Request,
    db: Session,
    *,
    producto: str | None,
    productos: str | None,
    provincia: str | None,
    dias: str | None,
) -> HTMLResponse:
    # Solo productos bandera, mismo criterio que `/control`: son los 5 que la
    # Fase 0 se comprometió a medir (§9), y la cola larga son 59 filas en 53
    # productos que no sostienen ninguna afirmación.
    pilot_names = pilot_product_names(db)
    pilot = frozenset(pilot_names)

    # `productos`, `provincia` y `dias` son query string; cada uno resuelve a un
    # valor válido o al default (query raro → se muestra todo con la ventana por
    # defecto, sin 404). Los filtros son una refinación de la vista, no exponen
    # nada nuevo: siguen dentro de «tarjetas de producto» del alcance §9.
    #
    # `?productos=aceite,arroz` es la multi-selección de los chips (slice 2);
    # `?producto=<slug>` y la ruta limpia `/producto/<slug>` siguen andando como
    # alias de un solo producto. `selected` (uno) gobierna la vista enfocada de
    # `#precios` —la tabla comparativa por provincia—, que solo tiene sentido
    # con exactamente un producto.
    active_products = resolve_products(pilot_names, productos)
    if not active_products:
        one = resolve_product(pilot_names, producto)
        if one is not None:
            active_products = [one]
    active_set = frozenset(active_products)
    selected = active_products[0] if len(active_products) == 1 else None

    selected_province = resolve_province(_all_province_names(db), provincia)
    window = resolve_days(dias)

    report = compute_retail_ranges(db, days=window)
    pilot_ranges = [r for r in report.ranges if r.product_name in pilot]

    # La barra de provincia lista las que tienen dato piloto en esta ventana; si
    # la elegida quedó afuera (sin dato en la ventana), se agrega igual para que
    # su chip siga visible y se pueda cambiar.
    province_names = sorted({r.province_name for r in pilot_ranges if r.province_name})
    if selected_province and selected_province not in province_names:
        province_names = sorted({*province_names, selected_province})

    ranges = pilot_ranges
    if active_set:
        ranges = [r for r in ranges if r.product_name in active_set]
    if selected_province is not None:
        ranges = [r for r in ranges if r.province_name == selected_province]
    cards = build_cards(ranges)

    # Misma ventana que las tarjetas a propósito: el gráfico tiene que describir
    # exactamente la muestra que la tarjeta resume, no otra. Dos ventanas
    # distintas en la misma página harían que el rango del titular no coincida
    # con lo que el usuario ve dibujado.
    obs = _visible_observations(
        db,
        pilot=pilot,
        products=active_set or None,
        province=selected_province,
        window=window,
    )
    series = build_series(obs, window_days=window, hoy=date.today())

    latest_day = _latest_price_day(db, pilot)
    freshness_days_ago = (date.today() - latest_day).days if latest_day else None

    prov = selected_province or ""
    active_slugs = [product_slug(name) for name in active_products]
    csv_href = download_href(
        producto=active_slugs[0] if len(active_slugs) == 1 else "",
        productos=active_slugs if len(active_slugs) > 1 else (),
        provincia=product_slug(selected_province) if selected_province else "",
        dias=window,
    )
    return request.app.state.templates.TemplateResponse(
        request,
        "public_home.html",
        {
            "map": build_map(_rows_by_province(db), _published_provinces(db)),
            "cards": cards,
            "series": series,
            "window_days": window,
            "csv_href": csv_href,
            "data_freshness_day": latest_day,
            "data_freshness_days_ago": freshness_days_ago,
            "product_chips": build_product_chips(
                pilot_names,
                active_products,
                selected_province=prov,
                selected_days=window,
            ),
            "product_panels": build_product_panels(
                active_products,
                cards,
                series,
                selected_province=prov,
                selected_days=window,
            ),
            "province_nav": build_province_nav(
                province_names,
                selected_province,
                selected_product=selected or "",
                selected_days=window,
            ),
            "window_nav": build_window_nav(
                window, selected, selected_province=prov
            ),
            "active_products": active_products,
            "selected_product": selected,
            "selected_province": selected_province,
        },
    )


@router.get("/", response_class=HTMLResponse)
def public_home(
    request: Request,
    producto: str | None = None,
    productos: str | None = None,
    provincia: str | None = None,
    dias: str | None = None,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Home pública. `productos` (multi-selección de chips), `producto` (alias de
    uno), `provincia` y `dias` son query string opcional; un valor raro no da
    404, se muestra todo."""
    return _render_home(
        request, db, producto=producto, productos=productos, provincia=provincia, dias=dias
    )


@router.get("/producto/{slug}", response_class=HTMLResponse)
def public_product(
    slug: str,
    request: Request,
    provincia: str | None = None,
    dias: str | None = None,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """URL dedicada por producto. Misma vista que `/?producto=<slug>`, que sigue
    siendo alias válido; `?provincia=` y `?dias=` también se aceptan acá.

    Un slug desconocido da 404, a diferencia de `?producto=` raro que muestra
    todo: en una ruta con nombre de recurso, «no existe» es la respuesta
    correcta.
    """
    if resolve_product(pilot_product_names(db), slug) is None:
        raise HTTPException(
            status_code=404, detail="No hay producto público con ese nombre"
        )
    return _render_home(
        request, db, producto=slug, productos=None, provincia=provincia, dias=dias
    )


#: Columnas del CSV. Solo lo que la página ya muestra: nada de `channel_id`,
#: `source_reference` ni identificador de origen — `CLAUDE.md` fija que no se
#: exponen en la capa pública. `anuncio` es el id de `raw_ingestions`, que ya
#: viaja en el HTML como enlace a `/anuncio/{id}`.
_CSV_COLUMNS = (
    "producto",
    "provincia",
    "dia",
    "precio_por_unidad",
    "moneda",
    "unidad",
    "anuncio",
)


@router.get("/datos.csv")
def public_data_csv(
    producto: str | None = None,
    productos: str | None = None,
    provincia: str | None = None,
    dias: str | None = None,
    db: Session = Depends(get_db),
) -> Response:
    """La evidencia detrás de la vista pública, en CSV, con los mismos tres
    filtros que `GET /`.

    Apartamiento anotado de §9 (`docs/decisions.md`, 2026-08-26): la sección
    acota el alcance a «mapa de calor simple y tarjetas de producto», y un
    endpoint de descarga no es eso. Se hace igual porque la procedencia del
    dato es el diferencial del proyecto (§6.5) y esto **no expone nada nuevo**:
    es el mismo universo de `_visible_observations` que alimenta la página, en
    otro formato. Sin `require_operator`, sin dato personal.
    """
    pilot_names = pilot_product_names(db)
    pilot = frozenset(pilot_names)
    active = resolve_products(pilot_names, productos)
    if not active:
        one = resolve_product(pilot_names, producto)
        if one is not None:
            active = [one]
    selected_province = resolve_province(_all_province_names(db), provincia)
    window = resolve_days(dias)

    rows = sorted(
        _visible_observations(
            db,
            pilot=pilot,
            products=frozenset(active) or None,
            province=selected_province,
            window=window,
        ),
        key=lambda o: (o.product_name, o.province_name or "", o.day),
    )

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(_CSV_COLUMNS)
    for o in rows:
        writer.writerow(
            (
                o.product_name,
                o.province_name or "",
                o.day.isoformat(),
                f"{o.unit_price:f}",
                o.currency,
                o.unit,
                str(o.evidence_id),
            )
        )

    return Response(
        content=buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="saimc-precios.csv"'},
    )


@router.get("/anuncio/{ingestion_id}", response_class=HTMLResponse)
def public_evidence(
    ingestion_id: uuid.UUID, request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    """Transparency Layer (§10.6, mitigación 2): «el anuncio y su contenido
    económico, no la identidad del anunciante».

    Solo texto. Una captura de imagen trae nombre del remitente y a veces
    teléfono en los propios píxeles (docs/spec-map.md, contradicción 1); el
    texto pegado no —medido contra las 143 filas curadas de texto en
    producción, 2026-08-25: ninguna contiene un patrón de teléfono— y ya no
    hay forma de redactar una imagen sin OCR/ML, fuera de alcance en Fase 0
    (CLAUDE.md, decisión 2). `channel_id`/`source_reference` nunca se leen acá:
    el identificador de origen no se expone en la capa pública (CLAUDE.md).

    404 idéntico si no existe, si es imagen o si nunca se curó: distinguir
    "existe pero es imagen" de "no existe" filtraría información por el
    camino que la mitigación 2 quiere cerrado.
    """
    ingestion = db.get(RawIngestion, ingestion_id)
    if (
        ingestion is None
        or ingestion.content_type != ContentType.TEXT
        or ingestion.processing_status != ProcessingStatus.CURATED
    ):
        raise HTTPException(status_code=404, detail="No hay anuncio público con ese id")

    return request.app.state.templates.TemplateResponse(
        request,
        "public_evidence.html",
        {
            "original_text": ingestion.original_text,
            "captured_day": ingestion.captured_at.date(),
        },
    )
