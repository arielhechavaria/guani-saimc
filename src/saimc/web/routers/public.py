"""Vista pública. La primera ruta del proyecto que no es del operador.

`spec.md` §9 pone «Visualización: Prototipo de mapa de calor simple y tarjetas
de producto» dentro del alcance de la **Fase 0** (`decisions.md`, 2026-08-22).
Hoy `GET /` tiene: mapa de cobertura por provincia (§9.0), tarjetas de rango
minorista con muestra visible, gráfico de evolución, y filtros por producto,
provincia y ventana de días vía query string (`?producto=`, `?provincia=`,
`?dias=`, 2026-08-26). El coloreado del **mapa** por precio y los **semáforos**
siguen bloqueados por el umbral sin calibrar (§12.6): decir si un precio
«está bien» exige una banda que nadie midió. Lo que sí se habilitó el
2026-09-17 fue la tira del tablero; desde el 2026-09-19 esa lectura —cuál
provincia es la más barata, con la cifra y la muestra escritas— la hace la
lista de productos (`watchlist_view`): una etiqueta de posición relativa, sin
bandas, sin veredicto, que no fija ningún umbral.

Sin `require_operator` a propósito: es la capa pública. Y sin identificador de
usuario ni dato de contacto en ninguna respuesta — `CLAUDE.md` fija que el
identificador de usuario origen nunca se expone acá.
"""

import csv
import io
import json
import uuid
from collections.abc import Sequence
from datetime import date, timedelta
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from saimc.cells import compute_retail_observations, compute_retail_ranges
from saimc.models import ContentType, PriceHistory, ProcessingStatus, Province, RawIngestion
from saimc.progress import pilot_product_names
from saimc.web.card_view import (
    attach_trends,
    build_cards,
    group_cards_by_province,
    province_color_slots,
)
from saimc.web.coverage_view import build_coverage
from saimc.web.deps import get_db
from saimc.web.explore_view import (
    WEEKS,
    build_explore_detail,
    build_explore_map,
    build_explore_nav,
    build_explore_products,
)
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
    build_median_steps,
    build_month_vs_month,
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
    effective_layer,
    product_slug,
    products_query,
    resolve_layer,
    resolve_product,
    resolve_products,
    resolve_province,
    resolve_views,
    resolve_vista,
    resolve_window,
)
from saimc.web.province_grid import build_product_grids, group_detail, price_layer
from saimc.web.series_view import build_series
from saimc.web.ticker_view import build_ticker
from saimc.web.watchlist_view import build_watchlist

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
    vista: str | None = None,
    pestana: str | None = None,
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
    selected_vista = resolve_vista(vista)
    selected_layer = resolve_layer(capa)
    # `selected_layer` es la que viaja en los enlaces; `map_layer` la que el
    # mapa dibuja: «precio» sin ningún producto abierto no tiene qué pintar.
    map_layer = effective_layer(selected_layer, active_products)
    if selected_vista == "explorar":
        return _render_explore(
            request,
            db,
            pilot=pilot,
            pilot_names=pilot_names,
            active_products=active_products,
            selected_province=selected_province,
            province_names=all_province_names,
            desde=ventana_desde,
            hasta=ventana_hasta,
            pestana=pestana,
        )
    # En el tablero (prototipo E, 2026-09-19) la provincia elegida enfoca solo
    # la lista de «Todos»; el detalle de un producto compara todas las
    # provincias, que es para lo que sirve. El mapa sí filtra por ella.
    scope_province = None if selected_vista == "tablero" else selected_province

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
    if scope_province is not None:
        ranges = [r for r in ranges if r.province_name == scope_province]
    cards = build_cards(ranges)

    # Misma ventana que las tarjetas a propósito: el gráfico tiene que describir
    # exactamente la muestra que la tarjeta resume, no otra. Dos ventanas
    # distintas en la misma página harían que el rango del titular no coincida
    # con lo que el usuario ve dibujado.
    obs = _visible_observations(
        db,
        pilot=pilot,
        products=active_set or None,
        province=scope_province,
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

    def vista_link(destino: str) -> str:
        """Enlace al mapa o al tablero, preservando producto(s), provincia y
        rango de calendario — no `ver`/`capa`, que son estado del mapa y no
        significan nada en el tablero (2026-09-16)."""
        return products_query(
            active_slugs,
            provincia=prov_slug,
            desde=ventana_desde,
            hasta=ventana_hasta,
            vista=destino,
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
            province=scope_province,
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
    # «¿Subió o bajó?» de todos los productos en todas las provincias: la
    # única fuente del cambio que se muestra —en las tarjetas, en la lista de
    # productos del tablero y en «Cómo se movió»—, para que el mismo producto
    # en la misma provincia no diga dos números distintos en la misma
    # pantalla.
    slopes = build_month_vs_month(
        all_provinces_obs, all_provinces_previous, window_days=window, hoy=hoy
    )
    # Tarjeta simple (2026-09-14): cambio vs. período anterior, para que
    # `#precios` conteste "¿sube o baja?" sin abrir otra ventana (spec.md
    # §6.7). `cards` se reasigna acá y no se vuelve a construir: todo lo que
    # lo use de ahora en más ya lleva el dato. Hasta el 2026-09-18 salía de
    # `panel_headlines` (uno por formato) y el último pisaba a los demás.
    cards = attach_trends(cards, slopes)

    # Ventana con pestañas (prototipo B, 2026-09-21): una tabla por producto
    # activo, con las 16 provincias. La mediana y el cambio salen de los mismos
    # `slopes` de arriba y la sparkline de las medianas por período de «Cómo se
    # movió»: ninguna cifra nueva, para que la tabla y los gráficos no se
    # contradigan. Siempre las 16, aunque haya una provincia elegida: el mapa
    # se tiñe comparando entre todas.
    steps = build_median_steps(all_provinces_obs, window_days=window, hoy=hoy)
    # Lo que trae cada fila al expandirse (cifra por formato, rango por período
    # y anuncios detrás): de TODAS las provincias, aunque haya una elegida —el
    # mapa se enfoca en ella, la tabla no se vacía—. Sin provincia elegida ya
    # está calculado arriba para el panel del mapa; con una, se recalcula.
    if scope_province is None:
        detail_headlines, detail_ranges, detail_ads = panel_headlines, panel_ranges, panel_ads
    else:
        detail_obs = [o for o in all_provinces_obs if o.product_name in active_set]
        detail_previous = [o for o in all_provinces_previous if o.product_name in active_set]
        detail_headlines = build_headlines(detail_obs, detail_previous)
        detail_ranges = build_range_series(detail_obs, window_days=window, hoy=hoy)
        detail_ads = build_cell_ads(detail_obs)
    product_grids = build_product_grids(
        active_products,
        slopes,
        steps,
        all_province_names,
        details=group_detail(detail_headlines, detail_ranges, detail_ads),
    )
    price_by_province = price_layer(product_grids)

    # «Cómo se movió»: con una provincia elegida, SOLO esa —el operador la
    # marcó como el hueco del zoom (2026-09-11): elegir una provincia dice
    # «solo me interesa esta», y esta ventana seguía mostrando las 16 como si
    # nada, la única que no se enfocaba (Precios y los paneles ya lo hacían).
    # Se filtra en Python sobre lo ya traído arriba: misma consulta, sin
    # pedirle a la base los mismos datos dos veces.
    if scope_province is not None:
        evolution_obs = [o for o in all_provinces_obs if o.province_name == scope_province]
        evolution_previous = [
            o for o in all_provinces_previous if o.province_name == scope_province
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
                "precio": price_by_province.get(province.slug, {}),
                "query": province_link(province.slug),
            }
            for province in map_view.provinces
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    # Tablero (2026-09-16): mismas tarjetas de siempre, agrupadas por
    # provincia — nada que la vista mapa no calculara ya. La plantilla la
    # elige el router y no un `{% if %}` adentro de `public_home.html`
    # (~2700 líneas ya delicadas): las dos vistas son archivos separados.
    cards_by_province = group_cards_by_province(cards, province_names)
    # Color por provincia en la tabla del tablero (2026-09-18, arreglo: el
    # operador reportó que todas las filas se veían del mismo color). Era un
    # bug real, no una percepción: hasta acá el slot salía de
    # `sorted(all_province_names)` —el catálogo completo de 16, alfabético—
    # para que el color de una provincia no cambiara de una carga a otra
    # (slice 4, Dirección D, 2026-09-16). Pero con datos reales solo en dos
    # provincias hoy (`La Habana` índice 8, `Santiago de Cuba` índice 14 en
    # ese orden), las dos caen fuera de los tres slots con nombre propio y
    # las dos quedan en el mismo neutral — la promesa de estabilidad no vale
    # nada si nunca hay más de un color visible. Se calcula ahora sobre
    # `province_names` (las que tienen dato EN ESTA vista, ya alfabético):
    # dos provincias con dato son slot 0 y 1, con colores distintos de
    # verdad. Se pierde la estabilidad entre cargas a cambio de que el color
    # sirva para algo hoy; se vuelve a evaluar cuando haya más de tres
    # provincias reales.
    province_slots = province_color_slots(province_names)
    # Lista de productos del tablero (dirección C, 2026-09-18): mediana y
    # cambio por provincia, sacados del mismo «¿subió o bajó?» que dibuja
    # «Cómo se movió» —`build_month_vs_month` sobre las 16 provincias, sin
    # filtro de producto: la lista tiene que mostrarlos a todos para poder
    # elegir—. Solo en el tablero; el mapa no la usa.
    # Las 13 semanas hasta `ventana_hasta` alimentan la sparkline de cada
    # celda (2026-09-26). Una consulta más, solo en el tablero.
    weekly_obs = (
        _visible_observations(
            db,
            pilot=pilot,
            products=None,
            province=None,
            desde=ventana_hasta - timedelta(days=WEEKS * 7),
            hasta=ventana_hasta,
        )
        if selected_vista == "tablero"
        else []
    )
    watchlist = (
        build_watchlist(
            pilot_names,
            active_products,
            slopes,
            provinces=province_names,
            selected_province=prov_slug,
            desde=ventana_desde,
            hasta=ventana_hasta,
            weekly_obs=weekly_obs,
        )
        if selected_vista == "tablero"
        else None
    )
    # Cintillo de precios (2026-09-26): de las mismas `slopes` que la lista, con
    # cada enlace hacia la vista en la que se está.
    ticker = build_ticker(
        pilot_names,
        slopes,
        href=lambda slug: products_query(
            [slug],
            provincia=prov_slug,
            desde=ventana_desde,
            hasta=ventana_hasta,
            vista=selected_vista,
        ),
    )
    template_name = "public_dashboard.html" if selected_vista == "tablero" else "public_home.html"
    return request.app.state.templates.TemplateResponse(
        request,
        template_name,
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
                vista=selected_vista,
                # El tablero no tiene panel flotante con su propia × — el
                # chip tiene que poder apagarse solo (product_nav.py).
                toggle=selected_vista == "tablero",
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
                vista=selected_vista,
            ),
            "window_nav": build_window_nav(
                ventana_desde,
                ventana_hasta,
                selected,
                selected_province=prov,
                active_products=active_products,
                ver=open_views,
                capa=selected_layer,
                vista=selected_vista,
                hoy=hoy,
            ),
            "layer_nav": build_layer_nav(
                map_layer,
                active_products=active_products,
                selected_province=prov,
                selected_desde=ventana_desde,
                selected_hasta=ventana_hasta,
                ver=open_views,
            ),
            "selected_layer": map_layer,
            "product_grids": product_grids,
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
            "vista": selected_vista,
            "ticker": ticker,
            "coverage": build_coverage(map_view.provinces, province_slots),
            "cards_by_province": cards_by_province,
            "province_slots": province_slots,
            # Recorrido hacia atrás hasta el anuncio (§6.5), por celda —mismo
            # `panel_ads`/`build_cell_ads` que ya arma «Cómo se movió» y el
            # panel flotante de producto, misma clave de 5 (2026-09-17,
            # pedido del operador: la tarjeta del tablero no tenía este
            # enlace y el panel del mapa sí).
            "card_ads": panel_ads,
            "mapa_href": vista_link("mapa"),
            "tablero_href": vista_link("tablero"),
            "explorar_href": vista_link("explorar"),
            "watchlist": watchlist,
        },
    )


def _render_explore(
    request: Request,
    db: Session,
    *,
    pilot: frozenset[str],
    pilot_names: Sequence[str],
    active_products: list[str],
    selected_province: str | None,
    province_names: list[str],
    desde: date,
    hasta: date,
    pestana: str | None,
) -> HTMLResponse:
    """Vista «Explorar» (P4, 2026-09-25: pestañas con orden estable — ver
    `docs/decisions.md`). Panel izquierdo con los productos, pestañas por
    producto abierto con su tabla en el centro y el detalle de la provincia
    elegida a la derecha. Todo sale de `explore_view`: acá solo se traen las
    observaciones y se pasan las estructuras a la plantilla.

    Las observaciones cubren lo más largo de tres cosas —la ventana, el período
    anterior del mismo largo y las 13 semanas de la tendencia— y son de TODAS
    las provincias y de todos los piloto: la lista de la izquierda necesita la
    muestra de cada producto aunque no esté abierto.

    `pestana` es el slug del producto que se muestra (`?pestana=`); si no
    resuelve contra `active_products` (vacío, viejo o manipulado a mano) cae
    al primero de la lista, igual que antes de P4.
    """
    window = (hasta - desde).days
    cubre = max(2 * window, WEEKS * 7)
    obs = _visible_observations(
        db,
        pilot=pilot,
        products=None,
        province=None,
        desde=hasta - timedelta(days=cubre),
        hasta=hasta,
    )
    productos = build_explore_products(
        pilot_names, obs, window_days=window, hoy=hasta, provinces=province_names
    )
    por_nombre = {p.name: p for p in productos}
    activo_nombre = resolve_province(active_products, pestana) or (
        active_products[0] if active_products else None
    )
    activo = por_nombre[activo_nombre] if activo_nombre else None
    active_slug = product_slug(activo_nombre) if activo_nombre else ""
    prov_slug = product_slug(selected_province) if selected_province else ""
    active_slugs = [product_slug(name) for name in active_products]

    elegido = None
    if activo is not None and activo.nodes:
        elegido = next(
            (n for n in activo.nodes if selected_province and n.name == selected_province),
            max(activo.nodes, key=lambda n: n.count),
        )

    def vista_link(destino: str) -> str:
        return products_query(
            active_slugs,
            pestana=active_slug,
            provincia=prov_slug,
            desde=desde,
            hasta=hasta,
            vista=destino,
        )

    def province_link(slug: str) -> str:
        return products_query(
            active_slugs, pestana=active_slug, provincia=slug, desde=desde, hasta=hasta, vista="explorar"
        )

    csv_href = download_href(
        producto=active_slugs[0] if len(active_slugs) == 1 else "",
        productos=active_slugs if len(active_slugs) > 1 else (),
        provincia=prov_slug,
        desde=desde,
        hasta=hasta,
    )
    # Cintillo de precios (2026-09-26): mismas ventanas que el tablero, cortadas de
    # la muestra que ya se trajo (cubre al menos dos ventanas). Cada enlace abre
    # la pestaña de su producto.
    ventana_actual = [o for o in obs if o.day >= desde]
    ventana_previa = [o for o in obs if desde - timedelta(days=window) <= o.day < desde]
    ticker = build_ticker(
        pilot_names,
        build_month_vs_month(ventana_actual, ventana_previa, window_days=window, hoy=hasta),
        href=lambda slug: products_query(
            [slug], pestana=slug, provincia=prov_slug, desde=desde, hasta=hasta, vista="explorar"
        ),
    )
    respuesta: HTMLResponse = request.app.state.templates.TemplateResponse(
        request,
        "public_explore.html",
        {
            "vista": "explorar",
            "ticker": ticker,
            "ex_nav": build_explore_nav(
                productos, active_products, active_slug, provincia=prov_slug, desde=desde, hasta=hasta
            ),
            "ex_active": activo,
            "ex_detail": build_explore_detail(activo, elegido) if activo and elegido else None,
            "ex_map": build_explore_map(activo, elegido.key if elegido else None),
            "ex_selected_key": elegido.key if elegido else "",
            "window_days": window,
            "selected_desde": desde,
            "selected_hasta": hasta,
            "csv_href": csv_href,
            "mapa_href": vista_link("mapa"),
            "tablero_href": vista_link("tablero"),
            "explorar_href": vista_link("explorar"),
            "province_link": province_link,
        },
    )
    return respuesta


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
    vista: str | None = None,
    pestana: str | None = None,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Home pública. `productos` (multi-selección de chips), `producto` (alias de
    uno), `provincia`, `desde`/`hasta` (rango de calendario del date picker,
    2026-09-14; `dias` sigue aceptándose como alias de compatibilidad —ver
    `resolve_window`—), `ver` (ventanas flotantes abiertas), `capa`
    (capa del mapa), `vista` (mapa/tablero/explorar, 2026-09-16) y `pestana`
    (qué producto se ve en «Explorar», P4 2026-09-25; sin efecto en las otras
    vistas) son query string opcional; un valor raro no da 404, se muestra todo.

    `?vista=` explícito manda siempre —un link compartido con `?vista=tablero`
    tiene que abrir en tablero pase lo que pase—; sin él, cae a la cookie
    `vista` (slice 3, 2026-09-16), que el toggle escribe al click. Es cookie y
    no `localStorage` a propósito: `localStorage` no lo puede leer el
    servidor, así que una visita nueva a esta ruta renderizaría el mapa
    completo —Leaflet, tiles— y recién después un redirect en el cliente
    mandaría al tablero. Dos cargas, la más pesada primero, exactamente en
    contra de por qué existe el tablero (aliviar la conexión lenta). La
    cookie deja que el servidor elija la plantilla correcta de entrada.

    `provincia` sigue la misma regla (S8, 2026-09-19), solo en el tablero:
    `?provincia=` explícito manda; sin él, la cookie `provincia` que escribe el
    clic en un chip de «¿En qué provincia estás?». El mapa no la lee —su
    encuadre no cambia por una preferencia que nadie le pidió—, y un valor
    de cookie que no sea provincia se ignora en `resolve_province`."""
    vista_efectiva = vista if vista is not None else request.cookies.get("vista")
    # `resolve_vista` y no la cruda: sin query ni cookie la vista es la
    # default (tablero, 2026-09-26) y también tiene que leer la provincia.
    if provincia is None and resolve_vista(vista_efectiva) == "tablero":
        provincia = request.cookies.get("provincia")
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
        vista=vista_efectiva,
        pestana=pestana,
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
