"""Mapa de Cuba para la vista pública: geometría + cobertura, sin pintar nada.

Mismo reparto de trabajo que `matrix.py`: acá se arma la estructura y la
plantilla solo la recorre. Es el invariante 1 del ADR 001 — si el número se
calcula en Jinja, en Fase 2 hay que reescribirlo y mientras tanto no se puede
testear.

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

from dataclasses import dataclass

from saimc.web.map_cuba import PROVINCE_PATHS, VIEWBOX
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
    """Una provincia dibujable: su forma, su estado de publicación y su muestra."""

    name: str
    path: str
    rows: int
    is_published: bool

    @property
    def slug(self) -> str:
        """Segmento de URL de la provincia, para el enlace del mapa a su hoja
        de detalle. Mismo `product_slug` que resuelve `?provincia=` de vuelta
        al nombre (`product_nav.resolve_province`), así que el ida y vuelta
        cierra: «La Habana» → «la-habana» → «La Habana»."""
        return product_slug(self.name)

    @property
    def has_data(self) -> bool:
        return self.rows > 0

    @property
    def state(self) -> str:
        """Sufijo de clase CSS y clave de `STATE_LABELS`.

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

    viewbox: str
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
    rows_by_province: dict[str, int],
    published: frozenset[str] | set[str] | None = None,
) -> MapView:
    """Cruza la geometría con el conteo de observaciones y el estado de publicación.

    Recorre `PROVINCE_PATHS` y no los diccionarios de entrada: las 16 provincias
    aparecen siempre, y una sin dato entra con `rows=0`. El cero **es** el dato
    —dice dónde no se está mirando—, igual que en `progress.compute_coverage`.
    Un nombre que venga en los conteos y no exista en la geometría se ignora en
    silencio acá; `tests/test_public_map.py` cubre que los 16 nombres coincidan
    con los de la tabla `provinces`.

    `published` omitido equivale a ninguna publicada, que es el estado real hoy y
    además el que menos afirma: una provincia se muestra como publicada solo si
    alguien lo decidió explícitamente en la base.
    """
    publicadas = published or frozenset()
    return MapView(
        viewbox=VIEWBOX,
        provinces=tuple(
            MapProvince(
                name=name,
                path=path,
                rows=rows_by_province.get(name, 0),
                is_published=name in publicadas,
            )
            for name, path in sorted(PROVINCE_PATHS.items())
        ),
    )
