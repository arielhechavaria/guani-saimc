"""La métrica de la Fase 0: una partición de lo que entró, dos números.

Contrato: `docs/spec-slice-B3.md`, secciones 4 y 5. Todo anuncio cae en
exactamente un casillero, por su estado de hoy y por el modo del evento que lo
resolvió:

- **A** publicado solo: `curated` con un evento `curated` de modo `auto`.
- **H** publicado por el operador: `curated`, sin ese evento.
- **D0** descartado solo: `discarded` y su último descarte es de modo `auto`.
- **Dh** descartado por el operador: `discarded`, cualquier otro caso.
- **P** pendiente.

De ahí salen N1 («Resuelto sin el operador») y los dos extremos de N2
(«Publicado sin el operador»). Ninguno se publica sin el error auditado de lo
que cuenta. No hay metas ni umbrales nuevos (`spec.md` §12.6), y tampoco un
número compuesto del tipo «publicado solo y correcto».

`automation_summary` es la única función que arma esto; la leen `measure.py` y
`/control`. No toca `progress.WeekAutomation`, que cuenta otra cosa.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from saimc.config import Settings
from saimc.models import (
    ContentType,
    CurationAction,
    CurationEvent,
    CurationMode,
    ProcessingStatus,
    RawIngestion,
)
from saimc.progress import _week_start
from saimc.validation import lane0, lane1

#: Semanas cerradas que muestra la serie, más la en curso.
COHORT_WEEKS = 8


@dataclass(frozen=True)
class Partition:
    """Los cinco casilleros de un conjunto de anuncios."""

    a: int = 0
    h: int = 0
    d0: int = 0
    dh: int = 0
    p: int = 0

    @property
    def total(self) -> int:
        return self.a + self.h + self.d0 + self.dh + self.p

    @property
    def n1(self) -> float | None:
        """Resuelto sin el operador: (A + D0) / todo lo que entró."""
        return (self.a + self.d0) / self.total if self.total else None

    @property
    def n2_min(self) -> float | None:
        """Piso de N2: A / (A + H + P), si todos los pendientes fueran válidos.
        `None` («sin datos») cuando A + H + P = 0."""
        base = self.a + self.h + self.p
        return self.a / base if base else None

    @property
    def n2_max(self) -> float | None:
        """Techo de N2: A / (A + H), si ningún pendiente fuera válido. `None`
        cuando A + H = 0: sin ningún válido resuelto no hay techo que calcular."""
        base = self.a + self.h
        return self.a / base if base else None

    def __add__(self, other: Partition) -> Partition:
        return Partition(
            self.a + other.a,
            self.h + other.h,
            self.d0 + other.d0,
            self.dh + other.dh,
            self.p + other.p,
        )


_CASILLEROS = ("a", "h", "d0", "dh", "p")


@dataclass(frozen=True)
class Slice:
    """Una cohorte (o el total), abierta en texto e imagen."""

    week_start: date | None
    text: Partition
    image: Partition
    #: La semana en curso: todavía le entran anuncios.
    partial: bool = False

    @property
    def both(self) -> Partition:
        return self.text + self.image


@dataclass(frozen=True)
class AutomationSummary:
    """Lo que leen `measure.py` y `/control`."""

    #: Ocho semanas cerradas y la en curso, de la más vieja a la más nueva.
    cohorts: list[Slice]
    #: Desde la semana del primer evento de modo `auto`, o vacío si no hay.
    total: Slice
    auto_since: date | None
    #: Falsos descartes auditados del carril 0 (D0), por tipo.
    lane0: Mapping[str, lane0.Bound] = field(default_factory=dict)
    #: Error por anuncio auditado del carril 1 (A).
    lane1: lane1.AuditState = field(default_factory=lambda: lane1.AuditState(0, 0, 0))

    @property
    def provisional(self) -> bool:
        """Algún número se publica sin su error auditado: va rotulado así."""
        return self.lane1.audited == 0 or any(b.n == 0 for b in self.lane0.values())


def _box(
    status: str,
    events: list[tuple[str, str]],
) -> str:
    """El casillero de un anuncio, por su estado de hoy y sus eventos
    `(acción, modo)` en orden de decisión."""
    if status == ProcessingStatus.PENDING:
        return "p"
    if status == ProcessingStatus.CURATED:
        auto = (CurationAction.CURATED, CurationMode.AUTO)
        return "a" if auto in events else "h"
    discards = [mode for action, mode in events if action == CurationAction.DISCARDED]
    return "d0" if discards and discards[-1] == CurationMode.AUTO else "dh"


def _partitions(
    session: Session,
) -> tuple[dict[tuple[date, str], Partition], date | None]:
    """`(semana de ingreso, tipo)` -> su partición, y la semana del primer
    evento de modo `auto`."""
    events: dict[uuid.UUID, list[tuple[str, str]]] = {}
    first_auto: date | None = None
    stmt = select(
        CurationEvent.ingestion_id,
        CurationEvent.action,
        CurationEvent.mode,
        CurationEvent.decided_at,
    ).order_by(CurationEvent.decided_at, CurationEvent.id)
    for ingestion_id, action, mode, decided_at in session.execute(stmt):
        events.setdefault(ingestion_id, []).append((str(action), str(mode)))
        if mode == CurationMode.AUTO and (
            first_auto is None or decided_at.date() < first_auto
        ):
            first_auto = decided_at.date()

    counts: dict[tuple[date, str], dict[str, int]] = {}
    rows = session.execute(
        select(
            RawIngestion.id,
            RawIngestion.processing_status,
            RawIngestion.content_type,
            RawIngestion.created_at,
        )
    )
    for ingestion_id, status, content_type, created_at in rows:
        key = (_week_start(created_at.date()), str(content_type))
        box = _box(str(status), events.get(ingestion_id, []))
        bucket = counts.setdefault(key, dict.fromkeys(_CASILLEROS, 0))
        bucket[box] += 1
    return (
        {key: Partition(**bucket) for key, bucket in counts.items()},
        _week_start(first_auto) if first_auto else None,
    )


def automation_summary(
    session: Session, settings: Settings, *, today: date | None = None
) -> AutomationSummary:
    """La partición por cohorte de ingreso, el total y el error auditado.

    Cohorte = semana (lunes a domingo, la del panel de A2d) de
    `raw_ingestions.created_at`, con el estado de hoy. No se agrupa por semana
    de decisión: eso no ve la cola y una semana sin curaduría daría 100 %.
    """
    current = _week_start(today or date.today())
    partitions, auto_since = _partitions(session)
    empty = Partition()
    text, image = str(ContentType.TEXT), str(ContentType.IMAGE)

    def slice_of(week: date, *, partial: bool = False) -> Slice:
        return Slice(
            week,
            partitions.get((week, text), empty),
            partitions.get((week, image), empty),
            partial=partial,
        )

    starts = [current - timedelta(weeks=COHORT_WEEKS - i) for i in range(COHORT_WEEKS)]
    cohorts = [slice_of(week) for week in starts] + [slice_of(current, partial=True)]

    total_text = total_image = empty
    if auto_since is not None:
        for (week, content_type), partition in partitions.items():
            if week < auto_since:
                continue
            if content_type == text:
                total_text += partition
            else:
                total_image += partition
    return AutomationSummary(
        cohorts=cohorts,
        total=Slice(auto_since, total_text, total_image),
        auto_since=auto_since,
        lane0=lane0.audit_bounds(session, today=today),
        lane1=lane1.audit_state(session, settings, today=today),
    )
