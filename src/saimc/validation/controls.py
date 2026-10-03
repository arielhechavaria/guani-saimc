"""Controles de `spec.md` §10.7 sobre una celda (slice C3). Código puro.

`cell_controls` recibe las filas de UNA celda, ya leídas y normalizadas, y
devuelve mediciones. No consulta la base, no escribe y no decide nada: quien
arma las filas es `validation.control_cells`, y quien actúe sobre el resultado
será B2b / B2c.

Dos cosas distintas salen de acá:

- **El índice de control (C3).** El índice principal es la mediana de la celda
  con todas sus filas. El de control es la misma mediana solo con las filas que
  verificó una persona (`provenance` `human` o `audited`). La celda **diverge**
  cuando la mediana principal cae fuera del cerco de las filas de control: el
  mismo cerco de la regla 2 del motor (`validation.fence`), sin ningún umbral
  nuevo. Con menos filas de control que el mínimo, la celda queda **sin ancla**
  y no se dice que diverge: no hay contra qué.
- **Cuatro señales de coordinación (C4).** Son conteos, sin umbral (§12.6): qué
  parte aporta el canal que más aporta, lo mismo por emisor, cuántas filas de
  canales distintos cayeron casi a la vez y cuántas repiten el mismo texto.

Es un apartamiento de §10.7, que define la fuente de alta confianza por emisor
(historial, consistencia, antigüedad): eso todavía no se puede medir. Motivo en
`docs/decisions.md` (2026-10-03, «C3») y contrato en `docs/spec-slice-C3.md`.
"""

from __future__ import annotations

import statistics
import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from saimc.models import Provenance
from saimc.validation.fence import robust_fence
from saimc.variants import variant_key

#: Las filas que verificó una persona: las que forman el índice de control.
CONTROL_PROVENANCE = frozenset({Provenance.HUMAN.value, Provenance.AUDITED.value})


@dataclass(frozen=True)
class ControlRow:
    """Una fila de la celda, con lo que los controles necesitan de ella."""

    #: El precio comparable: en CUP y por unidad canónica, el mismo que agrega
    #: la celda pública.
    price: Decimal
    provenance: str
    #: Id del canal de origen ya resuelto, o `None` si no se sabe.
    channel: uuid.UUID | None = None
    #: `raw_ingestions.sender_hash`. Solo se compara: nunca se muestra.
    sender: str | None = None
    #: Hora del anuncio (`raw_ingestions.captured_at`).
    at: datetime | None = None
    #: Texto del anuncio, ya redactado. `None` si la ingesta no tiene.
    text: str | None = None


@dataclass(frozen=True)
class CellControls:
    """Lo que se midió sobre una celda. Conteos, no veredictos."""

    count: int
    #: Índice principal: la mediana de todas las filas.
    median: Decimal
    control_count: int
    #: Índice de control: la mediana de las filas de control. `None` sin ninguna.
    control_median: Decimal | None
    #: Hay filas de control suficientes para comparar.
    anchored: bool
    #: La mediana principal cae fuera del cerco de las filas de control.
    #: Siempre `False` en una celda sin ancla.
    diverges: bool
    #: Filas del canal que más aporta, y cuántas no tienen canal conocido.
    top_channel_rows: int
    rows_without_channel: int
    #: Filas del emisor que más aporta, y cuántas no tienen emisor.
    top_sender_rows: int
    rows_without_sender: int
    #: Filas con otra fila de un canal distinto a menos de la ventana de ráfaga.
    synchronous_rows: int
    #: Filas cuyo texto normalizado repite el de otra fila de otro canal o de
    #: otro emisor, y cuántas quedaron fuera de la comparación por no tener texto.
    identical_text_rows: int
    rows_without_text: int

    @property
    def channel_share(self) -> float | None:
        """Parte de las filas con canal conocido que aporta el que más aporta."""
        identified = self.count - self.rows_without_channel
        return self.top_channel_rows / identified if identified else None

    @property
    def sender_share(self) -> float | None:
        """Lo mismo por emisor, sobre las filas que sí tienen emisor."""
        identified = self.count - self.rows_without_sender
        return self.top_sender_rows / identified if identified else None


def normalize_text(text: str | None) -> str:
    """Forma normal para comparar textos: minúsculas, sin tildes, sin dígitos ni
    espacios. Dos anuncios que solo cambian el precio quedan iguales.

    El plegado de tildes y mayúsculas es el de `variants.variant_key`.
    """
    return "".join(c for c in variant_key(text) if not c.isdigit() and not c.isspace())


def _top(values: Sequence[object]) -> int:
    """Cuántas veces aparece el valor más repetido. 0 con la lista vacía."""
    return max(Counter(values).values(), default=0)


def _synchronous(rows: Sequence[ControlRow], window: timedelta) -> int:
    """Filas con al menos otra de un canal DISTINTO a menos de `window`.

    Una fila sin canal conocido o sin hora no participa: de ella no se puede
    afirmar que viene de otro canal.
    """
    timed = sorted(
        ((row.at, row.channel) for row in rows if row.at is not None and row.channel is not None),
        key=lambda pair: pair[0],
    )
    hit = [False] * len(timed)
    for i, (at, channel) in enumerate(timed):
        for j in range(i + 1, len(timed)):
            other_at, other_channel = timed[j]
            if other_at - at >= window:
                break
            if other_channel != channel:
                hit[i] = hit[j] = True
    return sum(hit)


def _different_origin(a: ControlRow, b: ControlRow) -> bool:
    """Las dos filas vienen de canales distintos o de emisores distintos.

    Solo cuenta lo que se sabe: un canal o un emisor desconocido no se da por
    distinto.
    """
    channels = a.channel is not None and b.channel is not None and a.channel != b.channel
    senders = a.sender is not None and b.sender is not None and a.sender != b.sender
    return channels or senders


def _identical_text(rows: Sequence[ControlRow]) -> tuple[int, int]:
    """`(filas con texto repetido desde otro origen, filas sin texto)`.

    Es una igualdad sobre la forma normal, no un puntaje de similitud.
    """
    by_text: dict[str, list[ControlRow]] = {}
    without_text = 0
    for row in rows:
        normal = normalize_text(row.text)
        if normal:
            by_text.setdefault(normal, []).append(row)
        else:
            without_text += 1
    repeated = sum(
        1
        for group in by_text.values()
        for row in group
        if any(_different_origin(row, other) for other in group)
    )
    return repeated, without_text


def cell_controls(
    rows: Sequence[ControlRow], *, min_control_n: int, burst_window: timedelta
) -> CellControls:
    """Mide los controles de §10.7 sobre las filas de una celda.

    `rows` no puede estar vacío: una celda sin filas no existe.
    `min_control_n` es el mínimo de filas de control para que haya ancla
    (`persist.MIN_CELL_N`) y `burst_window` la ventana de la sincronía
    (`settings.burst_window_minutes`): los dos llegan de afuera para que este
    módulo no fije ningún número.
    """
    prices = [row.price for row in rows]
    median = statistics.median(prices)
    control = [row.price for row in rows if row.provenance in CONTROL_PROVENANCE]
    anchored = len(control) >= min_control_n
    diverges = False
    if anchored:
        low, high = robust_fence(control)
        # Misma comparación que la regla 2: los bordes del cerco quedan adentro.
        diverges = not low <= float(median) <= high

    channels = [row.channel for row in rows if row.channel is not None]
    senders = [row.sender for row in rows if row.sender is not None]
    identical, without_text = _identical_text(rows)
    return CellControls(
        count=len(rows),
        median=median,
        control_count=len(control),
        control_median=statistics.median(control) if control else None,
        anchored=anchored,
        diverges=diverges,
        top_channel_rows=_top(channels),
        rows_without_channel=len(rows) - len(channels),
        top_sender_rows=_top(senders),
        rows_without_sender=len(rows) - len(senders),
        synchronous_rows=_synchronous(rows, burst_window),
        identical_text_rows=identical,
        rows_without_text=without_text,
    )
