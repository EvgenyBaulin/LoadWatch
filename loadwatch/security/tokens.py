"""JWT-токены доступа."""

from __future__ import annotations

import uuid
from datetime import timedelta

import jwt

from loadwatch.config import Settings
from loadwatch.db import utcnow


class InvalidTokenError(Exception):
    """Токен повреждён, просрочен или подписан другим ключом."""


def create_access_token(user_id: uuid.UUID, settings: Settings) -> str:
    """Выпускает подписанный токен доступа для ``user_id``."""
    now = utcnow()
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
        "type": "access",
    }
    return jwt.encode(
        payload, settings.jwt_secret.get_secret_value(), algorithm=settings.jwt_algorithm
    )


def decode_access_token(token: str, settings: Settings) -> uuid.UUID:
    """Id пользователя из действительного токена, иначе :class:`InvalidTokenError`."""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "exp", "iat"]},
        )
        if payload.get("type") != "access":
            raise InvalidTokenError("wrong token type")
        return uuid.UUID(str(payload["sub"]))
    except (jwt.PyJWTError, ValueError) as exc:
        raise InvalidTokenError(str(exc)) from exc
