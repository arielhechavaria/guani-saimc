"""Servicio de conversión monetaria (`spec.md` §6.3 E).

Separado en módulos por seam de test (design.md, D1): `eltoque.py` (HTTP +
parseo puro), `cache.py` (repositorio), `service.py` (política de
resolución, Commit 2), `backfill.py` (CLI, Commit 3).
"""
