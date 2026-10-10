"""Один HTTP-запрос нагрузочного теста: закреплённый IP, тайминги, лимит тела."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from http.cookiejar import CookieJar, DefaultCookiePolicy
from typing import Any

import httpx

from loadwatch.http_body import BoundedDecoder, raw_chunks
from loadwatch.metrics import RequestRecord
from loadwatch.security.ssrf import PinnedTarget, pinned_request_parts


def default_expected_status(status: int) -> bool:
    """По умолчанию успех — 2xx и 3xx."""
    return 200 <= status < 400


@dataclass(slots=True)
class ResponseSnapshot:
    """То, что осталось от ответа: статус, заголовки и начало тела (до лимита)."""

    status: int
    headers: httpx.Headers
    body: bytes
    truncated: bool


@dataclass(slots=True)
class _TraceTimes:
    """Отметки времени из trace-расширения httpcore (секунды ``perf_counter``)."""

    stamps: dict[str, float] = field(default_factory=dict)

    async def __call__(self, event: str, info: Mapping[str, Any]) -> None:
        self.stamps[event] = time.perf_counter()

    def span_ms(self, start: str, end: str) -> float:
        if start in self.stamps and end in self.stamps:
            return max(0.0, (self.stamps[end] - self.stamps[start]) * 1000)
        return 0.0

    @property
    def new_connection(self) -> bool:
        return "connection.connect_tcp.started" in self.stamps


class TokenBucket:
    """Глобальный лимит запросов в секунду (``MAX_RPS``) для всех виртуальных пользователей.

    Классическое «ведро с жетонами»: жетоны копятся со скоростью ``rate``, но не
    больше ``burst`` штук, поэтому даже после остановки event loop проснувшиеся
    запросы не уйдут пачкой больше ``burst``.
    """

    def __init__(
        self, rate: float, burst: float = 1.0, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._rate = rate
        self._burst = max(1.0, burst)
        self._clock = clock
        self._tokens = self._burst
        self._updated = clock()

    def try_take(self) -> float:
        """Берёт жетон; возвращает 0 при успехе, иначе — сколько секунд ждать."""
        now = self._clock()
        self._tokens = min(self._burst, self._tokens + (now - self._updated) * self._rate)
        self._updated = now
        if self._tokens >= 1:
            self._tokens -= 1
            return 0.0
        return (1 - self._tokens) / self._rate

    async def acquire(self) -> None:
        """Ждёт жетон; после пробуждения проверяет заново (жетон мог достаться другому)."""
        while (delay := self.try_take()) > 0:
            await asyncio.sleep(delay)


def classify_error(exc: Exception) -> str:
    """Короткий тип сетевой ошибки для статистики (без текста и адресов)."""
    match exc:
        case httpx.ConnectTimeout():
            return "connect_timeout"
        case httpx.ReadTimeout():
            return "read_timeout"
        case httpx.WriteTimeout():
            return "write_timeout"
        case httpx.PoolTimeout():
            return "pool_timeout"
        case httpx.ConnectError() if "ssl" in str(exc).lower() or "certificate" in str(exc).lower():
            return "tls_error"
        case httpx.ConnectError():
            return "connect_error"
        case httpx.RemoteProtocolError():
            return "protocol_error"
        case httpx.ReadError():
            return "read_error"
        case httpx.HTTPError():
            return type(exc).__name__.lower()
    return "client_error"


def new_load_client(*, max_connections: int, timeout_sec: float) -> httpx.AsyncClient:
    """HTTP-клиент генератора: пул соединений, без прокси, редиректов и общих cookie."""
    # Cookie у каждого виртуального пользователя свои, общий jar клиента их не хранит.
    jar = CookieJar(policy=DefaultCookiePolicy(allowed_domains=[]))
    return httpx.AsyncClient(
        limits=httpx.Limits(
            max_connections=max_connections, max_keepalive_connections=max_connections
        ),
        timeout=httpx.Timeout(timeout_sec, connect=min(5.0, timeout_sec)),
        # Только то, что умеем распаковывать с лимитом (см. loadwatch.http_body).
        headers={"Accept-Encoding": "gzip, deflate"},
        follow_redirects=False,
        trust_env=False,
        cookies=httpx.Cookies(jar),
    )


class PinnedRequester:
    """Выполняет запросы к закреплённому IP и превращает их в :class:`RequestRecord`."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        target: PinnedTarget,
        *,
        dns_ms: float,
        max_response_bytes: int,
        rate_limiter: TokenBucket | None = None,
        deadline_sec: float = 30.0,
    ) -> None:
        self._client = client
        self._deadline_sec = deadline_sec
        self._target = target
        self._dns_ms = dns_ms
        self._max_response_bytes = max_response_bytes
        self._rate_limiter = rate_limiter

    async def request(
        self,
        step: str,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str] | None = None,
        content: bytes | None = None,
        expected_status: Callable[[int], bool] = default_expected_status,
    ) -> tuple[RequestRecord, ResponseSnapshot | None]:
        """Запрос к ``path`` (относительно origin сайта); ответа нет — ``None``."""
        if self._rate_limiter is not None:
            await self._rate_limiter.acquire()
        url, host_header, extensions = pinned_request_parts(self._target, path)
        trace = _TraceTimes()
        request = self._client.build_request(
            method,
            url,
            headers={**(headers or {}), **host_header},
            content=content,
            extensions={**extensions, "trace": trace},
        )
        sent = _request_size(request)
        started = time.perf_counter()
        response: httpx.Response | None = None
        try:
            # Общий дедлайн: таймауты httpx ограничивают паузы, а не весь запрос, и
            # сервер, отдающий по байту, держал бы слот конкурентности весь тест.
            async with asyncio.timeout(self._deadline_sec):
                response = await self._client.send(request, stream=True)
                try:
                    body, truncated = await self._read_body(response)
                finally:
                    await response.aclose()
        except (httpx.HTTPError, TimeoutError) as exc:
            kind = "request_deadline" if isinstance(exc, TimeoutError) else classify_error(exc)
            return self._record(step, trace, started, sent, response, error=kind), None
        error = None if expected_status(response.status_code) else f"http_{response.status_code}"
        record = self._record(step, trace, started, sent, response, error=error)
        return record, ResponseSnapshot(response.status_code, response.headers, body, truncated)

    async def _read_body(self, response: httpx.Response) -> tuple[bytes, bool]:
        """Читает тело не больше лимита, распаковывая с лимитом; остальное не качается."""
        encoding = (
            "" if response.is_stream_consumed else response.headers.get("content-encoding", "")
        )
        decoder = BoundedDecoder(encoding, self._max_response_bytes)
        async for chunk in raw_chunks(response):
            if decoder.feed(chunk):
                return decoder.body, True  # соединение закроется, остаток не скачивается
        return decoder.body, False

    def _record(
        self,
        step: str,
        trace: _TraceTimes,
        started: float,
        sent: int,
        response: httpx.Response | None,
        *,
        error: str | None,
    ) -> RequestRecord:
        finished = time.perf_counter()
        headers_done = trace.stamps.get("http11.receive_response_headers.complete", finished)
        received = (
            response.num_bytes_downloaded + _headers_size(response.headers) + 15
            if response is not None
            else 0
        )
        return RequestRecord(
            step=step,
            total_ms=(finished - started) * 1000,
            status=response.status_code if response is not None else None,
            error=error,
            dns_ms=self._dns_ms if trace.new_connection else 0.0,
            connect_ms=trace.span_ms(
                "connection.connect_tcp.started", "connection.connect_tcp.complete"
            ),
            tls_ms=trace.span_ms("connection.start_tls.started", "connection.start_tls.complete"),
            ttfb_ms=trace.span_ms(
                "http11.send_request_headers.started", "http11.receive_response_headers.complete"
            ),
            transfer_ms=max(0.0, (finished - headers_done) * 1000) if response is not None else 0.0,
            bytes_sent=sent,
            bytes_received=received,
        )


def _headers_size(headers: httpx.Headers) -> int:
    return sum(len(key) + len(value) + 4 for key, value in headers.raw)


def _request_size(request: httpx.Request) -> int:
    """Примерный размер запроса на проводе: стартовая строка, заголовки, тело."""
    line = len(request.method) + len(request.url.raw_path) + 12
    body = int(request.headers.get("content-length", "0") or 0)
    return line + _headers_size(request.headers) + 2 + body
