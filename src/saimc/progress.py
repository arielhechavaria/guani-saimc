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

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from saimc.models import (
    Channel,
    ContentType,
    CurationEvent,
    Currency,
    DiscardReason,
    PriceHistory,
    ProcessingStatus,
    Product,
    Province,
    RawIngestion,
)

# --- Objetivos del documento rector -------------------------------------------

# spec.md §9, «Resultado esperado» de la Fase 0.
PHASE0_TARGET_ROWS = 500

# spec.md §9, «Productos: 3-5 productos bandera». El rango es del documento;
# cuáles son los elegidos es decisión del operador (docs/decisions.md,
# 2026-08-11) y por eso no vive en el documento.
PHASE0_PRODUCTS_MIN = 3
PHASE0_PRODUCTS_MAX = 5


def pilot_product_names(session: Session) -> tuple[str, ...]:
    """Los nombres del conjunto piloto, leídos de la base.

    Era una tupla fija en este módulo hasta el 2026-08-15. Pasó a
    `products.is_pilot` para que el foco tenga UNA sola fuente de verdad: la
    curaduría lo usa para restringir qué se extrae y esta vista para medir el
    progreso, y con dos listas separadas bastaba promover un producto en
    /catalog para que dejaran de coincidir en silencio.
    """
    stmt = select(Product.name).where(Product.is_pilot.is_(True)).order_by(Product.name)
    return tuple(session.execute(stmt).scalars().all())


def pilot_products(session: Session) -> list[Product]:
    """Los productos del foco, en el orden en que el motor los ve.

    Hermana de `pilot_product_names` y por el mismo motivo: el catálogo que se
    le pasa a `propose_all` es UNA sola definición. La cola de curaduría la
    aplicaba desde el 2026-08-15 (`_pilot_products` en `web/routers/curation.py`)
    pero `extraction/measure.py` consultaba `select(Product)` sin filtro, así
    que H0 medía un extractor con 55 productos de más que en producción nunca
    se le ofrecen (2026-09-09, `docs/decisions.md`).

    El `order_by` es parte del contrato, no cosmética: `_match_product` corta en
    el PRIMER producto de la lista que matchea, así que dos consultas con el
    mismo filtro y distinto orden siguen siendo dos extractores distintos.
    """
    stmt = select(Product).where(Product.is_pilot.is_(True)).order_by(Product.name)
    return list(session.execute(stmt).scalars().all())


# spec.md §9, «Provincias: 2 provincias (La Habana y Santiago de Cuba)». Las
# nombra el documento: no es una elección de implementación.
PHASE0_PROVINCES: tuple[str, ...] = ("La Habana", "Santiago de Cuba")

# spec.md §12.5, fila «Tasa de descarte», columna Objetivo: «<30%».
DISCARD_RATE_OBJECTIVE = 0.30


def _above_objective(rate: float | None) -> bool:
    """Compara una tasa de descarte ya calculada contra el «<30%» de §12.5.

    Libre y no método porque `IntakeHealth` y `ChannelIntake` calculan
    `discard_rate` con criterios distintos (la primera neta lo fuera de
    foco, la segunda no) — no son la misma métrica, así que no comparten
    clase base; comparten solo este último paso, y por eso lo único que se
    extrae es la comparación, no el cálculo.
    """
    return rate is not None and rate > DISCARD_RATE_OBJECTIVE


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

    pilots = pilot_product_names(session)
    return Phase0Progress(
        rows=rows,
        target_rows=PHASE0_TARGET_ROWS,
        products_with_data=tuple(sorted(p for p in pilots if p in products_loaded)),
        products_missing=tuple(sorted(p for p in pilots if p not in products_loaded)),
        provinces_with_data=tuple(sorted(p for p in PHASE0_PROVINCES if p in provinces_loaded)),
        provinces_missing=tuple(sorted(p for p in PHASE0_PROVINCES if p not in provinces_loaded)),
    )


# --- Salud de la ingesta -------------------------------------------------------


@dataclass(frozen=True)
class IntakeHealth:
    """Qué entra por la bandeja y qué proporción se termina descartando.

    `discarded` es el total de descartes (lo que le importa a «Descarte por
    canal»: cinco descartes cuestan hora de curaduría sea cual sea el motivo).
    `discarded_off_focus` son los que traían producto y precio pero no eran de
    interés para la Fase 0 —no piloto, mayorista puro—; salen de la tasa que
    fija `spec.md` §12.5 («anuncios sin información económica extraíble»)
    porque ese anuncio sí es útil, solo que hoy no lo seguimos. Nulo en la
    base = motivo no registrado (los 378 descartes previos al 2026-09-03), y
    se cuenta como «sin dato» — lo conservador.
    """

    pending: int
    curated: int
    discarded: int
    discarded_off_focus: int = 0

    @property
    def decided(self) -> int:
        return self.curated + self.discarded

    @property
    def _no_data_discarded(self) -> int:
        """Descartes que §12.5 sí mide: sin precio, ilegible, agotado, spam."""
        return self.discarded - self.discarded_off_focus

    @property
    def discard_rate(self) -> float | None:
        """La tasa de `spec.md` §12.5: descartes «sin dato» sobre lo decidido,
        una vez sacado lo que se tiró solo por estar fuera de foco.

        `None` cuando, quitados los fuera de foco, no queda nada decidido: no
        hay qué medir, y un 0% afirmaría algo que el dato no dice.
        """
        base = self.decided - self.discarded_off_focus
        if base == 0:
            return None
        return self._no_data_discarded / base

    @property
    def off_focus_rate(self) -> float | None:
        """Qué fracción de lo decidido se tiró por estar fuera de foco. Es
        contexto, no la métrica del spec: un número alto acá dice que los
        canales suscritos venden mayormente cosas que la Fase 0 no sigue."""
        if self.decided == 0:
            return None
        return self.discarded_off_focus / self.decided

    @property
    def above_objective(self) -> bool:
        """Contra el «<30%» de spec.md §12.5 — objetivo del documento.

        Es la única comparación con veredicto de todo el módulo, y existe
        porque el número está escrito en la especificación, no porque parezca
        razonable. Compara contra `discard_rate` ya depurada de fuera de foco:
        el umbral del spec aplica a la métrica del spec.
        """
        return _above_objective(self.discard_rate)


def compute_intake_health(session: Session) -> IntakeHealth:
    counts = dict(
        session.execute(
            select(RawIngestion.processing_status, func.count()).group_by(
                RawIngestion.processing_status
            )
        ).all()
    )
    off_focus = session.execute(
        select(func.count()).where(
            RawIngestion.discard_reason == DiscardReason.OFF_FOCUS.value
        )
    ).scalar_one()
    return IntakeHealth(
        pending=counts.get(ProcessingStatus.PENDING, 0),
        curated=counts.get(ProcessingStatus.CURATED, 0),
        discarded=counts.get(ProcessingStatus.DISCARDED, 0),
        discarded_off_focus=off_focus,
    )


# --- Panel por canal ------------------------------------------------------------

# Una decisión que tardó más que esto no es tiempo de curaduría sino una
# pausa (el operador se levantó a mitad de un anuncio): se excluye de la suma
# en vez de inflarla. 15 minutos es una elección de medición, no del spec;
# A2d la reusa para que `/control` y este panel cuenten igual.
PAUSE_CAP_SECONDS = 15 * 60


def decision_seconds(
    shown_at: datetime | None, decided_at: datetime, *, batch_size: int = 1
) -> float | None:
    """Segundos de atención humana que costó una decisión de la bitácora.

    `None` cuando no se puede medir: sin `shown_at`, con la vista posterior a
    la decisión, o con una pausa absurda (más de `PAUSE_CAP_SECONDS`). Un
    lote guarda el mismo `shown_at` y `decided_at` en cada uno de sus anuncios
    (`spec-slice-A2.md`): su tiempo se reparte entre `batch_size` para que el
    lote se sume una sola vez.
    """
    if shown_at is None:
        return None
    seconds = (decided_at - shown_at).total_seconds()
    if seconds < 0 or seconds > PAUSE_CAP_SECONDS:
        return None
    return seconds / max(batch_size, 1)


@dataclass(frozen=True)
class StatusCounts:
    """Cuántos anuncios de un tipo de contenido hay en cada estado."""

    pending: int = 0
    curated: int = 0
    discarded: int = 0


@dataclass(frozen=True)
class ChannelIntake:
    """La vista de un canal para decidir qué hacer con él (A4a).

    `/control` ya mostraba la tasa de descarte global y decía «vale mirar de
    qué canales viene lo que se descarta antes de tocar nada». Esta es la vista
    que lo responde: dónde poner (o sacar) la hora diaria de curaduría.

    Se agrupa por `channel_id`, no por nombre: dos canales homónimos son dos
    canales. `channel_id is None` agrupa la captura manual — la otra mitad del
    trabajo del operador; `channel_name`, `platform` e `is_active` quedan
    `None` ahí porque no hay canal que describir.

    La tasa de descarte sigue el criterio de `IntakeHealth` (§12.5): los
    descartes por fuera de foco salen de numerador y denominador. Un descarte
    sin motivo registrado cuenta como «sin dato» — lo conservador.

    `human_minutes` y `rows_per_hour` salen de la bitácora (`curation_events`):
    solo suman decisiones de curar o descartar con tiempo medible. Son `None`
    mientras el canal no tenga ninguna.
    """

    channel_id: uuid.UUID | None
    channel_name: str | None
    platform: str | None
    is_active: bool | None
    text: StatusCounts
    image: StatusCounts
    discarded_off_focus: int = 0
    discarded_no_data: int = 0
    rows: int = 0
    timed_seconds: float | None = None
    timed_rows: int = 0

    @property
    def pending(self) -> int:
        return self.text.pending + self.image.pending

    @property
    def curated(self) -> int:
        return self.text.curated + self.image.curated

    @property
    def discarded(self) -> int:
        return self.text.discarded + self.image.discarded

    @property
    def discarded_unrecorded(self) -> int:
        """Descartes sin motivo registrado (los previos al 2026-09-03)."""
        return self.discarded - self.discarded_off_focus - self.discarded_no_data

    @property
    def decided(self) -> int:
        return self.curated + self.discarded

    @property
    def discard_rate(self) -> float | None:
        """Descartes «sin dato» sobre lo decidido, sin los fuera de foco. `None`
        cuando, quitados esos, no queda nada decidido."""
        base = self.decided - self.discarded_off_focus
        if base == 0:
            return None
        return (self.discarded - self.discarded_off_focus) / base

    @property
    def off_focus_rate(self) -> float | None:
        """Qué fracción de lo decidido se tiró por estar fuera de foco. Es
        contexto, no la métrica del spec."""
        if self.decided == 0:
            return None
        return self.discarded_off_focus / self.decided

    @property
    def above_objective(self) -> bool:
        """El único umbral con veredicto (§12.5, «<30%»), sobre la tasa neta."""
        return _above_objective(self.discard_rate)

    @property
    def rows_per_decided(self) -> float | None:
        """Filas de precio producidas por anuncio resuelto (curado o descartado):
        lo que el canal rinde de verdad, no cuántos anuncios sobreviven."""
        if self.decided == 0:
            return None
        return self.rows / self.decided

    @property
    def human_minutes(self) -> float | None:
        if self.timed_seconds is None:
            return None
        return self.timed_seconds / 60

    @property
    def rows_per_hour(self) -> float | None:
        """Filas por hora humana, sobre las mismas decisiones que dan el tiempo."""
        if not self.timed_seconds:
            return None
        return self.timed_rows / (self.timed_seconds / 3600)


def compute_channel_intake(session: Session) -> list[ChannelIntake]:
    """Desglose de `compute_intake_health` por canal, incluida la captura manual.

    Ordenado por descartes absolutos, no por tasa: un canal con 100% de
    descarte sobre dos anuncios cuesta minutos; uno con 40% sobre cincuenta
    cuesta una tarde. Un canal sin ninguna fila (recién dado de
    alta en /catalog) no aparece — no hay nada medido que mostrar.
    """
    counts = session.execute(
        select(
            RawIngestion.channel_id,
            RawIngestion.content_type,
            RawIngestion.processing_status,
            RawIngestion.discard_reason,
            func.count(),
        ).group_by(
            RawIngestion.channel_id,
            RawIngestion.content_type,
            RawIngestion.processing_status,
            RawIngestion.discard_reason,
        )
    ).all()

    status_by_type: dict[uuid.UUID | None, dict[str, dict[str, int]]] = {}
    off_focus: dict[uuid.UUID | None, int] = {}
    no_data: dict[uuid.UUID | None, int] = {}
    for channel_id, content_type, status, reason, count in counts:
        bucket = status_by_type.setdefault(channel_id, {}).setdefault(
            content_type, {"pending": 0, "curated": 0, "discarded": 0}
        )
        if status == ProcessingStatus.PENDING:
            bucket["pending"] += count
        elif status == ProcessingStatus.CURATED:
            bucket["curated"] += count
        elif status == ProcessingStatus.DISCARDED:
            bucket["discarded"] += count
            if reason == DiscardReason.OFF_FOCUS.value:
                off_focus[channel_id] = off_focus.get(channel_id, 0) + count
            elif reason == DiscardReason.NO_DATA.value:
                no_data[channel_id] = no_data.get(channel_id, 0) + count

    channels = {
        c.id: c
        for c in session.execute(select(Channel)).scalars().all()
    }

    rows_by_channel = dict(
        session.execute(
            select(RawIngestion.channel_id, func.count(PriceHistory.id))
            .select_from(PriceHistory)
            .join(RawIngestion, PriceHistory.source_id == RawIngestion.id)
            .group_by(RawIngestion.channel_id)
        ).all()
    )

    timed_seconds: dict[uuid.UUID | None, float] = {}
    timed_rows: dict[uuid.UUID | None, int] = {}
    events = session.execute(
        select(
            RawIngestion.channel_id,
            CurationEvent.action,
            CurationEvent.shown_at,
            CurationEvent.decided_at,
            CurationEvent.detail,
        )
        .join(RawIngestion, CurationEvent.ingestion_id == RawIngestion.id)
        .where(CurationEvent.action.in_(("curated", "discarded")))
    ).all()
    for channel_id, action, shown_at, decided_at, detail in events:
        detail = detail or {}
        seconds = decision_seconds(
            shown_at, decided_at, batch_size=int(detail.get("batch_size") or 1)
        )
        if seconds is None:
            continue
        timed_seconds[channel_id] = timed_seconds.get(channel_id, 0.0) + seconds
        if action == "curated":
            timed_rows[channel_id] = timed_rows.get(channel_id, 0) + int(
                detail.get("row_count") or 0
            )

    intake: list[ChannelIntake] = []
    for channel_id, by_type in status_by_type.items():
        channel = channels.get(channel_id) if channel_id is not None else None
        intake.append(
            ChannelIntake(
                channel_id=channel_id,
                channel_name=channel.name if channel else None,
                platform=channel.platform if channel else None,
                is_active=channel.is_active if channel else None,
                text=StatusCounts(**by_type.get(ContentType.TEXT.value, {})),
                image=StatusCounts(**by_type.get(ContentType.IMAGE.value, {})),
                discarded_off_focus=off_focus.get(channel_id, 0),
                discarded_no_data=no_data.get(channel_id, 0),
                rows=rows_by_channel.get(channel_id, 0),
                timed_seconds=timed_seconds.get(channel_id),
                timed_rows=timed_rows.get(channel_id, 0),
            )
        )
    intake.sort(key=lambda c: c.discarded, reverse=True)
    return intake


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
        for product_name in pilot_product_names(session)
        for province_name in PHASE0_PROVINCES
    ]
