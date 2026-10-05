"""Carril 0 encendido: descarte automático con auditoría (slice B2a).

Contrato: `docs/spec-slice-B2a.md`. A diferencia de `engine`, este módulo SÍ
lee y escribe la base, por eso no se importa desde `saimc.validation`.

Dos mitades:

- **Aplicar** (`apply_lane_0`): el pre-cálculo, después de guardar la
  propuesta, le pregunta al motor (`decide_lane_0`) y, si da carril 0, deja el
  anuncio descartado con su revisión y su evento `discarded/auto` en la misma
  transacción. Solo si el interruptor del tipo está encendido
  (`enabled_types`). «Completar con IA» no pasa por acá: el operador ya está
  mirando el anuncio.
- **Auditar** (`week_sample`, `rescue`, `mark_audited`, `audit_bounds`): cada
  semana cerrada se saca al azar una muestra de los descartes automáticos; el
  operador dice si estaban bien o los rescata, y con lo auditado de todas las
  semanas se calcula la cota de falsos descartes por tipo contra su meta.
  Superar la meta solo avisa: apagar el tipo lo hace el operador (el freno
  automático es de B2c, y es para el carril 1).

La semana es la del panel de A2d (`progress._week_start`): de lunes a domingo,
por la fecha de `decided_at` tal como está guardada (UTC).
"""

from __future__ import annotations

import random
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from saimc.config import Settings
from saimc.curation_events import record_curation_event
from saimc.extraction import llm_store
from saimc.extraction.llm_propose_ad import AdProposal
from saimc.models import (
    ContentType,
    CurationAction,
    CurationEvent,
    CurationMode,
    LlmProposal,
    ProcessingStatus,
    RawIngestion,
    ReviewOutcome,
)
from saimc.models.base import utc_now
from saimc.progress import _week_start
from saimc.validation import context
from saimc.validation.engine import decide_lane_0
from saimc.validation.stats import clopper_pearson_upper
from saimc.validation.types import LANE_DISCARD

TEXT = ContentType.TEXT.value
IMAGE = ContentType.IMAGE.value
TYPES = (TEXT, IMAGE)

#: Metas de falsos descartes del carril 0, por tipo. Las eligió el operador en
#: G1a (`docs/decisions.md`, 2026-10-03): son decisiones, no configuración del
#: despliegue, y por eso viven en código.
TARGETS: Mapping[str, float] = {TEXT: 0.10, IMAGE: 0.20}

#: Tamaño de la muestra semanal de la auditoría, por tipo (G1a, misma entrada).
#: 0 rescates en 30 textos dan una cota de 9,5 %, apenas bajo la meta de 10 %.
SAMPLE_SIZES: Mapping[str, int] = {TEXT: 30, IMAGE: 15}

#: La variable de entorno que apaga cada tipo. Es lo que la auditoría nombra
#: cuando la cota pasa la meta.
SWITCHES: Mapping[str, str] = {
    TEXT: "SAIMC_LANE0_TEXT_ENABLED",
    IMAGE: "SAIMC_LANE0_IMAGE_ENABLED",
}

_VERDICTS = (CurationAction.RESCUED.value, CurationAction.AUDITED.value)


# --- aplicar el carril 0 ------------------------------------------------------


def enabled_types(settings: Settings) -> frozenset[str]:
    """Los tipos de anuncio con el carril 0 encendido."""
    enabled = set()
    if settings.lane0_text_enabled:
        enabled.add(TEXT)
    if settings.lane0_image_enabled:
        enabled.add(IMAGE)
    return frozenset(enabled)


def apply_lane_0(
    session: Session,
    ingestion: RawIngestion,
    proposal: LlmProposal,
    result: AdProposal,
    *,
    text: str,
    catalog: context.Catalog,
) -> bool:
    """Descarta el anuncio si el motor lo manda al carril 0. `True` si lo hizo.

    `text` es el texto que vio el modelo, ya redactado: para una imagen, el
    OCR que acaba de correr el pre-cálculo (no se transcribe dos veces). No
    hace commit: el descarte va en la transacción de la propuesta.

    Lo que escribe es lo mismo que el lote de imágenes
    (`discard_images_batch`), con `mode = auto`: el estado y el motivo del
    modelo en `raw_ingestions`, la revisión en `llm_reviews` (para que quede
    fuera de H0 como todo lo asistido) y el evento de bitácora.
    """
    ctx = context.build_ad_context(session, ingestion, model=result, catalog=catalog, text=text)
    if decide_lane_0(ctx).lane != LANE_DISCARD:
        return False
    reason = result.discard_reason
    ingestion.processing_status = ProcessingStatus.DISCARDED
    ingestion.discard_reason = reason
    llm_store.save_review(
        session,
        proposal=proposal,
        outcome=ReviewOutcome.DISCARDED,
        discard_reason=reason,
        rows=llm_store.review_rows(proposal, []),
    )
    record_curation_event(
        session,
        ingestion_id=ingestion.id,
        action=CurationAction.DISCARDED,
        mode=CurationMode.AUTO,
        discard_reason=reason,
        proposal_id=proposal.id,
        shown_at=None,
        decided_at=utc_now(),
    )
    return True


# --- semanas -------------------------------------------------------------------


def week_label(week_start: date) -> str:
    """`2026-W40`: la semana ISO, como la escribe el selector y la semilla."""
    year, week, _ = week_start.isocalendar()
    return f"{year}-W{week:02d}"


def parse_week(label: str) -> date | None:
    """El lunes de `2026-W40`, o `None` si no es una semana válida."""
    try:
        year, week = label.strip().split("-W")
        return date.fromisocalendar(int(year), int(week), 1)
    except ValueError:
        return None


def sample_seed(week_start: date) -> str:
    """La semilla de la muestra: la misma semana da siempre la misma muestra."""
    return f"lane0-audit-{week_label(week_start)}"


def current_week(today: date | None = None) -> date:
    return _week_start(today or date.today())


def last_closed_week(today: date | None = None) -> date:
    """La semana anterior a la en curso: la que se audita por defecto."""
    return current_week(today) - timedelta(weeks=1)


def is_closed(week_start: date, today: date | None = None) -> bool:
    """¿Ya terminó? La semana en curso nunca se muestrea."""
    return week_start < current_week(today)


# --- la muestra ----------------------------------------------------------------


@dataclass(frozen=True)
class AuditItem:
    """Un descarte automático de la muestra y lo que dijo la auditoría."""

    ingestion: RawIngestion
    #: El evento `discarded/auto`: su motivo y su propuesta.
    event: CurationEvent
    #: `rescued`, `audited` o `None` si todavía no se auditó.
    verdict: str | None = None


@dataclass(frozen=True)
class WeekSample:
    week_start: date
    closed: bool
    #: Tipo -> cuántos descartes automáticos hubo esa semana (la población).
    population: Mapping[str, int] = field(default_factory=dict)
    texts: list[AuditItem] = field(default_factory=list)
    images: list[AuditItem] = field(default_factory=list)

    @property
    def label(self) -> str:
        return week_label(self.week_start)

    def items(self) -> list[AuditItem]:
        return self.texts + self.images

    def by_type(self, content_type: str) -> list[AuditItem]:
        return self.texts if content_type == TEXT else self.images


_Population = dict[date, dict[str, list[tuple[RawIngestion, CurationEvent]]]]


def _population(session: Session) -> _Population:
    """Semana -> tipo -> descartes automáticos `(anuncio, evento)`.

    Un anuncio aparece una sola vez por semana. El orden es por id: no
    depende del orden en que la base devuelva las filas, así que el sorteo es
    reproducible.
    """
    stmt = (
        select(RawIngestion, CurationEvent)
        .join(RawIngestion, RawIngestion.id == CurationEvent.ingestion_id)
        .where(
            CurationEvent.action == CurationAction.DISCARDED,
            CurationEvent.mode == CurationMode.AUTO,
        )
        .order_by(CurationEvent.decided_at)
    )
    seen: set[tuple[date, uuid.UUID]] = set()
    weeks: _Population = {}
    for ingestion, event in session.execute(stmt):
        week = _week_start(event.decided_at.date())
        if (week, ingestion.id) in seen:
            continue
        seen.add((week, ingestion.id))
        weeks.setdefault(week, {}).setdefault(str(ingestion.content_type), []).append(
            (ingestion, event)
        )
    for by_type in weeks.values():
        for pairs in by_type.values():
            pairs.sort(key=lambda pair: str(pair[0].id))
    return weeks


def _verdicts(session: Session, ingestion_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, str]:
    """Anuncio -> la última respuesta de la auditoría (`rescued` o `audited`)."""
    ids = list(ingestion_ids)
    if not ids:
        return {}
    stmt = (
        select(CurationEvent.ingestion_id, CurationEvent.action)
        .where(CurationEvent.ingestion_id.in_(ids), CurationEvent.action.in_(_VERDICTS))
        .order_by(CurationEvent.decided_at)
    )
    return {ingestion_id: str(action) for ingestion_id, action in session.execute(stmt)}


def rescued_ids(session: Session, ingestion_ids: Iterable[uuid.UUID]) -> set[uuid.UUID]:
    """Los anuncios cuya última respuesta de la auditoría es `rescued`.

    Los lotes de descarte (`/curation/sin-foco` y sus imágenes) los excluyen:
    un rescatado vuelve con su propuesta, que sigue diciendo «descartar», y un
    clic en el lote lo volvería a descartar sin que nadie lo mire.
    """
    return {
        ingestion_id
        for ingestion_id, verdict in _verdicts(session, ingestion_ids).items()
        if verdict == CurationAction.RESCUED
    }


def _draw(
    week_start: date, population: Mapping[str, list[tuple[RawIngestion, CurationEvent]]]
) -> dict[str, list[tuple[RawIngestion, CurationEvent]]]:
    """La muestra al azar de cada tipo, o todos si hay menos que el tamaño."""
    drawn: dict[str, list[tuple[RawIngestion, CurationEvent]]] = {}
    for content_type in TYPES:
        pairs = population.get(content_type, [])
        size = SAMPLE_SIZES[content_type]
        if len(pairs) <= size:
            chosen = list(pairs)
        else:
            # Un generador por tipo con la semilla de la semana: no es
            # criptografía, es que el sorteo se pueda repetir.
            rng = random.Random(sample_seed(week_start))  # noqa: S311
            chosen = rng.sample(pairs, size)
        chosen.sort(key=lambda pair: (pair[1].decided_at, str(pair[0].id)))
        drawn[content_type] = chosen
    return drawn


def _sample(
    session: Session, week_start: date, population: _Population, today: date | None
) -> WeekSample:
    by_type = population.get(week_start, {})
    counts = {t: len(by_type.get(t, [])) for t in TYPES}
    if not is_closed(week_start, today):
        return WeekSample(week_start=week_start, closed=False, population=counts)
    drawn = _draw(week_start, by_type)
    verdicts = _verdicts(
        session, (ingestion.id for pairs in drawn.values() for ingestion, _ in pairs)
    )

    def items(content_type: str) -> list[AuditItem]:
        return [
            AuditItem(ingestion=i, event=e, verdict=verdicts.get(i.id))
            for i, e in drawn[content_type]
        ]

    return WeekSample(
        week_start=week_start,
        closed=True,
        population=counts,
        texts=items(TEXT),
        images=items(IMAGE),
    )


def week_sample(session: Session, week_start: date, *, today: date | None = None) -> WeekSample:
    """La muestra de una semana: hasta 30 textos y 15 imágenes, al azar con la
    semilla de la semana. Vacía si la semana no terminó."""
    return _sample(session, week_start, _population(session), today)


def audited_weeks(session: Session, *, today: date | None = None) -> list[date]:
    """Las semanas cerradas con descartes automáticos, más nueva primero, y
    siempre la última cerrada aunque no tenga ninguno (la del selector)."""
    weeks = {w for w in _population(session) if is_closed(w, today)}
    weeks.add(last_closed_week(today))
    return sorted(weeks, reverse=True)


# --- las dos respuestas de la auditoría ----------------------------------------


def _find(
    session: Session, ingestion_id: uuid.UUID, week_start: date, today: date | None
) -> AuditItem | None:
    sample = week_sample(session, week_start, today=today)
    return next((i for i in sample.items() if i.ingestion.id == ingestion_id), None)


def rescue(
    session: Session, ingestion_id: uuid.UUID, week_start: date, *, today: date | None = None
) -> bool:
    """Devuelve a la cola un descarte automático de la muestra. `True` si lo hizo.

    Solo si el anuncio está en la muestra de esa semana, sigue descartado y no
    se rescató antes; si no, no cambia nada. Vuelve `pending`, sin
    `discard_reason`, con su propuesta: el pre-cálculo no lo retoma porque ya
    tiene una `ok`. El evento `rescued` va con `mode = individual` porque lo
    decide una persona. No hace commit.
    """
    item = _find(session, ingestion_id, week_start, today)
    if item is None or item.verdict == CurationAction.RESCUED:
        return False
    ingestion = item.ingestion
    if ingestion.processing_status != ProcessingStatus.DISCARDED:
        return False
    ingestion.processing_status = ProcessingStatus.PENDING
    ingestion.discard_reason = None
    record_curation_event(
        session,
        ingestion_id=ingestion.id,
        action=CurationAction.RESCUED,
        mode=CurationMode.INDIVIDUAL,
        proposal_id=item.event.proposal_id,
        detail={"week": week_label(week_start)},
    )
    return True


def mark_audited(
    session: Session, ingestion_id: uuid.UUID, week_start: date, *, today: date | None = None
) -> bool:
    """«Bien descartado»: escribe el evento `audited` y no cambia nada más.

    Mismas condiciones que `rescue` (en la muestra y todavía descartado), y
    además sin respuesta previa: un anuncio se audita una sola vez.
    """
    item = _find(session, ingestion_id, week_start, today)
    if item is None or item.verdict is not None:
        return False
    if item.ingestion.processing_status != ProcessingStatus.DISCARDED:
        return False
    record_curation_event(
        session,
        ingestion_id=item.ingestion.id,
        action=CurationAction.AUDITED,
        mode=CurationMode.INDIVIDUAL,
        proposal_id=item.event.proposal_id,
        detail={"week": week_label(week_start)},
    )
    return True


# --- la cota -------------------------------------------------------------------


@dataclass(frozen=True)
class Bound:
    """Falsos descartes auditados de un tipo, acumulados entre semanas."""

    content_type: str
    #: Rescatados de la muestra.
    k: int
    #: Rescatados más bien descartados de la muestra.
    n: int
    #: Rescatados fuera de cualquier muestra: se muestran aparte y no entran en
    #: la cota, porque no salieron al azar.
    rescued_outside: int = 0

    @property
    def target(self) -> float:
        return TARGETS[self.content_type]

    @property
    def switch(self) -> str:
        return SWITCHES[self.content_type]

    @property
    def rate(self) -> float | None:
        return self.k / self.n if self.n else None

    @property
    def upper(self) -> float | None:
        """Cota superior de Clopper-Pearson al 95 %. `None` sin nada auditado."""
        return clopper_pearson_upper(self.k, self.n)

    @property
    def exceeded(self) -> bool:
        upper = self.upper
        return upper is not None and upper > self.target


def audit_bounds(session: Session, *, today: date | None = None) -> dict[str, Bound]:
    """La cota de cada tipo sobre lo auditado de las muestras de todas las
    semanas cerradas: k = rescatados, n = rescatados + bien descartados."""
    population = _population(session)
    k = dict.fromkeys(TYPES, 0)
    n = dict.fromkeys(TYPES, 0)
    sampled: set[uuid.UUID] = set()
    for week in population:
        sample = _sample(session, week, population, today)
        for content_type in TYPES:
            for item in sample.by_type(content_type):
                sampled.add(item.ingestion.id)
                if item.verdict is None:
                    continue
                n[content_type] += 1
                k[content_type] += item.verdict == CurationAction.RESCUED

    auto_types = {
        ingestion.id: str(ingestion.content_type)
        for by_type in population.values()
        for pairs in by_type.values()
        for ingestion, _ in pairs
    }
    outside = dict.fromkeys(TYPES, 0)
    for ingestion_id, verdict in _verdicts(session, auto_types).items():
        content_type = auto_types[ingestion_id]
        if verdict == CurationAction.RESCUED and ingestion_id not in sampled:
            outside[content_type] = outside.get(content_type, 0) + 1
    return {t: Bound(t, k[t], n[t], outside[t]) for t in TYPES}


# --- /control ------------------------------------------------------------------


@dataclass(frozen=True)
class WeekCounts:
    """Descartes automáticos y rescatados de una semana, por tipo. Es lo único
    del carril 0 que va a `/control`: la cota no (se decidió cuando `/control`
    era público; hoy pide operador, y la cota sigue viviendo en la auditoría)."""

    week_start: date
    auto: Mapping[str, int]
    rescued: Mapping[str, int]


def week_counts(session: Session, *, today: date | None = None) -> WeekCounts:
    """Los de la semana en curso: lo descartado solo y lo rescatado en ella."""
    week = current_week(today)
    by_type = _population(session).get(week, {})
    rescued = dict.fromkeys(TYPES, 0)
    stmt = (
        select(RawIngestion.content_type, CurationEvent.decided_at)
        .join(RawIngestion, RawIngestion.id == CurationEvent.ingestion_id)
        .where(CurationEvent.action == CurationAction.RESCUED)
    )
    for content_type, decided_at in session.execute(stmt):
        if _week_start(decided_at.date()) == week:
            rescued[str(content_type)] = rescued.get(str(content_type), 0) + 1
    return WeekCounts(
        week_start=week,
        auto={t: len(by_type.get(t, [])) for t in TYPES},
        rescued=rescued,
    )
