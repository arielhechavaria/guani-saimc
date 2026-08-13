"""Dependencias inyectables para los endpoints de FastAPI."""

from collections.abc import Iterator
from datetime import date
from decimal import Decimal

from fastapi import Depends
from sqlalchemy.orm import Session

from saimc.config import Settings, get_settings
from saimc.db import session_scope
from saimc.exchange_rate import Fetcher
from saimc.exchange_rate.eltoque import fetch_rate


def get_db() -> Iterator[Session]:
    """Sesión por request: reusa `session_scope` (commit al salir, rollback si falla).

    Los tests reemplazan esta dependencia con `app.dependency_overrides[get_db]`
    para inyectar la sesión SQLite en memoria de `tests/conftest.py`.
    """
    with session_scope() as session:
        yield session


def get_rate_fetcher(settings: Settings = Depends(get_settings)) -> Fetcher:
    """Liga `eltoque.fetch_rate` a la configuración del proceso (`el_toque_api`/
    `el_toque_timeout`) y lo expone como `Fetcher` — el seam que los tests
    reemplazan con `app.dependency_overrides[get_rate_fetcher]` para nunca
    tocar la red ni el token real de `.env`.
    """

    def _fetch(key: str, day: date) -> Decimal | None:
        return fetch_rate(
            key, day, api_key=settings.el_toque_api, timeout=settings.el_toque_timeout
        )

    return _fetch
