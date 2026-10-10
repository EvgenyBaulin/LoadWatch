"""Схемы API для входа, регистрации и настроек пользователя."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

Locale = Literal["ru", "en"]


def _check_timezone(value: str) -> str:
    """Принимает только известные ``zoneinfo`` имена часовых поясов IANA."""
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("unknown time zone") from exc
    return value


def _strip(value: object) -> object:
    """Обрезает пробелы по краям e-mail и кодов (но никогда — паролей)."""
    return value.strip() if isinstance(value, str) else value


class RegisterRequest(BaseModel):
    """Новый аккаунт; пароль сохраняется ровно как введён."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    invite_code: str | None = Field(default=None, max_length=256)
    locale: Locale = "en"

    _strip_fields = field_validator("email", "invite_code", mode="before")(_strip)


class LoginRequest(BaseModel):
    """Данные для получения токена."""

    email: EmailStr
    password: str = Field(min_length=1, max_length=128)

    _strip_email = field_validator("email", mode="before")(_strip)


class UserOut(BaseModel):
    """Текущий пользователь в ответах API."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    locale: Locale
    timezone: str
    created_at: datetime


class AuthResponse(BaseModel):
    """Токен доступа и пользователь."""

    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105
    expires_in: int = Field(description="Token lifetime in seconds")
    user: UserOut


class PreferencesUpdate(BaseModel):
    """Частичное обновление настроек пользователя."""

    locale: Locale | None = None
    timezone: str | None = Field(default=None, max_length=64)

    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, value: str | None) -> str | None:
        return None if value is None else _check_timezone(value)
