"""Настройки и профили ресурсов."""

from collections.abc import Callable

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from loadwatch.config import PROFILE_DEFAULTS, RegistrationMode, ResourceProfile, Settings


def test_small_profile_is_default(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings()
    assert settings.resource_profile is ResourceProfile.SMALL
    assert settings.max_rps == 150
    assert settings.max_concurrency == 100
    assert settings.max_duration_sec == 120
    assert settings.max_bandwidth_mbit == 100
    assert settings.max_global_concurrent_runs == 1
    assert settings.api_workers == 1


@pytest.mark.parametrize("profile", list(ResourceProfile))
def test_profile_supplies_defaults(
    make_settings: Callable[..., Settings], profile: ResourceProfile
) -> None:
    settings = make_settings(resource_profile=profile.value)
    for name, value in PROFILE_DEFAULTS[profile].items():
        assert getattr(settings, name) == value


def test_explicit_value_overrides_profile(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings(resource_profile="large", max_rps=42)
    assert settings.max_rps == 42
    assert settings.api_workers == 2  # остальное по-прежнему из профиля


def test_environment_variable_overrides_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@h/db")
    monkeypatch.setenv("JWT_SECRET", "s" * 40)
    monkeypatch.setenv("RESOURCE_PROFILE", "medium")
    monkeypatch.setenv("MAX_BANDWIDTH_MBIT", "900")
    monkeypatch.setenv("MAX_CONCURRENCY", "")  # пустое значение в .env — «взять из профиля»
    settings = Settings(_env_file=None)
    assert settings.max_bandwidth_mbit == 900
    assert settings.max_concurrency == PROFILE_DEFAULTS[ResourceProfile.MEDIUM]["max_concurrency"]


def test_unknown_profile_rejected(make_settings: Callable[..., Settings]) -> None:
    with pytest.raises(ValidationError):
        make_settings(resource_profile="huge")


def test_short_jwt_secret_rejected(make_settings: Callable[..., Settings]) -> None:
    with pytest.raises(ValidationError):
        make_settings(jwt_secret="short")


def test_invalid_fernet_key_rejected(make_settings: Callable[..., Settings]) -> None:
    with pytest.raises(ValidationError, match="Fernet"):
        make_settings(secrets_encryption_key="not-a-key")


def test_valid_fernet_key_and_blank_values(make_settings: Callable[..., Settings]) -> None:
    key = Fernet.generate_key().decode()
    settings = make_settings(secrets_encryption_key=key, telegram_bot_token="")
    assert settings.secrets_encryption_key is not None
    assert settings.secrets_encryption_key.get_secret_value() == key
    assert settings.telegram_bot_token is None


def test_invite_mode_requires_code(make_settings: Callable[..., Settings]) -> None:
    with pytest.raises(ValidationError, match="INVITE_CODE"):
        make_settings(registration_mode="invite")
    settings = make_settings(registration_mode="invite", registration_invite_code="abc")
    assert settings.registration_mode is RegistrationMode.INVITE


def test_secrets_are_hidden_in_repr(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings()
    assert settings.jwt_secret.get_secret_value() not in repr(settings)


def test_cors_origin_list(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings(cors_origins="https://a.example, https://b.example,")
    assert settings.cors_origin_list == ["https://a.example", "https://b.example"]
