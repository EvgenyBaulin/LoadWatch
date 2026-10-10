"""Окружение Alembic: применяет миграции к ``DATABASE_URL``."""

from __future__ import annotations

from logging.config import fileConfig
from typing import Literal

from alembic import context
from sqlalchemy import create_engine, pool

import loadwatch.models  # noqa: F401  # регистрирует все таблицы в Base.metadata
from loadwatch.config import get_settings
from loadwatch.db import Base, UtcDateTime

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _render_item(type_: str, obj: object, autogen_context: object) -> str | Literal[False]:
    """Пишет ``UtcDateTime`` в миграции как обычный ``sa.DateTime(timezone=True)``.

    Миграции не должны зависеть от кода приложения: он меняется, а миграции — нет.
    """
    if type_ == "type" and isinstance(obj, UtcDateTime):
        return "sa.DateTime(timezone=True)"
    return False


def _database_url() -> str:
    """URL, переданный из кода (тесты), иначе настройка приложения."""
    url = config.attributes.get("database_url")
    return str(url) if url else get_settings().database_url


def run_migrations_offline() -> None:
    """Печатает SQL без подключения к базе (``alembic upgrade --sql``)."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Применяет миграции через подключение к базе."""
    engine = create_engine(_database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_item=_render_item,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
