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
- **Jev como segundo juez** (slice jev-decisor,
  `docs/spec-slice-jev-decisor.md`), solo con `SAIMC_LANE0_JEV_ENABLED`: un
  texto que el modelo descarta se descarta solo si además Jev da P(sí) <
  `JEV_DISCARD_BELOW` (sin señal, como antes); y un texto cuyo asistente
  degradó lo puede descartar Jev solo. Esos llevan `decider = jev` en el
  evento y tienen su propia cota (`jev_bound`).
- **Jev como árbitro** (slice jev-desacuerdo,
  `docs/spec-slice-jev-desacuerdo.md`), solo con
  `SAIMC_LANE0_JEV_ARBITER_ENABLED` además: un texto que el modelo descarta y
  en el que las reglas ven un piloto se descarta si Jev da P(sí) <
  `JEV_ARBITER_BELOW`. Llevan `decider = jev_arbiter`, su propia cota
  (`arbiter_bound`) y, en el primer tramo, se auditan completos.
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

import argparse
import dataclasses
import random
import sys
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from saimc.config import Settings, get_settings
from saimc.curation_events import record_curation_event
from saimc.db import session_scope
from saimc.extraction import llm_store
from saimc.extraction.llm_propose_ad import AdProposal
from saimc.models import (
    ContentType,
    CurationAction,
    CurationEvent,
    CurationMode,
    DiscardReason,
    LlmProposal,
    ProcessingStatus,
    RawIngestion,
    ReviewOutcome,
)
from saimc.models.base import utc_now
from saimc.progress import _week_start
from saimc.validation import context, jev_signal
from saimc.validation.engine import decide_lane_0, has_digit, pilot_in_text
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

#: Umbral de Jev en el carril 0: descarta con P(sí) MENOR que esto. Lo decidió
#: el operador el 2026-10-16 (`docs/analisis-jev-decisor.md` §6), medido en el
#: oro v7: 1 falso descarte en 139 (cota 3,4 %). Es decisión, no configuración
#: del despliegue, igual que `TARGETS`.
JEV_DISCARD_BELOW = 0.2

#: `detail["decider"]` de un descarte que hizo Jev solo (Rol 2): el asistente
#: no propuso. Con eso la auditoría le saca una cota propia (`jev_bound`).
JEV_DECIDER = "jev"
#: La variable de entorno que devuelve a Jev a la sombra.
JEV_SWITCH = "SAIMC_LANE0_JEV_ENABLED"

#: Umbral de Jev como árbitro del desacuerdo (slice jev-desacuerdo): el modelo
#: descarta, las reglas ven un piloto, y Jev descarta con P(sí) MENOR que esto.
#: Es el mismo 0,2 de `JEV_DISCARD_BELOW`, sin calibrar para este caso (no hay
#: muestra humana del desacuerdo): si la auditoría rescata alguno, se baja a 0,1.
JEV_ARBITER_BELOW = 0.2
#: `detail["decider"]` de un descarte que arbitró Jev. Tiene cota propia
#: (`arbiter_bound`).
JEV_ARBITER_DECIDER = "jev_arbiter"
#: La variable de entorno que apaga solo al árbitro.
JEV_ARBITER_SWITCH = "SAIMC_LANE0_JEV_ARBITER_ENABLED"
#: Mientras las semanas anteriores sumen menos descartes del árbitro que esto,
#: la muestra de la semana lleva TODOS los suyos, no solo los que salgan al
#: azar: el primer tramo se audita completo (spec, «Despliegue»).
ARBITER_FULL_AUDIT = 30

_DECIDER_SWITCHES: Mapping[str | None, str] = {
    JEV_DECIDER: JEV_SWITCH,
    JEV_ARBITER_DECIDER: JEV_ARBITER_SWITCH,
}


# --- aplicar el carril 0 ------------------------------------------------------


def enabled_types(settings: Settings) -> frozenset[str]:
    """Los tipos de anuncio con el carril 0 encendido."""
    enabled = set()
    if settings.lane0_text_enabled:
        enabled.add(TEXT)
    if settings.lane0_image_enabled:
        enabled.add(IMAGE)
    return frozenset(enabled)


def jev_questions_hash(session: Session, settings: Settings) -> str | None:
    """La huella vigente de Jev si Jev decide en el carril 0, o `None` (sombra).

    `None` también si el catálogo no da una huella (dos pilotos con la misma
    clave): sin huella no hay señal vigente y el carril 0 es el de siempre.
    """
    if not settings.lane0_jev_enabled:
        return None
    try:
        return jev_signal.load_options(session).questions_hash
    except ValueError:
        return None


def _triage_probability(
    session: Session, ingestion: RawIngestion, jev_hash: str | None
) -> float | None:
    """P(sí) del triage de la señal vigente del anuncio. Solo textos: Jev no
    ve imágenes (C2). Una señal de otra huella o un triage ilegible es `None`."""
    if jev_hash is None or ingestion.content_type != ContentType.TEXT:
        return None
    signal = jev_signal.current_signal(session, ingestion.id, jev_hash)
    if signal is None:
        return None
    triage = jev_signal.read_signal(signal).triage
    return None if triage.invalid else triage.p


@dataclass(frozen=True)
class _Decision:
    """Lo que decidió el carril 0 sobre un anuncio y lo que escribiría."""

    discard: bool
    reason: str | None = None
    detail: dict[str, Any] | None = None

    @property
    def decider(self) -> str | None:
        return None if self.detail is None else self.detail.get("decider")


def _decide(
    session: Session,
    ingestion: RawIngestion,
    result: AdProposal,
    *,
    text: str,
    catalog: context.Catalog,
    jev_hash: str | None,
    arbiter: bool,
) -> _Decision:
    p = _triage_probability(session, ingestion, jev_hash)
    ctx = context.build_ad_context(session, ingestion, model=result, catalog=catalog, text=text)
    ctx = dataclasses.replace(ctx, triage_probability=p)
    below = None if jev_hash is None else JEV_DISCARD_BELOW
    arbiter_below = JEV_ARBITER_BELOW if arbiter and jev_hash is not None else None
    verdict = decide_lane_0(ctx, external_discard_below=below, arbiter_below=arbiter_below)
    if verdict.lane != LANE_DISCARD:
        return _Decision(discard=False)
    detail: dict[str, Any] | None = None
    reason = result.discard_reason
    if p is not None:
        detail = {"jev_p": p}
        if result.degraded or result.failure:
            # Rol 2: el asistente no propuso y el motor descartó por Jev.
            detail["decider"] = JEV_DECIDER
            reason = (
                DiscardReason.OFF_FOCUS.value if has_digit(ctx.text) else DiscardReason.NO_DATA.value
            )
        elif pilot_in_text(ctx):
            # Con el piloto en el texto, el motor solo descarta si Jev arbitró.
            detail["decider"] = JEV_ARBITER_DECIDER
    return _Decision(discard=True, reason=reason, detail=detail)


def _write_discard(
    session: Session, ingestion: RawIngestion, proposal: LlmProposal, decision: _Decision
) -> None:
    ingestion.processing_status = ProcessingStatus.DISCARDED
    ingestion.discard_reason = decision.reason
    llm_store.save_review(
        session,
        proposal=proposal,
        outcome=ReviewOutcome.DISCARDED,
        discard_reason=decision.reason,
        rows=llm_store.review_rows(proposal, []),
    )
    record_curation_event(
        session,
        ingestion_id=ingestion.id,
        action=CurationAction.DISCARDED,
        mode=CurationMode.AUTO,
        discard_reason=decision.reason,
        proposal_id=proposal.id,
        shown_at=None,
        decided_at=utc_now(),
        detail=decision.detail,
    )


def apply_lane_0(
    session: Session,
    ingestion: RawIngestion,
    proposal: LlmProposal,
    result: AdProposal,
    *,
    text: str,
    catalog: context.Catalog,
    jev_hash: str | None = None,
    arbiter: bool = False,
) -> bool:
    """Descarta el anuncio si el motor lo manda al carril 0. `True` si lo hizo.

    `text` es el texto que vio el modelo, ya redactado: para una imagen, el
    OCR que acaba de correr el pre-cálculo (no se transcribe dos veces). No
    hace commit: el descarte va en la transacción de la propuesta.

    Lo que escribe es lo mismo que el lote de imágenes
    (`discard_images_batch`), con `mode = auto`: el estado y el motivo del
    modelo en `raw_ingestions`, la revisión en `llm_reviews` (para que quede
    fuera de H0 como todo lo asistido) y el evento de bitácora.

    `jev_hash` (de `jev_questions_hash`) enciende a Jev como segundo juez
    (slice jev-decisor): el motor recibe la P(sí) de la señal vigente con
    `JEV_DISCARD_BELOW`. Si descarta un anuncio cuyo asistente no propuso
    (Rol 2), el motivo lo pone una regla, porque Jev no distingue: con algún
    dígito en el texto, `off_focus`; sin ninguno, `no_data` (sin dígito no hay
    precio). El evento guarda la P(sí) usada y, en el Rol 2, quién decidió.

    `arbiter` (de `SAIMC_LANE0_JEV_ARBITER_ENABLED`, slice jev-desacuerdo)
    suma a Jev como árbitro con `JEV_ARBITER_BELOW`; solo cuenta con
    `jev_hash`. Esos descartes guardan el motivo del modelo y
    `decider = jev_arbiter`.
    """
    decision = _decide(
        session, ingestion, result, text=text, catalog=catalog, jev_hash=jev_hash, arbiter=arbiter
    )
    if not decision.discard:
        return False
    _write_discard(session, ingestion, proposal, decision)
    return True


# --- lo que ya está pendiente ------------------------------------------------------


def run_pending(
    session: Session,
    settings: Settings,
    *,
    execute: bool,
    out: Callable[[str], None] = print,
) -> int:
    """Aplica el carril 0 a los pendientes que YA tienen propuesta del modelo.

    El pre-cálculo solo aplica el carril 0 al generar una propuesta; cuando el
    motor cambia (p. ej. la regla de «sin ningún dígito», 2026-10-13), lo que
    ya estaba pendiente no se vuelve a mirar. Esto lo hace. Respeta el
    interruptor de cada tipo y, sin `execute`, no escribe nada ni gasta API.

    Con Jev decidiendo, también dice cuántos son del árbitro (slice
    jev-desacuerdo); con el árbitro apagado, cuántos sumaría encendido: es el
    paso en seco antes de encenderlo.
    """
    types = enabled_types(settings)
    if not types:
        out(
            "El carril 0 está apagado (SAIMC_LANE0_TEXT_ENABLED / SAIMC_LANE0_IMAGE_ENABLED): "
            "no se descarta nada."
        )
        return 2
    catalog = context.load_catalog(session)
    jev_hash = jev_questions_hash(session, settings)
    stmt = (
        select(RawIngestion)
        .where(
            RawIngestion.processing_status == ProcessingStatus.PENDING,
            RawIngestion.content_type.in_(types),
        )
        .order_by(RawIngestion.captured_at.desc(), RawIngestion.id)
    )
    arbiter = jev_hash is not None and settings.lane0_jev_arbiter_enabled
    would = by_arbiter = arbiter_off = 0
    for ingestion in list(session.execute(stmt).scalars()):
        if ingestion.original_text is None:
            continue  # una imagen sin transcribir: su texto no se conoce
        proposal = llm_store.latest_ok_proposal(session, ingestion.id)
        if proposal is None and jev_hash is not None:
            # Con Jev decidiendo, una propuesta degradada también sirve (Rol 2).
            proposal = llm_store.latest_proposal(session, ingestion.id)
        if proposal is None:
            continue
        result = context.stored_proposal(session, proposal, catalog)
        # Con Jev decidiendo se consulta siempre al árbitro: apagado, solo se
        # cuenta lo que descartaría (el motor sin árbitro manda eso a la cola).
        decision = _decide(
            session,
            ingestion,
            result,
            text=ingestion.original_text,
            catalog=catalog,
            jev_hash=jev_hash,
            arbiter=jev_hash is not None,
        )
        if not decision.discard:
            continue
        if decision.decider == JEV_ARBITER_DECIDER:
            if not arbiter:
                arbiter_off += 1
                continue
            by_arbiter += 1
        would += 1
        if execute:
            _write_discard(session, ingestion, proposal, decision)
            session.commit()
    out(f"{'Descartados' if execute else 'Descartaría'}: {would}")
    if arbiter:
        out(f"De ellos, por Jev como árbitro: {by_arbiter}")
    elif jev_hash is not None:
        out(f"Con {JEV_ARBITER_SWITCH}=true descartaría además: {arbiter_off}")
    if not execute:
        out("EN SECO: no se escribió nada. Con --execute se descartan.")
    return 0


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m saimc.validation.lane0",
        description="Aplica el carril 0 a los pendientes que ya tienen propuesta. En seco por defecto.",
    )
    parser.add_argument("--execute", action="store_true", help="Descarta de verdad.")
    args = parser.parse_args(argv)
    with session_scope() as session:
        code = run_pending(session, get_settings(), execute=args.execute)
    sys.exit(code)


if __name__ == "__main__":
    main()


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
    #: `True` si está en la muestra por la auditoría completa del primer tramo
    #: del árbitro, no por el sorteo: no entra en la cota de todo el tipo.
    census: bool = False


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

    @property
    def census_count(self) -> int:
        """Los que no salieron al azar: los del árbitro en su primer tramo."""
        return sum(item.census for item in self.texts)


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


def _census(
    week_start: date,
    population: _Population,
    drawn: Sequence[tuple[RawIngestion, CurationEvent]],
) -> list[tuple[RawIngestion, CurationEvent]]:
    """Los descartes del árbitro de la semana que el sorteo dejó afuera, si
    las semanas anteriores suman menos de `ARBITER_FULL_AUDIT` del árbitro.

    Se cuenta la población y no lo auditado: así la muestra de una semana no
    cambia según lo que el operador ya auditó de otras.
    """
    earlier = sum(
        _decided_by(event, JEV_ARBITER_DECIDER)
        for week, by_type in population.items()
        if week < week_start
        for _, event in by_type.get(TEXT, [])
    )
    if earlier >= ARBITER_FULL_AUDIT:
        return []
    chosen = {ingestion.id for ingestion, _ in drawn}
    return [
        (ingestion, event)
        for ingestion, event in population.get(week_start, {}).get(TEXT, [])
        if ingestion.id not in chosen and _decided_by(event, JEV_ARBITER_DECIDER)
    ]


def _sample(
    session: Session, week_start: date, population: _Population, today: date | None
) -> WeekSample:
    by_type = population.get(week_start, {})
    counts = {t: len(by_type.get(t, [])) for t in TYPES}
    if not is_closed(week_start, today):
        return WeekSample(week_start=week_start, closed=False, population=counts)
    drawn = _draw(week_start, by_type)
    census = _census(week_start, population, drawn[TEXT])
    verdicts = _verdicts(
        session,
        [ingestion.id for pairs in [*drawn.values(), census] for ingestion, _ in pairs],
    )

    def items(
        pairs: Iterable[tuple[RawIngestion, CurationEvent]], *, full: bool = False
    ) -> list[AuditItem]:
        return [AuditItem(ingestion=i, event=e, verdict=verdicts.get(i.id), census=full) for i, e in pairs]

    texts = items(drawn[TEXT]) + items(census, full=True)
    texts.sort(key=lambda item: (item.event.decided_at, str(item.ingestion.id)))
    return WeekSample(
        week_start=week_start,
        closed=True,
        population=counts,
        texts=texts,
        images=items(drawn[IMAGE]),
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
    #: `JEV_DECIDER` si la cota es solo de lo que descartó Jev solo
    #: (`jev_bound`), `JEV_ARBITER_DECIDER` si es de lo que arbitró
    #: (`arbiter_bound`); `None` para la de todo el tipo.
    decider: str | None = None

    @property
    def target(self) -> float:
        return TARGETS[self.content_type]

    @property
    def switch(self) -> str:
        """La variable que apaga lo que mide esta cota."""
        return _DECIDER_SWITCHES.get(self.decider, SWITCHES[self.content_type])

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
    semanas cerradas: k = rescatados, n = rescatados + bien descartados.

    Lo que está en la muestra solo por la auditoría completa del árbitro no
    salió al azar: no entra; si se rescata, cuenta como rescatado afuera."""
    population = _population(session)
    k = dict.fromkeys(TYPES, 0)
    n = dict.fromkeys(TYPES, 0)
    sampled: set[uuid.UUID] = set()
    for week in population:
        sample = _sample(session, week, population, today)
        for content_type in TYPES:
            for item in sample.by_type(content_type):
                if item.census:
                    continue
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


def jev_bound(session: Session, *, today: date | None = None) -> Bound:
    """La cota de lo que Jev descartó solo (Rol 2, slice jev-decisor): lo
    mismo que `audit_bounds` para texto, pero solo con los auditados cuyo
    evento dice `decider = jev`. Rol 1 no necesita cota propia: Jev ahí solo
    puede quitar descartes, nunca agregar uno. Su meta es la del texto."""
    return _decider_bound(session, JEV_DECIDER, today)


def arbiter_bound(session: Session, *, today: date | None = None) -> Bound:
    """La cota de lo que Jev arbitró (slice jev-desacuerdo): solo los
    auditados cuyo evento dice `decider = jev_arbiter`, con los del primer
    tramo auditado completo. Su meta es la del texto."""
    return _decider_bound(session, JEV_ARBITER_DECIDER, today)


def _decider_bound(session: Session, decider: str, today: date | None) -> Bound:
    population = _population(session)
    k = n = outside = 0
    sampled: set[uuid.UUID] = set()
    for week in population:
        for item in _sample(session, week, population, today).by_type(TEXT):
            sampled.add(item.ingestion.id)
            if item.verdict is None or not _decided_by(item.event, decider):
                continue
            n += 1
            k += item.verdict == CurationAction.RESCUED
    decided = {
        ingestion.id
        for by_type in population.values()
        for ingestion, event in by_type.get(TEXT, [])
        if _decided_by(event, decider)
    }
    for ingestion_id, verdict in _verdicts(session, decided).items():
        outside += verdict == CurationAction.RESCUED and ingestion_id not in sampled
    return Bound(TEXT, k, n, outside, decider=decider)


def _decided_by(event: CurationEvent, decider: str) -> bool:
    detail = event.detail
    return isinstance(detail, dict) and detail.get("decider") == decider


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
