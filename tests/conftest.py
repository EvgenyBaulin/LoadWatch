"""Общие фикстуры.

Тесты никогда не читают ``.env``: настройки передаются явно. Тесты с базой
используют ``TEST_DATABASE_URL`` — отдельную базу, которая очищается между тестами.
"""

from __future__ import annotations

import os

os.environ["LOADWATCH_ENV_FILE"] = ""  # до импорта loadwatch.config, иначе он прочитает .env

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
from loadwatch.security.ssrf import TargetResolutionError, TargetUrl
from loadwatch.verification import (
    OwnershipVerifier,
    VerificationInstructions,
    VerificationOutcome,
    VerificationResult,
    VerifierRegistry,
)

ROOT = Path(__file__).resolve().parent.parent
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://loadwatch:loadwatch@localhost:5432/loadwatch_test",
)
TEST_JWT_SECRET = "test-secret-" + "x" * 40
PUBLIC_IP = "93.184.216.34"


class FakeVerifier(OwnershipVerifier):
    """Верификатор без сети: итог задаёт тест через ``outcome``."""

    def __init__(self, method: str) -> None:
        self.method = method  # type: ignore[misc]
        self.outcome = VerificationOutcome.VERIFIED
        self.calls: list[tuple[str, str]] = []

    def instructions(self, target: TargetUrl, token: str) -> VerificationInstructions:
        return VerificationInstructions(self.method, f"{self.method}:{target.host}", token)

    def verify(self, target: TargetUrl, token: str) -> VerificationResult:
        self.calls.append((target.host, token))
        return VerificationResult(self.outcome, "fake")


class FakeDns:
    """Подменный резолвер: по умолчанию любой хост — публичный адрес."""

    def __init__(self) -> None:
        self.records: dict[str, list[str]] = {}

    def __call__(self, host: str, port: int) -> list[str]:
        if host in self.records:
            if not self.records[host]:
                raise TargetResolutionError(host)
            return self.records[host]
        return [PUBLIC_IP]


def _check_disposable(url: str) -> str:
    """Отказывается чистить базу, имя которой не оканчивается на ``_test``."""
    name = make_url(url).database or ""
    if not name.endswith("_test"):
        pytest.exit(f"TEST_DATABASE_URL must point to a *_test database, got {name!r}", 2)
    return url


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Не даёт переменным LoadWatch из терминала влиять на настройки тестов."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


def alembic_config(database_url: str) -> Config:
    """Конфиг Alembic на ``database_url`` без чтения настроек приложения."""
    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["database_url"] = database_url
    config.attributes["configure_logger"] = False
    return config


@pytest.fixture
def make_settings() -> Callable[..., Settings]:
    """Фабрика настроек с безопасными тестовыми значениями; аргументы их переопределяют."""

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
    """Пересоздаёт схему миграциями: откат до base, затем подъём до head."""
    config = alembic_config(_check_disposable(TEST_DATABASE_URL))
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    yield TEST_DATABASE_URL


@pytest.fixture
def clean_database(migrated_database: str) -> Iterator[str]:
    """Очищает все таблицы после теста."""
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
def fake_verifiers() -> dict[str, FakeVerifier]:
    """Верификаторы ``dns`` и ``http`` без сети."""
    return {"dns": FakeVerifier("dns"), "http": FakeVerifier("http")}


@pytest.fixture
def fake_dns() -> FakeDns:
    """Резолвер без сети."""
    return FakeDns()


@pytest.fixture
def make_client(
    clean_database: str,
    make_settings: Callable[..., Settings],
    fake_verifiers: dict[str, FakeVerifier],
    fake_dns: FakeDns,
) -> Iterator[Callable[..., TestClient]]:
    """Фабрика API-клиентов; аргументы переопределяют настройки. Сеть и DNS подменены."""
    apps: list[FastAPI] = []

    def factory(**overrides: Any) -> TestClient:
        app = create_app(make_settings(**overrides))
        app.state.verifiers = VerifierRegistry(list(fake_verifiers.values()))
        app.state.resolver = fake_dns
        apps.append(app)
        return TestClient(app)

    yield factory
    for app in apps:
        app.state.engine.dispose()


@pytest.fixture
def client(make_client: Callable[..., TestClient]) -> TestClient:
    """API-клиент с тестовыми настройками по умолчанию."""
    return make_client()


def register(client: TestClient, email: str = "owner@example.com", **extra: Any) -> Any:
    """Регистрирует пользователя и возвращает JSON ответа."""
    payload = {"email": email, "password": "correct horse battery", **extra}
    response = client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def auth_headers(token: str) -> dict[str, str]:
    """Заголовок Authorization для ``token``."""
    return {"Authorization": f"Bearer {token}"}
