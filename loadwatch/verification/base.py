"""Интерфейс проверки владения сайтом и реестр реализаций."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from loadwatch.security.ssrf import TargetUrl


class VerificationOutcome(StrEnum):
    """Итог проверки; от него зависит, что делать при перепроверке."""

    VERIFIED = "verified"
    TOKEN_NOT_FOUND = "token_not_found"  # noqa: S105  # владелец убрал токен — владение потеряно
    FORBIDDEN = "target_forbidden"  # сайт указывает во внутреннюю сеть
    ERROR = "error"  # сеть, таймаут, DNS недоступен — неизвестно, временно ли


@dataclass(frozen=True, slots=True)
class VerificationResult:
    """Результат проверки; ``detail`` — короткое пояснение без секретов."""

    outcome: VerificationOutcome
    detail: str = ""

    @property
    def ok(self) -> bool:
        """Владение подтверждено."""
        return self.outcome is VerificationOutcome.VERIFIED


@dataclass(frozen=True, slots=True)
class VerificationInstructions:
    """Что владелец должен разместить, чтобы пройти проверку."""

    method: str
    record_name: str  # имя DNS-записи или путь файла
    record_value: str  # значение TXT-записи или содержимое файла


class OwnershipVerifier(ABC):
    """Проверка владения сайтом одним способом."""

    method: ClassVar[str]

    @abstractmethod
    def instructions(self, target: TargetUrl, token: str) -> VerificationInstructions:
        """Что нужно разместить для этого способа."""

    @abstractmethod
    def verify(self, target: TargetUrl, token: str) -> VerificationResult:
        """Проверяет, что токен размещён."""


class VerifierRegistry:
    """Реестр верификаторов по имени способа (``dns``, ``http``)."""

    def __init__(self, verifiers: list[OwnershipVerifier]) -> None:
        self._verifiers = {verifier.method: verifier for verifier in verifiers}

    def get(self, method: str) -> OwnershipVerifier:
        """Верификатор для способа; неизвестный способ — ``KeyError``."""
        return self._verifiers[method]

    def all(self) -> list[OwnershipVerifier]:
        """Все верификаторы в порядке регистрации."""
        return list(self._verifiers.values())
