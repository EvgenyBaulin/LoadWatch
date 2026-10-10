"""Верификаторы владения: DNS TXT и HTTP-файл (сеть подменена)."""

from collections.abc import Callable

import httpx
import pytest

from loadwatch.config import Settings
from loadwatch.security.ssrf import SafeHttpClient, parse_target_url
from loadwatch.verification import (
    DnsTxtVerifier,
    HttpFileVerifier,
    VerificationOutcome,
    VerifierRegistry,
    default_registry,
)
from loadwatch.verification.dns_txt import TxtLookupError

TOKEN = "0123456789abcdef0123456789abcdef"
TARGET = parse_target_url("https://example.com")


def test_dns_instructions() -> None:
    info = DnsTxtVerifier(lambda host: []).instructions(TARGET, TOKEN)
    assert (info.method, info.record_name, info.record_value) == (
        "dns",
        "example.com",
        f"loadwatch-verify={TOKEN}",
    )


def test_dns_verified_among_other_records() -> None:
    records = ["v=spf1 -all", f"  loadwatch-verify={TOKEN} ", "google-site-verification=x"]
    looked_up: list[str] = []

    def lookup(host: str) -> list[str]:
        looked_up.append(host)
        return records

    assert DnsTxtVerifier(lookup).verify(TARGET, TOKEN).ok
    assert looked_up == ["example.com"]


@pytest.mark.parametrize(
    "records",
    [[], ["loadwatch-verify=wrong"], [f"loadwatch-verify={TOKEN}extra"], [TOKEN]],
)
def test_dns_token_not_found(records: list[str]) -> None:
    result = DnsTxtVerifier(lambda host: records).verify(TARGET, TOKEN)
    assert result.outcome is VerificationOutcome.TOKEN_NOT_FOUND


def test_dns_lookup_error_is_not_loss_of_ownership() -> None:
    def failing(host: str) -> list[str]:
        raise TxtLookupError("Timeout")

    result = DnsTxtVerifier(failing).verify(TARGET, TOKEN)
    assert result.outcome is VerificationOutcome.ERROR


def test_dns_needs_domain_name() -> None:
    target = parse_target_url("http://93.184.216.34")
    assert (
        DnsTxtVerifier(lambda host: []).verify(target, TOKEN).outcome is VerificationOutcome.ERROR
    )


def http_verifier(handler: httpx.MockTransport) -> HttpFileVerifier:
    client = SafeHttpClient(resolver=lambda host, port: ["93.184.216.34"], transport=handler)
    return HttpFileVerifier(client)


def test_http_instructions() -> None:
    info = http_verifier(httpx.MockTransport(lambda r: httpx.Response(404))).instructions(
        TARGET, TOKEN
    )
    assert info.record_name == f"/.well-known/loadwatch-{TOKEN}.txt"
    assert info.record_value == TOKEN


def test_http_verified() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, text=f"{TOKEN}\n")

    assert http_verifier(httpx.MockTransport(handler)).verify(TARGET, TOKEN).ok
    assert seen == [f"/.well-known/loadwatch-{TOKEN}.txt"]


@pytest.mark.parametrize(
    ("response", "outcome"),
    [
        (httpx.Response(404), VerificationOutcome.TOKEN_NOT_FOUND),
        (httpx.Response(200, text="something else"), VerificationOutcome.TOKEN_NOT_FOUND),
        (httpx.Response(500), VerificationOutcome.ERROR),
        (
            httpx.Response(302, headers={"Location": "https://attacker.example/x"}),
            VerificationOutcome.FORBIDDEN,
        ),
    ],
)
def test_http_failures(response: httpx.Response, outcome: VerificationOutcome) -> None:
    verifier = http_verifier(httpx.MockTransport(lambda request: response))
    assert verifier.verify(TARGET, TOKEN).outcome is outcome


def test_http_network_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timeout", request=request)

    result = http_verifier(httpx.MockTransport(handler)).verify(TARGET, TOKEN)
    assert result.outcome is VerificationOutcome.ERROR


def test_http_private_target_forbidden() -> None:
    client = SafeHttpClient(
        resolver=lambda host, port: ["10.0.0.1"],
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text=TOKEN)),
    )
    result = HttpFileVerifier(client).verify(TARGET, TOKEN)
    assert result.outcome is VerificationOutcome.FORBIDDEN


def test_registry_polymorphism(make_settings: Callable[..., Settings]) -> None:
    registry = default_registry(make_settings())
    assert isinstance(registry.get("dns"), DnsTxtVerifier)
    assert isinstance(registry.get("http"), HttpFileVerifier)
    assert [v.method for v in registry.all()] == ["dns", "http"]
    with pytest.raises(KeyError):
        registry.get("email")


def test_registry_is_injectable() -> None:
    verifier = DnsTxtVerifier(lambda host: [])
    assert VerifierRegistry([verifier]).get("dns") is verifier


class FakeAnswerRecord:
    def __init__(self, *parts: bytes) -> None:
        self.strings = parts


def test_dnspython_lookup_joins_split_records(monkeypatch: pytest.MonkeyPatch) -> None:
    import dns.resolver

    from loadwatch.verification.dns_txt import dnspython_txt_lookup

    def fake_resolve(self: object, host: str, rdtype: str) -> list[FakeAnswerRecord]:
        assert (host, rdtype) == ("example.com", "TXT")
        return [FakeAnswerRecord(b"loadwatch-verify=", b"abc")]

    monkeypatch.setattr(dns.resolver.Resolver, "resolve", fake_resolve)
    assert dnspython_txt_lookup("example.com") == ["loadwatch-verify=abc"]


@pytest.mark.parametrize(
    ("error", "expected"),
    [("NXDOMAIN", []), ("NoAnswer", []), ("LifetimeTimeout", TxtLookupError)],
)
def test_dnspython_lookup_errors(
    monkeypatch: pytest.MonkeyPatch, error: str, expected: object
) -> None:
    import dns.resolver

    from loadwatch.verification.dns_txt import dnspython_txt_lookup

    def fake_resolve(self: object, host: str, rdtype: str) -> None:
        raise getattr(dns.resolver, error)()

    monkeypatch.setattr(dns.resolver.Resolver, "resolve", fake_resolve)
    if expected == []:
        assert dnspython_txt_lookup("example.com") == []
    else:
        with pytest.raises(TxtLookupError):
            dnspython_txt_lookup("example.com")
