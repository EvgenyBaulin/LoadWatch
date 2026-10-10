"""Миграции совпадают с ORM-моделями."""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

import loadwatch.models  # noqa: F401
from loadwatch.db import Base


def test_models_and_migrations_are_in_sync(migrated_database: str) -> None:
    engine = create_engine(migrated_database)
    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        diff = compare_metadata(context, Base.metadata)
    engine.dispose()
    assert diff == [], f"run `alembic revision --autogenerate`: {diff}"
