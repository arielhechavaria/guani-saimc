"""Base declarativa compartida por todos los modelos."""

from datetime import UTC, datetime

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def utc_now() -> datetime:
    """Reloj compartido para los `created_at` de todos los modelos.

    Vive acá y no en `raw_ingestion.py` para que la Processing Layer no
    dependa de la Raw Layer por una simple función de reloj.
    """
    return datetime.now(UTC)
