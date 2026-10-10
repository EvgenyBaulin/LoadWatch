"""SSRF-фильтр: адреса, резолв, закрепление IP и редиректы."""

import ipaddress
from collections.abc import AsyncIterator, Iterable

import httpx
import pytest

from loadwatch.security import ssrf
from loadwatch.security.ssrf import (
    InvalidTargetUrlError,
    SafeHttpClient,
    TargetForbiddenError,
    TargetResolutionError,
    is_ip_allowed,
    parse_target_url,
    pinned_request_parts,
    resolve_target,
)

PUBLIC_V4 = "93.184.216.34"
PUBLIC_V6 = "2606:2800:220:1:248:1893:25c8:1946"


def resolver_for(mapping: dict[str, list[str]]) -> ssrf.Resolver:
    def resolve(host: str, port: int) -> Iterable[str]:
        if host not in mapping:
            raise TargetResolutionError(host)
        return mapping[host]

    return resolve


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "127.10.0.5",
        "10.1.2.3",
        "172.16.0.1",
        "172.31.255.255",
        "192.168.1.1",
        "169.254.169.254",  # метаданные облака
        "100.64.0.1",
        "0.0.0.0",
        "224.0.0.1",
        "255.255.255.255",
        "::1",
        "::",
        "fc00::1",
        "fd12:3456::1",
        "fe80::1",
        "::ffff:127.0.0.1",  # IPv4, спрятанный в IPv6
        "::ffff:10.0.0.1",
        "64:ff9b::7f00:1",  # NAT64 к 127.0.0.1
        "2002:7f00:1::1",  # 6to4 к 127.0.0.1
        "ff02::1",
    ],
)
def test_private_and_special_addresses_blocked(address: str) -> None:
    assert not is_ip_allowed(ipaddress.ip_address(address))


@pytest.mark.parametrize("address", [PUBLIC_V4, "8.8.8.8", PUBLIC_V6, "2a00:1450:4001:80b::200e"])
def test_public_addresses_allowed(address: str) -> None:
    assert is_ip_allowed(ipaddress.ip_address(address))


def test_allow_private_opens_private_but_not_unspecified() -> None:
    assert is_ip_allowed(ipaddress.ip_address("127.0.0.1"), allow_private=True)
    assert is_ip_allowed(ipaddress.ip_address("192.168.0.10"), allow_private=True)
    assert not is_ip_allowed(ipaddress.ip_address("0.0.0.0"), allow_private=True)
    assert not is_ip_allowed(ipaddress.ip_address("224.0.0.1"), allow_private=True)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com",
        "file:///etc/passwd",
        "gopher://example.com",
        "https://user:pass@example.com",
        "https://",
        "https://example.com:99999",
        "example.com",
    ],
)
def test_invalid_urls(url: str) -> None:
    with pytest.raises(InvalidTargetUrlError):
        parse_target_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost",
        "http://LOCALHOST:8080",
        "http://api.localhost",
        "http://127.0.0.1",
        "http://[::1]:8080",
        "http://[::ffff:127.0.0.1]",
        "http://169.254.169.254/latest/meta-data",
    ],
)
def test_forbidden_urls(url: str) -> None:
    with pytest.raises(TargetForbiddenError):
        parse_target_url(url)


def test_url_normalization() -> None:
    target = parse_target_url("HTTPS://Example.COM.:443/")
    assert (target.scheme, target.host, target.port) == ("https", "example.com", 443)
    assert target.origin == "https://example.com"
    assert parse_target_url("http://example.com:8080").origin == "http://example.com:8080"
    assert parse_target_url("https://пример.рф").host == "xn--e1afmkfd.xn--p1ai"
    assert parse_target_url(f"http://[{PUBLIC_V6}]:8080").origin == f"http://[{PUBLIC_V6}]:8080"


def test_localhost_allowed_with_flag() -> None:
    assert parse_target_url("http://localhost:8081", allow_private=True).host == "localhost"


def test_resolve_pins_first_public_address() -> None:
    target = parse_target_url("https://example.com")
    pinned = resolve_target(target, resolver=resolver_for({"example.com": [PUBLIC_V4, PUBLIC_V6]}))
    assert str(pinned.ip) == PUBLIC_V4


def test_resolve_rejects_if_any_address_is_private() -> None:
    target = parse_target_url("https://evil.example")
    resolver = resolver_for({"evil.example": [PUBLIC_V4, "10.0.0.5"]})
    with pytest.raises(TargetForbiddenError):
        resolve_target(target, resolver=resolver)


def test_resolve_unknown_host() -> None:
    with pytest.raises(TargetResolutionError):
        resolve_target(parse_target_url("https://nowhere.example"), resolver=resolver_for({}))


def test_resolve_empty_answer() -> None:
    with pytest.raises(TargetResolutionError):
        resolve_target(
            parse_target_url("https://empty.example"), resolver=resolver_for({"empty.example": []})
        )


def test_dns_rebinding_is_caught_on_every_resolution() -> None:
    """Первый резолв публичный, второй — внутренний: второй запуск обязан упасть."""
    answers = iter([[PUBLIC_V4], ["127.0.0.1"]])

    def rebinding(host: str, port: int) -> list[str]:
        return next(answers)

    target = parse_target_url("https://rebind.example")
    assert str(resolve_target(target, resolver=rebinding).ip) == PUBLIC_V4
    with pytest.raises(TargetForbiddenError):
        resolve_target(target, resolver=rebinding)


def test_pinned_request_uses_ip_host_header_and_sni() -> None:
    pinned = resolve_target(
        parse_target_url("https://example.com:8443"),
        resolver=resolver_for({"example.com": [PUBLIC_V6]}),
    )
    url, headers, extensions = pinned_request_parts(pinned, "/a?b=1")
    assert url == f"https://[{PUBLIC_V6}]:8443/a?b=1"
    assert headers == {"Host": "example.com:8443"}
    assert extensions == {"sni_hostname": "example.com"}


def test_pinned_request_plain_http_has_no_sni() -> None:
    pinned = resolve_target(
        parse_target_url("http://example.com"), resolver=resolver_for({"example.com": [PUBLIC_V4]})
    )
    url, headers, extensions = pinned_request_parts(pinned, "/")
    assert url == f"http://{PUBLIC_V4}:80/"
    assert headers == {"Host": "example.com"}
    assert extensions == {}


def test_pinned_request_rejects_absolute_path() -> None:
    pinned = resolve_target(
        parse_target_url("http://example.com"), resolver=resolver_for({"example.com": [PUBLIC_V4]})
    )
    with pytest.raises(TargetForbiddenError):
        pinned_request_parts(pinned, "http://other.example/")


class Recorder:
    """MockTransport, который записывает запросы и отвечает по таблице путей."""

    def __init__(self, routes: dict[str, httpx.Response]) -> None:
        self.routes = routes
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.routes.get(request.url.path, httpx.Response(404))


def make_client(
    recorder: Recorder, mapping: dict[str, list[str]], **kwargs: object
) -> SafeHttpClient:
    return SafeHttpClient(
        resolver=resolver_for(mapping),
        transport=httpx.MockTransport(recorder),
        **kwargs,  # type: ignore[arg-type]
    )


def test_safe_client_connects_to_pinned_ip() -> None:
    recorder = Recorder({"/file.txt": httpx.Response(200, text="token")})
    client = make_client(recorder, {"example.com": [PUBLIC_V4]})
    result = client.get("https://example.com/file.txt")
    assert (result.status_code, result.body) == (200, b"token")
    request = recorder.requests[0]
    assert request.url.host == PUBLIC_V4
    assert request.headers["host"] == "example.com"
    assert request.extensions["sni_hostname"] == "example.com"


def test_safe_client_follows_same_host_redirect() -> None:
    recorder = Recorder(
        {
            "/old": httpx.Response(301, headers={"Location": "https://example.com/new"}),
            "/new": httpx.Response(200, text="ok"),
        }
    )
    client = make_client(recorder, {"example.com": [PUBLIC_V4]})
    result = client.get("http://example.com/old")  # http → https на том же хосте — можно
    assert result.body == b"ok"
    assert result.final_url == "https://example.com/new"


def test_safe_client_blocks_cross_host_redirect() -> None:
    recorder = Recorder(
        {"/go": httpx.Response(302, headers={"Location": "http://169.254.169.254/latest"})}
    )
    client = make_client(recorder, {"example.com": [PUBLIC_V4]})
    with pytest.raises(TargetForbiddenError):
        client.get("https://example.com/go")
    assert len(recorder.requests) == 1  # к метаданным облака запрос не ушёл


def test_safe_client_rechecks_dns_on_redirect() -> None:
    """Редирект на тот же хост, но DNS к этому моменту указывает во внутреннюю сеть."""
    answers = iter([[PUBLIC_V4], ["10.0.0.1"]])
    recorder = Recorder({"/a": httpx.Response(302, headers={"Location": "/b"})})
    client = SafeHttpClient(
        resolver=lambda host, port: next(answers), transport=httpx.MockTransport(recorder)
    )
    with pytest.raises(TargetForbiddenError):
        client.get("https://example.com/a")
    assert len(recorder.requests) == 1


def test_safe_client_limits_redirects() -> None:
    recorder = Recorder({"/loop": httpx.Response(302, headers={"Location": "/loop"})})
    client = make_client(recorder, {"example.com": [PUBLIC_V4]}, max_redirects=2)
    with pytest.raises(TargetForbiddenError, match="too many redirects"):
        client.get("https://example.com/loop")
    assert len(recorder.requests) == 3


def test_safe_client_truncates_body() -> None:
    recorder = Recorder({"/big": httpx.Response(200, content=b"x" * 10_000)})
    client = make_client(recorder, {"example.com": [PUBLIC_V4]}, max_body_bytes=100)
    assert len(client.get("https://example.com/big").body) == 100


def test_safe_client_post_does_not_follow_redirects() -> None:
    recorder = Recorder({"/hook": httpx.Response(307, headers={"Location": "http://10.0.0.1/"})})
    client = make_client(recorder, {"hooks.example": [PUBLIC_V4]})
    status = client.post("https://hooks.example/hook", content=b"{}", headers={"X-A": "1"})
    assert status == 307
    assert len(recorder.requests) == 1
    assert recorder.requests[0].url.host == PUBLIC_V4


def test_safe_client_post_blocks_private_webhook() -> None:
    client = make_client(Recorder({}), {"internal.example": ["192.168.0.2"]})
    with pytest.raises(TargetForbiddenError):
        client.post("https://internal.example/hook", content=b"{}", headers={})


def test_system_resolver_resolves_literal_localhost() -> None:
    assert "127.0.0.1" in ssrf.system_resolver("127.0.0.1", 80)


def test_system_resolver_failure() -> None:
    with pytest.raises(TargetResolutionError):
        ssrf.system_resolver("nonexistent.invalid", 80)


class Streamed(httpx.AsyncByteStream):
    """Тело ответа, которое отдаётся потоком, как из сети (не лежит в памяти заранее)."""

    def __init__(self, content: bytes, chunk: int = 4096) -> None:
        self.content = content
        self.chunk = chunk
        self.sent = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for i in range(0, len(self.content), self.chunk):
            self.sent += self.chunk
            yield self.content[i : i + self.chunk]


def gzip_response(content: bytes, stream: Streamed | None = None) -> httpx.Response:
    return httpx.Response(
        200, stream=stream or Streamed(content), headers={"Content-Encoding": "gzip"}
    )


def test_safe_client_decompresses_within_limit() -> None:
    """Gzip-бомба: 10 МБ нулей сжимаются в ~10 КБ, но распакуется не больше лимита."""
    import gzip

    bomb = gzip.compress(b"0" * 10_000_000)
    stream = Streamed(bomb, chunk=1024)
    recorder = Recorder({"/f": gzip_response(bomb, stream)})
    client = make_client(recorder, {"example.com": [PUBLIC_V4]}, max_body_bytes=1000)
    assert client.get("https://example.com/f").body == b"0" * 1000
    assert stream.sent < len(bomb)  # дочитывать бомбу до конца не стали
    assert recorder.requests[0].headers["accept-encoding"] == "identity"


def test_safe_client_small_gzip_body() -> None:
    import gzip

    recorder = Recorder(
        {
            "/f": httpx.Response(
                200, content=gzip.compress(b"token"), headers={"Content-Encoding": "gzip"}
            )
        }
    )
    client = make_client(recorder, {"example.com": [PUBLIC_V4]})
    assert client.get("https://example.com/f").body == b"token"


@pytest.mark.parametrize(("encoding", "content"), [("br", b"x"), ("gzip", b"not gzip")])
def test_safe_client_rejects_unknown_or_corrupt_encoding(encoding: str, content: bytes) -> None:
    response = httpx.Response(200, stream=Streamed(content), headers={"Content-Encoding": encoding})
    recorder = Recorder({"/f": response})
    client = make_client(recorder, {"example.com": [PUBLIC_V4]})
    with pytest.raises(httpx.DecodingError):
        client.get("https://example.com/f")


def test_safe_client_malformed_redirect() -> None:
    recorder = Recorder({"/r": httpx.Response(302, headers={"Location": "http://[invalid/"})})
    client = make_client(recorder, {"example.com": [PUBLIC_V4]})
    with pytest.raises(InvalidTargetUrlError):
        client.get("https://example.com/r")


def test_safe_client_total_deadline() -> None:
    """Сервер, который тянет время, не держит воркер дольше общего лимита."""
    import asyncio

    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200)

    client = SafeHttpClient(
        resolver=lambda host, port: [PUBLIC_V4],
        transport=httpx.MockTransport(slow),
        deadline=0.2,
    )
    with pytest.raises(httpx.TimeoutException, match="deadline"):
        client.get("https://example.com/")


def test_safe_client_ignores_proxy_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://10.0.0.1:3128")
    recorder = Recorder({"/": httpx.Response(200, text="ok")})
    client = make_client(recorder, {"example.com": [PUBLIC_V4]})
    assert client.get("https://example.com/").status_code == 200
    assert recorder.requests[0].url.host == PUBLIC_V4
