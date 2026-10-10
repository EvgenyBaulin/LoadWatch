"""Проверка владения сайтом: DNS TXT и HTTP-файл."""

from loadwatch.config import Settings
from loadwatch.security.ssrf import SafeHttpClient
from loadwatch.verification.base import (
    OwnershipVerifier,
    VerificationInstructions,
    VerificationOutcome,
    VerificationResult,
    VerifierRegistry,
)
from loadwatch.verification.dns_txt import DnsTxtVerifier
from loadwatch.verification.http_file import HttpFileVerifier


def default_registry(settings: Settings) -> VerifierRegistry:
    """Боевой набор верификаторов с настройками SSRF из конфига."""
    client = SafeHttpClient(allow_private=settings.allow_private_targets)
    return VerifierRegistry([DnsTxtVerifier(), HttpFileVerifier(client)])


__all__ = [
    "DnsTxtVerifier",
    "HttpFileVerifier",
    "OwnershipVerifier",
    "VerificationInstructions",
    "VerificationOutcome",
    "VerificationResult",
    "VerifierRegistry",
    "default_registry",
]
