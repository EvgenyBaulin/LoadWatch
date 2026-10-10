"""Проверка владения через файл ``/.well-known/loadwatch-<token>.txt``."""

from __future__ import annotations

import httpx

from loadwatch.security.ssrf import (
    SafeHttpClient,
    TargetForbiddenError,
    TargetResolutionError,
    TargetUrl,
)
from loadwatch.verification.base import (
    OwnershipVerifier,
    VerificationInstructions,
    VerificationOutcome,
    VerificationResult,
)


def file_path(token: str) -> str:
    """Путь файла верификации."""
    return f"/.well-known/loadwatch-{token}.txt"


class HttpFileVerifier(OwnershipVerifier):
    """Скачивает файл верификации с сайта через SSRF-безопасный клиент.

    Файл должен содержать токен (пробелы и перевод строки по краям допустимы).
    Редиректы разрешены только в пределах того же хоста, например http → https.
    """

    method = "http"

    def __init__(self, client: SafeHttpClient) -> None:
        self._client = client

    def instructions(self, target: TargetUrl, token: str) -> VerificationInstructions:
        """Путь файла и его содержимое."""
        return VerificationInstructions(self.method, file_path(token), token)

    def verify(self, target: TargetUrl, token: str) -> VerificationResult:
        """Запрашивает файл и сравнивает содержимое с токеном."""
        try:
            result = self._client.get(f"{target.origin}{file_path(token)}")
        except TargetForbiddenError as exc:
            return VerificationResult(VerificationOutcome.FORBIDDEN, str(exc))
        except (TargetResolutionError, httpx.HTTPError) as exc:
            return VerificationResult(VerificationOutcome.ERROR, type(exc).__name__)
        if result.status_code == 404:
            return VerificationResult(VerificationOutcome.TOKEN_NOT_FOUND, "file not found")
        if result.status_code != 200:
            return VerificationResult(
                VerificationOutcome.ERROR, f"unexpected HTTP status {result.status_code}"
            )
        if result.body.decode("utf-8", "replace").strip() != token:
            return VerificationResult(
                VerificationOutcome.TOKEN_NOT_FOUND, "file content does not match the token"
            )
        return VerificationResult(VerificationOutcome.VERIFIED)
