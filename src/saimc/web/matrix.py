"""Presentación de la matriz de celdas: agrupado por producto y barra de rango.

`cells.py` decide **qué** es una celda; este módulo decide **cómo se lee**. La
separación importa: la identidad de la celda (producto × provincia × unidad ×
formato) es dominio y no se toca desde una decisión visual.

Dos cosas, las dos con geometría calculada en Python y no en Jinja, para que
sean testeables:

1. `group_cells` junta las series del mismo producto en la misma provincia bajo
   un encabezado. Antes «Arroz» se repetía tres veces y se leía como tres
   productos en vez de un producto con tres series.
2. `build_range_bar` dibuja el rango mínimo–mediana–máximo de cada serie.

**La barra es codificación proporcional, no veredicto.** `spec.md` §12.6
prohíbe fijar un umbral de dispersión, así que ninguna celda se pinta como
buena o mala: la barra dibuja el rango que la celda ya tiene y quien juzga es
el operador — lo mismo que ya hacía la columna de amplitud, pero en una forma
que salta a la vista en vez de quedar como un número al final de la fila.

**La escala es logarítmica** porque la amplitud es una razón (máximo ÷ mínimo),
no una diferencia: en escala lineal una celda de 44× y una de 103× se verían
casi iguales contra un máximo de 103, y una de 2× sería invisible. En
logarítmica las tres se distinguen. Por el mismo motivo la mediana se ubica en
escala logarítmica dentro de su propio rango: es la posición que responde
«¿dónde está la masa de las observaciones?».

La normalización es contra **toda la tabla**, no contra el grupo: si cada
producto se escalara solo, la amplitud de Azúcar se vería igual de ancha que
la de Arroz y la comparación entre productos mentiría.

3. `build_matrix` pivota esos grupos a una grilla: una fila por producto, una
   columna por provincia. Apilados, «Arroz · Santiago» y «Arroz · La Habana»
   caían separados por otros productos —el orden es por muestra— y comparar
   provincias, que es el objeto medido de `spec.md` §5, había que hacerlo de
   memoria. En columnas quedan lado a lado.

**El color de provincia es identidad, no veredicto.** Es la misma regla de
§12.6 que gobierna la barra, aplicada al color: la paleta responde «¿de qué
provincia es esta columna?» y nunca «¿está bien o mal?». Por eso son matices
categóricos —índigo, violeta, cian, magenta— y ninguno es rojo ni verde: un
semáforo afirmaría un umbral que nadie fijó. La ranura se asigna por el orden
de `PHASE0_PROVINCES` y no por los datos, así que Santiago conserva su color
aunque La Habana se quede un día sin cargar; si dependiera de la muestra, el
operador vería a una provincia cambiar de color de un día para el otro y el
color dejaría de servir como identidad.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, replace
from decimal import Decimal

from saimc.cells import Cell
from saimc.progress import PHASE0_PROVINCES

# Cuántas ranuras de color define el CSS de `control.html`. Con más provincias
# que ranuras los colores se repiten: es preferible a inventar matices sobre la
# marcha, y con las 2 provincias de la Fase 0 (§9) no llega a pasar.
_PROVINCE_SLOTS = 4

# Ranura de la celda sin provincia. Fuera del rango de las provincias reales
# para que el CSS le dé el gris neutro: «sin provincia» no se funde con nada
# (ver `cells.py`), y darle un color de identidad la haría pasar por una
# provincia más.
UNKNOWN_PROVINCE_SLOT = 9

# Piso de ancho visible. Sin él, una amplitud apenas mayor a 1 contra un
# máximo grande da una barra de fracción de píxel, que se lee como «no hay
# barra» y no como «el rango es chiquito» — que son cosas distintas.
_MIN_VISIBLE_WIDTH_PCT = 4.0


@dataclass(frozen=True)
class RangeBar:
    """Geometría de la barra de una serie, en porcentajes listos para CSS."""

    # Largo de la barra: la amplitud de esta celda contra la mayor de la tabla.
    width_pct: float
    # Posición del marcador de la mediana dentro de la barra.
    median_pct: float
    # `False` cuando no hay rango que dibujar (mínimo == máximo, una sola
    # observación, o un mínimo en cero que haría explotar la razón).
    has_range: bool


def build_range_bar(
    *, minimum: Decimal, median: Decimal, maximum: Decimal, max_spread: Decimal
) -> RangeBar:
    """Barra de una serie, escalada contra `max_spread` (el de toda la tabla)."""
    sin_barra = RangeBar(width_pct=0.0, median_pct=0.0, has_range=False)

    if minimum <= 0 or maximum <= minimum:
        return sin_barra

    spread = math.log(float(maximum) / float(minimum))
    techo = math.log(float(max_spread)) if max_spread > 1 else 0.0
    if techo <= 0:
        return sin_barra

    width_pct = max(_MIN_VISIBLE_WIDTH_PCT, min(100.0, spread / techo * 100.0))

    # Dónde cae la mediana dentro del propio rango de la celda. Pegada al
    # borde izquierdo significa que la masa de las observaciones está abajo y
    # el máximo es un extremo suelto — la forma típica de una celda que mezcla
    # precio minorista con un saco mayorista.
    median_pct = min(100.0, max(0.0, math.log(float(median) / float(minimum)) / spread * 100.0))

    return RangeBar(width_pct=width_pct, median_pct=median_pct, has_range=True)


@dataclass(frozen=True)
class CellWithBar:
    """Una celda con su barra ya calculada, para no hacer geometría en Jinja."""

    cell: Cell
    bar: RangeBar
    # Mediana minorista ÷ mediana de esta celda, cuando esta celda es la
    # mayorista y su grupo tiene una minorista **limpia** en la misma unidad
    # (2026-09-02, opción A). `None` en cualquier otro caso: en la minorista,
    # cuando no hay mayorista con qué comparar, cuando las unidades difieren,
    # o cuando alguna de las dos arrastra un flag que le quita sentido a la
    # mediana. Es una razón, no un veredicto: el `n` de las dos series queda a
    # la vista al lado y §12.6 prohíbe fijar un corte de muestra mínima.
    retail_ratio: Decimal | None = None


@dataclass(frozen=True)
class CellGroup:
    """Todas las series de un producto en una provincia."""

    product_name: str
    province_name: str | None
    cells: list[CellWithBar]
    total: int
    # `True` cuando el grupo tiene al menos una celda con variante declarada y
    # al menos una sin declarar: el punto donde declarar una variante partió
    # la serie en dos (design.md D6). Nunca se oculta — mismo criterio que
    # `unconverted`/`unquantified` (§12.6): se ve la fractura, no se adivina
    # ni se esconde.
    has_undeclared_variants: bool = False
    #: Las mismas series de `cells`, leídas como filas: minorista principal y
    #: mayorista en capa (`build_series_rows`, 2026-10-16).
    rows: tuple[SeriesRow, ...] = ()


# Orden de los formatos dentro de una unidad: el minorista arriba porque es el
# precio que define `spec.md` §8.1 Nivel 0 y el que alimenta el dashboard
# público; el mayorista debajo, como contexto. Cualquier otro valor —o el
# nulo— va al final.
_FORMAT_RANK = {"retail": 0, "wholesale": 1}


def _cell_is_clean(cell: Cell) -> bool:
    """La celda agrega precios por unidad en CUP, sin nada pendiente.

    Solo estas comparan: una celda con `unconverted` (sin tasa), `unquantified`
    (sin cantidad), `unresolved_basis` (sin saber si dividir) o
    `unconverted_unit` (unidad sin equivalencia) tiene una mediana que no es un
    precio por unidad en CUP, así que ponerla contra otra mediría dos cosas
    distintas.
    """
    return not (
        cell.unconverted
        or cell.unquantified
        or cell.unresolved_basis
        or cell.unconverted_unit
    )


# Clave de emparejamiento: variante primero (design.md D5), formato, unidad.
_PairKey = tuple[uuid.UUID | None, uuid.UUID | None, str | None]


def _pair_formats(celdas: list[CellWithBar]) -> list[CellWithBar]:
    """Empareja minorista y mayorista **del mismo formato y variante** y anota
    el ratio.

    Hasta el 2026-09-04 el par se armaba por `unit`, que era lo único que
    había. Con `product_formats` eso es incorrecto: la caja de 40 lb y el
    paquete de 10 lb llevan los dos `unit = "lb"`, así que emparejarlos ponía
    la mayorista de una contra la minorista del otro — dos cosas distintas,
    que es el defecto que este cambio entero corrige, un nivel más abajo.
    Desde product-variants (design.md D5), la variante entra por el mismo
    motivo: «Aceite Vitalín retail» no se empareja con «Aceite genérico
    wholesale» aunque compartan formato — son series distintas porque una
    declara una marca y la otra no.

    El par correcto es **el mismo formato en dos escalones de volumen**, y los
    anuncios de contenedor lo prueban: «1475 cajas × 31,8 USD» contra «35
    USD/caja por unidades en almacén» es la misma caja de 40 lb a dos precios.
    El formato responde *qué comprás*; `sale_format`, *a qué volumen*.

    Dos pasos, los dos de presentación —ninguno toca la normalización—:

    1. **Ratio.** Por cada (variante, formato) con una minorista limpia, la
       mayorista limpia de esa misma combinación recibe `retail_ratio =
       mediana_minorista / mediana_mayorista`. Se anota en la mayorista
       porque la minorista es la referencia: la pregunta es «cuánto más caro
       sale comprar suelto». El ratio se calcula sobre `median` (precio por
       unidad base) y no sobre `format_median`: como las dos series comparten
       formato, el factor de conversión es el mismo y se cancela — pero
       hacerlo sobre el precio normalizado lo deja bien definido incluso sin
       formato declarado.
    2. **Orden.** La combinación con más muestra queda arriba y, dentro de
       ella, la minorista antes que la mayorista. Así el par queda pegado en
       vez de separado por las series con flag, que caen al final por su
       propio `_FORMAT_RANK`.
    """
    minorista_por_formato: dict[_PairKey, Decimal] = {
        (c.cell.variant_id, c.cell.format_id, c.cell.unit): c.cell.median
        for c in celdas
        if c.cell.sale_format == "retail" and _cell_is_clean(c.cell)
    }

    con_ratio = []
    for c in celdas:
        clave: _PairKey = (c.cell.variant_id, c.cell.format_id, c.cell.unit)
        es_par_mayorista = (
            c.cell.sale_format == "wholesale"
            and _cell_is_clean(c.cell)
            and clave in minorista_por_formato
            and c.cell.median > 0
        )
        if es_par_mayorista:
            con_ratio.append(
                replace(c, retail_ratio=minorista_por_formato[clave] / c.cell.median)
            )
        else:
            con_ratio.append(c)

    # Rango de cada combinación por su muestra combinada (desc), con el sin
    # formato/variante al final. `sorted` es estable, así que dentro de cada
    # casillero se conserva el orden por `-count` de `compute_cells`.
    muestra_por_formato: dict[_PairKey, int] = {}
    etiqueta: dict[_PairKey, str] = {}
    for c in con_ratio:
        clave = (c.cell.variant_id, c.cell.format_id, c.cell.unit)
        muestra_por_formato[clave] = muestra_por_formato.get(clave, 0) + c.cell.count
        # Desempate estable y legible: el UUID no sirve como criterio de orden
        # —cambia entre entornos— así que se usa la etiqueta del formato, y el
        # carácter alto manda al final lo que no tiene ninguna.
        etiqueta[clave] = c.cell.format_label or c.cell.unit or "￿"

    rango_formato = {
        clave: i
        for i, clave in enumerate(
            sorted(
                muestra_por_formato,
                key=lambda k: (-muestra_por_formato[k], etiqueta[k]),
            )
        )
    }

    return sorted(
        con_ratio,
        key=lambda c: (
            rango_formato[(c.cell.variant_id, c.cell.format_id, c.cell.unit)],
            _FORMAT_RANK.get(c.cell.sale_format or "", 2),
            -c.cell.count,
        ),
    )


@dataclass(frozen=True)
class SeriesRow:
    """Una fila de lectura de la matriz: la referencia minorista con el
    mayorista de su mismo par como capa secundaria (2026-10-16).

    El precio de referencia del sistema es el minorista
    (`cells.REFERENCE_SALE_FORMAT`), así que la fila lo trae como cifra
    principal y el mayorista del mismo (variante, formato, unidad) va adentro,
    como contexto que se abre a pedido. **No funde nada**: cada serie sigue
    siendo su propia celda de `compute_cells`, con su mediana y su `n`; la
    fila solo decide cuál se lee primero.

    - `primary` es la serie principal: el minorista, una serie con flag o
      una sin `sale_format` declarado (pendiente). `None` cuando el par solo
      tiene mayorista: el mayorista nunca sube a cifra principal, porque no es
      la referencia.
    - `wholesale` son las series mayoristas del par, con su `retail_ratio`
      cuando el minorista limpio existe (`_pair_formats`).
    """

    primary: CellWithBar | None
    wholesale: tuple[CellWithBar, ...] = ()

    @property
    def pending(self) -> bool:
        """`sale_format` sin responder: cola de trabajo, no una tercera escala."""
        return self.primary is not None and self.primary.cell.sale_format is None

    @property
    def wholesale_count(self) -> int:
        return sum(c.cell.count for c in self.wholesale)


def _pair_key(c: CellWithBar) -> _PairKey:
    return (c.cell.variant_id, c.cell.format_id, c.cell.unit)


def build_series_rows(celdas: list[CellWithBar]) -> list[SeriesRow]:
    """Arma las filas de lectura sobre las series ya emparejadas y ordenadas
    por `_pair_formats`, sin reordenar nada.

    El ancla de cada par es su minorista limpio —el que define el ratio—; si
    no hay, el primer minorista del par. Un mayorista sin ancla abre su propia
    fila sin cifra principal, y los demás mayoristas del mismo par se suman a
    esa misma fila. Lo demás (minoristas con flag, sin `sale_format`) es una
    fila por serie, como hasta ahora.
    """
    anclas: dict[_PairKey, CellWithBar] = {}
    for c in celdas:
        if c.cell.sale_format != "retail":
            continue
        clave = _pair_key(c)
        actual = anclas.get(clave)
        if actual is None or (_cell_is_clean(c.cell) and not _cell_is_clean(actual.cell)):
            anclas[clave] = c

    primarias: list[CellWithBar | None] = []
    capas: list[list[CellWithBar]] = []
    fila_de: dict[_PairKey, int] = {}
    for c in celdas:
        clave = _pair_key(c)
        if c.cell.sale_format == "wholesale":
            if clave not in fila_de:
                # Sin ancla minorista que la reciba: fila propia, sin cifra
                # principal. Si el ancla todavía no salió, se reserva su
                # lugar y la recibe cuando llegue.
                fila_de[clave] = len(primarias)
                primarias.append(None)
                capas.append([])
            capas[fila_de[clave]].append(c)
            continue
        if anclas.get(clave) is c and clave in fila_de:
            primarias[fila_de[clave]] = c
            continue
        if anclas.get(clave) is c:
            fila_de[clave] = len(primarias)
        primarias.append(c)
        capas.append([])

    return [SeriesRow(primary=p, wholesale=tuple(w)) for p, w in zip(primarias, capas, strict=True)]


def group_cells(cells: list[Cell]) -> list[CellGroup]:
    """Agrupa por producto × provincia, con los grupos de mayor muestra primero.

    Dentro de cada grupo las series se emparejan por formato (ver
    `_pair_formats`): la minorista y la mayorista de una misma unidad quedan
    adyacentes y la mayorista trae el ratio contra su minorista. El orden por
    tamaño de muestra que trae `compute_cells` se conserva dentro de cada
    casillero (unidad, formato).

    La provincia entra en la clave del grupo: fundir el Arroz de dos provincias
    bajo un mismo encabezado afirmaría una comparación que nadie pidió, y es
    justamente la separación que la matriz existe para sostener.
    """
    if not cells:
        return []

    # La amplitud mayor de la tabla fija la escala de todas las barras.
    amplitudes = [
        Decimal(cell.maximum) / Decimal(cell.minimum) for cell in cells if cell.minimum > 0
    ]
    max_spread = max(amplitudes, default=Decimal(1))

    grouped: dict[tuple[str, str | None], list[CellWithBar]] = {}
    for cell in cells:
        clave = (cell.product_name, cell.province_name)
        grouped.setdefault(clave, []).append(
            CellWithBar(
                cell=cell,
                bar=build_range_bar(
                    minimum=cell.minimum,
                    median=cell.median,
                    maximum=cell.maximum,
                    max_spread=max_spread,
                ),
            )
        )

    grupos = []
    for (product_name, province_name), celdas in grouped.items():
        emparejadas = _pair_formats(celdas)
        grupos.append(
            CellGroup(
                product_name=product_name,
                province_name=province_name,
                cells=emparejadas,
                total=sum(c.cell.count for c in celdas),
                has_undeclared_variants=(
                    any(c.cell.variant_id is not None for c in celdas)
                    and any(c.cell.variant_id is None for c in celdas)
                ),
                rows=tuple(build_series_rows(emparejadas)),
            )
        )

    # Mismo criterio que `compute_cells`: lo que tiene muestra primero, con
    # desempates explícitos para que el orden no lo decida el hash del dict.
    grupos.sort(key=lambda g: (-g.total, g.product_name, g.province_name or ""))
    return grupos


# --- Pivote a grilla producto × provincia --------------------------------------


@dataclass(frozen=True)
class ProvinceColumn:
    """Una columna de la grilla: la provincia, su ranura de color y su muestra.

    `total` es la muestra de la provincia entera, sumando todos los productos.
    Va acá y no en la plantilla porque es lo que rotula la pestaña, y una
    pestaña tiene que decir cuánto hay del otro lado antes de que la abras.
    """

    name: str | None
    label: str
    slot: int
    total: int


@dataclass(frozen=True)
class MatrixCell:
    """El cruce producto × provincia. Puede venir vacío: eso es un hueco."""

    column: ProvinceColumn
    cells: list[CellWithBar]
    total: int
    # Propagado desde `CellGroup.has_undeclared_variants` (design.md D6): el
    # pivote no puede perder este hecho de grupo, es lo único de donde
    # `control.html` puede leerlo para el encabezado del cruce.
    has_undeclared_variants: bool = False
    #: Propagado desde `CellGroup.rows`: las filas de lectura del cruce.
    rows: tuple[SeriesRow, ...] = ()


@dataclass(frozen=True)
class ProductRow:
    """Un producto con una entrada por cada columna de la matriz, hueco incluido.

    `columns` es lo que se dibuja; `tally` es el reparto por provincia que va
    en la cabecera de la tarjeta. Son campos distintos porque con pestañas
    dejan de coincidir: se dibuja una provincia sola, pero la cabecera de una
    tarjeta cerrada tiene que seguir diciendo cuánto hay en **todas** — si no,
    la pestaña esconde el reparto y no queda forma de saber dónde está la
    muestra sin ir pestaña por pestaña.
    """

    product_name: str
    total: int
    columns: list[MatrixCell]
    tally: list[MatrixCell]


@dataclass(frozen=True)
class Matrix:
    columns: list[ProvinceColumn]
    rows: list[ProductRow]


def _province_slot(name: str | None, otras: list[str]) -> int:
    """Ranura de color, asignada por catálogo y no por los datos."""
    if name is None:
        return UNKNOWN_PROVINCE_SLOT
    if name in PHASE0_PROVINCES:
        return PHASE0_PROVINCES.index(name) % _PROVINCE_SLOTS
    return (len(PHASE0_PROVINCES) + otras.index(name)) % _PROVINCE_SLOTS


def _build_columns(totales: dict[str | None, int]) -> list[ProvinceColumn]:
    """Las del piloto en el orden de la especificación, el resto detrás.

    El orden no lo deciden los datos: si lo decidiera la muestra, las columnas
    se reacomodarían solas de un día para el otro y el operador tendría que
    releer los encabezados en cada carga.
    """
    nombres = set(totales)
    otras = sorted(n for n in nombres if n is not None and n not in PHASE0_PROVINCES)
    ordenadas: list[str | None] = [p for p in PHASE0_PROVINCES if p in nombres]
    ordenadas += otras
    if None in nombres:
        ordenadas.append(None)

    return [
        ProvinceColumn(
            name=nombre,
            label=nombre or "sin provincia",
            slot=_province_slot(nombre, otras),
            total=totales[nombre],
        )
        for nombre in ordenadas
    ]


def build_matrix(cells: list[Cell]) -> Matrix:
    """Grilla producto × provincia, con las series y sus barras ya calculadas.

    Reutiliza `group_cells`, así que la escala de las barras sigue siendo la de
    toda la tabla: el pivote es presentación y no toca la normalización.
    """
    grupos = group_cells(cells)
    if not grupos:
        return Matrix(columns=[], rows=[])

    # La muestra de cada provincia, sumando todos los productos: es lo que
    # rotula su pestaña.
    totales: dict[str | None, int] = {}
    for grupo in grupos:
        totales[grupo.province_name] = totales.get(grupo.province_name, 0) + grupo.total

    columnas = _build_columns(totales)

    por_producto: dict[str, dict[str | None, CellGroup]] = {}
    for grupo in grupos:
        por_producto.setdefault(grupo.product_name, {})[grupo.province_name] = grupo

    def _cruce(columna: ProvinceColumn, por_provincia: dict[str | None, CellGroup]) -> MatrixCell:
        """El cruce producto × provincia, vacío cuando no hay ninguna serie.

        Devolverlo vacío en vez de omitirlo es la ganancia del pivote: un hueco
        de cobertura se ve, en vez de tener que deducirlo de una ausencia.
        """
        grupo = por_provincia.get(columna.name)
        if grupo is None:
            return MatrixCell(column=columna, cells=[], total=0)
        return MatrixCell(
            column=columna,
            cells=grupo.cells,
            total=grupo.total,
            has_undeclared_variants=grupo.has_undeclared_variants,
            rows=grupo.rows,
        )

    filas = []
    for product_name, por_provincia in por_producto.items():
        cruces = [_cruce(columna, por_provincia) for columna in columnas]
        filas.append(
            ProductRow(
                product_name=product_name,
                total=sum(g.total for g in por_provincia.values()),
                columns=cruces,
                # Sin recortar, lo que se dibuja y el reparto son lo mismo.
                tally=cruces,
            )
        )

    # Mismo criterio de siempre: lo que tiene muestra primero. Acá la muestra
    # del producto suma sus provincias, porque la fila es el producto.
    filas.sort(key=lambda f: (-f.total, f.product_name))
    return Matrix(columns=columnas, rows=filas)


# --- Recorte a una provincia (pestañas) ----------------------------------------


def resolve_column(matrix: Matrix, raw: str | None) -> ProvinceColumn | None:
    """Qué provincia mira la vista, a partir del parámetro de la URL.

    Nada de acá puede reventar la página: un parámetro raro —una URL vieja, un
    nombre tipeado a mano— cae en la primera provincia y no en un 500.

    `raw is None` (parámetro ausente) y `raw == ""` no son lo mismo. La cadena
    vacía es cómo se nombra «sin provincia» en los enlaces al historial que ya
    existen, así que la pestaña usa el mismo lenguaje en vez de inventar otro.
    """
    if not matrix.columns:
        return None
    if raw is None:
        return matrix.columns[0]
    for columna in matrix.columns:
        if (columna.name or "") == raw:
            return columna
    return matrix.columns[0]


def select_province(matrix: Matrix, column: ProvinceColumn) -> Matrix:
    """La matriz recortada a una sola provincia, para la pestaña activa.

    Las filas se reordenan por la muestra **de esa provincia**: conservar el
    orden por el total de las dos pondría arriba de la pestaña de La Habana un
    producto que en La Habana casi no tiene nada.

    Un producto sin series en esta provincia **no se esconde**: queda con su
    cruce vacío y cae al final por el propio orden. Esconderlo sería volver a
    la matriz apilada, donde un hueco de cobertura no se veía porque la
    combinación simplemente no aparecía.

    No se recalcula ninguna barra. Recortar es presentación: si cada pestaña
    renormalizara sola, la misma serie se vería de un ancho en una pestaña y de
    otro en la de al lado.
    """
    filas = [
        ProductRow(
            product_name=fila.product_name,
            total=cruce.total,
            columns=[cruce],
            # El reparto se conserva entero, no recortado: es lo que deja que
            # una tarjeta cerrada siga diciendo cuánto hay en la otra pestaña.
            tally=fila.tally,
        )
        for fila in matrix.rows
        for cruce in fila.columns
        if cruce.column.name == column.name
    ]
    filas.sort(key=lambda f: (-f.total, f.product_name))
    return Matrix(columns=[column], rows=filas)
