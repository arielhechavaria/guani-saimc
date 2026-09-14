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
import json
import uuid
from datetime import date, timedelta
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from saimc.cells import compute_retail_observations, compute_retail_ranges
from saimc.models import ContentType, PriceHistory, ProcessingStatus, Province, RawIngestion
from saimc.progress import pilot_product_names
from saimc.web.card_view import attach_trends, build_cards
from saimc.web.deps import get_db
from saimc.web.map_view import build_map
from saimc.web.panel_view import (
    ProductPanel,
    attach_blocks,
    build_product_panels,
    group_blocks_by_province,
)
from saimc.web.price_charts import (
    build_cell_ads,
    build_evolution_blocks,
    build_headlines,
    build_province_comparison,
    build_range_series,
)
from saimc.web.product_nav import (
    build_layer_nav,
    build_product_chips,
    build_province_nav,
    build_view_menu,
    build_window_nav,
    download_href,
    product_slug,
    products_query,
    resolve_layer,
    resolve_product,
    resolve_products,
    resolve_province,
    resolve_views,
    resolve_window,
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
    desde: date,
    hasta: date,
) -> list:
    """Las observaciones minoristas que la vista pública muestra: piloto,
    convertidas a CUP, dentro del rango de calendario y de los filtros
    activos.

    Una sola definición para que la página (tarjetas y gráfico) y la descarga
    CSV no puedan divergir: lo que se baja es exactamente lo que se ve.
    `unconverted` queda afuera igual que en `build_cards` / `build_series`.
    `products` vacío o `None` no filtra: se muestran todos los piloto.

    `desde`/`hasta` (2026-09-14, date picker): se traducen a `days`/`hoy`/
    `hasta` de `compute_retail_observations` tal que `cutoff == desde` —
    `days=(hasta-desde).days, hoy=hasta` reproduce exactamente ese corte.
    """
    report = compute_retail_observations(
        session, days=(hasta - desde).days, hoy=hasta, hasta=hasta
    )
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


def _build_focused_product_panel(
    db: Session,
    *,
    pilot: frozenset[str],
    product_name: str,
    selected_province: str | None,
    desde: date,
    hasta: date,
) -> ProductPanel:
    """Arma el panel de UN producto enfocado: cifra, rango y anuncios por
    provincia (y formato) — lo que usa la vista liviana de `/producto/{slug}`
    (2026-09-14, reporte de mejoras no consideradas, idea #5).

    Repite parte de lo que `_render_home` arma inline para el caso general
    (varios productos a la vez, más `cards`/`series` para el resto de esa
    página: chips, `#precios`, CSV). No se extrajo un helper compartido para
    los dos casos a propósito: `_render_home` ya está probada y en
    producción, y tocarla como efecto secundario de esta vista nueva es el
    tipo de riesgo que no se puede verificar sin un navegador real.
    """
    active_products = [product_name]
    active_set = frozenset(active_products)
    window = (hasta - desde).days
    hoy = hasta

    report = compute_retail_ranges(db, days=window, hoy=hoy, hasta=hasta)
    ranges = [
        r
        for r in report.ranges
        if r.product_name in pilot
        and r.product_name in active_set
        and (selected_province is None or r.province_name == selected_province)
    ]
    cards = build_cards(ranges)

    obs = _visible_observations(
        db, pilot=pilot, products=active_set, province=selected_province, desde=desde, hasta=hasta
    )
    series = build_series(obs, window_days=window, hoy=hoy)
    previous_obs = [
        o
        for o in _visible_observations(
            db,
            pilot=pilot,
            products=active_set,
            province=selected_province,
            desde=desde - timedelta(days=window),
            hasta=hasta,
        )
        if o.day < desde
    ]
    panel_headlines = build_headlines(obs, previous_obs)
    panel_ranges = build_range_series(obs, window_days=window, hoy=hoy)
    panel_ads = build_cell_ads(obs)

    (panel,) = attach_blocks(
        build_product_panels(
            active_products,
            cards,
            series,
            selected_province=selected_province or "",
            selected_desde=desde,
            selected_hasta=hasta,
        ),
        panel_headlines,
        panel_ranges,
        panel_ads,
    )
    return panel


def _render_home(
    request: Request,
    db: Session,
    *,
    producto: str | None,
    productos: str | None,
    provincia: str | None,
    desde: str | None,
    hasta: str | None,
    dias: str | None,
    ver: str | None = None,
    capa: str | None = None,
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

    all_province_names = _all_province_names(db)
    selected_province = resolve_province(all_province_names, provincia)
    ventana_desde, ventana_hasta = resolve_window(desde, hasta, dias)
    window = (ventana_hasta - ventana_desde).days
    hoy = ventana_hasta
    selected_layer = resolve_layer(capa)

    report = compute_retail_ranges(db, days=window, hoy=hoy, hasta=ventana_hasta)
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
        desde=ventana_desde,
        hasta=ventana_hasta,
    )
    series = build_series(obs, window_days=window, hoy=hoy)

    latest_day = _latest_price_day(db, pilot)
    freshness_days_ago = (date.today() - latest_day).days if latest_day else None

    prov = selected_province or ""
    prov_slug = product_slug(selected_province) if selected_province else ""
    active_slugs = [product_slug(name) for name in active_products]

    # Ventanas flotantes abiertas desde el menú del header (`?ver=`). Varias a
    # la vez. Todos los enlaces de la página las preservan: prender un chip,
    # elegir una provincia o cambiar la ventana de días no cierra ninguna.
    open_views = resolve_views(ver)
    view_menu = build_view_menu(
        open_views,
        active_products=active_products,
        selected_province=prov,
        selected_desde=ventana_desde,
        selected_hasta=ventana_hasta,
        capa=selected_layer,
    )
    # «Ver el detalle» de un panel abre la ventana Precios sin cerrar las demás.
    open_precios_query = products_query(
        active_slugs,
        provincia=prov_slug,
        desde=ventana_desde,
        hasta=ventana_hasta,
        ver=open_views if "precios" in open_views else [*open_views, "precios"],
        capa=selected_layer,
    )

    def province_link(slug: str) -> str:
        """Enlace de una provincia del mapa a la vista filtrada por ella,
        preservando los chips activos, el rango de calendario, las ventanas
        flotantes abiertas y la capa del mapa."""
        return products_query(
            active_slugs,
            provincia=slug,
            desde=ventana_desde,
            hasta=ventana_hasta,
            ver=open_views,
            capa=selected_layer,
        )

    # Panel de producto (muestrario aprobado el 2026-09-10): cifra con su
    # muestra, comparación con el período anterior del mismo largo y rango por
    # período. El período anterior sale de la misma normalización y los mismos
    # filtros que la ventana, pidiendo un rango del mismo largo justo antes de
    # `ventana_desde`, así los dos períodos no se pisan.
    previous_obs = [
        o
        for o in _visible_observations(
            db,
            pilot=pilot,
            products=active_set or None,
            province=selected_province,
            desde=ventana_desde - timedelta(days=window),
            hasta=ventana_hasta,
        )
        if o.day < ventana_desde
    ]
    panel_headlines = build_headlines(obs, previous_obs)
    panel_ranges = build_range_series(obs, window_days=window, hoy=hoy)
    # Recorrido hacia atrás hasta el anuncio (§6.5), por celda —no por
    # producto entero, que es lo que ya hacía «Cómo se movió»—: pedido el
    # 2026-09-14 comparando con el enlace «ver anuncios» que ya tiene
    # `/control` sobre cada número de la matriz.
    panel_ads = build_cell_ads(obs)
    # Tarjeta simple (2026-09-14): mismo cambio vs. período anterior que ya
    # muestra el panel flotante, para que `#precios` conteste "¿sube o baja?"
    # sin abrir otra ventana (spec.md §6.7). `cards` se reasigna acá y no se
    # vuelve a construir: todo lo que lo use de ahora en más ya lleva el dato.
    cards = attach_trends(cards, panel_headlines)

    # Comparación entre provincias (Precios y la hoja de detalle): SIEMPRE con
    # las 16, aunque haya una elegida —la pregunta es «¿dónde está más caro?»,
    # y la hoja resalta la propia contra todas las demás—. Todos los piloto;
    # la plantilla elige qué productos mostrar.
    all_provinces_obs = _visible_observations(
        db, pilot=pilot, products=None, province=None, desde=ventana_desde, hasta=ventana_hasta
    )
    all_provinces_previous = [
        o
        for o in _visible_observations(
            db,
            pilot=pilot,
            products=None,
            province=None,
            desde=ventana_desde - timedelta(days=window),
            hasta=ventana_hasta,
        )
        if o.day < ventana_desde
    ]

    # «Cómo se movió»: con una provincia elegida, SOLO esa —el operador la
    # marcó como el hueco del zoom (2026-09-11): elegir una provincia dice
    # «solo me interesa esta», y esta ventana seguía mostrando las 16 como si
    # nada, la única que no se enfocaba (Precios y los paneles ya lo hacían).
    # Se filtra en Python sobre lo ya traído arriba: misma consulta, sin
    # pedirle a la base los mismos datos dos veces.
    if selected_province is not None:
        evolution_obs = [
            o for o in all_provinces_obs if o.province_name == selected_province
        ]
        evolution_previous = [
            o for o in all_provinces_previous if o.province_name == selected_province
        ]
    else:
        evolution_obs = all_provinces_obs
        evolution_previous = all_provinces_previous

    csv_href = download_href(
        producto=active_slugs[0] if len(active_slugs) == 1 else "",
        productos=active_slugs if len(active_slugs) > 1 else (),
        provincia=product_slug(selected_province) if selected_province else "",
        desde=ventana_desde,
        hasta=ventana_hasta,
    )
    map_view = build_map(all_province_names, _rows_by_province(db), _published_provinces(db))
    # El mapa interactivo (Leaflet, 2026-09-12) no recorre `map.provinces` en
    # Jinja como el SVG de antes: el JS del pie lee este JSON para colorear
    # cada polígono de `cuba_provincias.geojson` por `slug` y armar el clic a
    # `province_link`. `separators` compacto porque viaja en el HTML de cada
    # carga, no para leerlo a mano.
    # Insignia del mapa (2026-09-13): un panel por producto activo con dato,
    # agrupado por provincia. Se calcula acá y no `selected_province` porque
    # tiene que verse también mirando el país entero, no solo con una
    # provincia elegida (esa es la parte que el operador reportó como "no se
    # ve nada" — solo se armaba con `selected_province`).
    product_panels = attach_blocks(
        build_product_panels(
            active_products,
            cards,
            series,
            selected_province=prov,
            selected_desde=ventana_desde,
            selected_hasta=ventana_hasta,
            ver=open_views,
            capa=selected_layer,
        ),
        panel_headlines,
        panel_ranges,
        panel_ads,
    )
    map_badges_by_province = group_blocks_by_province(
        [p for p in product_panels if p.has_data]
    )

    map_provinces_json = json.dumps(
        [
            {
                "slug": province.slug,
                "state": province.state,
                "label": province.label,
                "rows": province.rows,
                "density_ratio": round(province.density_ratio, 3),
                "query": province_link(province.slug),
            }
            for province in map_view.provinces
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return request.app.state.templates.TemplateResponse(
        request,
        "public_home.html",
        {
            "map": map_view,
            "map_provinces_json": map_provinces_json,
            "map_badges_by_province": map_badges_by_province,
            "cards": cards,
            "series": series,
            "window_days": window,
            "selected_desde": ventana_desde,
            "selected_hasta": ventana_hasta,
            "csv_href": csv_href,
            "data_freshness_day": latest_day,
            "data_freshness_days_ago": freshness_days_ago,
            "product_chips": build_product_chips(
                pilot_names,
                active_products,
                selected_province=prov,
                selected_desde=ventana_desde,
                selected_hasta=ventana_hasta,
                ver=open_views,
                capa=selected_layer,
            ),
            "product_panels": product_panels,
            "province_nav": build_province_nav(
                province_names,
                selected_province,
                selected_product=selected or "",
                selected_desde=ventana_desde,
                selected_hasta=ventana_hasta,
                active_products=active_products,
                ver=open_views,
                capa=selected_layer,
            ),
            "window_nav": build_window_nav(
                ventana_desde,
                ventana_hasta,
                selected,
                selected_province=prov,
                active_products=active_products,
                ver=open_views,
                capa=selected_layer,
                hoy=hoy,
            ),
            "layer_nav": build_layer_nav(
                selected_layer,
                active_products=active_products,
                selected_province=prov,
                selected_desde=ventana_desde,
                selected_hasta=ventana_hasta,
                ver=open_views,
            ),
            "selected_layer": selected_layer,
            "province_comparisons": build_province_comparison(all_provinces_obs),
            "evolution_blocks": build_evolution_blocks(
                evolution_obs, evolution_previous, window_days=window, hoy=hoy
            ),
            "view_menu": view_menu,
            "open_views": open_views,
            "open_precios_query": open_precios_query,
            "province_link": province_link,
            "active_products": active_products,
            "selected_product": selected,
            "selected_province_slug": prov_slug,
            "selected_province": selected_province,
        },
    )


@router.get("/", response_class=HTMLResponse)
def public_home(
    request: Request,
    producto: str | None = None,
    productos: str | None = None,
    provincia: str | None = None,
    desde: str | None = None,
    hasta: str | None = None,
    dias: str | None = None,
    ver: str | None = None,
    capa: str | None = None,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Home pública. `productos` (multi-selección de chips), `producto` (alias de
    uno), `provincia`, `desde`/`hasta` (rango de calendario del date picker,
    2026-09-14; `dias` sigue aceptándose como alias de compatibilidad —ver
    `resolve_window`—), `ver` (ventanas flotantes abiertas) y `capa`
    (capa del mapa) son query string opcional; un valor raro no da 404, se
    muestra todo."""
    return _render_home(
        request,
        db,
        producto=producto,
        productos=productos,
        provincia=provincia,
        desde=desde,
        hasta=hasta,
        dias=dias,
        ver=ver,
        capa=capa,
    )


@router.get("/producto/{slug}", response_class=HTMLResponse)
def public_product(
    slug: str,
    request: Request,
    provincia: str | None = None,
    desde: str | None = None,
    hasta: str | None = None,
    dias: str | None = None,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """URL dedicada por producto — la que queda en la barra de direcciones
    tras elegir un chip sin otro filtro activo (`_product_link`,
    `product_nav.py`) y la pensada para compartir.

    Vista liviana a propósito (2026-09-14, reporte de mejoras no
    consideradas, idea #5): sin mapa, sin Leaflet, sin compás ni dock —
    reemplaza el mapa de 147 KB + tiles en vivo por un enlace hacia él, para
    quien lo quiera. `/?producto=<slug>` sigue siendo la vista completa e
    interactiva; dejó de ser lo mismo que esta ruta (docs/decisions.md,
    entrada que reemplaza a la del mismo día que la registraba como
    candidato sin implementar).

    `?provincia=` y `?desde=`/`?hasta=` (o `?dias=`, alias de compatibilidad)
    siguen aceptándose para acotar el panel; `?ver=` y `?capa=` ya no
    significan nada acá —no hay ventanas ni capas de mapa en esta vista— y
    un enlace viejo con esos parámetros los ignora en vez de romperse.

    Un slug desconocido da 404, a diferencia de `?producto=` raro que muestra
    todo: en una ruta con nombre de recurso, «no existe» es la respuesta
    correcta.
    """
    pilot_names = pilot_product_names(db)
    product_name = resolve_product(pilot_names, slug)
    if product_name is None:
        raise HTTPException(
            status_code=404, detail="No hay producto público con ese nombre"
        )

    pilot = frozenset(pilot_names)
    all_province_names = _all_province_names(db)
    selected_province = resolve_province(all_province_names, provincia)
    ventana_desde, ventana_hasta = resolve_window(desde, hasta, dias)

    panel = _build_focused_product_panel(
        db,
        pilot=pilot,
        product_name=product_name,
        selected_province=selected_province,
        desde=ventana_desde,
        hasta=ventana_hasta,
    )
    latest_day = _latest_price_day(db, pilot)
    freshness_days_ago = (date.today() - latest_day).days if latest_day else None

    # `cast` y no dejar que mypy infiera `Any`: es la ÚNICA ruta pública que
    # arma su propio `TemplateResponse` en vez de delegar en `_render_home`
    # (que ya suma esta misma imprecisión de tipos a la deuda aceptada del
    # proyecto, junto con `public_evidence`) — sumar una tercera instancia
    # sin marcarla explícitamente sería deuda nueva y silenciosa.
    return cast(
        HTMLResponse,
        request.app.state.templates.TemplateResponse(
            request,
            "public_product_light.html",
            {
                "panel": panel,
                "window_days": (ventana_hasta - ventana_desde).days,
                "selected_desde": ventana_desde,
                "selected_hasta": ventana_hasta,
                "data_freshness_day": latest_day,
                "data_freshness_days_ago": freshness_days_ago,
                "selected_province": selected_province,
                "selected_province_slug": product_slug(selected_province) if selected_province else "",
                "map_href": products_query(
                    [slug],
                    provincia=product_slug(selected_province) if selected_province else "",
                    desde=ventana_desde,
                    hasta=ventana_hasta,
                ),
            },
        ),
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
    desde: str | None = None,
    hasta: str | None = None,
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
    ventana_desde, ventana_hasta = resolve_window(desde, hasta, dias)

    rows = sorted(
        _visible_observations(
            db,
            pilot=pilot,
            products=frozenset(active) or None,
            province=selected_province,
            desde=ventana_desde,
            hasta=ventana_hasta,
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
