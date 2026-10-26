"""Motor de validación §6.2 y carriles (`docs/spec-motor-validacion.md`).

Código puro: ni `validate_ad` ni `validate_stored_row` consultan la base, hacen
red o escriben. Lo usan el informe de sombra (A5c), el carril 0 del pre-cálculo
(B2a) y, desde B1, el guardado: cada fila de `prices_history` lleva el estado
que le dio el motor (`docs/spec-slice-B1.md`).

Los módulos que SÍ tocan la base no se importan desde acá, para que el motor
siga siendo puro: `context` (arma el contexto de un anuncio o de sus filas
guardadas), `shadow` (`python -m saimc.validation.shadow`, el informe del modo
sombra), `lane0` (el carril 0 encendido y su auditoría, B2a), `persist` (escribe
la validación en las filas, B1), `revalidate`
(`python -m saimc.validation.revalidate`, B1), `control_cells` (lee las celdas
públicas para los controles de §10.7, C3) y `measure_n`
(`python -m saimc.validation.measure_n`, C3).

- `types`: la entrada (`AdContext`), los parámetros (`ValidationConfig`) y la
  salida (`AdValidation`, `RowValidation`).
- `rules`: las seis reglas, cada una con resultado `pass` / `fail` / `unknown`.
- `engine`: `validate_ad`, que junta las reglas y decide el carril;
  `decide_lane_0`, que decide solo el carril 0 sin configuración (B2a);
  `validate_stored_row`, que valida una fila ya guardada sin decidir carril
  (B1); y `ENGINE_VERSION`.
- `stats`: las cotas de Clopper-Pearson.
- `fence`: la banda robusta, compartida con los gráficos de la home.
- `controls`: `cell_controls`, los controles de §10.7 sobre una celda (C3). No
  es parte del motor: no cambia el estado de ninguna fila.
"""

from saimc.validation.engine import (
    ENGINE_VERSION,
    decide_lane_0,
    import_price_term,
    validate_ad,
    validate_stored_row,
)
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
    "ENGINE_VERSION",
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
    "decide_lane_0",
    "import_price_term",
    "validate_ad",
    "validate_stored_row",
]
