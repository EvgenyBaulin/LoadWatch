"""Защита от SSRF: проверка адресов, резолв с закреплением IP и безопасные запросы.

Порядок для любого исходящего запроса к пользовательскому адресу:

1. :func:`parse_target_url` — синтаксис, схема, отсутствие логина в URL;
2. :func:`resolve_target` — резолв имени и проверка **всех** полученных адресов;
3. запрос идёт на проверенный IP (:func:`pinned_request_parts`), имя хоста
   передаётся в ``Host`` и в SNI, поэтому повторного резолва нет и DNS rebinding
   между проверкой и подключением невозможен;
4. редиректы — только вручную и только в пределах того же хоста, каждый шаг
   проходит пункты 2–3 заново (:class:`SafeHttpClient`).
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import socket
import zlib
from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import dataclass
from typing import Final
from urllib.parse import urljoin, urlsplit

import httpx

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

# Частные и служебные сети плюс прочие диапазоны, которые не должны быть целью теста.
BLOCKED_NETWORKS: Final = tuple(
    ipaddress.ip_network(net)
    for net in (
        "0.0.0.0/8",  # «этот» хост
        "10.0.0.0/8",
        "100.64.0.0/10",  # CGNAT (адреса провайдеров)
        "127.0.0.0/8",
        "169.254.0.0/16",  # link-local, в том числе метаданные облаков
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.0.2.0/24",  # документационные сети
        "192.168.0.0/16",
        "198.18.0.0/15",  # бенчмарки
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/4",  # групповая рассылка
        "240.0.0.0/4",  # зарезервировано и broadcast
        "::/128",
        "::1/128",
        "64:ff9b::/96",  # NAT64: скрывает IPv4 внутри IPv6
        "100::/64",
        "2001:db8::/32",
        "fc00::/7",  # частные IPv6 (unique local)
        "fe80::/10",  # локальные адреса канала
        "ff00::/8",  # групповая рассылка IPv6
    )
)
# Эти адреса запрещены даже при ALLOW_PRIVATE_TARGETS=true: подключиться к ним нельзя.
ALWAYS_BLOCKED: Final = tuple(
    ipaddress.ip_network(net) for net in ("0.0.0.0/8", "::/128", "224.0.0.0/4", "ff00::/8")
)
FORBIDDEN_HOSTNAMES: Final = frozenset({"localhost", "localhost.localdomain"})
ALLOWED_SCHEMES: Final = frozenset({"http", "https"})
DEFAULT_PORTS: Final = {"http": 80, "https": 443}


class TargetForbiddenError(Exception):
    """Адрес запрещён политикой SSRF (внутренняя сеть, localhost, неверная схема)."""


class InvalidTargetUrlError(TargetForbiddenError):
    """URL синтаксически не подходит (схема, логин в URL, пустой или кривой хост)."""


class TargetResolutionError(Exception):
    """Имя хоста не резолвится."""


@dataclass(frozen=True, slots=True)
class TargetUrl:
    """Разобранный и нормализованный адрес цели."""

    scheme: str
    host: str  # в нижнем регистре, IDN — в punycode, IPv6 без скобок
    port: int

    @property
    def origin(self) -> str:
        """Origin в каноническом виде, без порта по умолчанию."""
        host = f"[{self.host}]" if ":" in self.host else self.host
        if DEFAULT_PORTS[self.scheme] == self.port:
            return f"{self.scheme}://{host}"
        return f"{self.scheme}://{host}:{self.port}"

    @property
    def host_header(self) -> str:
        """Значение заголовка ``Host``."""
        return self.origin.split("://", 1)[1]


@dataclass(frozen=True, slots=True)
class PinnedTarget:
    """Цель с проверенным IP, к которому и будет подключение."""

    url: TargetUrl
    ip: IPAddress


Resolver = Callable[[str, int], Iterable[str]]


def system_resolver(host: str, port: int) -> list[str]:
    """Резолвит имя через системный резолвер (A и AAAA)."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as exc:
        raise TargetResolutionError(f"cannot resolve {host}") from exc
    return [str(info[4][0]) for info in infos]


def is_ip_allowed(ip: IPAddress, *, allow_private: bool = False) -> bool:
    """Можно ли подключаться к ``ip``."""
    if isinstance(ip, ipaddress.IPv6Address):
        # IPv4 внутри IPv6: ::ffff:127.0.0.1, 6to4 (2002::/16) и Teredo проверяем как IPv4.
        embedded = ip.ipv4_mapped or ip.sixtofour or (ip.teredo[1] if ip.teredo else None)
        if embedded is not None and not is_ip_allowed(embedded, allow_private=allow_private):
            return False
    blocked = ALWAYS_BLOCKED if allow_private else BLOCKED_NETWORKS
    if any(ip in net for net in blocked if net.version == ip.version):
        return False
    # Страховка на случай диапазонов, которых нет в списке: только глобальные адреса.
    return allow_private or ip.is_global


def _normalize_host(raw: str) -> str:
    host = raw.strip().rstrip(".").lower()
    if not host:
        raise InvalidTargetUrlError("empty host")
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        pass
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise InvalidTargetUrlError("invalid host name") from exc


def parse_target_url(url: str, *, allow_private: bool = False) -> TargetUrl:
    """Разбирает URL цели и отклоняет то, что запрещено ещё до резолва."""
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError as exc:
        raise InvalidTargetUrlError("malformed URL") from exc
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise InvalidTargetUrlError("only http and https are allowed")
    if parts.username is not None or parts.password is not None:
        raise InvalidTargetUrlError("credentials in URL are not allowed")
    if not parts.hostname:
        raise InvalidTargetUrlError("host is required")
    host = _normalize_host(parts.hostname)
    if not allow_private and (host in FORBIDDEN_HOSTNAMES or host.endswith(".localhost")):
        raise TargetForbiddenError("localhost is not allowed")
    target = TargetUrl(scheme=scheme, host=host, port=port or DEFAULT_PORTS[scheme])
    literal = _as_ip(host)
    if literal is not None and not is_ip_allowed(literal, allow_private=allow_private):
        raise TargetForbiddenError(f"address {literal} is not allowed")
    return target


def _as_ip(host: str) -> IPAddress | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def resolve_target(
    target: TargetUrl, *, resolver: Resolver = system_resolver, allow_private: bool = False
) -> PinnedTarget:
    """Резолвит хост и закрепляет первый адрес; любой запрещённый адрес — отказ целиком.

    Отказ при *любом* запрещённом адресе, а не выбор «хорошего», нужен потому, что
    имя, которое хоть раз указывает во внутреннюю сеть, само по себе подозрительно.
    """
    literal = _as_ip(target.host)
    raw_addresses = (
        [target.host] if literal is not None else list(resolver(target.host, target.port))
    )
    if not raw_addresses:
        raise TargetResolutionError(f"no addresses for {target.host}")
    addresses: list[IPAddress] = []
    for raw in raw_addresses:
        ip = ipaddress.ip_address(raw.split("%", 1)[0])  # отбрасываем zone id IPv6
        if not is_ip_allowed(ip, allow_private=allow_private):
            raise TargetForbiddenError(f"{target.host} resolves to forbidden address {ip}")
        addresses.append(ip)
    return PinnedTarget(url=target, ip=addresses[0])


def pinned_request_parts(
    pinned: PinnedTarget, path: str
) -> tuple[str, dict[str, str], dict[str, str]]:
    """Возвращает URL на IP, заголовок ``Host`` и расширения httpx (SNI) для запроса."""
    if not path.startswith("/"):
        raise TargetForbiddenError("path must be relative to the site origin")
    ip_host = f"[{pinned.ip}]" if pinned.ip.version == 6 else str(pinned.ip)
    url = f"{pinned.url.scheme}://{ip_host}:{pinned.url.port}{path}"
    extensions = {"sni_hostname": pinned.url.host} if pinned.url.scheme == "https" else {}
    return url, {"Host": pinned.url.host_header}, extensions


@dataclass(frozen=True, slots=True)
class FetchResult:
    """Ответ безопасного запроса; тело обрезано до лимита."""

    status_code: int
    body: bytes
    final_url: str


class SafeHttpClient:
    """HTTP-клиент для служебных запросов (верификация, вебхуки).

    Каждый запрос и каждый шаг редиректа проходит SSRF-проверку и идёт на
    закреплённый IP. Внутри — async-клиент: только так можно ограничить *общее*
    время запроса (``deadline``), а не только паузу между байтами, и сервер не
    удержит воркер, отдавая по байту раз в несколько секунд. Синхронные ``get`` и
    ``post`` — обёртки для кода без event loop (API на sync SQLAlchemy, runner).
    """

    def __init__(
        self,
        *,
        resolver: Resolver = system_resolver,
        allow_private: bool = False,
        timeout: float = 5.0,
        deadline: float = 15.0,
        max_redirects: int = 3,
        max_body_bytes: int = 64 * 1024,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._resolver = resolver
        self._allow_private = allow_private
        self._timeout = timeout
        self._deadline = deadline
        self._max_redirects = max_redirects
        self._max_body_bytes = max_body_bytes
        self._transport = transport

    def get(self, url: str) -> FetchResult:
        """Синхронная версия :meth:`aget`; нельзя вызывать из работающего event loop."""
        return asyncio.run(self.aget(url))

    def post(self, url: str, *, content: bytes, headers: dict[str, str]) -> int:
        """Синхронная версия :meth:`apost`; нельзя вызывать из работающего event loop."""
        return asyncio.run(self.apost(url, content=content, headers=headers))

    async def aget(self, url: str) -> FetchResult:
        """GET с ручной обработкой редиректов в пределах того же хоста."""
        target = parse_target_url(url, allow_private=self._allow_private)
        path = _path_of(url)
        async with self._client() as client, self._total_deadline():
            for _ in range(self._max_redirects + 1):
                status, body, location = await self._fetch_once(client, target, path)
                if location is None:
                    return FetchResult(status, body, f"{target.origin}{path}")
                try:
                    next_url = urljoin(f"{target.origin}{path}", location)
                except ValueError as exc:
                    raise InvalidTargetUrlError("malformed redirect location") from exc
                next_target = parse_target_url(next_url, allow_private=self._allow_private)
                if next_target.host != target.host:
                    raise TargetForbiddenError("redirect to another host is not allowed")
                target, path = next_target, _path_of(next_url)
        raise TargetForbiddenError("too many redirects")

    async def apost(self, url: str, *, content: bytes, headers: dict[str, str]) -> int:
        """POST без редиректов (для вебхуков); тело ответа не читается."""
        target = parse_target_url(url, allow_private=self._allow_private)
        async with self._client() as client, self._total_deadline():
            pinned = await self._resolve(target)
            request_url, host_header, extensions = pinned_request_parts(pinned, _path_of(url))
            async with client.stream(
                "POST",
                request_url,
                content=content,
                headers={**headers, **host_header},
                extensions=extensions,
            ) as response:
                return response.status_code

    def _client(self) -> httpx.AsyncClient:
        # trust_env=False: прокси из окружения ломают закрепление IP и SNI.
        return httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=False,
            trust_env=False,
            transport=self._transport,
        )

    @contextlib.asynccontextmanager
    async def _total_deadline(self) -> AsyncIterator[None]:
        try:
            async with asyncio.timeout(self._deadline):
                yield
        except TimeoutError as exc:
            raise httpx.TimeoutException("total request deadline exceeded") from exc

    async def _resolve(self, target: TargetUrl) -> PinnedTarget:
        return await asyncio.to_thread(
            resolve_target, target, resolver=self._resolver, allow_private=self._allow_private
        )

    async def _fetch_once(
        self, client: httpx.AsyncClient, target: TargetUrl, path: str
    ) -> tuple[int, bytes, str | None]:
        pinned = await self._resolve(target)
        request_url, host_header, extensions = pinned_request_parts(pinned, path)
        # Просим без сжатия; если сервер всё равно сжал, распаковываем сами с лимитом.
        headers = {**host_header, "Accept-Encoding": "identity"}
        async with client.stream(
            "GET", request_url, headers=headers, extensions=extensions
        ) as response:
            if response.is_redirect:
                return response.status_code, b"", response.headers.get("location")
            # Ответ уже в памяти (так бывает у MockTransport) — httpx его уже распаковал.
            encoding = (
                "" if response.is_stream_consumed else response.headers.get("content-encoding", "")
            )
            decoder = _BoundedDecoder(encoding, self._max_body_bytes)
            async for chunk in _raw_chunks(response):
                if decoder.feed(chunk):
                    break  # лимит набран, остальное не читаем: защита памяти
            return response.status_code, decoder.body, None


class _BoundedDecoder:
    """Распаковка gzip/deflate, которая никогда не выдаёт больше ``limit`` байт.

    Встроенная распаковка httpx сначала распаковывает кусок целиком, поэтому
    маленький «zip-бомба»-ответ занял бы сотни мегабайт до проверки лимита.
    """

    def __init__(self, content_encoding: str, limit: int) -> None:
        encoding = content_encoding.strip().lower()
        if encoding in ("", "identity"):
            self._inflater = None
        elif encoding in ("gzip", "x-gzip", "deflate"):
            # wbits=47: автоопределение заголовка gzip или zlib.
            self._inflater = zlib.decompressobj(47)
        else:
            raise httpx.DecodingError(f"unsupported content encoding: {encoding}")
        self._limit = limit
        self._body = bytearray()

    def feed(self, chunk: bytes) -> bool:
        """Добавляет кусок; ``True`` — лимит достигнут."""
        room = self._limit - len(self._body)
        if self._inflater is None:
            self._body.extend(chunk[:room])
        else:
            try:
                self._body.extend(self._inflater.decompress(chunk, room))
            except zlib.error as exc:
                raise httpx.DecodingError("corrupt compressed body") from exc
        return len(self._body) >= self._limit

    @property
    def body(self) -> bytes:
        """Накопленное тело."""
        return bytes(self._body)


async def _raw_chunks(response: httpx.Response) -> AsyncIterator[bytes]:
    """Сырые байты ответа без распаковки силами httpx."""
    if response.is_stream_consumed:
        yield response.content
        return
    async for chunk in response.aiter_raw():
        yield chunk


def _path_of(url: str) -> str:
    parts = urlsplit(url)
    path = parts.path or "/"
    return f"{path}?{parts.query}" if parts.query else path
