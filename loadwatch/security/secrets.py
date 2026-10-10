"""Шифрование секретов сценариев (Fernet) и их маскирование."""

from __future__ import annotations

from typing import Final

from cryptography.fernet import Fernet, InvalidToken

MASK: Final = "********"


class SecretsUnavailableError(Exception):
    """Ключ шифрования не настроен или не подходит к сохранённым данным."""


class SecretBox:
    """Шифрует и расшифровывает значения ключом ``SECRETS_ENCRYPTION_KEY``."""

    def __init__(self, key: str | None) -> None:
        self._fernet = Fernet(key.encode()) if key else None

    @property
    def available(self) -> bool:
        """Задан ли ключ."""
        return self._fernet is not None

    def encrypt(self, value: str) -> str:
        """Шифрует значение; без ключа — :class:`SecretsUnavailableError`."""
        if self._fernet is None:
            raise SecretsUnavailableError("SECRETS_ENCRYPTION_KEY is not set")
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, token: str) -> str:
        """Расшифровывает значение; неверный ключ — :class:`SecretsUnavailableError`."""
        if self._fernet is None:
            raise SecretsUnavailableError("SECRETS_ENCRYPTION_KEY is not set")
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken as exc:
            raise SecretsUnavailableError("secret cannot be decrypted with this key") from exc


def mask(value: str) -> str:
    """Маска значения: длина и содержимое не раскрываются."""
    return MASK if value else ""
