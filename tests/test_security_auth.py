"""Password hashing and JWT access tokens."""

import uuid
from collections.abc import Callable
from datetime import timedelta

import jwt
import pytest

from loadwatch.config import Settings
from loadwatch.db import utcnow
from loadwatch.security.passwords import hash_password, verify_password
from loadwatch.security.tokens import InvalidTokenError, create_access_token, decode_access_token


def test_password_roundtrip() -> None:
    hashed = hash_password("s3cret-password")
    assert hashed.startswith("$argon2id$")
    assert "s3cret-password" not in hashed
    assert verify_password(hashed, "s3cret-password")
    assert not verify_password(hashed, "wrong-password")


def test_verify_without_hash_fails() -> None:
    assert not verify_password(None, "anything")


def test_verify_with_garbage_hash_fails() -> None:
    assert not verify_password("not-a-hash", "anything")


def test_token_roundtrip(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings()
    user_id = uuid.uuid4()
    assert decode_access_token(create_access_token(user_id, settings), settings) == user_id


def test_token_with_other_secret_rejected(make_settings: Callable[..., Settings]) -> None:
    token = create_access_token(uuid.uuid4(), make_settings(jwt_secret="a" * 40))
    with pytest.raises(InvalidTokenError):
        decode_access_token(token, make_settings(jwt_secret="b" * 40))


def _forge(settings: Settings, **claims: object) -> str:
    now = utcnow()
    payload = {"sub": str(uuid.uuid4()), "iat": now, "exp": now + timedelta(minutes=5)}
    payload.update(claims)
    return jwt.encode(payload, settings.jwt_secret.get_secret_value(), algorithm="HS256")


def test_expired_token_rejected(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings()
    token = _forge(settings, type="access", exp=utcnow() - timedelta(seconds=1))
    with pytest.raises(InvalidTokenError):
        decode_access_token(token, settings)


def test_wrong_token_type_rejected(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings()
    with pytest.raises(InvalidTokenError):
        decode_access_token(_forge(settings, type="trigger"), settings)


def test_non_uuid_subject_rejected(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings()
    with pytest.raises(InvalidTokenError):
        decode_access_token(_forge(settings, type="access", sub="42"), settings)


def test_unsigned_token_rejected(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings()
    payload = {"sub": str(uuid.uuid4()), "iat": utcnow(), "exp": utcnow() + timedelta(minutes=5)}
    token = jwt.encode(payload | {"type": "access"}, key=None, algorithm="none")  # type: ignore[arg-type]
    with pytest.raises(InvalidTokenError):
        decode_access_token(token, settings)
