"""Shared fixtures.

Tests never read ``.env``: configuration is passed explicitly. Database tests use
``TEST_DATABASE_URL`` (a separate database that is wiped between tests).
"""

from __future__ import annotations

import os

os.environ["LOADWATCH_ENV_FILE"] = ""  # must happen before loadwatch.config is imported

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, make_url, text

from loadwatch.api.main import create_app
from loadwatch.config import Settings

ROOT = Path(__file__).resolve().parent.parent
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://loadwatch:loadwatch@localhost:5432/loadwatch_test",
)
TEST_JWT_SECRET = "test-secret-" + "x" * 40


def _check_disposable(url: str) -> str:
    """Refuse to wipe a database whose name does not end with ``_test``."""
    name = make_url(url).database or ""
    if not name.endswith("_test"):
        pytest.exit(f"TEST_DATABASE_URL must point to a *_test database, got {name!r}", 2)
    return url


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep exported LoadWatch variables of the shell from changing test settings."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


def alembic_config(database_url: str) -> Config:
    """Alembic config pointed at ``database_url`` without touching app settings."""
    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["database_url"] = database_url
    config.attributes["configure_logger"] = False
    return config


@pytest.fixture
def make_settings() -> Callable[..., Settings]:
    """Factory for settings with safe test defaults; keyword arguments override."""

    def factory(**overrides: Any) -> Settings:
        values: dict[str, Any] = {
            "database_url": TEST_DATABASE_URL,
            "jwt_secret": TEST_JWT_SECRET,
        }
        values.update(overrides)
        return Settings(_env_file=None, **values)

    return factory


@pytest.fixture(scope="session")
def migrated_database() -> Iterator[str]:
    """Rebuild the test schema from migrations (down to base, then up to head)."""
    config = alembic_config(_check_disposable(TEST_DATABASE_URL))
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    yield TEST_DATABASE_URL


@pytest.fixture
def clean_database(migrated_database: str) -> Iterator[str]:
    """Empty every table after the test."""
    yield migrated_database
    engine = create_engine(migrated_database)
    with engine.begin() as connection:
        tables = connection.scalars(
            text(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
            )
        ).all()
        if tables:
            connection.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
    engine.dispose()


@pytest.fixture
def make_client(
    clean_database: str, make_settings: Callable[..., Settings]
) -> Iterator[Callable[..., TestClient]]:
    """Factory for API clients; keyword arguments override settings."""
    apps: list[FastAPI] = []

    def factory(**overrides: Any) -> TestClient:
        app = create_app(make_settings(**overrides))
        apps.append(app)
        return TestClient(app)

    yield factory
    for app in apps:
        app.state.engine.dispose()


@pytest.fixture
def client(make_client: Callable[..., TestClient]) -> TestClient:
    """API client with default test settings."""
    return make_client()


def register(client: TestClient, email: str = "owner@example.com", **extra: Any) -> Any:
    """Register a user and return the JSON response."""
    payload = {"email": email, "password": "correct horse battery", **extra}
    response = client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def auth_headers(token: str) -> dict[str, str]:
    """Authorization header for ``token``."""
    return {"Authorization": f"Bearer {token}"}
