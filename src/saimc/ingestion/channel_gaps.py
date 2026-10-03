"""K4 — aviso de hueco de ingesta por canal.

Entre el 2026-09-08 y el 2026-09-20 no entró nada y nadie lo notó a tiempo: el
indicador global de `/control` pasa a ámbar a los 2 días y solo se ve ahí. Acá
se mide el silencio de cada canal activo contra SU propio ritmo, y se avisa en
`/curation` y en `/control`.

La regla: un canal activo avisa cuando el tiempo desde su último mensaje supera
`max(PISO, FACTOR × p95 de sus huecos entre mensajes consecutivos en la
ventana)`. Se calcula en vivo, sin configuración por canal.

Casos de borde, decididos el 2026-10-03:

- Canal inactivo: nunca avisa.
- Canal activo que **nunca** recibió un mensaje: no avisa. Está recién dado de
  alta y no hay ritmo contra el cual estar callado.
- Canal con menos de 2 mensajes en la ventana: no tiene huecos, rige el piso.
  Si su último mensaje (de cuando sea) tiene más de `PISO` horas, avisa.

El p95 se calcula en Python, con la interpolación lineal de
`percentile_cont(0.95)` de PostgreSQL, para que SQLite (tests) y PostgreSQL
(producción) coincidan.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import pairwise

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, aliased

from saimc.models import Channel, RawIngestion
from saimc.validation.fence import quantile

# Umbrales de AVISO elegidos por el operador el 2026-10-03 a partir de una
# medición de ese día: el p95 de los huecos estaba entre 9 y 27 h en los seis
# canales activos más movidos (el silencio de la noche) y en 58 y 67 h en los
# dos lentos; los huecos máximos de 250–400 h eran la caída de septiembre y no
# se usaron. NO son umbrales de publicación: §12.6 no los gobierna, solo
# deciden cuándo llamarle la atención al operador. Son ajustables.
GAP_FLOOR_HOURS = 24.0
GAP_P95_FACTOR = 2.0
GAP_WINDOW_DAYS = 30


@dataclass(frozen=True)
class ChannelRef:
    """Lo mínimo de un canal que la regla necesita."""

    id: uuid.UUID
    name: str
    is_active: bool


@dataclass(frozen=True)
class SilentChannel:
    name: str
    silence_hours: float
    threshold_hours: float


@dataclass(frozen=True)
class GapReport:
    """Resultado de la regla: qué canales están callados y si lo están todos."""

    silent: tuple[SilentChannel, ...] = ()
    # Todos los canales activos con línea de base están callados a la vez.
    all_silent: bool = False
    # Horas desde el mensaje más reciente de cualquiera de ellos.
    hours_without_intake: float = 0.0

    @property
    def headline(self) -> str:
        """El mensaje único cuando calla todo; vacío en cualquier otro caso."""
        if not self.all_silent:
            return ""
        return f"No entra nada desde hace {int(self.hours_without_intake)} h"

    @property
    def hint(self) -> str:
        if not self.all_silent:
            return ""
        return "Lo más habitual es que WAHA esté caído o deslogueado: revisá /control."


def threshold_hours(p95_hours: float | None) -> float:
    """Umbral de aviso de un canal. Sin huecos medidos rige el piso."""
    if p95_hours is None:
        return GAP_FLOOR_HOURS
    return max(GAP_FLOOR_HOURS, GAP_P95_FACTOR * p95_hours)


def _as_utc(value: datetime) -> datetime:
    # SQLite (tests) devuelve fechas sin zona; lo guardado siempre es UTC.
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def p95_gap_hours(timestamps: Sequence[datetime], *, now: datetime) -> float | None:
    """p95 de los huecos entre mensajes consecutivos dentro de la ventana.

    `None` si en la ventana hay menos de 2 mensajes.
    """
    inicio = now - timedelta(days=GAP_WINDOW_DAYS)
    en_ventana = sorted(t for t in map(_as_utc, timestamps) if inicio <= t <= now)
    if len(en_ventana) < 2:
        return None
    huecos = [(b - a).total_seconds() / 3600 for a, b in pairwise(en_ventana)]
    return quantile(sorted(huecos), 0.95)


def channel_gap_alerts(
    now: datetime,
    channels: Sequence[ChannelRef],
    timestamps_by_channel: Mapping[uuid.UUID, Sequence[datetime]],
) -> GapReport:
    """Aplica la regla. Puro: no toca base ni reloj.

    `timestamps_by_channel` trae las marcas de la ventana y, además, el último
    mensaje de cada canal aunque sea anterior a la ventana.
    """
    silent: list[SilentChannel] = []
    con_base = 0
    silencios_con_base: list[float] = []
    for canal in channels:
        marcas = timestamps_by_channel.get(canal.id)
        if not canal.is_active or not marcas:
            continue
        con_base += 1
        ultimo = max(map(_as_utc, marcas))
        silencio = max(0.0, (now - ultimo).total_seconds() / 3600)
        silencios_con_base.append(silencio)
        umbral = threshold_hours(p95_gap_hours(marcas, now=now))
        if silencio > umbral:
            silent.append(SilentChannel(canal.name, silencio, umbral))

    if not silent:
        return GapReport()
    silent.sort(key=lambda s: s.silence_hours / s.threshold_hours, reverse=True)
    todos = len(silent) == con_base
    return GapReport(
        silent=tuple(silent),
        all_silent=todos,
        hours_without_intake=min(silencios_con_base),
    )


def load_channel_gap_report(db: Session, *, now: datetime | None = None) -> GapReport:
    """Carga con dos consultas (canales; marcas) y aplica la regla.

    La segunda trae las marcas de la ventana y, por canal, la más reciente de
    todas aunque quede fuera de ella.
    """
    ahora = now or datetime.now(UTC)
    inicio = ahora - timedelta(days=GAP_WINDOW_DAYS)

    canales = [
        ChannelRef(id=c.id, name=c.name, is_active=c.is_active)
        for c in db.execute(select(Channel)).scalars()
    ]

    otra = aliased(RawIngestion)
    max_del_canal = (
        select(func.max(otra.captured_at))
        .where(otra.channel_id == RawIngestion.channel_id)
        .scalar_subquery()
    )
    pares = db.execute(
        select(RawIngestion.channel_id, RawIngestion.captured_at).where(
            RawIngestion.channel_id.is_not(None),
            or_(
                RawIngestion.captured_at >= inicio,
                RawIngestion.captured_at == max_del_canal,
            ),
        )
    ).all()

    marcas: dict[uuid.UUID, list[datetime]] = {}
    for channel_id, captured_at in pares:
        if channel_id is not None:  # el WHERE ya los excluye; es para el tipado
            marcas.setdefault(channel_id, []).append(captured_at)
    return channel_gap_alerts(ahora, canales, marcas)
