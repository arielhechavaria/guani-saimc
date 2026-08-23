"""Agregación por celda producto × provincia × moneda × unidad × formato.

Es el instrumento de control del operador: responde «¿lo que estoy cargando
sirve?» sin tener que consultar la base a mano. La matriz producto × provincia
es la salida que la especificación define como núcleo de la propuesta (§5, «objeto
medido»), así que esta agregación es también el primer paso hacia el dashboard
público de la Fase 1.

**Reporta mediciones, no veredictos.** No hay umbral de muestra mínima ni
marca de «dispersión alta»: §12.6 es explícito en que un umbral
asignado por intuición y presentado como metodología es peor que no tener
umbral. N se calibra con datos reales —«análisis de estabilidad de la mediana
frente al tamaño de muestra»— y esta vista es justamente lo que permite
hacerlo. Quien juzga es el operador.

La moneda **ya no** entra en la clave de la celda fundida (2026-08-13):
`price_cup` se llena desde §6.3 E, así que una fila en USD con conversión
resuelta se funde con las filas en CUP del mismo producto/provincia/unidad/
formato, usando `price_cup` en vez de `price`. Lo que sigue separado es la
fila cuya conversión **no** se resolvió (`currency != CUP` y `price_cup IS
NULL`): fundirla a ciegas asumiendo que ya está en CUP inventaría un valor,
así que queda en su propia celda, con la moneda original en la clave y
`Cell.unconverted = True` — mismo criterio que el nulo de `sale_format`
descrito más abajo: lo que falta resolver no se mezcla con lo resuelto, se
deja visible aparte.

`unit` y `sale_format` entran por el mismo motivo. Un precio por libra y uno por
kilo no son la misma serie, y un saco mayorista no es comparable con la libra
suelta aunque ambos digan `lb`. Fundirlos produce una mediana que no describe
nada — el caso real del 2026-08-12: `Arroz` en Santiago mezclaba 730 por libra,
750 por kilo y dos sacos de 110 lb en una sola celda.

Ambas columnas son nulables, y el nulo **no se funde con nada**: «sin responder»
es su propia celda. Tratarlo como un valor más afirmaría una equivalencia que
nadie declaró, que es el mismo criterio por el que `canonical_unit()` devuelve
`None` en vez de adivinar. La contracara es que mientras el operador no responda
`sale_format` la cola de nulos crece — y eso es justamente lo que la vista debe
dejar ver, no esconder.

**El precio que se agrega es por unidad, no total, y la división ya no es
incondicional** (2026-08-13, corregido 2026-08-21). Hasta el 2026-08-13 la
regla era dividir siempre por `quantity`; eso mismo era el bug: una fila
`price=340, quantity=340` («Arroz guyanés a 340 libras», `price_basis`
todavía no existía) se dividía igual, y agregaba 1 CUP/lb en vez de ~340. La
división depende de `price_basis`: `'unit'` deja `price` como está —ya es
por unidad—, `'total'` lo divide por `quantity`, y sin resolver (`NULL` con
`quantity != 1`) no se decide en absoluto, queda en su propia celda (ver
`unresolved_basis` abajo). Con `quantity == 1` la pregunta es aritméticamente
inerte —unitario y total son el mismo número— y no depende de `price_basis`.
La moneda se resuelve **antes** de esta decisión; al revés daría un precio
unitario en la moneda de origen.

Una fila sin `quantity` (o con cantidad cero) no se puede normalizar, y
tampoco se funde asumiendo cantidad 1 —eso sería inventar el dato—: queda en
su propia celda con `Cell.unquantified = True`, agregando totales. Mismo
criterio que el nulo de `sale_format` y que la celda sin convertir.

Una fila con `quantity != 1` cuya `price_basis` es `NULL` tampoco se puede
normalizar, pero por un motivo distinto: no falta la cantidad, falta saber
qué representa el precio. Asumir `'total'` para resolverla es exactamente el
bug de 2026-08-13; en cambio queda en su propia celda con
`Cell.unresolved_basis = True`, agregando el total sin dividir — igual que
`unquantified`, visible y nunca fundida con lo resuelto.

El envase (`packaging`) **no** entra en la clave. Cuando se escribió esta regla
el motivo era que fuese texto libre; desde el 2026-08-12 tiene vocabulario
cerrado (Paquete / Saco / A granel / Otro), así que el motivo hoy es otro y más
fuerte: **normalizado por cantidad, el envase dejó de ser un eje que separe
series**. Un saco y un paquete del mismo producto por la misma unidad son
comparables una vez que el precio es por kg; lo que los distingue —comprar al
por mayor sale más barato— es economía real y ya lo captura `sale_format`.
Agrupar por envase multiplicaría las celdas sin separar nada, y encima
produciría una celda por variante de redacción mientras convivan las filas
viejas de texto libre con las nuevas.

`compute_retail_ranges` es la otra mitad del módulo: responde «¿en qué rango
se movió este producto en los últimos N días?», donde el envase deja de
importar porque el producto sigue siendo uno solo. Ver su docstring para los
cuatro filtros que hacen que esa pregunta tenga una respuesta comparable —
incluido el de compra mínima, el único que existe para proteger al lector del
dashboard y no a la comparabilidad de la serie.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from saimc.models import Currency, PriceHistory, Product, Province, SaleFormat
from saimc.units import canonical_unit, convert_price_per_unit


@dataclass(frozen=True)
class Cell:
    """Una celda de la matriz, con su muestra ya resumida."""

    product_name: str
    province_name: str | None
    currency: str
    unit: str | None
    sale_format: str | None
    count: int
    median: Decimal
    minimum: Decimal
    maximum: Decimal
    # `True` cuando la celda quedó separada por no tener `price_cup` resuelto
    # (moneda != CUP sin tasa) — ver docstring del módulo. Con default para no
    # romper construcciones posicionales existentes.
    unconverted: bool = False
    # `True` cuando la celda agrega precios TOTALES en vez de precios por
    # unidad, porque sus filas no tienen `quantity` (2026-08-13). Quien lea
    # `median` necesita saber cuál de las dos cosas está mirando.
    unquantified: bool = False
    # `True` cuando la fila tiene `quantity != 1` pero `price_basis` en NULL:
    # no se sabe si `price` es por unidad o por el lote (2026-08-21). Motivo
    # distinto de `unquantified` (ahí falta la cantidad; acá falta saber qué
    # representa el precio) — asumir 'total' para resolverla sería el mismo
    # bug que este campo corrige (ver docstring del módulo).
    unresolved_basis: bool = False

    @property
    def spread(self) -> Decimal | None:
        """Cuántas veces el máximo contiene al mínimo.

        Es la señal que delata una celda que mezcla cosas distintas —precio
        minorista por libra contra saco mayorista, por ejemplo— sin necesidad
        de que el sistema decida qué es «demasiado». `None` cuando el mínimo
        es cero, que no debería pasar pero no vale la pena que rompa la vista.
        """
        if self.minimum == 0:
            return None
        return self.maximum / self.minimum


def compute_cells(session: Session) -> list[Cell]:
    """Resume `prices_history` en celdas, de mayor a menor tamaño de muestra.

    El orden es deliberado: lo que tiene muestra aparece primero, y la cola
    larga de celdas con una sola observación queda a la vista al final. Esa
    cola es en sí misma el diagnóstico de dispersión de productos.
    """
    stmt = (
        select(
            Product.name,
            Province.name,
            PriceHistory.currency,
            PriceHistory.unit,
            PriceHistory.sale_format,
            PriceHistory.quantity,
            PriceHistory.price,
            PriceHistory.price_cup,
            PriceHistory.price_basis,
        )
        .join(Product, Product.id == PriceHistory.product_id)
        .outerjoin(Province, Province.id == PriceHistory.province_id)
    )

    # `unconverted`, `unquantified` y `unresolved_basis` entran en la clave
    # para separar, sin ambigüedad, el grupo resuelto del que quedó
    # pendiente: moneda, cantidad y basis respectivamente. Mezclar cualquiera
    # de los tres afirmaría una equivalencia que nadie declaró.
    CellKey = tuple[str, str | None, str, str | None, str | None, bool, bool, bool]
    grouped: dict[CellKey, list[Decimal]] = {}
    for (
        product_name,
        province_name,
        currency,
        unit,
        sale_format,
        quantity,
        price,
        price_cup,
        price_basis,
    ) in session.execute(stmt):
        if currency == Currency.CUP.value:
            # Ya está en CUP: nada que convertir.
            effective_price = price
            key_currency, unconverted = Currency.CUP.value, False
        elif price_cup is not None:
            # Conversión resuelta (§6.3 E): se funde con el grupo CUP.
            effective_price = price_cup
            key_currency, unconverted = Currency.CUP.value, False
        else:
            # Sin tasa resuelta todavía: no se funde a ciegas, celda aparte.
            effective_price = price
            key_currency, unconverted = currency, True

        # La moneda se resuelve primero y la cantidad/basis después: dividir
        # antes de convertir daría un precio unitario en la moneda de origen,
        # que no es comparable con el resto de la celda.
        unquantified = quantity is None or quantity == 0
        unresolved_basis = False
        if not unquantified:
            if quantity == 1:
                pass  # unitario y total son el mismo número: nada que dividir
            elif price_basis == "unit":
                pass  # el precio YA es por unidad
            elif price_basis == "total":
                effective_price = effective_price / quantity
            else:
                # No es «falta cantidad» (eso es `unquantified`): es «no se
                # sabe si dividir». Asumir `total` es exactamente el bug de
                # 2026-08-13 que este campo corrige.
                unresolved_basis = True

        key: CellKey = (
            product_name,
            province_name,
            key_currency,
            unit,
            sale_format,
            unconverted,
            unquantified,
            unresolved_basis,
        )
        grouped.setdefault(key, []).append(effective_price)

    cells = [
        Cell(
            product_name=product_name,
            province_name=province_name,
            currency=currency,
            unit=unit,
            sale_format=sale_format,
            count=len(prices),
            # La mediana se calcula en Python y no en SQL a propósito:
            # `percentile_cont` es de PostgreSQL y los tests corren sobre
            # SQLite (ver tests/conftest.py). Con el volumen de la Fase 0 la
            # diferencia de costo es irrelevante.
            median=statistics.median(prices),
            minimum=min(prices),
            maximum=max(prices),
            unconverted=unconverted,
            unquantified=unquantified,
            unresolved_basis=unresolved_basis,
        )
        for (
            product_name,
            province_name,
            currency,
            unit,
            sale_format,
            unconverted,
            unquantified,
            unresolved_basis,
        ), prices in grouped.items()
    ]

    # Los desempates son explícitos porque partir la celda multiplica los
    # empates de `count`: sin ellos el orden lo decidiría el hash del dict y la
    # vista cambiaría de forma entre recargas sin que nada haya cambiado.
    # `unconverted` va al final de la tupla: en un empate de todo lo demás,
    # la celda sin convertir queda después de la fundida, no es un requisito
    # duro pero ordena la lectura de mayor a menor confianza en el dato.
    cells.sort(
        key=lambda cell: (
            -cell.count,
            cell.product_name,
            cell.province_name or "",
            cell.unit or "",
            cell.sale_format or "",
            cell.unconverted,
            cell.unquantified,
            cell.unresolved_basis,
        )
    )
    return cells


@dataclass(frozen=True)
class RetailRange:
    """Rango de precio **minorista** de un producto, normalizado por unidad.

    A diferencia de `Cell`, esta fila ya no distingue envase ni formato: es
    justamente lo que colapsa `compute_retail_ranges` una vez que descarta lo
    mayorista y lo no comparable. `unit` queda igual en la salida porque es la
    unidad canónica del producto, no un eje de agrupación — todas las filas de
    una misma `RetailRange` ya están en esa unidad.
    """

    product_name: str
    province_name: str | None
    currency: str
    unit: str
    count: int
    median: Decimal
    minimum: Decimal
    maximum: Decimal
    # Mismo criterio que `Cell.unconverted`: `True` cuando la fila quedó
    # separada por no tener `price_cup` resuelto.
    unconverted: bool = False


@dataclass(frozen=True)
class RetailRangeReport:
    """Los rangos y lo que quedó afuera por no ser comprable.

    El conteo no vive en `RetailRange` porque no es propiedad de ningún
    producto en particular: es propiedad del cómputo. Y no se omite, porque un
    filtro que el operador no ve es un filtro que no puede calibrar — mismo
    criterio que hace visibles `unconverted` y `unquantified`.
    """

    ranges: list[RetailRange]
    excluded_by_minimum: int
    # Filas con `quantity != 1` y `price_basis` sin resolver, que los otros
    # tres filtros habrían admitido — conteo separado del de compra mínima
    # para que el operador priorice cuál cola de curaduría atacar primero
    # (2026-08-21).
    excluded_by_basis: int
    # Filas cuya unidad no es convertible a la canónica del producto: `caja`,
    # que no declara cuánto contiene, y las magnitudes distintas (masa a
    # conteo, masa a volumen). Antes del 2026-08-22 esta cola incluía todo el
    # kg de un producto declarado en lb y se descartaba en silencio; ahora la
    # conversión la vació de casi todo y lo que queda señala catálogo mal
    # declarado, no dato malo.
    excluded_by_unit: int


def compute_retail_ranges(
    session: Session, *, days: int = 7, hoy: date | None = None
) -> RetailRangeReport:
    """Rango de precio minorista por producto, últimos `days` días.

    Es la otra mitad de `compute_cells`: esa vista separa por formato para que
    el operador vea si está mezclando cosas distintas al cargar; esta vista
    asume que ya lo revisó y responde una pregunta distinta — «¿en qué rango
    se movió Arroz esta semana?» — donde el producto es uno solo aunque el
    envase varíe (2026-08-12).

    Tres filtros, todos deliberados y ninguno inventado:

    - **Solo `sale_format == "retail"`.** Normalizar por cantidad no vuelve
      comparable un saco con un paquete: el caso real medido el 2026-08-12 da
      ~2.5x de diferencia por libra entre mayorista y minorista, que es
      economía real, no ruido de carga. Mezclarlos reproduciría el mismo
      problema que separar por `sale_format` en `compute_cells` resolvió. El
      precio que le importa al hogar —el usuario que define spec.md §8.1
      Nivel 0— es el minorista.
    - **El precio se lleva a la unidad canónica del producto** (`Product.unit`,
      pasada por `canonical_unit()` porque el seed la guarda sin canonicalizar:
      "litro", no "l"). Hasta el 2026-08-22 esta vista **exigía** que la unidad
      de la fila ya coincidiera y descartaba el resto, citando §12.6. Era una
      lectura equivocada: §12.6 gobierna umbrales de juicio que deciden qué se
      publica y que la especificación manda calibrar contra datos reales, y una
      equivalencia entre unidades no es ninguna de esas cosas — la libra son
      0,45359237 kg exactos por definición. El filtro costaba el 65% de las
      filas de productos bandera (`decisions.md`, 2026-08-22). Hoy
      `convert_price_per_unit()` la convierte cuando comparten magnitud, y solo
      queda afuera lo que no es convertible ni en principio: `caja`, que no
      dice cuánto contiene, y las magnitudes distintas (masa a conteo, masa a
      volumen). Ese descarte ahora se cuenta en `excluded_by_unit` en vez de
      ser silencioso, y la fila sigue visible en `compute_cells`.
    - **Requiere `quantity`.** Sin cantidad no hay precio por unidad que
      calcular.
    - **La fila tiene que ser comprable en la cantidad que cotiza**
      (2026-08-14): si `min_purchase_quantity` supera a `quantity`, el precio
      es real pero no al volumen que la fila declara — el caso de la paca
      («Pacas de 30 paquetes, $730 por unidad»), donde 730 por kg no se
      consigue comprando un kilo. Es una comparación estructural entre dos
      columnas de la misma fila, **nunca un umbral**: §12.6 prohíbe fijar un
      corte por intuición, así que «cuánto dinero es demasiado para un hogar»
      es justamente la pregunta que este filtro no contesta. El saco de 50 kg
      con mínimo de un saco sigue adentro, porque comprar un saco entero es
      una transacción minorista real (2026-08-12). El remedio para la paca es
      cargarla como el lote que es (`quantity=30`, `price=21900`), que vuelve
      a entrar con el mismo 730 por kg: el filtro corrige cómo se anota la
      observación, no la borra.

    La fecha de referencia es `published_date` cuando existe; si no, el día en
    que se cargó la fila (`created_at`) — mismo criterio de repliegue que ya
    usa `guess_published_date` en otras partes del sistema. Ambas quedan en
    huso UTC sin ajustar a hora de Cuba: al ritmo de curaduría de la Fase 0
    (~1h diaria) un corrimiento de horas no cambia a qué día cae la fila.
    """
    report = compute_retail_observations(session, days=days, hoy=hoy)

    # Mismo criterio de fusión que `compute_cells` (ver su docstring):
    # `unconverted` en la clave separa sin ambigüedad el grupo fundido del
    # que quedó sin tasa resuelta.
    RangeKey = tuple[str, str | None, str, str, bool]
    grouped: dict[RangeKey, list[Decimal]] = {}
    for observation in report.observations:
        key: RangeKey = (
            observation.product_name,
            observation.province_name,
            observation.currency,
            observation.unit,
            observation.unconverted,
        )
        grouped.setdefault(key, []).append(observation.unit_price)

    ranges = [
        RetailRange(
            product_name=product_name,
            province_name=province_name,
            currency=currency,
            unit=unit,
            count=len(unit_prices),
            median=statistics.median(unit_prices),
            minimum=min(unit_prices),
            maximum=max(unit_prices),
            unconverted=unconverted,
        )
        for (
            product_name,
            province_name,
            currency,
            unit,
            unconverted,
        ), unit_prices in grouped.items()
    ]

    ranges.sort(
        key=lambda r: (-r.count, r.product_name, r.province_name or "", r.unconverted)
    )
    return RetailRangeReport(
        ranges=ranges,
        excluded_by_minimum=report.excluded_by_minimum,
        excluded_by_basis=report.excluded_by_basis,
        excluded_by_unit=report.excluded_by_unit,
    )


@dataclass(frozen=True)
class RetailObservation:
    """Una fila minorista ya normalizada: su precio por unidad canónica y su día.

    Es el grano del que sale todo lo demás. `compute_retail_ranges` la agrupa y
    resume; la vista de evolución la necesita fila por fila, porque el gráfico
    dibuja observaciones y no promedios diarios.
    """

    product_name: str
    province_name: str | None
    currency: str
    unit: str
    unconverted: bool
    day: date
    unit_price: Decimal


@dataclass(frozen=True)
class RetailObservationReport:
    """Las observaciones normalizadas y lo que quedó afuera por no ser comprable."""

    observations: list[RetailObservation]
    excluded_by_minimum: int
    excluded_by_basis: int
    excluded_by_unit: int


def compute_retail_observations(
    session: Session, *, days: int = 7, hoy: date | None = None
) -> RetailObservationReport:
    """Normaliza fila por fila el universo minorista, sin agrupar.

    Extraído de `compute_retail_ranges` el 2026-08-23, cuando el gráfico de
    evolución pasó a ser el tercer consumidor de esta misma normalización —
    moneda, `price_basis`, conversión de unidad y los tres filtros. Duplicarla
    una vez más era garantizar que las tres copias se separaran. Los criterios y
    su motivo siguen documentados en `compute_retail_ranges`, que ahora es un
    agrupamiento sobre esta salida.
    """
    cutoff = (hoy or date.today()) - timedelta(days=days)

    stmt = (
        select(
            Product.name,
            Product.unit,
            Province.name,
            PriceHistory.currency,
            PriceHistory.unit,
            PriceHistory.quantity,
            PriceHistory.min_purchase_quantity,
            PriceHistory.price,
            PriceHistory.price_cup,
            PriceHistory.price_basis,
            PriceHistory.published_date,
            PriceHistory.created_at,
        )
        .join(Product, Product.id == PriceHistory.product_id)
        .outerjoin(Province, Province.id == PriceHistory.province_id)
        .where(PriceHistory.sale_format == SaleFormat.RETAIL.value)
        .where(PriceHistory.unit.is_not(None))
        .where(PriceHistory.quantity.is_not(None))
    )

    observations: list[RetailObservation] = []
    excluded_by_minimum = 0
    excluded_by_basis = 0
    excluded_by_unit = 0
    for (
        product_name,
        product_unit,
        province_name,
        currency,
        row_unit,
        quantity,
        min_purchase_quantity,
        price,
        price_cup,
        price_basis,
        published_date,
        created_at,
    ) in session.execute(stmt):
        if quantity == 0:
            continue

        effective_date = published_date or created_at.date()
        if effective_date < cutoff:
            continue

        # Después de los otros filtros a propósito: el conteo cuenta filas que
        # habrían entrado, no todo lo que la consulta trajo.
        if min_purchase_quantity is not None and min_purchase_quantity > quantity:
            excluded_by_minimum += 1
            continue

        # Mismo criterio, después del filtro de compra mínima: una fila con
        # `quantity != 1` y `price_basis` sin resolver no se puede normalizar
        # sin fabricar la respuesta que el operador nunca dio (2026-08-21).
        if quantity != 1 and price_basis not in ("unit", "total"):
            excluded_by_basis += 1
            continue

        if currency == Currency.CUP.value:
            effective_price = price
            key_currency, unconverted = Currency.CUP.value, False
        elif price_cup is not None:
            effective_price = price_cup
            key_currency, unconverted = Currency.CUP.value, False
        else:
            effective_price = price
            key_currency, unconverted = currency, True

        # Con las filas sin resolver ya afuera, las que quedan tienen
        # `quantity == 1` (unitario y total son el mismo número) o
        # `price_basis` resuelto: dividir es la identidad en el primer caso,
        # y en el segundo depende del basis, igual que en `compute_cells`.
        unit_price = (
            effective_price if price_basis == "unit" else effective_price / quantity
        )

        # Último de los filtros, mismo criterio que los dos de arriba: cuenta
        # filas que por todo lo demás habrían entrado. Se convierte el precio
        # **por unidad**, no el crudo, así que tiene que ir después de dividir
        # por la cantidad: al revés convertiría el total del lote.
        target_unit = canonical_unit(product_unit)
        converted = convert_price_per_unit(unit_price, frm=row_unit, to=target_unit)
        if converted is None:
            excluded_by_unit += 1
            continue

        observations.append(
            RetailObservation(
                product_name=product_name,
                province_name=province_name,
                currency=key_currency,
                unit=target_unit,
                unconverted=unconverted,
                day=effective_date,
                unit_price=converted,
            )
        )

    return RetailObservationReport(
        observations=observations,
        excluded_by_minimum=excluded_by_minimum,
        excluded_by_basis=excluded_by_basis,
        excluded_by_unit=excluded_by_unit,
    )


@dataclass(frozen=True)
class BasisSuspect:
    """Una fila `price_basis='unit'` que su propia celda desmiente.

    No afirma que la fila esté mal: afirma que **leída como total encaja mejor
    con el resto de su celda que leída como unitaria**, y muestra los dos
    números para que el operador decida. Quien juzga sigue siendo él.
    """

    product_name: str
    province_name: str | None
    price: Decimal
    quantity: Decimal
    row_unit: str
    unit: str
    #: El precio por unidad si la fila dice la verdad (`basis='unit'`).
    as_unit: Decimal
    #: El precio por unidad si en realidad el precio era el total del lote.
    as_total: Decimal
    #: Mediana de las filas NO ambiguas de la misma celda, contra la que se compara.
    reference: Decimal

    @property
    def ratio_to_reference(self) -> Decimal:
        """Cuántas veces se aparta la lectura declarada. Es la magnitud del error."""
        return self.as_unit / self.reference


def detect_basis_suspects(
    session: Session, *, days: int = 30, hoy: date | None = None
) -> list[BasisSuspect]:
    """Filas cuyo `price_basis='unit'` contradice al resto de su propia celda.

    **Por qué hace falta.** Una fila marcada `'unit'` que en realidad trae el
    total del lote no produce un error proporcional: produce uno del orden del
    factor de conversión de unidad. El caso medido el 2026-08-22 es
    `1,90 USD · 900 ml · basis='unit'` de Aceite — son 1,90 por la botella, pero
    leído como precio por mililitro y convertido a litro da **1.264.260 CUP/l**.
    Una sola fila así envenena el máximo de su celda, y el rango mín–máx es
    justamente lo que §6.7 manda publicar.

    **Cómo decide, y por qué no hay ningún umbral.** Para cada fila candidata se
    calculan las dos lecturas posibles del mismo número —`as_unit` y
    `as_total = price / quantity`—, ambas convertidas a la unidad canónica, y se
    elige la que queda **más cerca en proporción** de la mediana de las filas no
    ambiguas de su misma celda. No hay corte de precio, ni «ningún arroz vale
    más de X», ni un factor mínimo de sospecha: solo se compara cuál de las dos
    interpretaciones concuerda mejor con el propio vecindario de la fila. Eso es
    lo que `docs/backlog.md` pedía —comparación relativa dentro de la celda— y
    lo que §12.6 exige: no se inventa ningún parámetro.

    La distancia se mide en proporción y no en diferencia absoluta porque el
    error es multiplicativo: 30.885 contra 312 y 1.264.260 contra 3.400 son el
    mismo tipo de equivocación a escalas distintas, y una resta los trataría
    como problemas incomparables.

    **Fila no ambigua** es la que no puede estar mal marcada de esta forma:
    `quantity == 1` (unitario y total son el mismo número) o
    `price_basis == 'total'` (el operador ya lo resolvió, y en el otro sentido).
    Si una celda no tiene ninguna, **no hay veredicto**: la fila no se reporta.
    Es el caso de Picadillo, cuyas cuatro filas `'unit'` con `quantity != 1`
    están bien y no tienen contra qué contrastarse — inventarles un juicio sería
    exactamente el error que este diseño evita (`docs/backlog.md`, 2026-08-22).

    Recorre el mismo universo de filas que `compute_retail_ranges` y con los
    mismos criterios de moneda y unidad; lo que cambia es que no agrega, señala.
    """
    cutoff = (hoy or date.today()) - timedelta(days=days)

    stmt = (
        select(
            Product.name,
            Product.unit,
            Province.name,
            PriceHistory.currency,
            PriceHistory.unit,
            PriceHistory.quantity,
            PriceHistory.price,
            PriceHistory.price_cup,
            PriceHistory.price_basis,
            PriceHistory.published_date,
            PriceHistory.created_at,
        )
        .join(Product, Product.id == PriceHistory.product_id)
        .outerjoin(Province, Province.id == PriceHistory.province_id)
        .where(PriceHistory.sale_format == SaleFormat.RETAIL.value)
        .where(PriceHistory.unit.is_not(None))
        .where(PriceHistory.quantity.is_not(None))
    )

    SuspectKey = tuple[str, str | None, str]
    unambiguous: dict[SuspectKey, list[Decimal]] = {}
    candidates: list[tuple[SuspectKey, dict[str, object]]] = []

    for (
        product_name,
        product_unit,
        province_name,
        currency,
        row_unit,
        quantity,
        price,
        price_cup,
        price_basis,
        published_date,
        created_at,
    ) in session.execute(stmt):
        if quantity is None or quantity == 0:
            continue
        if (published_date or created_at.date()) < cutoff:
            continue

        # Solo entra lo que ya está en CUP o tiene la conversión resuelta: una
        # fila sin tasa no es comparable con las de su celda, y mezclarla
        # inventaría la equivalencia que `unconverted` existe para evitar.
        if currency == Currency.CUP.value:
            effective_price = price
        elif price_cup is not None:
            effective_price = price_cup
        else:
            continue

        target_unit = canonical_unit(product_unit)
        key: SuspectKey = (product_name, province_name, target_unit or "")

        if quantity == 1 or price_basis == "total":
            resolved = convert_price_per_unit(
                effective_price if quantity == 1 else effective_price / quantity,
                frm=row_unit,
                to=target_unit,
            )
            if resolved is not None:
                unambiguous.setdefault(key, []).append(resolved)
            continue

        if price_basis != "unit":
            # `None` con `quantity != 1` no es sospechoso, es indeterminado:
            # `compute_retail_ranges` ya lo excluye y cuenta aparte.
            continue

        as_unit = convert_price_per_unit(effective_price, frm=row_unit, to=target_unit)
        as_total = convert_price_per_unit(
            effective_price / quantity, frm=row_unit, to=target_unit
        )
        if as_unit is None or as_total is None:
            continue

        candidates.append(
            (
                key,
                {
                    "product_name": product_name,
                    "province_name": province_name,
                    "price": price,
                    "quantity": quantity,
                    "row_unit": row_unit,
                    "unit": target_unit or row_unit,
                    "as_unit": as_unit,
                    "as_total": as_total,
                },
            )
        )

    suspects = []
    for key, row in candidates:
        sample = unambiguous.get(key)
        if not sample:
            continue  # sin referencia no hay veredicto
        reference = statistics.median(sample)
        if reference <= 0:
            continue

        as_unit: Decimal = row["as_unit"]  # type: ignore[assignment]
        as_total: Decimal = row["as_total"]  # type: ignore[assignment]
        # Distancia proporcional, simétrica: para un valor y su referencia da lo
        # mismo estar 10 veces por encima que 10 veces por debajo.
        if _proportional_distance(as_total, reference) < _proportional_distance(
            as_unit, reference
        ):
            suspects.append(BasisSuspect(reference=reference, **row))  # type: ignore[arg-type]

    suspects.sort(key=lambda s: -s.ratio_to_reference)
    return suspects


def _proportional_distance(value: Decimal, reference: Decimal) -> Decimal:
    """Cuántas veces se apartan uno de otro, mirado desde el lado que más da.

    `max(a/b, b/a)`: evita `log()` —que forzaría pasar por `float` y perder la
    exactitud de `Decimal` justo en la comparación que decide— y es monótona
    respecto del valor absoluto del logaritmo, que es lo único que se necesita
    para ordenar dos candidatas.
    """
    if value <= 0:
        return Decimal("Infinity")
    return max(value / reference, reference / value)
