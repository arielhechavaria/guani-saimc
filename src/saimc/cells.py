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

La resolución de cada fila cruda a su identidad efectiva —precio a CUP y por
unidad canónica, más las cuatro marcas de lo pendiente— vive en `resolve_row`,
no acá dentro: `/curation/history` la necesita fila por fila para aislar «los
anuncios detrás de este número» de una celda fundida, y dos copias de esta
lógica se desincronizarían.

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

`sale_format` entra en la clave: un saco mayorista no es comparable con la libra
suelta aunque ambos digan `lb`, y fundirlos produce una mediana que no describe
nada — el caso real del 2026-08-12: `Arroz` en Santiago mezclaba 730 por libra,
750 por kilo y dos sacos de 110 lb en una sola celda.

`unit` **ya no** separa kg de lb (2026-09-02). Hasta entonces esta vista era
«control de carga» y los mantenía aparte a propósito; el operador pidió lo
contrario: para leer cómo se movió un producto, la diferencia de formato de
anuncio es ruido. Cada fila se lleva ahora a la unidad canónica del producto
(`Product.unit`, pasada por `canonical_unit()`) con `convert_price_per_unit()`,
que es el mismo camino que ya recorren `compute_retail_ranges` y
`compute_retail_observations`. La conversión va **después** de dividir por
`quantity` —convertir el total del lote daría otro número— y solo se intenta
en la fila que quedó resuelta: la que no tiene cantidad o basis se queda con su
unidad cruda, porque sin un precio por unidad confiable no hay nada que
convertir. Lo que no convierte ni en principio —`caja`, que no dice cuánto
contiene, o una magnitud distinta a la del catálogo— conserva su unidad y se
marca `Cell.unconverted_unit`: misma idea que la exclusión de
`compute_retail_ranges`, pero visible como celda en vez de contada aparte.

`sale_format` es nulable y el nulo **no se funde con nada**: «sin responder» es
su propia celda. Tratarlo como un valor más afirmaría una equivalencia que
nadie declaró, que es el mismo criterio por el que `canonical_unit()` devuelve
`None` en vez de adivinar. Una fila sin `unit` cae en la misma lógica: su celda
tiene `unit = None` y **no** se marca `unconverted_unit` —es «sin unidad», no
«no se pudo convertir»—. La contracara es que mientras el operador no responda
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
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from saimc.formats import format_label
from saimc.models import (
    ContentType,
    Currency,
    PriceHistory,
    Product,
    ProductFormat,
    ProductVariant,
    Province,
    RawIngestion,
    SaleFormat,
)
from saimc.units import canonical_unit, convert_price_per_unit, convert_quantity


class _FormatPriced:
    """Precio del formato para un agregado ya resumido por unidad canónica.

    Lo comparten `Cell` (Matriz interna) y `RetailRange` (tarjetas públicas):
    los dos guardan `median`/`minimum`/`maximum` como precio por unidad base y
    la terna `(format_packaging, format_quantity, format_unit)` del formato de
    venta. El titular publicado es el precio del formato; el precio por unidad
    base no se reemplaza, se degrada a **comparador entre formatos** — la
    respuesta a «¿me conviene la caja o la libra?» (design.md D8).

    Espera esos atributos por composición de dataclass; no define campos.
    """

    unit: str | None
    median: Decimal
    minimum: Decimal
    maximum: Decimal
    format_packaging: str | None
    format_quantity: Decimal | None
    format_unit: str | None

    @property
    def format_label(self) -> str | None:
        """«caja de 40 lb», o `None` sin formato. Es lo que la persona compra."""
        if (
            self.format_packaging is None
            or self.format_quantity is None
            or self.format_unit is None
        ):
            return None
        return format_label(self.format_packaging, self.format_quantity, self.format_unit)

    @property
    def _format_factor(self) -> Decimal | None:
        """La cantidad del formato expresada en la unidad del agregado.

        La normalización deja el precio por unidad canónica del producto; para
        volver al precio del formato hace falta saber cuántas de esas unidades
        trae. Un formato en kg sobre un producto canónico en lb necesita la
        conversión (10 kg = 22,046 lb); si no es convertible —`caja`, o
        magnitudes distintas— no hay precio de formato que derivar.
        """
        if self.format_quantity is None:
            return None
        return convert_quantity(self.format_quantity, frm=self.format_unit, to=self.unit)

    def _in_format(self, value: Decimal) -> Decimal | None:
        factor = self._format_factor
        return None if factor is None else value * factor

    @property
    def format_median(self) -> Decimal | None:
        """Mediana expresada como precio del formato. El titular publicado."""
        return self._in_format(self.median)

    @property
    def format_minimum(self) -> Decimal | None:
        return self._in_format(self.minimum)

    @property
    def format_maximum(self) -> Decimal | None:
        return self._in_format(self.maximum)


class _VariantLabeled:
    """Etiqueta de la variante declarada, para un agregado ya resumido.

    Comparten esto `Cell` y `RetailRange`: los dos guardan `variant_id` más
    las tres columnas de display (`variant_kind`/`variant_brand`/
    `variant_origin`). `None` en `variant_id` es «variante no declarada», que
    agrega en su propia celda igual que `format_id = NULL` (design.md D4).

    Espera esos atributos por composición de dataclass; no define campos.
    """

    variant_kind: str | None
    variant_brand: str | None
    variant_origin: str | None

    @property
    def variant_label(self) -> str | None:
        """«girasol Vitalín importado», o `None` sin variante declarada."""
        ejes = [e for e in (self.variant_kind, self.variant_brand, self.variant_origin) if e]
        return " ".join(ejes) if ejes else None


@dataclass(frozen=True)
class Cell(_FormatPriced, _VariantLabeled):
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
    # `True` cuando la fila tiene unidad pero no se pudo llevar a la unidad
    # canónica del producto (2026-09-02): `caja`, que no dice cuánto contiene,
    # o una magnitud distinta a la del catálogo (masa contra conteo). NO se
    # marca la fila sin unidad —esa es «sin unidad», no «no se pudo
    # convertir»— ni la que sí se fundió. Mismo desenlace que la exclusión de
    # `compute_retail_ranges`, pero acá la celda queda visible.
    unconverted_unit: bool = False
    # El formato de venta de la serie: la terna `(envase, cantidad, unidad)`
    # contra la que se agrega. `None` = «formato no establecido», que agrega en
    # su propia celda igual que `unquantified` — nunca se funde con lo resuelto.
    format_id: uuid.UUID | None = None
    format_packaging: str | None = None
    format_quantity: Decimal | None = None
    format_unit: str | None = None
    # La variante declarada (kind/brand/origin): segundo eje de identidad,
    # libre y cruzable con el formato. `None` = «variante no declarada», que
    # agrega en su propia celda igual que `format_id = NULL` (product-variants).
    variant_id: uuid.UUID | None = None
    variant_kind: str | None = None
    variant_brand: str | None = None
    variant_origin: str | None = None

    # --- Composición de la muestra (2026-09-18) ---------------------------
    # `count` cuenta mensajes, y solo se lee como si contara observaciones
    # independientes. Medido contra producción: la celda de Arroz en Santiago
    # tenía 27 mensajes de 2 emisores en 15 días, con el 85% de un solo canal.
    # No son duplicados —`raw_ingestions.content_hash` es único y no hay uno
    # repetido en todo el corpus; `docs/decisions.md` 2026-08-23 ya lo
    # verificó contra este mismo grupo—: son reposteos reales del mismo
    # vendedor, que es dato legítimo y con fecha. Lo que engaña no es la fila,
    # es el número: `n = 27` afirma una densidad que la muestra no tiene.
    #
    # `spec.md` §10.7 C1 pide para la Fase 0 exactamente esto —«informativa:
    # tamaño de muestra visible»— y C4 nombra la señal que estos campos hacen
    # legible: «concentración inusual del aporte por fuente o canal en una
    # celda». Son mediciones, no veredicto: acá no hay umbral de cuántos
    # emisores son pocos (§12.6), lo decide quien mira.
    #
    # Emisores identificados por `raw_ingestions.channel_id`.
    sources: int = 0
    # Filas cuya ingesta no declara canal: no dicen de quién vienen. Contarlas
    # todas como un emisor afirmaría que son el mismo vendedor; contar cada
    # una como emisor propio afirmaría que son distintos. Ninguna de las dos
    # cosas está declarada, así que se cuentan aparte — mismo criterio que
    # `unconverted` y `unquantified`, que no se funden con lo resuelto. Al
    # 2026-09-18 son 106 de 336 filas en producción, así que no es un borde.
    unidentified_sources: int = 0
    # Días distintos en los que la celda tiene alguna observación, con el
    # mismo repliegue que ya usan `compute_retail_observations` y
    # `compute_availability`: `published_date or created_at.date()`.
    days: int = 0

    @property
    def sample_label(self) -> str | None:
        """«2 emisores · 15 días», o `None` sin composición calculada.

        Se arma acá y no en Jinja por el mismo motivo que `variant_label`: una
        cadena construida en la plantilla no se puede testear. No dice nunca
        «0 emisores» —sería falso: no es que no haya vendedor, es que la
        ingesta no declara cuál— ni esconde las filas sin identificar.
        """
        if not self.days and not self.sources and not self.unidentified_sources:
            return None
        if self.sources:
            emisores = f"{self.sources} emisor" + ("es" if self.sources > 1 else "")
            if self.unidentified_sources:
                emisores += f" +{self.unidentified_sources} sin identificar"
        else:
            emisores = f"{self.unidentified_sources} sin emisor identificado"
        dias = f"{self.days} día" + ("s" if self.days > 1 else "")
        return f"{emisores} · {dias}"

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


@dataclass
class _Sample:
    """Lo que se acumula por celda mientras se recorren las filas.

    Antes del 2026-09-18 era una `list[Decimal]` de precios y alcanzaba. Ahora
    la celda también reporta de cuántos emisores y de cuántos días viene su
    muestra (ver los campos de composición en `Cell`), y esos dos no se pueden
    derivar de los precios: hay que verlos fila por fila. Mutable a propósito —
    es un acumulador local de `compute_cells`, no un valor de dominio.
    """

    prices: list[Decimal] = field(default_factory=list)
    channels: set[uuid.UUID] = field(default_factory=set)
    # Conteo y no set: una ingesta sin canal no tiene identidad de emisor que
    # deduplicar. Ver el comentario de `Cell.unidentified_sources`.
    unidentified: int = 0
    days: set[date] = field(default_factory=set)

    def add(
        self, *, price: Decimal, channel_id: uuid.UUID | None, day: date
    ) -> None:
        self.prices.append(price)
        if channel_id is None:
            self.unidentified += 1
        else:
            self.channels.add(channel_id)
        self.days.add(day)


@dataclass(frozen=True)
class ResolvedRow:
    """La identidad efectiva de una fila de `prices_history`: el precio ya
    normalizado a CUP y por unidad canónica, más las cuatro marcas que deciden
    en qué celda cae.

    Es lo que `compute_cells` agrupa. `/curation/history` la recomputa fila por
    fila para responder «los anuncios detrás de este número» sin volver a
    partir esta lógica en dos implementaciones que se desincronizan
    (2026-09-02): antes de fundir kg con lb, el historial filtraba por la
    columna cruda `prices_history.unit` y bastaba; ahora una celda fundida
    abarca varias unidades crudas y solo se puede aislar recorriendo la misma
    resolución.
    """

    effective_price: Decimal
    currency: str
    unit: str | None
    unconverted: bool
    unquantified: bool
    unresolved_basis: bool
    unconverted_unit: bool


def resolve_row(
    *,
    currency: str,
    price: Decimal,
    price_cup: Decimal | None,
    quantity: Decimal | None,
    price_basis: str | None,
    unit: str | None,
    product_unit: str | None,
) -> ResolvedRow:
    """Lleva una fila cruda a su identidad efectiva. El orden importa y está
    fijado por el docstring del módulo: moneda primero, cantidad/basis
    después, unidad al final —se convierte un precio por unidad, así que solo
    tiene sentido cuando ya lo es—.
    """
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

    key_unit = unit
    unconverted_unit = False
    if not unquantified and not unresolved_basis:
        target_unit = canonical_unit(product_unit)
        converted = convert_price_per_unit(effective_price, frm=unit, to=target_unit)
        if converted is not None:
            effective_price = converted
            key_unit = target_unit
        elif unit is not None:
            # Tiene unidad pero no convierte: `caja` o magnitud distinta.
            # `unit is None` no se marca — es «sin unidad», no «sin convertir».
            unconverted_unit = True

    return ResolvedRow(
        effective_price=effective_price,
        currency=key_currency,
        unit=key_unit,
        unconverted=unconverted,
        unquantified=unquantified,
        unresolved_basis=unresolved_basis,
        unconverted_unit=unconverted_unit,
    )


def compute_cells(session: Session) -> list[Cell]:
    """Resume `prices_history` en celdas, de mayor a menor tamaño de muestra.

    El orden es deliberado: lo que tiene muestra aparece primero, y la cola
    larga de celdas con una sola observación queda a la vista al final. Esa
    cola es en sí misma el diagnóstico de dispersión de productos.
    """
    stmt = (
        select(
            Product.name,
            Product.unit,
            Province.name,
            PriceHistory.currency,
            PriceHistory.unit,
            PriceHistory.sale_format,
            PriceHistory.quantity,
            PriceHistory.price,
            PriceHistory.price_cup,
            PriceHistory.price_basis,
            PriceHistory.format_id,
            ProductFormat.packaging,
            ProductFormat.quantity,
            ProductFormat.unit,
            PriceHistory.variant_id,
            ProductVariant.kind,
            ProductVariant.brand,
            ProductVariant.origin,
            # Composición de la muestra: de quién viene y de qué día. El join a
            # `raw_ingestions` es interno porque `source_id` es NOT NULL — toda
            # fila tiene su evidencia (ver el comentario de la columna).
            RawIngestion.channel_id,
            PriceHistory.published_date,
            PriceHistory.created_at,
        )
        .join(Product, Product.id == PriceHistory.product_id)
        .outerjoin(Province, Province.id == PriceHistory.province_id)
        .outerjoin(ProductFormat, ProductFormat.id == PriceHistory.format_id)
        .outerjoin(ProductVariant, ProductVariant.id == PriceHistory.variant_id)
        .join(RawIngestion, RawIngestion.id == PriceHistory.source_id)
    )

    # `unconverted`, `unquantified`, `unresolved_basis` y `unconverted_unit`
    # entran en la clave para separar, sin ambigüedad, el grupo resuelto del
    # que quedó pendiente: moneda, cantidad, basis y unidad respectivamente.
    # Mezclar cualquiera afirmaría una equivalencia que nadie declaró.
    #
    # `format_id` entra por el mismo motivo, y es el eje que este módulo
    # excluía hasta el 2026-09-04: el formato de venta ES la identidad de la
    # serie, no una etiqueta sobre ella (ver docstring del módulo).
    #
    # `variant_id` es el segundo eje de identidad, libre y cruzable con el
    # formato (product-variants): una variante declarada y la genérica del
    # mismo producto nunca se funden.
    CellKey = tuple[
        str,
        str | None,
        str,
        str | None,
        str | None,
        bool,
        bool,
        bool,
        bool,
        uuid.UUID | None,
        str | None,
        Decimal | None,
        str | None,
        uuid.UUID | None,
        str | None,
        str | None,
        str | None,
    ]
    grouped: dict[CellKey, _Sample] = {}
    for (
        product_name,
        product_unit,
        province_name,
        currency,
        unit,
        sale_format,
        quantity,
        price,
        price_cup,
        price_basis,
        format_id,
        format_packaging,
        format_quantity,
        format_unit,
        variant_id,
        variant_kind,
        variant_brand,
        variant_origin,
        channel_id,
        published_date,
        created_at,
    ) in session.execute(stmt):
        row = resolve_row(
            currency=currency,
            price=price,
            price_cup=price_cup,
            quantity=quantity,
            price_basis=price_basis,
            unit=unit,
            product_unit=product_unit,
        )
        key: CellKey = (
            product_name,
            province_name,
            row.currency,
            row.unit,
            sale_format,
            row.unconverted,
            row.unquantified,
            row.unresolved_basis,
            row.unconverted_unit,
            format_id,
            format_packaging,
            format_quantity,
            format_unit,
            variant_id,
            variant_kind,
            variant_brand,
            variant_origin,
        )
        grouped.setdefault(key, _Sample()).add(
            price=row.effective_price,
            channel_id=channel_id,
            day=published_date or created_at.date(),
        )

    cells = [
        Cell(
            product_name=product_name,
            province_name=province_name,
            currency=currency,
            unit=unit,
            sale_format=sale_format,
            count=len(muestra.prices),
            # La mediana se calcula en Python y no en SQL a propósito:
            # `percentile_cont` es de PostgreSQL y los tests corren sobre
            # SQLite (ver tests/conftest.py). Con el volumen de la Fase 0 la
            # diferencia de costo es irrelevante.
            median=statistics.median(muestra.prices),
            minimum=min(muestra.prices),
            maximum=max(muestra.prices),
            sources=len(muestra.channels),
            unidentified_sources=muestra.unidentified,
            days=len(muestra.days),
            unconverted=unconverted,
            unquantified=unquantified,
            unresolved_basis=unresolved_basis,
            unconverted_unit=unconverted_unit,
            format_id=format_id,
            format_packaging=format_packaging,
            format_quantity=format_quantity,
            format_unit=format_unit,
            variant_id=variant_id,
            variant_kind=variant_kind,
            variant_brand=variant_brand,
            variant_origin=variant_origin,
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
            unconverted_unit,
            format_id,
            format_packaging,
            format_quantity,
            format_unit,
            variant_id,
            variant_kind,
            variant_brand,
            variant_origin,
        ), muestra in grouped.items()
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
            cell.format_label or "",
            cell.variant_label or "",
            cell.unconverted,
            cell.unquantified,
            cell.unresolved_basis,
            cell.unconverted_unit,
        )
    )
    return cells


@dataclass(frozen=True)
class RetailRange(_FormatPriced, _VariantLabeled):
    """Rango de precio **minorista** de un formato, normalizado por unidad.

    Hasta el 2026-09-04 no distinguía formato: colapsaba envase y respondía
    «¿en qué rango se movió Arroz?». La pregunta del hogar es por el formato —
    «¿cómo se movió el paquete de arroz?»— así que ahora `format_id` entra a la
    identidad. `unit` sigue siendo la unidad canónica del producto: es la base
    en la que se guarda `median`, y el comparador entre formatos.
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
    format_id: uuid.UUID | None = None
    format_packaging: str | None = None
    format_quantity: Decimal | None = None
    format_unit: str | None = None
    # Mismo criterio que en `Cell`: segundo eje de identidad, libre y
    # cruzable con el formato (product-variants).
    variant_id: uuid.UUID | None = None
    variant_kind: str | None = None
    variant_brand: str | None = None
    variant_origin: str | None = None


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
    session: Session, *, days: int = 7, hoy: date | None = None, hasta: date | None = None
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
    report = compute_retail_observations(session, days=days, hoy=hoy, hasta=hasta)

    # La clave de la celda vive en `retail_cell_key`, con su motivo.
    grouped: dict[RetailCellKey, list[Decimal]] = {}
    for observation in report.observations:
        grouped.setdefault(retail_cell_key(observation), []).append(observation.unit_price)

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
            format_id=format_id,
            format_packaging=format_packaging,
            format_quantity=format_quantity,
            format_unit=format_unit,
            variant_id=variant_id,
            variant_kind=variant_kind,
            variant_brand=variant_brand,
            variant_origin=variant_origin,
        )
        for (
            product_name,
            province_name,
            currency,
            unit,
            unconverted,
            format_id,
            format_packaging,
            format_quantity,
            format_unit,
            variant_id,
            variant_kind,
            variant_brand,
            variant_origin,
        ), unit_prices in grouped.items()
    ]

    ranges.sort(
        key=lambda r: (
            -r.count,
            r.product_name,
            r.province_name or "",
            r.format_label or "",
            r.variant_label or "",
            r.unconverted,
        )
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
    #: `raw_ingestions.id` de la fila que sostiene esta observación —
    #: Transparency Layer pública, `GET /anuncio/{id}` (docs/decisions.md,
    #: entrada de 2026-08-25). `evidence_is_public` es quien decide si vale la
    #: pena armar el enlace: ese endpoint da 404 igual para imagen, así que
    #: acá se resuelve una vez por fila en vez de que cada vista repita el
    #: mismo chequeo de `content_type`.
    evidence_id: uuid.UUID
    evidence_is_public: bool
    #: Formato de venta de la fila, para que `compute_retail_ranges` agrupe por
    #: él. `None` cuando la fila no tiene formato establecido.
    format_id: uuid.UUID | None = None
    format_packaging: str | None = None
    format_quantity: Decimal | None = None
    format_unit: str | None = None
    #: Variante declarada de la fila (product-variants), para que
    #: `compute_retail_ranges` agrupe por ella igual que por el formato.
    #: `None` cuando la fila no tiene variante declarada.
    variant_id: uuid.UUID | None = None
    variant_kind: str | None = None
    variant_brand: str | None = None
    variant_origin: str | None = None
    #: Quién hizo la fila (`prices_history.provenance`). Lo leen los controles
    #: de §10.7 (`validation.control_cells`, slice C3) para separar las filas
    #: que verificó una persona; ninguna vista pública lo usa.
    provenance: str | None = None


#: Identidad de una celda minorista: la clave por la que `compute_retail_ranges`
#: agrupa las observaciones. Es la celda que ve el público.
RetailCellKey = tuple[
    str,
    str | None,
    str,
    str,
    bool,
    uuid.UUID | None,
    str | None,
    Decimal | None,
    str | None,
    uuid.UUID | None,
    str | None,
    str | None,
    str | None,
]


def retail_cell_key(observation: RetailObservation) -> RetailCellKey:
    """La celda minorista de una observación.

    `format_id` entra a la clave: el rango es por formato, no por producto
    (supersede 2026-08-12). `unconverted` sigue separando el grupo fundido del
    que quedó sin tasa, mismo criterio que `compute_cells`. `variant_id` entra
    por el mismo motivo que en `compute_cells`: segundo eje de identidad, libre
    y cruzable con el formato (product-variants).

    Es función aparte desde el slice C3: los controles de §10.7 agrupan por
    esta misma clave, y dos copias se desincronizarían.
    """
    return (
        observation.product_name,
        observation.province_name,
        observation.currency,
        observation.unit,
        observation.unconverted,
        observation.format_id,
        observation.format_packaging,
        observation.format_quantity,
        observation.format_unit,
        observation.variant_id,
        observation.variant_kind,
        observation.variant_brand,
        observation.variant_origin,
    )


@dataclass(frozen=True)
class RetailObservationReport:
    """Las observaciones normalizadas y lo que quedó afuera por no ser comprable."""

    observations: list[RetailObservation]
    excluded_by_minimum: int
    excluded_by_basis: int
    excluded_by_unit: int


def compute_retail_observations(
    session: Session, *, days: int = 7, hoy: date | None = None, hasta: date | None = None
) -> RetailObservationReport:
    """Normaliza fila por fila el universo minorista, sin agrupar.

    Extraído de `compute_retail_ranges` el 2026-08-23, cuando el gráfico de
    evolución pasó a ser el tercer consumidor de esta misma normalización —
    moneda, `price_basis`, conversión de unidad y los tres filtros. Duplicarla
    una vez más era garantizar que las tres copias se separaran. Los criterios y
    su motivo siguen documentados en `compute_retail_ranges`, que ahora es un
    agrupamiento sobre esta salida.

    `hasta` (2026-09-14, date picker de rango de calendario en la home
    pública): tope superior opcional, `None` por default —sin tope, todo lo
    que sea `>= cutoff` entra, comportamiento sin cambios para cada caller
    que no lo pase (`/control`, `compute_availability`, todo lo de antes).
    Puesto, acota el otro lado: `[cutoff, hasta]` en vez de `[cutoff, ∞)`.
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
            PriceHistory.source_id,
            RawIngestion.content_type,
            PriceHistory.format_id,
            ProductFormat.packaging,
            ProductFormat.quantity,
            ProductFormat.unit,
            PriceHistory.variant_id,
            ProductVariant.kind,
            ProductVariant.brand,
            ProductVariant.origin,
            PriceHistory.provenance,
        )
        .join(Product, Product.id == PriceHistory.product_id)
        .outerjoin(Province, Province.id == PriceHistory.province_id)
        .join(RawIngestion, RawIngestion.id == PriceHistory.source_id)
        .outerjoin(ProductFormat, ProductFormat.id == PriceHistory.format_id)
        .outerjoin(ProductVariant, ProductVariant.id == PriceHistory.variant_id)
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
        source_id,
        content_type,
        format_id,
        format_packaging,
        format_quantity,
        format_unit,
        variant_id,
        variant_kind,
        variant_brand,
        variant_origin,
        provenance,
    ) in session.execute(stmt):
        if quantity == 0:
            continue

        effective_date = published_date or created_at.date()
        if effective_date < cutoff:
            continue
        if hasta is not None and effective_date > hasta:
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
                evidence_id=source_id,
                evidence_is_public=content_type == ContentType.TEXT,
                format_id=format_id,
                format_packaging=format_packaging,
                format_quantity=format_quantity,
                format_unit=format_unit,
                variant_id=variant_id,
                variant_kind=variant_kind,
                variant_brand=variant_brand,
                variant_origin=variant_origin,
                provenance=provenance,
            )
        )

    return RetailObservationReport(
        observations=observations,
        excluded_by_minimum=excluded_by_minimum,
        excluded_by_basis=excluded_by_basis,
        excluded_by_unit=excluded_by_unit,
    )


@dataclass(frozen=True)
class Availability:
    """Anuncios minoristas por producto × provincia, esta ventana contra la
    anterior — sin score ni umbral.

    `spec.md` §7.3 define `availability_index` con un `score`/100 y un
    `trend`, pero nunca dice cómo se calcula el score, y el ejemplo de §6.7
    ("32/100 -> Escasez") es un umbral sobre una escala que la especificación
    no fija en ninguna parte. Inventarla violaría §12.6. Lo único que se
    puede afirmar sin inventar nada es cuántos anuncios hubo esta ventana
    contra la anterior, mismo ancho: el operador o el lector del dashboard
    juzgan qué tan grave es la diferencia, esta vista no la clasifica.
    """

    product_name: str
    province_name: str | None
    current_count: int
    previous_count: int

    @property
    def delta(self) -> int:
        """Positivo: más anuncios que la ventana anterior. Negativo: menos.
        Ninguna magnitud se etiqueta "mucho"/"poco" — ver docstring de la
        clase."""
        return self.current_count - self.previous_count


def compute_availability(
    session: Session, *, days: int = 7, hoy: date | None = None
) -> list[Availability]:
    """Cuenta anuncios minoristas por producto × provincia en `[hoy-days, hoy)`
    contra `[hoy-2*days, hoy-days)`.

    Reutiliza `compute_retail_observations` pidiendo el doble de ventana
    (`2*days`) y particiona el resultado en dos mitades por
    `RetailObservation.day`, en vez de llamarla dos veces con `hoy` corrido:
    esa función no tiene tope superior de fecha (cuenta todo lo que sea
    `>= cutoff`, ver su docstring), así que una segunda llamada con `hoy`
    más viejo traería de nuevo las filas de la ventana actual y las contaría
    en las dos mitades.

    Un producto que se queda en silencio no desaparece de la lista:
    `current_count=0` con `previous_count>0` es señal válida (justo la que
    esta vista existe para mostrar). Lo que sí se omite es la combinación
    producto/provincia sin ninguna fila en ninguna de las dos ventanas —
    fabricar un 0 contra 0 para todo el catálogo inventaría una observación
    que nadie hizo.
    """
    hoy = hoy or date.today()
    boundary = hoy - timedelta(days=days)
    report = compute_retail_observations(session, days=days * 2, hoy=hoy)

    counts: dict[tuple[str, str | None], list[int]] = {}
    for observation in report.observations:
        bucket = counts.setdefault(
            (observation.product_name, observation.province_name), [0, 0]
        )
        if observation.day >= boundary:
            bucket[0] += 1
        else:
            bucket[1] += 1

    availability = [
        Availability(
            product_name=product_name,
            province_name=province_name,
            current_count=current_count,
            previous_count=previous_count,
        )
        for (product_name, province_name), (
            current_count,
            previous_count,
        ) in counts.items()
    ]
    availability.sort(
        key=lambda a: (-a.current_count, a.product_name, a.province_name or "")
    )
    return availability


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
    #: `True` cuando `reference` no salió de la provincia de la fila sino de
    #: todas juntas, porque la propia no tenía ninguna fila no ambigua
    #: (2026-09-05). No se puede esconder: sin ese dato el operador no puede
    #: descartar que lo que ve sea una diferencia regional real, y quien juzga
    #: sigue siendo él.
    reference_from_all_provinces: bool = False

    @property
    def ratio_to_reference(self) -> Decimal:
        """Cuántas veces se aparta la lectura declarada. Es la magnitud del error."""
        return self.as_unit / self.reference


def cell_sample(
    own_province: Sequence[Decimal],
    all_provinces: Sequence[Decimal],
    *,
    min_references: int,
) -> tuple[Sequence[Decimal], bool] | None:
    """La muestra de referencia de una celda, con repliegue. Código puro.

    Manda la provincia propia si junta `min_references`; si no, se repliega a
    todas las provincias juntas (mismo criterio del 2026-09-05, ver
    `detect_basis_suspects`); si tampoco alcanzan, `None`: no hay con qué
    opinar. El segundo valor dice si la muestra salió del repliegue.
    """
    if len(own_province) >= min_references:
        return own_province, False
    if len(all_provinces) >= min_references:
        return all_provinces, True
    return None


@dataclass(frozen=True)
class BasisReading:
    """Las dos lecturas de un mismo precio, contrastadas con su celda."""

    #: Mediana de la muestra de referencia.
    reference: Decimal
    reference_from_all_provinces: bool
    #: `True` si la lectura que la fila NO declara queda estrictamente más
    #: cerca, en proporción, de la referencia. Un empate no desmiente a la fila.
    other_fits_better: bool


def evaluate_basis(
    *,
    declared: Decimal,
    other: Decimal,
    own_province: Sequence[Decimal],
    all_provinces: Sequence[Decimal],
    min_references: int,
) -> BasisReading | None:
    """Evalúa UNA fila contra la referencia de su celda. Código puro.

    `declared` es el precio por unidad canónica leído como la fila dice
    (`price_basis`), `other` el mismo número leído de la otra forma. Las dos
    muestras son los precios de referencia de la celda (la provincia propia y
    todas juntas), ya normalizados igual. Devuelve `None` cuando no hay
    veredicto: menos de `min_references` referencias, o mediana no positiva.

    Es el cálculo que `detect_basis_suspects` hacía en línea; se sacó acá en
    A5b para que el motor de validación (regla 6) evalúe filas en memoria sin
    tocar la base. No hay umbral: solo cuál de las dos lecturas queda más cerca
    (ver el docstring de `detect_basis_suspects`).
    """
    picked = cell_sample(own_province, all_provinces, min_references=min_references)
    if picked is None:
        return None
    sample, from_all_provinces = picked
    if not sample:
        return None
    reference = statistics.median(sample)
    if reference <= 0:
        return None
    # Distancia proporcional, simétrica: para un valor y su referencia da lo
    # mismo estar 10 veces por encima que 10 veces por debajo.
    return BasisReading(
        reference=reference,
        reference_from_all_provinces=from_all_provinces,
        other_fits_better=_proportional_distance(other, reference)
        < _proportional_distance(declared, reference),
    )


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
    Si el producto no tiene ninguna, **no hay veredicto**: la fila no se
    reporta. Es el caso de Picadillo, cuyas cuatro filas `'unit'` con
    `quantity != 1` están bien y no tienen contra qué contrastarse — inventarles
    un juicio sería exactamente el error que este diseño evita
    (`docs/backlog.md`, 2026-08-22).

    **La referencia se repliega a todas las provincias cuando la propia no
    tiene ninguna** (2026-09-05). Hasta entonces el vecindario era siempre
    producto × provincia × unidad, y eso dejaba ciego al detector justo en el
    producto de una sola carga: la caja de pollo de 40 lb a 24.800 marcada
    `'unit'` —que puso la caja en 992.000 CUP— era la única fila de pollo en
    Santiago y nunca se reportó. No hay umbral nuevo: la regla sigue siendo
    cuál de las dos lecturas queda más cerca en proporción, y lo único que
    cambia es de dónde sale la mediana. Se sostiene porque las dos escalas no
    se parecen — el error que se busca es del orden del factor de cantidad o
    de conversión (×40, ×99) mientras que la diferencia real de precio entre
    provincias es de ×1,5 como mucho—, pero el repliegue queda anotado en
    `BasisSuspect.reference_from_all_provinces` para que el operador pueda
    descartar una diferencia regional él mismo. Es un repliegue y no un
    reemplazo: donde la provincia ya tenía vecindario, manda el suyo.

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
    # Mismo vecindario sin la provincia: es el repliegue, no un segundo criterio
    # (ver docstring). Se llena en paralelo con el de arriba y solo se consulta
    # cuando la provincia de la fila no aportó ninguna referencia.
    ProductKey = tuple[str, str]
    unambiguous: dict[SuspectKey, list[Decimal]] = {}
    unambiguous_anywhere: dict[ProductKey, list[Decimal]] = {}
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
                unambiguous_anywhere.setdefault((key[0], key[2]), []).append(resolved)
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
        # `/control` opina con una sola referencia (`min_references=1`): es el
        # comportamiento de siempre. El motor de validación llama a la misma
        # función con su `min_cell_n`.
        reading = evaluate_basis(
            declared=row["as_unit"],  # type: ignore[arg-type]
            other=row["as_total"],  # type: ignore[arg-type]
            own_province=unambiguous.get(key, ()),
            all_provinces=unambiguous_anywhere.get((key[0], key[2]), ()),
            min_references=1,
        )
        if reading is None:
            continue  # sin referencia en ninguna provincia no hay veredicto
        if reading.other_fits_better:
            suspects.append(
                BasisSuspect(
                    reference=reading.reference,
                    reference_from_all_provinces=reading.reference_from_all_provinces,
                    **row,  # type: ignore[arg-type]
                )
            )

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
