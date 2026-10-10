"""Application settings loaded from the environment and resource profiles.

Every hard limit lives here. ``RESOURCE_PROFILE`` supplies defaults for the
resource-bound limits; each of them can still be overridden by its own
environment variable.
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
    """Hardware tier the service is tuned for."""

    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"


class RegistrationMode(StrEnum):
    """Who may create an account."""

    OPEN = "open"
    CLOSED = "closed"
    INVITE = "invite"


class Environment(StrEnum):
    """Deployment environment."""

    DEVELOPMENT = "development"
    PRODUCTION = "production"


# Starting values; real generator ceilings are measured by scripts/benchmark_runner.py.
PROFILE_DEFAULTS: Final[dict[ResourceProfile, dict[str, int]]] = {
    # 1 vCPU, 1 GB RAM, 200 Mbit/s
    ResourceProfile.SMALL: {
        "max_rps": 150,
        "max_concurrency": 100,
        "max_duration_sec": 120,
        "max_bandwidth_mbit": 100,
        "max_global_concurrent_runs": 1,
        "api_workers": 1,
        "db_pool_size": 5,
    },
    # Same CPU/RAM, 1 Gbit/s link: bandwidth cap rises, RPS stays CPU-bound.
    ResourceProfile.MEDIUM: {
        "max_rps": 150,
        "max_concurrency": 100,
        "max_duration_sec": 120,
        "max_bandwidth_mbit": 500,
        "max_global_concurrent_runs": 1,
        "api_workers": 1,
        "db_pool_size": 5,
    },
    # 1 vCPU, 2 GB RAM: second API worker and more concurrent connections.
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
    """Return the dotenv path to read; an empty ``LOADWATCH_ENV_FILE`` disables it."""
    value = os.environ.get(ENV_FILE_VARIABLE, ".env")
    return value or None


class Settings(BaseSettings):
    """All runtime configuration of LoadWatch."""

    model_config = SettingsConfigDict(
        env_file=_env_file(),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    environment: Environment = Environment.DEVELOPMENT
    database_url: str
    jwt_secret: SecretStr = Field(min_length=32)
    jwt_expire_minutes: int = Field(default=720, ge=5, le=60 * 24 * 30)
    jwt_algorithm: Literal["HS256"] = "HS256"
    secrets_encryption_key: SecretStr | None = None
    public_base_url: str = "http://localhost:5173"
    cors_origins: str = "http://localhost:5173"

    registration_mode: RegistrationMode = RegistrationMode.OPEN
    registration_invite_code: SecretStr | None = None
    auth_rate_limit_per_minute: int = Field(default=10, ge=1)

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
        """Fill resource limits from the selected profile unless set explicitly."""
        if not isinstance(data, dict):
            return data
        raw_profile = data.get("resource_profile") or ResourceProfile.SMALL
        try:
            profile = ResourceProfile(str(raw_profile).lower())
        except ValueError:
            return data  # field validation reports the bad profile name
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
        """Treat empty variables from the template as unset."""
        return None if value == "" else value

    @field_validator("secrets_encryption_key")
    @classmethod
    def _check_fernet_key(cls, value: SecretStr | None) -> SecretStr | None:
        """Reject keys that Fernet cannot use, so the error shows at startup."""
        if value is not None:
            try:
                Fernet(value.get_secret_value().encode())
            except ValueError as exc:
                raise ValueError("SECRETS_ENCRYPTION_KEY is not a valid Fernet key") from exc
        return value

    @model_validator(mode="after")
    def _check_invite_mode(self) -> Settings:
        """Invite mode is useless without a code."""
        if self.registration_mode is RegistrationMode.INVITE and not self.registration_invite_code:
            raise ValueError("REGISTRATION_INVITE_CODE is required when REGISTRATION_MODE=invite")
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        """CORS origins as a list."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return process-wide settings (read once)."""
    return Settings()
