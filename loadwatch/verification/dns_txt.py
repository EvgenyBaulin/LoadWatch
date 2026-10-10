"""Проверка владения через TXT-запись ``loadwatch-verify=<token>``."""

from __future__ import annotations

import ipaddress
from collections.abc import Callable, Iterable

import dns.exception
import dns.resolver

from loadwatch.security.ssrf import TargetUrl
from loadwatch.verification.base import (
    OwnershipVerifier,
    VerificationInstructions,
    VerificationOutcome,
    VerificationResult,
)

TXT_PREFIX = "loadwatch-verify="

TxtLookup = Callable[[str], Iterable[str]]


class TxtLookupError(Exception):
    """DNS не ответил (таймаут, SERVFAIL) — результат неизвестен."""


def dnspython_txt_lookup(host: str) -> list[str]:
    """Читает TXT-записи хоста через dnspython; отсутствие записей — пустой список."""
    resolver = dns.resolver.Resolver()
    resolver.lifetime = 5.0
    try:
        answer = resolver.resolve(host, "TXT")
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return []
    except dns.exception.DNSException as exc:
        raise TxtLookupError(type(exc).__name__) from exc
    # Длинная TXT-запись может быть разбита на несколько строк — склеиваем.
    return [b"".join(record.strings).decode("utf-8", "replace") for record in answer]


class DnsTxtVerifier(OwnershipVerifier):
    """Ищет TXT-запись с токеном на домене сайта."""

    method = "dns"

    def __init__(self, txt_lookup: TxtLookup = dnspython_txt_lookup) -> None:
        self._lookup = txt_lookup

    def instructions(self, target: TargetUrl, token: str) -> VerificationInstructions:
        """TXT-запись на том же имени, что и сайт."""
        return VerificationInstructions(self.method, target.host, f"{TXT_PREFIX}{token}")

    def verify(self, target: TargetUrl, token: str) -> VerificationResult:
        """Ищет запись среди TXT-записей хоста."""
        if _is_ip(target.host):
            return VerificationResult(
                VerificationOutcome.ERROR, "DNS verification needs a domain name"
            )
        try:
            records = list(self._lookup(target.host))
        except TxtLookupError as exc:
            return VerificationResult(VerificationOutcome.ERROR, f"DNS lookup failed: {exc}")
        expected = f"{TXT_PREFIX}{token}"
        if any(record.strip() == expected for record in records):
            return VerificationResult(VerificationOutcome.VERIFIED)
        return VerificationResult(VerificationOutcome.TOKEN_NOT_FOUND, "TXT record not found")


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True
