"""Fixtures compartidas.

Los tests corren contra SQLite en memoria, no contra PostgreSQL. Lo que se está
probando acá es el manejo de archivos y el ciclo de la bandeja, no dialecto SQL:
levantar una base real por test costaría tiempo sin agregar cobertura útil.

El esquema real de PostgreSQL se verifica aplicando la migración de Alembic.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from saimc.models import Base


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
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
