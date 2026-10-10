"""Настройки приложения из переменных окружения и профили ресурсов.

Все жёсткие лимиты живут здесь. ``RESOURCE_PROFILE`` задаёт значения по
умолчанию для лимитов, зависящих от железа; каждый из них можно переопределить
своей переменной окружения.
"""

from __future__ import annotations

import os
from enum import StrEnum
from functools import lru_cache
from typing import Any, Final, Literal

from cryptography.fernet import Fernet
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ResourceProfile(StrEnum):
    """Тариф сервера, под который настроен сервис."""

    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"


class RegistrationMode(StrEnum):
    """Кто может зарегистрироваться."""

    OPEN = "open"
    CLOSED = "closed"
    INVITE = "invite"


class Environment(StrEnum):
    """Окружение развёртывания."""

    DEVELOPMENT = "development"
    PRODUCTION = "production"


# Стартовые значения; реальный потолок генератора меряет scripts/benchmark_runner.py.
PROFILE_DEFAULTS: Final[dict[ResourceProfile, dict[str, int]]] = {
    # 1 vCPU, 1 ГБ RAM, 200 Мбит/с
    ResourceProfile.SMALL: {
        "max_rps": 150,
        "max_concurrency": 100,
        "max_duration_sec": 120,
        "max_bandwidth_mbit": 100,
        "max_global_concurrent_runs": 1,
        "api_workers": 1,
        "db_pool_size": 5,
    },
    # Те же CPU и RAM, канал 1 Гбит/с: растёт лимит полосы, RPS упирается в процессор.
    ResourceProfile.MEDIUM: {
        "max_rps": 150,
        "max_concurrency": 100,
        "max_duration_sec": 120,
        "max_bandwidth_mbit": 500,
        "max_global_concurrent_runs": 1,
        "api_workers": 1,
        "db_pool_size": 5,
    },
    # 1 vCPU, 2 ГБ RAM: второй воркер API и больше одновременных соединений.
    ResourceProfile.LARGE: {
        "max_rps": 200,
        "max_concurrency": 200,
        "max_duration_sec": 180,
        "max_bandwidth_mbit": 100,
        "max_global_concurrent_runs": 1,
        "api_workers": 2,
        "db_pool_size": 8,
    },
}

ENV_FILE_VARIABLE: Final = "LOADWATCH_ENV_FILE"


def _env_file() -> str | None:
    """Путь к dotenv-файлу; пустой ``LOADWATCH_ENV_FILE`` отключает чтение файла."""
    value = os.environ.get(ENV_FILE_VARIABLE, ".env")
    return value or None


class Settings(BaseSettings):
    """Вся конфигурация LoadWatch."""

    model_config = SettingsConfigDict(
        env_file=_env_file(),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    environment: Environment = Environment.DEVELOPMENT
    database_url: str = Field(min_length=1)
    jwt_secret: SecretStr = Field(min_length=32)
    jwt_expire_minutes: int = Field(default=720, ge=5, le=60 * 24 * 30)
    jwt_algorithm: Literal["HS256"] = "HS256"
    secrets_encryption_key: SecretStr | None = None
    public_base_url: str = "http://localhost:5173"
    cors_origins: str = "http://localhost:5173"

    registration_mode: RegistrationMode = RegistrationMode.OPEN
    registration_invite_code: SecretStr | None = None
    auth_rate_limit_per_minute: int = Field(default=10, ge=1)
    verify_rate_limit_per_minute: int = Field(default=6, ge=1)

    resource_profile: ResourceProfile = ResourceProfile.SMALL
    max_rps: int = Field(ge=1)
    max_concurrency: int = Field(ge=1)
    max_duration_sec: int = Field(ge=1)
    max_bandwidth_mbit: int = Field(ge=1)
    max_global_concurrent_runs: int = Field(ge=1)
    api_workers: int = Field(ge=1)
    db_pool_size: int = Field(ge=1)
    max_response_bytes: int = Field(default=256 * 1024, ge=1024)
    site_cooldown_sec: int = Field(default=300, ge=0)

    allow_private_targets: bool = False
    reverify_days: int = Field(default=7, ge=1)

    alert_consecutive_runs: int = Field(default=2, ge=1)
    alert_repeat_minutes: int = Field(default=60, ge=1)
    recovery_runs: int = Field(default=2, ge=1)

    retention_days: int = Field(default=90, ge=1)

    telegram_bot_token: SecretStr | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from: str = "loadwatch@example.com"
    smtp_starttls: bool = True

    @model_validator(mode="before")
    @classmethod
    def _apply_profile_defaults(cls, data: Any) -> Any:
        """Заполняет лимиты из профиля, если они не заданы явно."""
        if not isinstance(data, dict):
            return data
        raw_profile = data.get("resource_profile") or ResourceProfile.SMALL
        try:
            profile = ResourceProfile(str(raw_profile).lower())
        except ValueError:
            return data  # неверное имя профиля сообщит валидация поля
        for name, value in PROFILE_DEFAULTS[profile].items():
            if data.get(name) in (None, ""):
                data[name] = value
        return data

    @field_validator(
        "secrets_encryption_key",
        "registration_invite_code",
        "telegram_bot_token",
        "smtp_password",
        mode="before",
    )
    @classmethod
    def _blank_to_none(cls, value: Any) -> Any:
        """Пустые переменные из шаблона считаются незаданными."""
        return None if value == "" else value

    @field_validator("secrets_encryption_key")
    @classmethod
    def _check_fernet_key(cls, value: SecretStr | None) -> SecretStr | None:
        """Отклоняет ключи, непригодные для Fernet, чтобы ошибка была видна при старте."""
        if value is not None:
            try:
                Fernet(value.get_secret_value().encode())
            except ValueError as exc:
                raise ValueError("SECRETS_ENCRYPTION_KEY is not a valid Fernet key") from exc
        return value

    @model_validator(mode="after")
    def _check_invite_mode(self) -> Settings:
        """Режим приглашений без кода бессмыслен."""
        if self.registration_mode is RegistrationMode.INVITE and not self.registration_invite_code:
            raise ValueError("REGISTRATION_INVITE_CODE is required when REGISTRATION_MODE=invite")
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        """Разрешённые CORS origin списком."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Настройки процесса (читаются один раз)."""
    return Settings()
