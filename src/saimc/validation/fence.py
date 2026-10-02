"""Cerco robusto de un conjunto de precios. Código puro, sin base ni red.

Nació en `web/price_charts.py` para decidir qué entra en el eje de un gráfico.
Desde A5b lo comparten dos usos, y por eso vive acá:

- **Dibujo** (`price_charts`): los precios fuera del cerco no se borran, se
  marcan en el borde del gráfico.
- **Validación** (`validation.rules`, regla 2 del motor): un precio fuera del
  cerco de su celda es una anomalía y manda el anuncio a revisión humana.

El segundo uso es un apartamiento de `spec.md` §6.2 regla 2 (que da una fórmula
70/30 sin umbral) decidido en `docs/spec-motor-validacion.md` y registrado en
`docs/decisions.md` (2026-10-02, «A5a»).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from decimal import Decimal

#: Cerco robusto: un precio queda fuera si se aleja de la mitad central de su
#: celda más de `FENCE_IQR` anchos de caja, medidos en escala logarítmica porque
#: el error de carga es multiplicativo (×40, ×99, ×700), y nunca con un margen
#: menor a ×`FENCE_MIN_FACTOR`: con precios casi iguales la caja no tiene ancho
#: y sin ese piso cualquier variación normal saldría del cerco.
#:
#: Ya no es solo de dibujo: la regla 2 del motor de validación usa estas mismas
#: dos constantes. Cambiarlas mueve a la vez el eje de los gráficos y qué
#: precios van a revisión.
FENCE_IQR = 3
FENCE_MIN_FACTOR = 2


def quantile(ordered: Sequence[float], q: float) -> float:
    """Cuantil con interpolación lineal (el método por defecto de numpy)."""
    i = (len(ordered) - 1) * q
    lo, hi = math.floor(i), math.ceil(i)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (i - lo)


def robust_fence(prices: Iterable[Decimal]) -> tuple[float, float]:
    """Límites inferior y superior del cerco robusto de un conjunto de precios."""
    logs = sorted(math.log(float(p)) for p in prices if p > 0)
    if not logs:
        return 0.0, math.inf
    q1, q3 = quantile(logs, 0.25), quantile(logs, 0.75)
    margin = max(FENCE_IQR * (q3 - q1), math.log(FENCE_MIN_FACTOR))
    return math.exp(q1 - margin), math.exp(q3 + margin)
