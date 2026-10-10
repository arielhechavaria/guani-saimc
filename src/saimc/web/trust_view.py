"""Por qué confiamos en cada dato, por celda (B4, `docs/spec-slice-B4.md`).

De qué está hecho el n de una celda (producto × provincia): cuántas filas hizo
una persona, cuántas aceptó el sistema y cuántas de estas revisó una persona
después (`prices_history.provenance`), y en qué estado de validación quedaron.

Solo conteos (§6.5, §12.6): ni porcentajes, ni «confiable», ni umbral. Ningún
canal, emisor, texto ni id de anuncio entra acá.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from saimc.cells import RetailObservation
from saimc.models import Provenance
from saimc.validation.types import ValidationStatus


@dataclass(frozen=True)
class CellTrust:
    n: int
    human: int
    auto: int
    audited: int
    #: `validation_status = approved`: pasó las seis reglas.
    approved: int
    #: Cualquier otro estado del motor: hay una señal que mirar.
    flagged: int
    #: Estado nulo: fila anterior a la 0027 que `revalidate` no llenó.
    unevaluated: int

    @property
    def by_system(self) -> int:
        """Las que aceptó el sistema, auditadas o no."""
        return self.auto + self.audited

    @property
    def line(self) -> str:
        """La línea de la celda: «12 anuncios · 9 a mano · 3 por el sistema».

        Un grupo en cero no se nombra; si todo es a mano, «todos a mano»."""
        noun = "anuncio" if self.n == 1 else "anuncios"
        if self.by_system == 0:
            parte = "todos a mano" if self.n > 1 else "a mano"
            return f"{self.n} {noun} · {parte}"
        partes = [f"{self.n} {noun}"]
        if self.human:
            partes.append(f"{self.human} a mano")
        partes.append(f"{self.by_system} por el sistema")
        return " · ".join(partes)

    @property
    def summary(self) -> str:
        """La procedencia de todo el n en una frase, para el subtítulo y
        «Sobre los datos»: «25 anuncios: 20 revisados a mano y 5 aceptados por
        el sistema (2 ya revisados después por una persona)». Sin filas del
        sistema, no nombra al sistema."""
        noun = _plural(self.n, "anuncio", "anuncios")
        if self.by_system == 0:
            return f"{self.n} {noun} {_plural(self.n, 'revisado', 'revisados')} a mano"
        system = f"{self.by_system} {_plural(self.by_system, 'aceptado', 'aceptados')} por el sistema"
        if self.audited:
            system += (
                f" ({self.audited} {_plural(self.audited, 'ya revisado', 'ya revisados')}"
                " después por una persona)"
            )
        if self.human == 0:
            return f"{self.n} {noun} {system}"
        human = f"{self.human} {_plural(self.human, 'revisado', 'revisados')} a mano"
        return f"{self.n} {noun}: {human} y {system}"


def _plural(n: int, singular: str, plural: str) -> str:
    return singular if n == 1 else plural


def sum_trust(parts: Sequence[CellTrust | None]) -> CellTrust | None:
    """El desglose de varias celdas juntas, o `None` si no hay ninguna o a
    alguna le falta (la suma de algo desconocido no se sabe)."""
    if not parts or any(p is None for p in parts):
        return None
    cells = [p for p in parts if p is not None]
    return CellTrust(
        n=sum(c.n for c in cells),
        human=sum(c.human for c in cells),
        auto=sum(c.auto for c in cells),
        audited=sum(c.audited for c in cells),
        approved=sum(c.approved for c in cells),
        flagged=sum(c.flagged for c in cells),
        unevaluated=sum(c.unevaluated for c in cells),
    )


def trust_of(observations: Sequence[RetailObservation]) -> CellTrust | None:
    """El desglose de una celda, o `None` si no hay filas o alguna no trae
    procedencia (la columna es NOT NULL: solo pasa con datos sintéticos).
    No se cuenta como a mano lo que no se sabe."""
    if not observations:
        return None
    known = {p.value for p in Provenance}
    if any(o.provenance not in known for o in observations):
        return None
    approved = sum(1 for o in observations if o.validation_status == ValidationStatus.APPROVED)
    unevaluated = sum(1 for o in observations if o.validation_status is None)
    return CellTrust(
        n=len(observations),
        human=sum(1 for o in observations if o.provenance == Provenance.HUMAN),
        auto=sum(1 for o in observations if o.provenance == Provenance.AUTO),
        audited=sum(1 for o in observations if o.provenance == Provenance.AUDITED),
        approved=approved,
        flagged=len(observations) - approved - unevaluated,
        unevaluated=unevaluated,
    )
