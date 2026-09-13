"""Fixtures compartidas.

Los tests corren contra SQLite en memoria, no contra PostgreSQL. Lo que se está
probando acá es el manejo de archivos y el ciclo de la bandeja, no dialecto SQL:
levantar una base real por test costaría tiempo sin agregar cobertura útil.

El esquema real de PostgreSQL se verifica aplicando la migración de Alembic.
"""

import sys
from collections.abc import Iterator
from pathlib import Path

# El venv compartido tiene `saimc` instalado en editable con `pip install -e`,
# que apunta a UN checkout fijo — no sabe de worktrees. Sin esto, correr
# `pytest` desde un worktree importa `saimc` del OTRO checkout (el que estaba
# activo cuando se instaló), en silencio: mismo nombre de paquete, código
# distinto — un test puede pasar probando un archivo que no es el que se
# editó. `insert(0, ...)` gana contra el `.pth` del sitio porque siempre se
# resuelve antes: es lo primero en `sys.path`, y `saimc` se resuelve una sola
# vez, la primera vez que algo lo importa.
_SRC = str(Path(__file__).resolve().parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import pytest  # noqa: E402 — después del sys.path.insert a propósito
from sqlalchemy import create_engine, event  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from saimc.models import Base  # noqa: E402


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # SQLite ignora las FKs salvo que el pragma esté activo por conexión.
    # Sin esto, los tests de ON DELETE RESTRICT pasarían sin probar nada.
    # Va antes de create_all a propósito: con StaticPool la conexión se
    # abre una sola vez, y un listener registrado después de create_all
    # nunca llega a correr.
    @event.listens_for(engine, "connect")
    def _sqlite_enable_foreign_keys(dbapi_connection, connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db_session:
        yield db_session
    engine.dispose()


@pytest.fixture
def inbox_dir(tmp_path: Path) -> Path:
    path = tmp_path / "inbox"
    path.mkdir()
    return path


@pytest.fixture
def store_dir(tmp_path: Path) -> Path:
    return tmp_path / "data" / "raw"
