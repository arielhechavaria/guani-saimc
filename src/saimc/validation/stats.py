"""Cotas exactas de una proporción (Clopper-Pearson). Código puro.

Vivía en `validation.shadow` (A5c). Pasó acá en B2a porque la auditoría del
carril 0 (`validation.lane0`) y la pantalla `/curation/auditoria` también la
usan, y `shadow` importa la base y `argparse`: nadie tiene que cargar el
informe de sombra para calcular una cota. `shadow` la sigue reexportando.

Un porcentaje nunca se muestra sin su n y su cota
(`docs/spec-motor-validacion.md`, «Protocolo del modo sombra»).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

#: Nivel de la cota: unilateral al 95%, el de la especificación (0 errores en
#: 60 dan 4,9%).
CONFIDENCE = 0.95


def _binomial_cdf(k: int, n: int, p: float) -> float:
    """P(X ≤ k) con X ~ Binomial(n, p). Se suma en logaritmos (`lgamma`) para
    que n grandes no desborden."""
    if p <= 0:
        return 1.0
    if p >= 1:
        return 1.0 if k >= n else 0.0
    log_p, log_q = math.log(p), math.log1p(-p)
    total = 0.0
    for i in range(k + 1):
        log_comb = math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1)
        total += math.exp(log_comb + i * log_p + (n - i) * log_q)
    return min(total, 1.0)


def clopper_pearson_upper(k: int, n: int, confidence: float = CONFIDENCE) -> float | None:
    """Cota superior exacta (Clopper-Pearson, unilateral) de una proporción.

    Es el p más grande todavía compatible con haber visto `k` o menos en `n`:
    la solución de P(X ≤ k | p) = 1 − confianza, buscada por bisección (la
    probabilidad baja cuando p sube). `None` sin muestra; 1.0 si k = n.
    """
    if n <= 0:
        return None
    if k >= n:
        return 1.0
    alpha = 1.0 - confidence
    low, high = k / n, 1.0
    for _ in range(200):  # 200 mitades: mucho más fino que la precisión de un float
        mid = (low + high) / 2
        if _binomial_cdf(k, n, mid) > alpha:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def clopper_pearson_lower(k: int, n: int, confidence: float = CONFIDENCE) -> float | None:
    """Cota inferior exacta: por simetría, 1 − la cota superior de los fallos."""
    upper = clopper_pearson_upper(n - k, n, confidence)
    return None if upper is None else 1.0 - upper


@dataclass(frozen=True)
class Rate:
    """`k` de `n`. Nunca se muestra el porcentaje sin el n y la cota."""

    k: int = 0
    n: int = 0

    @property
    def rate(self) -> float | None:
        return self.k / self.n if self.n else None

    @property
    def upper(self) -> float | None:
        return clopper_pearson_upper(self.k, self.n)

    @property
    def lower(self) -> float | None:
        return clopper_pearson_lower(self.k, self.n)

    def as_dict(self) -> dict[str, Any]:
        return {"k": self.k, "n": self.n, "rate": self.rate, "upper95": self.upper}


def pct(value: float) -> str:
    """Porcentaje con coma decimal: `9,3 %`."""
    return f"{100 * value:.1f} %".replace(".", ",")


def format_rate(k: int, n: int, *, lower: bool = False) -> str:
    """`k/n (p %; cota superior al 95 %: c %)`. Con `lower`, suma la inferior."""
    rate = Rate(k, n)
    if rate.rate is None or rate.upper is None or rate.lower is None:
        return f"{k}/{n} (sin muestra)"
    bounds = f"cota superior al 95 %: {pct(rate.upper)}"
    if lower:
        bounds = f"cota inferior al 95 %: {pct(rate.lower)}; {bounds}"
    return f"{k}/{n} ({pct(rate.rate)}; {bounds})"
