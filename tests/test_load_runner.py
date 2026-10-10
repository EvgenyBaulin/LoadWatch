"""Генератор нагрузки против настоящего локального HTTP-сервера."""

import time
from collections.abc import Iterator
from typing import Any

import pytest

from loadwatch.runner.generator import LoadRunner, RunLimits, RunOutcome, VirtualUser, simple_get
from loadwatch.runner.http import PinnedRequester, TokenBucket, new_load_client
from loadwatch.runner.profiles import ConstantLoad
from loadwatch.security.ssrf import parse_target_url, resolve_target
from tests import target_app

HOST = "loadwatch.test"


@pytest.fixture(scope="module")
def target_port() -> Iterator[int]:
    with target_app.serve() as port:
        yield port


async def run_load(
    port: int,
    path: str = "/",
    *,
    rate: float = 20,
    duration: float = 1.5,
    max_concurrency: int = 50,
    max_bandwidth_mbit: float = 1000,
    max_response_bytes: int = 256 * 1024,
    lag_threshold_ms: float = 50,
    saturation_seconds: int = 3,
    max_rps: float | None = None,
    iteration: Any = None,
    on_second: Any = None,
    drain_timeout_sec: float = 5,
    deadline_sec: float = 30,
) -> RunOutcome:
    # Имя хоста резолвится подменой в 127.0.0.1: так проверяется закрепление IP.
    target = parse_target_url(f"http://{HOST}:{port}", allow_private=True)
    pinned = resolve_target(target, resolver=lambda h, p: ["127.0.0.1"], allow_private=True)
    limits = RunLimits(
        max_concurrency=max_concurrency,
        max_bandwidth_mbit=max_bandwidth_mbit,
        lag_threshold_ms=lag_threshold_ms,
        saturation_seconds=saturation_seconds,
        drain_timeout_sec=drain_timeout_sec,
    )
    async with new_load_client(max_connections=max_concurrency, timeout_sec=5) as client:
        requester = PinnedRequester(
            client,
            pinned,
            dns_ms=1.5,
            max_response_bytes=max_response_bytes,
            rate_limiter=TokenBucket(max_rps) if max_rps else None,
            deadline_sec=deadline_sec,
        )
        runner = LoadRunner(requester, limits)
        return await runner.run(
            ConstantLoad(rate, duration), iteration or simple_get(path), on_second=on_second
        )


async def test_constant_load_against_real_server(target_port: int) -> None:
    target_app.seen_hosts.clear()
    outcome = await run_load(target_port, rate=20, duration=2)
    summary = outcome.collector.summary(outcome.duration_sec, apdex_t_ms=500)
    assert outcome.stop_reason == "completed"
    assert 36 <= summary["requests"] <= 42  # 20/с × 2 с
    assert summary["error_rate"] == 0
    assert summary["status_codes"] == {"200": summary["requests"]}
    assert summary["apdex"] == 1.0
    assert summary["rps"] == pytest.approx(20, rel=0.15)
    # Закрепление IP: подключение к 127.0.0.1, а сервер видит исходное имя хоста.
    assert set(target_app.seen_hosts) == {f"{HOST}:{target_port}"}
    timings = summary["timings"]
    assert timings["ttfb_ms"] > 0
    assert timings["connect_ms"] > 0  # хотя бы одно новое соединение
    assert 0 < timings["dns_ms"] <= 1.5  # DNS только на новых соединениях
    assert summary["bytes"]["received"] > 0 and summary["bytes"]["sent"] > 0
    assert outcome.generator["saturated"] is False
    assert outcome.profile == {"type": "constant", "rate": 20.0, "duration_sec": 2.0}
    assert len(outcome.collector.series(outcome.duration_sec)) == 2


async def test_concurrency_limit_drops_iterations(target_port: int) -> None:
    outcome = await run_load(target_port, "/slow?ms=800", rate=40, duration=1.5, max_concurrency=5)
    summary = outcome.collector.summary(outcome.duration_sec)
    assert summary["iterations"]["dropped"] > 20
    assert summary["requests"] <= 15


async def test_global_rps_limit(target_port: int) -> None:
    outcome = await run_load(target_port, rate=100, duration=1.5, max_rps=20)
    summary = outcome.collector.summary(outcome.duration_sec)
    assert summary["rps"] <= 20 * 1.1  # проходы ждут своей очереди, темп не выше MAX_RPS


async def test_response_body_is_truncated(target_port: int) -> None:
    bodies: list[tuple[int, bool]] = []

    async def iteration(user: VirtualUser) -> bool:
        record, response = await user.requester.request("big", "GET", "/big?kb=4096")
        user.collector.record_request(record, user.now_sec())
        assert response is not None
        bodies.append((len(response.body), response.truncated))
        return True

    outcome = await run_load(
        target_port, rate=4, duration=0.6, max_response_bytes=10_000, iteration=iteration
    )
    assert bodies and all(body == (10_000, True) for body in bodies)
    received = outcome.collector.summary(outcome.duration_sec)["bytes"]["received"]
    assert received < len(bodies) * 1024 * 1024  # 4 МБ ответа целиком не скачивались


async def test_bandwidth_limit_stops_test(target_port: int) -> None:
    outcome = await run_load(target_port, "/big?kb=256", rate=20, duration=4, max_bandwidth_mbit=1)
    assert outcome.stop_reason == "bandwidth_limit"
    assert outcome.duration_sec < 3


async def test_unexpected_status_counts_as_error(target_port: int) -> None:
    outcome = await run_load(target_port, "/status/503", rate=10, duration=1)
    summary = outcome.collector.summary(outcome.duration_sec)
    assert summary["error_rate"] == 1
    assert summary["errors_top"][0]["type"] == "http_503"
    assert summary["iterations"]["failed"] == summary["iterations"]["total"]


async def test_connection_refused(target_port: int) -> None:
    closed_port = target_app.free_port()
    outcome = await run_load(closed_port, rate=5, duration=0.6)
    summary = outcome.collector.summary(outcome.duration_sec)
    assert summary["errors_top"][0]["type"] == "connect_error"
    assert summary["status_codes"] == {}


async def test_cancel_from_hook(target_port: int) -> None:
    async def cancel_on_first(second: int) -> bool:
        return second >= 0

    outcome = await run_load(target_port, rate=10, duration=5, on_second=cancel_on_first)
    assert outcome.stop_reason == "cancelled"
    # Хук работает в фоне: его ответ читается на следующей секунде.
    assert outcome.duration_sec < 3


async def test_generator_saturation_is_detected(target_port: int) -> None:
    """Проход блокирует event loop — генератор не успевает и обязан это заметить."""

    async def blocking(user: VirtualUser) -> bool:
        time.sleep(0.15)  # имитация перегруженного процессора
        return True

    outcome = await run_load(
        target_port, rate=20, duration=3, iteration=blocking, saturation_seconds=1
    )
    assert outcome.generator_saturated
    assert outcome.generator["lag_max_ms"] > 50


async def test_crashing_iteration_is_counted_as_failed(target_port: int) -> None:
    async def broken(user: VirtualUser) -> bool:
        raise RuntimeError("bug in flow")

    outcome = await run_load(target_port, rate=10, duration=0.5, iteration=broken)
    iterations = outcome.collector.summary(outcome.duration_sec)["iterations"]
    assert iterations["failed"] == iterations["total"] > 0


async def test_unfinished_iterations_are_counted_as_aborted(target_port: int) -> None:
    outcome = await run_load(
        target_port, "/slow?ms=3000", rate=10, duration=0.5, drain_timeout_sec=0.2
    )
    iterations = outcome.collector.summary(outcome.duration_sec)["iterations"]
    assert iterations["aborted"] >= 4
    assert iterations["total"] == 0  # ни один не успел завершиться


async def test_request_deadline(target_port: int) -> None:
    outcome = await run_load(target_port, "/slow?ms=2000", rate=4, duration=0.5, deadline_sec=0.3)
    summary = outcome.collector.summary(outcome.duration_sec)
    assert summary["errors_top"][0]["type"] == "request_deadline"
    assert summary["error_rate"] == 1


async def test_gzip_bomb_is_not_unpacked_beyond_limit(target_port: int) -> None:
    sizes: list[int] = []

    async def iteration(user: VirtualUser) -> bool:
        record, response = await user.requester.request("bomb", "GET", "/gzip-bomb")
        user.collector.record_request(record, user.now_sec())
        assert response is not None
        sizes.append(len(response.body))
        return record.error is None

    await run_load(target_port, rate=4, duration=1, max_response_bytes=50_000, iteration=iteration)
    assert sizes and all(size == 50_000 for size in sizes)
