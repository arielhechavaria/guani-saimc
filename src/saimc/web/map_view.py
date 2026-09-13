"""Mapa de Cuba para la vista pública: cobertura por provincia, sin pintar nada.

Mismo reparto de trabajo que `matrix.py`: acá se arma la estructura y la
plantilla (o el JS de Leaflet, desde 2026-09-12) solo la recorre. Es el
invariante 1 del ADR 001 — si el número se calcula en Jinja, en Fase 2 hay
que reescribirlo y mientras tanto no se puede testear.

**Ya no vive acá ninguna geometría.** Hasta el 2026-09-11 este módulo cruzaba
`map_cuba.PROVINCE_PATHS` (rutas SVG proyectadas) con el conteo; el mapa pasó
a Leaflet interactivo (docs/decisions.md, 2026-09-12 — apartamiento consciente
de la decisión cerrada 1) y la geometría real vive en
`static/cuba_provincias.geojson` (lon/lat, generado por
`tools/build_cuba_geojson.py`), que el navegador pide aparte. Este módulo solo
sabe de nombres de provincia —los mismos que ya resuelve `?provincia=`— y de
su estado; `build_map` los recibe de afuera (`_all_province_names`, la
consulta a `Province` que ya usaba `resolve_province`) en vez de leerlos de un
archivo de geometría, así que ya no depende de que exista ningún SVG.

**Por qué no hay un umbral de «datos suficientes».** `spec.md` §6.7 describe el
gris del mapa como «sin datos suficientes», pero nunca dice cuántas
observaciones son suficientes, y §12.6 es explícita: los umbrales que la
especificación introdujo sin valor asignado **no se inventan**. Así que el mapa
no colorea por precio ni por «suficiencia»: colorea por el estado de publicación,
que sí está definido, y expone el tamaño de muestra al lado.

**Los tres estados salen de §9.0, no de una elección de diseño.** La sección es
vinculante: «La interfaz muestra explícitamente qué provincias están publicadas
y cuáles en ingesta sin publicación, con su progreso hacia el umbral. La ausencia
de datos se comunica como decisión metodológica, no como falla». Eso son dos ejes
distintos y conviene no confundirlos:

- **Publicación** — `Province.is_published`, que el modelo define como dos
  estados y no tres, también citando §9.0. Es una decisión, no una medición.
- **Presencia de dato** — si la provincia tiene observaciones. Es una medición,
  no una decisión.

Cruzados dan los tres estados que la interfaz nombra: `publicada`,
`en-ingesta` (recolectando, sin exponer la serie) y `sin-datos`. Hoy las 16 están
en `is_published=False`, así que el mapa entero está en los dos últimos — y eso
es exactamente lo que §9.0 quiere que se vea, en lugar de esconderlo.

**El «progreso hacia el umbral» se muestra como el conteo crudo**, no como un
porcentaje: un porcentaje necesita el denominador N, que §9.0 manda calibrar con
datos reales y §12.6 prohíbe inventar. Decir «113 observaciones» es honesto;
decir «57% del umbral» sería fabricar el umbral.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from saimc.web.product_nav import product_slug

#: Etiquetas visibles de cada estado. Viven acá y no en la plantilla para que
#: los tests puedan afirmarlas sin parsear HTML, y para que exista un solo lugar
#: donde se lee qué dice la interfaz.
STATE_LABELS = {
    "publicada": "Publicada",
    "en-ingesta": "Recogiendo datos, todavía sin publicar",
    "sin-datos": "Sin datos todavía",
}


@dataclass(frozen=True)
class MapProvince:
    """Una provincia del mapa: su estado de publicación y su muestra.

    Sin geometría — el `<div id="ph-leaflet-map">` la busca por nombre en
    `cuba_provincias.geojson`, que viaja aparte. `slug` es la clave común
    entre las dos fuentes.
    """

    name: str
    rows: int
    is_published: bool
    #: El máximo de `rows` entre las 16, para `density_ratio`. Vive acá y no
    #: como argumento aparte de la capa de densidad porque es el mismo valor
    #: para las 16 provincias de un mismo `MapView`.
    max_rows: int = 0

    @property
    def density_ratio(self) -> float:
        """`rows` contra el máximo entre las 16 provincias, en 0..1.

        Capa «densidad de anuncios» del mapa (2026-09-11): relativo al máximo
        actual, nunca un umbral fijo —§12.6 prohíbe inventar uno, y esto no
        decide qué se publica, solo cómo se sombrea—. La provincia con más
        anuncios siempre pinta a fondo, tenga 5 o 5000; sin ninguna
        observación en las 16, todas quedan en 0.0.
        """
        if self.max_rows <= 0:
            return 0.0
        return self.rows / self.max_rows

    @property
    def slug(self) -> str:
        """Segmento de URL de la provincia, y clave para emparejar con
        `cuba_provincias.geojson` del lado del cliente. Mismo `product_slug`
        que resuelve `?provincia=` de vuelta al nombre
        (`product_nav.resolve_province`), así que el ida y vuelta cierra:
        «La Habana» → «la-habana» → «La Habana»."""
        return product_slug(self.name)

    @property
    def has_data(self) -> bool:
        return self.rows > 0

    @property
    def state(self) -> str:
        """Clave de `STATE_LABELS`, y el estado que el JS de Leaflet lee para
        elegir el color de relleno del polígono.

        La publicación manda sobre la presencia de dato: una provincia publicada
        se muestra como tal aunque su conteo esté en cero, porque publicar es una
        decisión tomada y esconderla sería el opuesto de lo que pide §9.0.
        """
        if self.is_published:
            return "publicada"
        return "en-ingesta" if self.has_data else "sin-datos"

    @property
    def label(self) -> str:
        return STATE_LABELS[self.state]


@dataclass(frozen=True)
class MapView:
    """El mapa entero, listo para recorrer en la plantilla."""

    provinces: tuple[MapProvince, ...]

    @property
    def covered(self) -> int:
        return sum(1 for province in self.provinces if province.has_data)

    @property
    def published(self) -> int:
        return sum(1 for province in self.provinces if province.is_published)

    @property
    def total_rows(self) -> int:
        return sum(province.rows for province in self.provinces)


def build_map(
    province_names: Iterable[str],
    rows_by_province: dict[str, int],
    published: frozenset[str] | set[str] | None = None,
) -> MapView:
    """Cruza los 16 nombres de provincia con el conteo de observaciones y el
    estado de publicación.

    Recorre `province_names` (típicamente `_all_province_names(db)`, la misma
    consulta que ya resuelve `?provincia=`) y no los diccionarios de entrada:
    las 16 provincias aparecen siempre, y una sin dato entra con `rows=0`. El
    cero **es** el dato —dice dónde no se está mirando—, igual que en
    `progress.compute_coverage`. Un nombre que venga en los conteos y no esté
    en `province_names` se ignora en silencio acá.

    `published` omitido equivale a ninguna publicada, que es el estado real hoy y
    además el que menos afirma: una provincia se muestra como publicada solo si
    alguien lo decidió explícitamente en la base.
    """
    publicadas = published or frozenset()
    max_rows = max([*rows_by_province.values(), 0])
    return MapView(
        provinces=tuple(
            MapProvince(
                name=name,
                rows=rows_by_province.get(name, 0),
                is_published=name in publicadas,
                max_rows=max_rows,
            )
            for name in sorted(province_names)
        ),
    )
