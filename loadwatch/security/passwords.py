"""Хэширование паролей Argon2id."""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# Значения библиотеки по умолчанию (RFC 9106, 64 МиБ) слишком тяжелы для сервера с 1 ГБ
# при одновременных входах; 19 МиБ и t=2 — минимальная рекомендация OWASP.
_hasher = PasswordHasher(time_cost=2, memory_cost=19 * 1024, parallelism=1)

# Проверяется, когда пользователя нет, чтобы по времени ответа нельзя было узнать об аккаунте.
_DUMMY_HASH = _hasher.hash("loadwatch-dummy-password")


def hash_password(password: str) -> str:
    """Argon2id-хэш пароля."""
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """Сверяет пароль с хэшем; при ``None`` тратит то же время и возвращает ``False``."""
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
