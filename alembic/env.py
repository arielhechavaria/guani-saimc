"""Entorno de Alembic.

La URL de la base sale de la configuración de la aplicación, no de alembic.ini:
una sola fuente de verdad y nada de credenciales versionadas.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from saimc.config import get_settings

# Importar los modelos registra las tablas en Base.metadata, que es lo que
# `alembic revision --autogenerate` compara contra la base real.
from saimc.models import Base  # noqa: F401
import saimc.models.raw_ingestion  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
