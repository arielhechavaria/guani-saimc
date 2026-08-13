"""Servicio de conversión monetaria (`spec.md` §6.3 E).

Separado en módulos por seam de test (design.md, D1): `eltoque.py` (HTTP +
parseo puro), `cache.py` (repositorio), `service.py` (política de
resolución), `backfill.py` (CLI, Commit 3).
"""

from saimc.exchange_rate.service import (
    NO_RATE,
    Fetcher,
    RateResolution,
    RateResolver,
    anchor_date,
    apply_resolution,
)

__all__ = [
    "NO_RATE",
    "Fetcher",
    "RateResolution",
    "RateResolver",
    "anchor_date",
    "apply_resolution",
]
