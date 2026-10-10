"""Ограничение частоты скользящим окном."""

import pytest

from loadwatch.api.rate_limit import SlidingWindowRateLimiter
from loadwatch.errors import RateLimitedError


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_blocks_after_limit_and_reports_retry_after() -> None:
    clock = FakeClock()
    limiter = SlidingWindowRateLimiter(3, 60, clock=clock)
    for _ in range(3):
        limiter.hit("ip")
    clock.now += 20
    with pytest.raises(RateLimitedError) as info:
        limiter.hit("ip")
    assert info.value.retry_after == 40


def test_window_slides() -> None:
    clock = FakeClock()
    limiter = SlidingWindowRateLimiter(2, 60, clock=clock)
    limiter.hit("ip")
    clock.now += 30
    limiter.hit("ip")
    clock.now += 31  # первое обращение вышло из окна
    limiter.hit("ip")
    with pytest.raises(RateLimitedError):
        limiter.hit("ip")


def test_keys_are_independent() -> None:
    limiter = SlidingWindowRateLimiter(1, 60, clock=FakeClock())
    limiter.hit("a")
    limiter.hit("b")
    with pytest.raises(RateLimitedError):
        limiter.hit("a")


def test_memory_is_bounded_without_resetting_active_clients() -> None:
    clock = FakeClock()
    limiter = SlidingWindowRateLimiter(1, 60, max_keys=10, clock=clock)
    for i in range(10):
        limiter.hit(f"ip-{i}")
    with pytest.raises(RateLimitedError):  # таблица заполнена активными клиентами: новому отказ
        limiter.hit("newcomer")
    with pytest.raises(RateLimitedError):  # у известного клиента лимит не сбросился
        limiter.hit("ip-0")
    assert len(limiter._hits) == 10
    clock.now += 61  # все устарели — место снова есть
    limiter.hit("newcomer")
    assert len(limiter._hits) == 1


def test_reset() -> None:
    limiter = SlidingWindowRateLimiter(1, 60, clock=FakeClock())
    limiter.hit("a")
    limiter.reset()
    limiter.hit("a")
