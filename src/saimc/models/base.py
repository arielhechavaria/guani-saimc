"""Base declarativa compartida por todos los modelos."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
