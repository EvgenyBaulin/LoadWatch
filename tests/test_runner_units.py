"""Профили нагрузки, ограничитель RPS, самоконтроль генератора, классификация ошибок."""

import httpx
import pytest

from loadwatch.metrics import SecondStats
from loadwatch.runner.generator import LagMonitor
from loadwatch.runner.http import TokenBucket, classify_error, default_expected_status
from loadwatch.runner.profiles import ConstantLoad, ProfileLimits, profile_from_dict


def test_constant_load() -> None:
    profile = ConstantLoad(rate=10, duration_sec=30)
    assert profile.rate_at(0) == 10
    assert profile.rate_at(29.9) == 10
    assert profile.rate_at(30) == 0
    assert profile.duration_sec == 30
    assert profile.to_dict() == {"type": "constant", "rate": 10.0, "duration_sec": 30.0}
    profile.observe(0, SecondStats(), saturated=True)  # обратная связь не нужна
    assert profile.stop_reason() is None


def test_constant_load_is_clamped_by_server_limits() -> None:
    clamped = ConstantLoad(rate=1000, duration_sec=3600).clamp(
        ProfileLimits(max_rate=150, max_duration_sec=120)
    )
    assert clamped.to_dict() == {"type": "constant", "rate": 150.0, "duration_sec": 120.0}
    small = ConstantLoad(rate=5, duration_sec=10).clamp(ProfileLimits(150, 120))
    assert small.to_dict()["rate"] == 5


@pytest.mark.parametrize(("rate", "duration"), [(0, 10), (10, 0), (-1, 10)])
def test_constant_load_rejects_non_positive(rate: float, duration: float) -> None:
    with pytest.raises(ValueError, match="positive"):
        ConstantLoad(rate, duration)


def test_profile_factory() -> None:
    profile = profile_from_dict({"type": "constant", "rate": 3, "duration_sec": 5})
    assert isinstance(profile, ConstantLoad)
    with pytest.raises(ValueError, match="unknown"):
        profile_from_dict({"type": "spike", "rate": 3})
    with pytest.raises(TypeError):
        profile_from_dict({"type": "constant", "rate": 3, "oops": 1})


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_token_bucket_spaces_requests() -> None:
    clock = FakeClock()
    bucket = TokenBucket(rate=10, burst=1, clock=clock)
    assert bucket.try_take() == 0
    assert bucket.try_take() == pytest.approx(0.1)  # жетона нет — ждать 100 мс
    clock.now += 0.11
    assert bucket.try_take() == 0


def test_token_bucket_burst_after_stall_is_capped() -> None:
    """После долгой паузы event loop уйдёт не больше ``burst`` запросов разом."""
    clock = FakeClock()
    bucket = TokenBucket(rate=10, burst=2, clock=clock)
    clock.now += 10
    taken = sum(1 for _ in range(10) if bucket.try_take() == 0)
    assert taken == 2


def test_profile_rejects_nan() -> None:
    with pytest.raises(ValueError, match="finite"):
        ConstantLoad(float("nan"), 10)
    with pytest.raises(ValueError, match="finite"):
        ConstantLoad(10, float("inf"))


async def test_token_bucket_acquire_waits() -> None:
    bucket = TokenBucket(rate=1000)
    for _ in range(3):
        await bucket.acquire()


def test_lag_monitor_marks_saturation_after_streak() -> None:
    monitor = LagMonitor(threshold_ms=50, saturation_seconds=3)
    for samples in ([60, 70, 80], [55, 90, 10], [5, 5, 5], [80, 80], [70], [90]):
        monitor._current = [float(v) for v in samples]
        monitor.close_second()
    assert monitor.saturated  # три плохие секунды подряд в конце
    assert monitor.summary()["saturated"] is True


def test_lag_monitor_ignores_short_spikes() -> None:
    monitor = LagMonitor(threshold_ms=50, saturation_seconds=3)
    for samples in ([200], [200], [1], [200], [200], [1], [5]):
        monitor._current = [float(v) for v in samples]
        monitor.close_second()
    assert not monitor.saturated


@pytest.mark.parametrize(
    ("exc", "kind"),
    [
        (httpx.ConnectTimeout("t"), "connect_timeout"),
        (httpx.ReadTimeout("t"), "read_timeout"),
        (httpx.WriteTimeout("t"), "write_timeout"),
        (httpx.PoolTimeout("t"), "pool_timeout"),
        (httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED]"), "tls_error"),
        (httpx.ConnectError("Connection refused"), "connect_error"),
        (httpx.RemoteProtocolError("bad"), "protocol_error"),
        (httpx.ReadError("reset"), "read_error"),
        (httpx.UnsupportedProtocol("x"), "unsupportedprotocol"),
        (RuntimeError("x"), "client_error"),
    ],
)
def test_classify_error(exc: Exception, kind: str) -> None:
    assert classify_error(exc) == kind


def test_default_expected_status() -> None:
    assert default_expected_status(200)
    assert default_expected_status(302)
    assert not default_expected_status(404)
    assert not default_expected_status(503)


def test_lag_monitor_counts_fully_blocked_seconds_as_bad() -> None:
    """Event loop стоял целые секунды — замеров нет, и это тоже насыщение."""
    monitor = LagMonitor(threshold_ms=50, saturation_seconds=3)
    for _ in range(3):
        monitor._current = []
        monitor.close_second()
    assert monitor.saturated
