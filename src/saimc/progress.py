"""Progreso de la Fase 0 — ¿esto va a llegar a donde tiene que llegar?

`cells.py` responde «¿lo que estoy cargando sirve?». Este módulo responde la
otra mitad: **«¿cuánto falta, y qué falta exactamente?»**. Es el instrumento
para decidir dónde poner la hora diaria de curaduría, que hoy es el recurso
más escaso del proyecto.

**Los únicos dos objetivos que se comparan acá salen del documento rector.**
`spec.md` §9 fija el resultado esperado de la Fase 0 —«500 registros validados
de 3–5 productos en 2 provincias»— y nombra las dos provincias (La Habana y
Santiago de Cuba). §12.5 fija la tasa de descarte objetivo en «<30%». Ninguno
de los dos se inventó acá, y no hay ningún otro: §12.6 es explícito en que un
umbral asignado por intuición y presentado como metodología es peor que no
tener umbral, así que el ritmo, los huecos y la cobertura se reportan como
medición cruda, sin veredicto. Quien juzga es el operador.

La extrapolación de `Pace` es **aritmética, no pronóstico**: divide lo que
falta por el promedio observado. No modela estacionalidad ni nada parecido —
la capa predictiva es Fase 2 y depende de H0, que todavía no se midió.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from saimc.models import Currency, PriceHistory, ProcessingStatus, Product, Province, RawIngestion

# --- Objetivos del documento rector -------------------------------------------

# spec.md §9, «Resultado esperado» de la Fase 0.
PHASE0_TARGET_ROWS = 500

# spec.md §9, «Productos: 3-5 productos bandera». El rango es del documento;
# cuáles son los cuatro elegidos es decisión del operador (docs/decisions.md,
# 2026-08-11) y por eso vive acá y no en el documento.
PHASE0_PRODUCTS_MIN = 3
PHASE0_PRODUCTS_MAX = 5
PILOT_PRODUCTS: tuple[str, ...] = ("Aceite", "Arroz", "Azúcar blanca", "Pollo")

# spec.md §9, «Provincias: 2 provincias (La Habana y Santiago de Cuba)». Las
# nombra el documento: no es una elección de implementación.
PHASE0_PROVINCES: tuple[str, ...] = ("La Habana", "Santiago de Cuba")

# spec.md §12.5, fila «Tasa de descarte», columna Objetivo: «<30%».
DISCARD_RATE_OBJECTIVE = 0.30


# --- Progreso hacia la meta ----------------------------------------------------


@dataclass(frozen=True)
class Phase0Progress:
    """Lo cargado contra los tres ejes de la meta de §9."""

    rows: int
    target_rows: int
    products_with_data: tuple[str, ...]
    products_missing: tuple[str, ...]
    provinces_with_data: tuple[str, ...]
    provinces_missing: tuple[str, ...]

    @property
    def rows_remaining(self) -> int:
        return max(0, self.target_rows - self.rows)

    @property
    def rows_pct(self) -> float:
        """Acotado a 100 para que la barra no se desborde si se pasa la meta."""
        if self.target_rows == 0:
            return 0.0
        return min(100.0, self.rows * 100.0 / self.target_rows)


def compute_phase0_progress(session: Session) -> Phase0Progress:
    rows = session.execute(select(func.count()).select_from(PriceHistory)).scalar_one()

    # Un producto/provincia «tiene dato» con una fila: acá se mide cobertura,
    # no suficiencia. Cuántas filas hacen falta por celda es exactamente el
    # parámetro N que §12.6 manda calibrar con datos reales, no fijar ahora.
    products_loaded = set(
        session.execute(
            select(Product.name).join(PriceHistory, PriceHistory.product_id == Product.id).distinct()
        )
        .scalars()
        .all()
    )
    provinces_loaded = set(
        session.execute(
            select(Province.name)
            .join(PriceHistory, PriceHistory.province_id == Province.id)
            .distinct()
        )
        .scalars()
        .all()
    )

    return Phase0Progress(
        rows=rows,
        target_rows=PHASE0_TARGET_ROWS,
        products_with_data=tuple(sorted(p for p in PILOT_PRODUCTS if p in products_loaded)),
        products_missing=tuple(sorted(p for p in PILOT_PRODUCTS if p not in products_loaded)),
        provinces_with_data=tuple(sorted(p for p in PHASE0_PROVINCES if p in provinces_loaded)),
        provinces_missing=tuple(sorted(p for p in PHASE0_PROVINCES if p not in provinces_loaded)),
    )


# --- Salud de la ingesta -------------------------------------------------------


@dataclass(frozen=True)
class IntakeHealth:
    """Qué entra por la bandeja y qué proporción se termina descartando."""

    pending: int
    curated: int
    discarded: int

    @property
    def decided(self) -> int:
        return self.curated + self.discarded

    @property
    def discard_rate(self) -> float | None:
        """`None` cuando no hay nada decidido todavía.

        Cero decididos no es «0% de descarte»: es que no hay qué medir, y
        mostrar 0% afirmaría algo que el dato no dice.
        """
        if self.decided == 0:
            return None
        return self.discarded / self.decided

    @property
    def above_objective(self) -> bool:
        """Contra el «<30%» de spec.md §12.5 — objetivo del documento.

        Es la única comparación con veredicto de todo el módulo, y existe
        porque el número está escrito en la especificación, no porque parezca
        razonable.
        """
        rate = self.discard_rate
        return rate is not None and rate > DISCARD_RATE_OBJECTIVE


def compute_intake_health(session: Session) -> IntakeHealth:
    counts = dict(
        session.execute(
            select(RawIngestion.processing_status, func.count()).group_by(
                RawIngestion.processing_status
            )
        ).all()
    )
    return IntakeHealth(
        pending=counts.get(ProcessingStatus.PENDING, 0),
        curated=counts.get(ProcessingStatus.CURATED, 0),
        discarded=counts.get(ProcessingStatus.DISCARDED, 0),
    )


# --- Ritmo ---------------------------------------------------------------------


@dataclass(frozen=True)
class DayActivity:
    day: date
    rows: int


class _HasRows(Protocol):
    rows: int


def compute_daily_activity(
    session: Session, *, days: int = 14, hoy: date | None = None
) -> list[DayActivity]:
    """Filas cargadas por día, del más viejo al más nuevo, con los ceros dentro.

    El eje es `created_at` —cuándo el operador cargó la fila—, no
    `published_date`: lo que se mide es el ritmo de trabajo, no la fecha del
    anuncio. Los días vacíos se devuelven explícitos porque el hueco es parte
    de la lectura: una barra faltante y una barra en cero se leen distinto.

    La agrupación por día se hace en Python y no en SQL a propósito, mismo
    criterio que `cells.compute_cells`: `date_trunc` es de PostgreSQL y los
    tests corren sobre SQLite (tests/conftest.py).
    """
    today = hoy or date.today()
    first = today - timedelta(days=days - 1)

    counts: dict[date, int] = {}
    for (created_at,) in session.execute(select(PriceHistory.created_at)):
        day = created_at.date()
        if first <= day <= today:
            counts[day] = counts.get(day, 0) + 1

    return [
        DayActivity(day=first + timedelta(days=i), rows=counts.get(first + timedelta(days=i), 0))
        for i in range(days)
    ]


@dataclass(frozen=True)
class Pace:
    """Ritmo observado y cuánto falta a ese ritmo."""

    window_days: int
    rows_in_window: int
    days_to_target: int | None

    @property
    def per_day(self) -> float:
        if self.window_days == 0:
            return 0.0
        return self.rows_in_window / self.window_days


def compute_pace(activity: list[_HasRows], *, rows_remaining: int) -> Pace:
    """Extrapola linealmente lo que falta contra el promedio de la ventana.

    Es aritmética, no un pronóstico: no hay modelo detrás, y por eso la vista
    la presenta como «a este ritmo» y no como una fecha de llegada.

    La división final es entera a propósito. Con `float` el redondeo hacia
    arriba se vuelve inestable en los casos que caen justo (10 filas en 7 días
    para 10 restantes da 6.999… o 7.000…1 según el error de coma flotante, y
    eso cambia el resultado en un día entero).
    """
    window_days = len(activity)
    rows_in_window = sum(day.rows for day in activity)

    if rows_remaining <= 0:
        days_to_target: int | None = 0
    elif rows_in_window == 0:
        # Sin actividad no hay «infinitos días»: no se puede estimar.
        days_to_target = None
    else:
        # Techo exacto de (rows_remaining * window_days) / rows_in_window.
        days_to_target = -(-rows_remaining * window_days // rows_in_window)

    return Pace(
        window_days=window_days,
        rows_in_window=rows_in_window,
        days_to_target=days_to_target,
    )


# --- Huecos accionables --------------------------------------------------------


@dataclass(frozen=True)
class Gap:
    """Un campo sin responder, con el enlace para ir a responderlo.

    `query` explota el contrato de `/curation/history` (ver su docstring): un
    parámetro presente pero vacío filtra por NULL. Sin ese enlace el hueco
    sería un número que no se puede accionar.
    """

    key: str
    label: str
    hint: str
    count: int
    query: str


def compute_open_gaps(session: Session) -> list[Gap]:
    """Los cuatro campos que quedan sin responder al curar, ya contados.

    No es una lista de errores: `unit` y `sale_format` en NULL son «el operador
    todavía no respondió», y `price_cup` en NULL es «elTOQUE no resolvió la
    tasa». Ninguno bloquea el guardado por diseño (design.md, D2/D4), y
    justamente por eso hace falta verlos acumulados en algún lado.
    """

    def _count(*where) -> int:
        return session.execute(
            select(func.count()).select_from(PriceHistory).where(*where)
        ).scalar_one()

    return [
        Gap(
            key="province",
            label="Sin provincia",
            hint="Sin provincia la fila no entra en la matriz por provincia, que es el núcleo de la propuesta (§5).",
            count=_count(PriceHistory.province_id.is_(None)),
            query="province=",
        ),
        Gap(
            key="unit",
            label="Sin unidad",
            hint="Sin unidad no hay precio normalizado: la fila queda fuera del rango minorista.",
            count=_count(PriceHistory.unit.is_(None)),
            query="unit=",
        ),
        Gap(
            key="sale_format",
            label="Sin formato de venta",
            hint="Sin minorista/mayorista la celda no se puede comparar con las demás.",
            count=_count(PriceHistory.sale_format.is_(None)),
            query="sale_format=",
        ),
        Gap(
            key="unconverted",
            label="Sin convertir a CUP",
            hint="elTOQUE no resolvió la tasa. Se reintenta con el backfill; no requiere recurar.",
            count=_count(
                PriceHistory.currency != Currency.CUP.value,
                PriceHistory.price_cup.is_(None),
            ),
            query="unconverted=1",
        ),
    ]


# --- Cobertura de la meta ------------------------------------------------------


@dataclass(frozen=True)
class CoverageCell:
    product_name: str
    province_name: str
    rows: int


def compute_coverage(session: Session) -> list[CoverageCell]:
    """Grilla completa producto piloto × provincia objetivo, ceros incluidos.

    El cero **es** el dato: dice exactamente qué falta capturar para cumplir la
    meta de §9. Una vista que solo mostrara lo cargado escondería justamente lo
    que hay que salir a buscar.
    """
    counts = {
        (product_name, province_name): rows
        for product_name, province_name, rows in session.execute(
            select(Product.name, Province.name, func.count())
            .join(PriceHistory, PriceHistory.product_id == Product.id)
            .join(Province, Province.id == PriceHistory.province_id)
            .group_by(Product.name, Province.name)
        )
    }

    return [
        CoverageCell(
            product_name=product_name,
            province_name=province_name,
            rows=counts.get((product_name, province_name), 0),
        )
        for product_name in PILOT_PRODUCTS
        for province_name in PHASE0_PROVINCES
    ]
