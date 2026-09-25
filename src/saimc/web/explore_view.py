"""Datos de la vista «Explorar» (2026-09-21, prototipo C aprobado por el operador).

Por producto, un árbol provincia → formato → anuncios con lo que la tabla
muestra en cada nivel: mediana, cambio, rango, muestra, último día, tendencia
y calor semanal. No dibuja nada: acá se arma la estructura —incluido el trazo
SVG de la sparkline— y la plantilla solo la recorre (mismo reparto que
`province_grid.py`, `watchlist_view.py` y `matrix.py`).

Cada cifra sigue la regla que ya publica el resto de la aplicación, para que
esta vista no contradiga a las demás:

- **La fila de provincia funde los formatos** (`price_charts._by_product`, lo
  que hacen la lista del tablero y «Cómo se movió»): compara provincias, no
  envases. **El detalle los separa** (`price_charts._group`, la cifra por celda
  que ya trae el panel del mapa): «¿cómo varía según el envase?».
- **Mediana, rango y cambio con el cerco robusto** (`split_outliers`): un precio
  fuera de escala cuenta en la muestra y se marca, pero no arrastra la mediana
  ni el rango. El cambio es contra el período anterior del mismo largo, sin
  flecha ni color (§12.6).
- **Ninguna cifra nueva**: no hay «mediana nacional»; el titular de cada
  producto es el rango entre la provincia más barata y la más cara.

**La tendencia y el calor semanal miran siempre 13 semanas** hacia atrás desde
`hoy`, aunque la ventana sea más corta (decisión del 2026-09-21): con 30 días
una serie de cinco semanas no dice nada. Mediana, cambio y muestra siguen la
ventana; los anuncios de julio dibujan la tendencia de agosto y no cuentan en
la muestra. Una semana sin anuncios queda vacía y corta la línea: ninguna
línea une períodos que no se tocan.
"""

from __future__ import annotations

import statistics
import uuid
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from saimc.cells import RetailObservation
from saimc.units import unit_noun
from saimc.web.card_view import format_price
from saimc.web.cuba_svg import build_cuba_map
from saimc.web.price_charts import AdRow, _short_day, observation_format_label, split_outliers
from saimc.web.product_nav import product_slug, products_query
from saimc.web.province_grid import Sparkline, _n
from saimc.web.watchlist_view import _alfabeto

#: Semanas de la tendencia y cuántas se ven como calor en la tabla.
WEEKS = 13
SHOWN_WEEKS = 8
#: Lienzo de la sparkline de la fila, en unidades del `viewBox`.
SPARK_W = 92.0
SPARK_H = 24.0
SPARK_PAD = 3.0
_BAR_FILL = 0.68


@dataclass(frozen=True)
class RangeBar:
    """Del precio más bajo al más alto, con una raya en la mediana, todo en
    porcentaje del rango de TODO el producto: así las barras de dos filas se
    pueden comparar a ojo."""

    left: float
    width: float
    tick: float


@dataclass(frozen=True)
class WeekCell:
    """Una semana del calor: la mediana escrita y su tinte de 0 a 1 contra las
    ocho semanas de todo el producto. Sin anuncios: texto vacío y sin tinte."""

    text: str
    tint: float | None


@dataclass(frozen=True)
class ExploreNode:
    #: `la-habana` para una provincia, `la-habana|saco-de-100-lb` para un formato.
    key: str
    name: str
    kind: Literal["province", "format"]
    province_name: str
    median: Decimal
    median_text: str
    #: Puntos porcentuales contra el período anterior; `None` si no hubo
    #: anuncios antes: sin contra qué comparar no se muestra ningún cambio.
    change: int | None
    change_text: str
    minimum_text: str
    maximum_text: str
    count: int
    outliers: int
    last_day: date
    last_days: int
    last_text: str
    range_bar: RangeBar
    #: 13 medianas semanales, la última es la semana que termina hoy.
    weekly: tuple[Decimal | None, ...]
    weeks: tuple[WeekCell, ...]
    spark: Sparkline | None
    #: Los anuncios de la ventana detrás de la cifra, del más reciente al más
    #: viejo. Solo los trae el formato: la provincia los reparte entre sus hijos.
    ads: tuple[AdRow, ...] = ()
    #: 0 (la provincia más barata con dato) a 1 (la más cara), para el mapa.
    #: Solo las provincias; `None` en los formatos.
    tint: float | None = None
    children: tuple[ExploreNode, ...] = ()


@dataclass(frozen=True)
class ExploreProduct:
    name: str
    slug: str
    currency: str
    unit: str
    unit_noun: str
    #: Las provincias con anuncios en la ventana, de mayor a menor mediana.
    nodes: tuple[ExploreNode, ...]
    #: Las demás, por nombre: la ausencia de dato se dice como tal (§9.0).
    sin_datos: tuple[str, ...]
    #: Inicio de cada una de las semanas del calor, para rotular la cabecera.
    week_labels: tuple[str, ...]

    @property
    def has_data(self) -> bool:
        return bool(self.nodes)

    @property
    def low_text(self) -> str:
        return min(self.nodes, key=lambda n: n.median).median_text if self.nodes else ""

    @property
    def high_text(self) -> str:
        return max(self.nodes, key=lambda n: n.median).median_text if self.nodes else ""

    @property
    def count(self) -> int:
        return sum(n.count for n in self.nodes)

    @property
    def province_count(self) -> int:
        return len(self.nodes)


@dataclass(frozen=True)
class _Stats:
    count: int
    outliers: int
    median: Decimal
    minimum: Decimal
    maximum: Decimal
    previous_median: Decimal | None
    last_day: date


def _stats(rows: Sequence[RetailObservation], previous: Sequence[RetailObservation]) -> _Stats | None:
    """Misma cifra que `price_charts.build_headlines`: mediana, mínimo y máximo
    de lo que cae dentro del cerco; la muestra cuenta todo."""
    dentro, fuera = split_outliers(rows)
    if not dentro:
        return None
    prev_dentro, _ = split_outliers(previous)
    precios = sorted(r.unit_price for r in dentro)
    return _Stats(
        count=len(rows),
        outliers=len(fuera),
        median=statistics.median(precios),
        minimum=precios[0],
        maximum=precios[-1],
        previous_median=(statistics.median([r.unit_price for r in prev_dentro]) if prev_dentro else None),
        last_day=max(r.day for r in rows),
    )


def _change(median: Decimal, previous: Decimal | None) -> int | None:
    if previous is None or previous == 0:
        return None
    return round((float(median) / float(previous) - 1) * 100)


def _change_text(pct: int | None) -> str:
    if pct is None:
        return ""
    return f"+{pct}%" if pct > 0 else (f"−{abs(pct)}%" if pct < 0 else "0%")


def _weekly(rows: Sequence[RetailObservation], hoy: date) -> tuple[Decimal | None, ...]:
    """La mediana de cada una de las últimas 13 semanas (la casilla 12 termina
    hoy); `None` donde no hubo anuncios. El cerco se calcula sobre las 13
    semanas de la propia serie."""
    dentro, _ = split_outliers(rows)
    por_semana: dict[int, list[Decimal]] = defaultdict(list)
    for r in dentro:
        antes = (hoy - r.day).days // 7
        if 0 <= antes < WEEKS:
            por_semana[WEEKS - 1 - antes].append(r.unit_price)
    return tuple(statistics.median(por_semana[j]) if j in por_semana else None for j in range(WEEKS))


def sparkline_from_values(
    values: Sequence[Decimal | float | None], *, width: float, height: float, pad: float = SPARK_PAD
) -> Sparkline | None:
    """El trazo de una serie con huecos, normalizado contra sí misma.

    La forma —¿sube, baja, oscila?— es lo que dice una sparkline; el nivel ya lo
    dice la mediana de la fila. Cada valor cae en la posición de su semana, y un
    hueco corta la línea en vez de unir dos semanas que no se tocan. Con un solo
    dato queda a media altura, sin pendiente que inventar. `None` sin ningún
    dato.
    """
    puestos = [(i, float(v)) for i, v in enumerate(values) if v is not None]
    if not puestos:
        return None
    slots = len(values)
    valores = [v for _, v in puestos]
    lo, hi = min(valores), max(valores)
    inner_w, inner_h = width - 2 * pad, height - 2 * pad
    base = height - pad

    def norm(v: float) -> float:
        return 0.5 if hi == lo else (v - lo) / (hi - lo)

    def x_of(i: int) -> float:
        return width / 2 if slots <= 1 else pad + inner_w * i / (slots - 1)

    xy = [(x_of(i), pad + inner_h * (1 - norm(v))) for i, v in puestos]
    linea = ""
    area = ""
    tramo: list[tuple[float, float]] = []
    previo = -2
    for (i, _), punto in zip(puestos, xy, strict=True):
        if i != previo + 1 and tramo:
            linea, area = _cerrar(linea, area, tramo, base)
            tramo = []
        tramo.append(punto)
        previo = i
    linea, area = _cerrar(linea, area, tramo, base)
    ancho_barra = inner_w / slots * _BAR_FILL
    barras = ""
    for i, v in puestos:
        alto = 3 + (inner_h - 3) * norm(v)
        x = pad + inner_w * (i + (1 - _BAR_FILL) / 2) / slots
        barras += f"M{_n(x)},{_n(base)}v{_n(-alto)}h{_n(ancho_barra)}v{_n(alto)}z"
    return Sparkline(line=linea, area=area, cols=barras, end=(round(xy[-1][0], 1), round(xy[-1][1], 1)))


def _cerrar(linea: str, area: str, tramo: list[tuple[float, float]], base: float) -> tuple[str, str]:
    trazo = "M" + "L".join(f"{_n(x)},{_n(y)}" for x, y in tramo)
    return linea + trazo, area + f"{trazo}L{_n(tramo[-1][0])},{_n(base)}L{_n(tramo[0][0])},{_n(base)}Z"


def _display(label: str | None, unit: str) -> str:
    """«saco de 100 lb» → «Saco de 100 lb». Sin formato declarado se nombra por
    la unidad, «Libra»: es el precio por unidad, sin envase."""
    texto = label if label is not None else (unit_noun(unit) or unit)
    return texto[:1].upper() + texto[1:]


@dataclass(frozen=True)
class _Raw:
    key: str
    name: str
    kind: Literal["province", "format"]
    province_name: str
    stats: _Stats
    weekly: tuple[Decimal | None, ...]
    ads: tuple[AdRow, ...]
    children: tuple[_Raw, ...]


def _by(rows: Sequence[RetailObservation]) -> dict[str, list[RetailObservation]]:
    por: dict[str, list[RetailObservation]] = defaultdict(list)
    for r in rows:
        por[r.province_name or ""].append(r)
    return por


def _by_format(rows: Sequence[RetailObservation]) -> dict[str | None, list[RetailObservation]]:
    por: dict[str | None, list[RetailObservation]] = defaultdict(list)
    for r in rows:
        por[observation_format_label(r)].append(r)
    return por


def _ads(rows: Sequence[RetailObservation]) -> tuple[AdRow, ...]:
    ads = [
        AdRow(
            o.day,
            o.province_name or "",
            o.unit_price,
            o.currency,
            o.unit,
            o.evidence_id,
            o.evidence_is_public,
        )
        for o in rows
    ]
    ads.sort(key=lambda a: -a.day.toordinal())
    return tuple(ads)


def _tint(value: float, lo: float, hi: float) -> float:
    return 0.5 if hi == lo else round((value - lo) / (hi - lo), 3)


def _finish(
    raw: _Raw,
    *,
    range_lo: float,
    range_span: float,
    week_lo: float,
    week_hi: float,
    hoy: date,
    tint: float | None,
    unit: str,
) -> ExploreNode:
    st = raw.stats
    dias = (hoy - st.last_day).days
    pct = _change(st.median, st.previous_median)
    ultimas = raw.weekly[-SHOWN_WEEKS:]
    return ExploreNode(
        key=raw.key,
        name=raw.name,
        kind=raw.kind,
        province_name=raw.province_name,
        median=st.median,
        median_text=format_price(st.median),
        change=pct,
        change_text=_change_text(pct),
        minimum_text=format_price(st.minimum),
        maximum_text=format_price(st.maximum),
        count=st.count,
        outliers=st.outliers,
        last_day=st.last_day,
        last_days=dias,
        last_text="hoy" if dias <= 0 else f"hace {dias} d",
        range_bar=RangeBar(
            left=round(100 * (float(st.minimum) - range_lo) / range_span, 1),
            width=round(100 * float(st.maximum - st.minimum) / range_span, 1),
            tick=round(100 * (float(st.median) - range_lo) / range_span, 1),
        ),
        weekly=raw.weekly,
        weeks=tuple(
            WeekCell("", None) if v is None else WeekCell(format_price(v), _tint(float(v), week_lo, week_hi))
            for v in ultimas
        ),
        spark=sparkline_from_values(raw.weekly, width=SPARK_W, height=SPARK_H),
        ads=raw.ads,
        tint=tint,
        children=tuple(
            _finish(
                c,
                range_lo=range_lo,
                range_span=range_span,
                week_lo=week_lo,
                week_hi=week_hi,
                hoy=hoy,
                tint=None,
                unit=unit,
            )
            for c in raw.children
        ),
    )


def _build_product(
    name: str,
    observations: Sequence[RetailObservation],
    *,
    window_days: int,
    hoy: date,
    provinces: Sequence[str],
) -> ExploreProduct:
    desde = hoy - timedelta(days=window_days)
    anterior_desde = desde - timedelta(days=window_days)
    etiquetas = tuple(
        _short_day(hoy - timedelta(days=(WEEKS - 1 - j) * 7 + 6)) for j in range(WEEKS - SHOWN_WEEKS, WEEKS)
    )
    propios = [o for o in observations if o.product_name == name and o.province_name and not o.unconverted]
    actuales = [o for o in propios if desde <= o.day <= hoy]
    if not actuales:
        return ExploreProduct(
            name=name,
            slug=product_slug(name),
            currency="",
            unit="",
            unit_noun="",
            nodes=(),
            sin_datos=tuple(sorted(provinces, key=_alfabeto)),
            week_labels=etiquetas,
        )
    pares = Counter((o.currency, o.unit) for o in actuales)
    moneda, unidad = sorted(pares, key=lambda p: (-pares[p], p))[0]

    def mismo(o: RetailObservation) -> bool:
        return (o.currency, o.unit) == (moneda, unidad)

    actuales = [o for o in actuales if mismo(o)]
    previas = [o for o in propios if mismo(o) and anterior_desde <= o.day < desde]
    horizonte = [o for o in propios if mismo(o) and 0 <= (hoy - o.day).days < WEEKS * 7]

    act_prov, prev_prov, hor_prov = _by(actuales), _by(previas), _by(horizonte)
    crudos: list[_Raw] = []
    for provincia, filas in act_prov.items():
        st = _stats(filas, prev_prov.get(provincia, []))
        if st is None:
            continue
        prev_f, hor_f = _by_format(prev_prov.get(provincia, [])), _by_format(hor_prov[provincia])
        hijos: list[_Raw] = []
        for formato, filas_f in _by_format(filas).items():
            st_f = _stats(filas_f, prev_f.get(formato, []))
            if st_f is None:
                continue
            hijos.append(
                _Raw(
                    key=f"{product_slug(provincia)}|{product_slug(_display(formato, unidad))}",
                    name=_display(formato, unidad),
                    kind="format",
                    province_name=provincia,
                    stats=st_f,
                    weekly=_weekly(hor_f[formato], hoy),
                    ads=_ads(filas_f),
                    children=(),
                )
            )
        hijos.sort(key=lambda h: (-h.stats.count, h.name))
        crudos.append(
            _Raw(
                key=product_slug(provincia),
                name=provincia,
                kind="province",
                province_name=provincia,
                stats=st,
                weekly=_weekly(hor_prov[provincia], hoy),
                ads=(),
                children=tuple(hijos),
            )
        )
    crudos.sort(key=lambda c: (-c.stats.median, _alfabeto(c.name)))

    todos = [n for c in crudos for n in (c, *c.children)]
    range_lo = min(float(n.stats.minimum) for n in todos)
    range_span = (max(float(n.stats.maximum) for n in todos) - range_lo) or 1.0
    semanas = [float(v) for n in todos for v in n.weekly[-SHOWN_WEEKS:] if v is not None]
    week_lo, week_hi = (min(semanas), max(semanas)) if semanas else (0.0, 0.0)
    medianas = [float(c.stats.median) for c in crudos]
    m_lo, m_hi = min(medianas), max(medianas)
    nodos = tuple(
        _finish(
            c,
            range_lo=range_lo,
            range_span=range_span,
            week_lo=week_lo,
            week_hi=week_hi,
            hoy=hoy,
            tint=_tint(float(c.stats.median), m_lo, m_hi),
            unit=unidad,
        )
        for c in crudos
    )
    con_dato = {c.name for c in crudos}
    return ExploreProduct(
        name=name,
        slug=product_slug(name),
        currency=moneda,
        unit=unidad,
        unit_noun=unit_noun(unidad) or unidad,
        nodes=nodos,
        sin_datos=tuple(sorted((p for p in provinces if p not in con_dato), key=_alfabeto)),
        week_labels=etiquetas,
    )


def build_explore_products(
    active_products: Sequence[str],
    observations: Sequence[RetailObservation],
    *,
    window_days: int,
    hoy: date,
    provinces: Sequence[str],
) -> tuple[ExploreProduct, ...]:
    """Un árbol por producto activo, en el orden recibido.

    `observations` tiene que cubrir lo más largo de tres cosas: la ventana, el
    período anterior del mismo largo (para el cambio) y las 13 semanas de la
    tendencia. `provinces` es el catálogo completo, para nombrar las que no
    tienen anuncios.
    """
    return tuple(
        _build_product(name, observations, window_days=window_days, hoy=hoy, provinces=provinces)
        for name in active_products
    )


@dataclass(frozen=True)
class DetailAd:
    day_text: str
    price_text: str
    format_name: str
    evidence_id: uuid.UUID
    is_public: bool


@dataclass(frozen=True)
class ExploreDetail:
    """Lo que cuenta el panel derecho de la provincia elegida."""

    name: str
    sub: str
    median_text: str
    change_text: str
    count: int
    #: La mediana semana a semana de las 13 semanas, en un lienzo de 300 × 100.
    chart: Sparkline | None
    ymax_text: str
    ymin_text: str
    ads: tuple[DetailAd, ...]


#: Lienzo del gráfico del detalle, en unidades del `viewBox`.
DETAIL_W = 300.0
DETAIL_H = 100.0
DETAIL_PAD = 8.0
#: Anuncios que se listan en el detalle; el resto vive en la fila de cada formato.
DETAIL_ADS = 6


def build_explore_detail(
    product: ExploreProduct, node: ExploreNode, *, limit: int = DETAIL_ADS
) -> ExploreDetail:
    """El detalle de una provincia: su cifra, su serie y los anuncios de todos
    sus formatos, del más reciente al más viejo. Las cifras son las de la fila:
    el panel no calcula nada que la tabla no diga."""
    formatos = len(node.children)
    con_dato = [float(v) for v in node.weekly if v is not None]
    anuncios = sorted(
        ((ad, hijo.name) for hijo in node.children for ad in hijo.ads),
        key=lambda par: -par[0].day.toordinal(),
    )
    return ExploreDetail(
        name=node.name,
        sub=f"{product.name} · {formatos} formato{'s' if formatos != 1 else ''}",
        median_text=node.median_text,
        change_text=node.change_text,
        count=node.count,
        chart=sparkline_from_values(node.weekly, width=DETAIL_W, height=DETAIL_H, pad=DETAIL_PAD),
        ymax_text=format_price(Decimal(str(max(con_dato)))) if con_dato else "",
        ymin_text=format_price(Decimal(str(min(con_dato)))) if con_dato else "",
        ads=tuple(
            DetailAd(ad.day_text, ad.price_text, nombre, ad.evidence_id, ad.evidence_is_public)
            for ad, nombre in anuncios[:limit]
        ),
    )


@dataclass(frozen=True)
class MapCell:
    slug: str
    name: str
    d: str
    #: 0 (la más barata con dato) a 1 (la más cara); `None` sin anuncios del producto.
    tint: float | None
    is_selected: bool

    @property
    def has_data(self) -> bool:
        return self.tint is not None


@dataclass(frozen=True)
class ExploreMap:
    width: float
    height: float
    cells: tuple[MapCell, ...]

    @property
    def viewbox(self) -> str:
        return f"0 0 {self.width:g} {self.height:g}"


#: Ancho del mapa del panel derecho, en unidades del `viewBox`: el SVG escala al
#: ancho del panel, esto es proporción y no tamaño.
MAP_W = 340.0


def build_explore_map(product: ExploreProduct | None, selected_slug: str | None) -> ExploreMap:
    """Las 16 provincias con el tinte del producto abierto y la elegida marcada.
    Sin producto, ninguna tiene dato: el mapa se muestra en blanco, no oculto."""
    mapa = build_cuba_map(MAP_W)
    tintes = {n.key: n.tint for n in product.nodes} if product else {}
    return ExploreMap(
        width=mapa.width,
        height=mapa.height,
        cells=tuple(
            MapCell(
                slug=c.slug,
                name=c.name,
                d=c.d,
                tint=tintes.get(c.slug),
                is_selected=c.slug == selected_slug,
            )
            for c in mapa.provinces
        ),
    )


@dataclass(frozen=True)
class ExploreLink:
    """Un producto de la lista de la izquierda o del «+»: marcarlo lo abre y
    desmarcarlo lo cierra, y el enlace ya lleva esa URL."""

    name: str
    slug: str
    count: int
    is_on: bool
    query: str


@dataclass(frozen=True)
class ExploreTab:
    name: str
    slug: str
    count: int
    #: La pestaña que se muestra ahora mismo (`?pestana=`), no la primera de
    #: la lista: el orden de apertura es estable (P4, 2026-09-25) y activar
    #: una pestaña ya abierta no reordena las demás.
    is_on: bool
    #: Sin JavaScript, tocar la pestaña es navegar con `?pestana=` en ese
    #: producto; el `?productos=` no cambia.
    query: str
    close_query: str


@dataclass(frozen=True)
class ExploreNav:
    products: tuple[ExploreLink, ...]
    tabs: tuple[ExploreTab, ...]
    add: tuple[ExploreLink, ...]


def build_explore_nav(
    products: Sequence[ExploreProduct],
    active: Sequence[str],
    active_slug: str,
    *,
    provincia: str,
    desde: date,
    hasta: date,
) -> ExploreNav:
    """Los enlaces de la vista: la lista de la izquierda en el orden de
    `products`, las pestañas en el orden de apertura (`active`, ESTABLE —
    activar una ya abierta no las reordena, P4 2026-09-25) y `active_slug` es
    la que se ve ahora (`?pestana=`). `provincia` es el slug de la elegida
    (o vacío): viaja en cada enlace para que abrir, cerrar o activar un
    producto no la suelte.

    Abrir uno nuevo lo agrega al final y lo activa. Cerrar el que está activo
    activa su vecino —el que ocupaba su lugar, o el anterior si era el
    último—; cerrar cualquier otro no mueve la aguja."""
    abiertos = [product_slug(n) for n in active]

    def url(slugs: Sequence[str], pestana: str) -> str:
        return products_query(
            slugs, provincia=provincia, desde=desde, hasta=hasta, vista="explorar", pestana=pestana
        )

    def alternar(slug: str) -> str:
        if slug not in abiertos:
            return url([*abiertos, slug], slug)
        resto = [s for s in abiertos if s != slug]
        si_estaba_activa = resto[min(abiertos.index(slug), len(resto) - 1)] if resto else ""
        nueva_activa = si_estaba_activa if slug == active_slug else active_slug
        return url(resto, nueva_activa)

    enlaces = tuple(
        ExploreLink(p.name, p.slug, p.count, p.slug in abiertos, alternar(p.slug)) for p in products
    )
    por_slug = {p.slug: p for p in products}
    tabs = tuple(
        ExploreTab(
            name=por_slug[s].name,
            slug=s,
            count=por_slug[s].count,
            is_on=s == active_slug,
            query=url(abiertos, s),
            close_query=alternar(s),
        )
        for s in abiertos
        if s in por_slug
    )
    return ExploreNav(products=enlaces, tabs=tabs, add=tuple(e for e in enlaces if not e.is_on))
