"""Request and response models for authentication and user preferences."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

Locale = Literal["ru", "en"]


def _check_timezone(value: str) -> str:
    """Accept only IANA time zone names known to ``zoneinfo``."""
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("unknown time zone") from exc
    return value


def _strip(value: object) -> object:
    """Trim surrounding whitespace of e-mails and codes (never of passwords)."""
    return value.strip() if isinstance(value, str) else value


class RegisterRequest(BaseModel):
    """New account; the password is kept exactly as typed."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    invite_code: str | None = Field(default=None, max_length=256)
    locale: Locale = "en"

    _strip_fields = field_validator("email", "invite_code", mode="before")(_strip)


class LoginRequest(BaseModel):
    """Credentials for an access token."""

    email: EmailStr
    password: str = Field(min_length=1, max_length=128)

    _strip_email = field_validator("email", mode="before")(_strip)


class UserOut(BaseModel):
    """Public view of the current user."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    locale: Locale
    timezone: str
    created_at: datetime


class AuthResponse(BaseModel):
    """Access token plus the authenticated user."""

    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105
    expires_in: int = Field(description="Token lifetime in seconds")
    user: UserOut


class PreferencesUpdate(BaseModel):
    """Partial update of user preferences."""

    locale: Locale | None = None
    timezone: str | None = Field(default=None, max_length=64)

    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, value: str | None) -> str | None:
        return None if value is None else _check_timezone(value)
