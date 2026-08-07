"""Dependencias inyectables para los endpoints de FastAPI."""

from collections.abc import Iterator

from sqlalchemy.orm import Session

from saimc.db import session_scope


def get_db() -> Iterator[Session]:
    """Sesión por request: reusa `session_scope` (commit al salir, rollback si falla).

    Los tests reemplazan esta dependencia con `app.dependency_overrides[get_db]`
    para inyectar la sesión SQLite en memoria de `tests/conftest.py`.
    """
    with session_scope() as session:
        yield session
