"""Motor de validación §6.2 y carriles (`docs/spec-motor-validacion.md`).

Código puro: `validate_ad` no consulta la base, no hace red y no escribe. Nada
lo llama todavía desde la aplicación; lo usan el informe de sombra (A5c), el
guardado (B1) y los carriles (B2).

Los dos módulos que SÍ leen la base no se importan desde acá, para que el motor
siga siendo puro: `context` (arma el `AdContext` de un anuncio) y `shadow`
(`python -m saimc.validation.shadow`, el informe del modo sombra).

- `types`: la entrada (`AdContext`), los parámetros (`ValidationConfig`) y la
  salida (`AdValidation`, `RowValidation`).
- `rules`: las seis reglas, cada una con resultado `pass` / `fail` / `unknown`.
- `engine`: `validate_ad`, que junta las reglas y decide el carril.
- `fence`: la banda robusta, compartida con los gráficos de la home.
"""

from saimc.validation.engine import validate_ad
from saimc.validation.types import (
    LANE_ACCEPT,
    LANE_DISCARD,
    LANE_REVIEW,
    AdContext,
    AdValidation,
    CellReference,
    ChannelContext,
    ExternalSignal,
    Outcome,
    RecentRow,
    RowContext,
    RowValidation,
    ValidationConfig,
    ValidationStatus,
)

__all__ = [
    "LANE_ACCEPT",
    "LANE_DISCARD",
    "LANE_REVIEW",
    "AdContext",
    "AdValidation",
    "CellReference",
    "ChannelContext",
    "ExternalSignal",
    "Outcome",
    "RecentRow",
    "RowContext",
    "RowValidation",
    "ValidationConfig",
    "ValidationStatus",
    "validate_ad",
]
