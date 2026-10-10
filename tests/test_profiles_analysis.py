"""RampUpLoad, StepLoad, RegressionDetector и SLO на известных данных."""

from collections.abc import Callable
from typing import Any

import pytest

from loadwatch.analysis import RegressionDetector, RunPoint, slo_met
from loadwatch.config import Settings
from loadwatch.metrics import SecondStats
from loadwatch.runner.profiles import (
    ProfileLimits,
    RampUpLoad,
    StepLoad,
    profile_from_dict,
)

LIMITS = ProfileLimits(max_rate=150, max_duration_sec=120)


def second(requests: int, errors: int = 0, latency_ms: float = 50.0) -> SecondStats:
    stats = SecondStats(requests=requests, errors=errors)
    stats.latency.record(latency_ms, times=max(requests, 1))
    return stats


# --- RampUpLoad -----------------------------------------------------------------


def test_ramp_up_rates() -> None:
    profile = RampUpLoad(start_rate=10, end_rate=50, ramp_sec=40, hold_sec=20)
    assert profile.duration_sec == 60
    assert profile.rate_at(0) == 10
    assert profile.rate_at(20) == 30
    assert profile.rate_at(40) == 50
    assert profile.rate_at(59) == 50
    assert profile.rate_at(60) == 0


def test_ramp_up_clamp_and_serialization() -> None:
    clamped = RampUpLoad(0, 1000, ramp_sec=100, hold_sec=100).clamp(LIMITS)
    assert clamped.to_dict() == {
        "type": "ramp_up",
        "start_rate": 0.0,
        "end_rate": 150.0,
        "ramp_sec": 100.0,
        "hold_sec": 20.0,
    }
    assert RampUpLoad(1, 2, ramp_sec=500).clamp(LIMITS).duration_sec == 120
    assert isinstance(profile_from_dict(clamped.to_dict()), RampUpLoad)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"start_rate": -1, "end_rate": 1, "ramp_sec": 1},
        {"start_rate": 0, "end_rate": 0, "ramp_sec": 1},
        {"start_rate": 0, "end_rate": 1, "ramp_sec": 0},
        {"start_rate": 0, "end_rate": float("nan"), "ramp_sec": 1},
        {"start_rate": 50, "end_rate": 10, "ramp_sec": 1},
    ],
)
def test_ramp_up_validation(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match=r"positive|finite|decrease"):
        RampUpLoad(**kwargs)


# --- StepLoad -------------------------------------------------------------------


def feed(
    profile: StepLoad, seconds: list[SecondStats], *, saturated_from: int | None = None
) -> None:
    for index, stats in enumerate(seconds):
        profile.observe(
            index, stats, saturated=saturated_from is not None and index >= saturated_from
        )
        if profile.stop_reason():
            break


def test_step_load_finds_ceiling_on_errors() -> None:
    profile = StepLoad(start_rate=10, step_rate=10, step_duration_sec=2, max_steps=10)
    assert [profile.rate_at(t) for t in (0, 1.9, 2, 5)] == [10, 10, 20, 30]
    # ступени по 2 секунды: 10, 20, 30 — без ошибок, на 40 — 20% ошибок
    feed(
        profile,
        [
            second(10),
            second(10),
            second(20),
            second(20),
            second(30),
            second(30),
            second(40, errors=8),
            second(40, errors=8),
        ],
    )
    assert profile.stop_reason() == "threshold_exceeded"
    result = profile.result()
    assert result["ceiling_rps"] == 30
    assert result["reason"] == "threshold_exceeded"
    assert result["ceiling_is_lower_bound"] is False
    assert [step["passed"] for step in result["steps"]] == [True, True, True, False]
    assert result["steps"][3] == {
        "step": 3,
        "target_rate": 40.0,
        "rps": 40.0,
        "error_rate": 0.2,
        "p95_ms": 50.0,
        "passed": False,
    }
    assert profile.rate_at(9) == 0  # после остановки нагрузки нет


def test_step_load_p95_threshold() -> None:
    profile = StepLoad(10, 10, 1, 5, max_error_rate=0.5, max_p95_ms=200)
    feed(
        profile, [second(10, latency_ms=50), second(20, latency_ms=120), second(30, latency_ms=900)]
    )
    assert profile.result()["ceiling_rps"] == 20


def test_step_load_saturation_gives_no_ceiling() -> None:
    profile = StepLoad(10, 10, 1, 5)
    feed(profile, [second(10), second(20), second(30)], saturated_from=2)
    assert profile.stop_reason() == "generator_saturated"
    result = profile.result()
    assert result["ceiling_rps"] is None
    assert result["reason"] == "generator_saturated"


def test_step_load_all_steps_pass_gives_lower_bound() -> None:
    profile = StepLoad(10, 10, 1, 3)
    feed(profile, [second(10), second(20), second(30)])
    assert profile.stop_reason() is None  # конец по длительности, не досрочно
    result = profile.result()
    assert (result["ceiling_rps"], result["reason"], result["ceiling_is_lower_bound"]) == (
        30,
        "max_steps_reached",
        True,
    )


def test_step_without_responses_fails() -> None:
    profile = StepLoad(10, 10, 1, 3)
    feed(profile, [second(10), SecondStats()])
    assert profile.stop_reason() == "threshold_exceeded"
    assert profile.result()["ceiling_rps"] == 10


def test_step_load_clamp() -> None:
    clamped = StepLoad(10, 20, 30, 100).clamp(LIMITS)
    params = clamped.to_dict()
    assert params["max_steps"] == 4  # 4 ступени по 30 с = 120 с
    by_rate = StepLoad(100, 30, 5, 10).clamp(LIMITS)
    assert by_rate.step_rate_of(by_rate.max_steps - 1) <= 150  # 100, 130 — дальше лимит
    assert by_rate.max_steps == 2
    assert isinstance(profile_from_dict(params), StepLoad)


@pytest.mark.parametrize(
    "args",
    [(0, 10, 5, 3), (10, 0, 5, 3), (10, 10, 0, 3), (10, 10, 5, 0)],
)
def test_step_load_validation(args: tuple[float, float, float, int]) -> None:
    with pytest.raises(ValueError, match="positive"):
        StepLoad(*args)
    with pytest.raises(ValueError, match="range"):
        StepLoad(10, 10, 5, 3, max_error_rate=2)


# --- регрессии и SLO ----------------------------------------------------------------


@pytest.fixture
def detector(make_settings: Callable[..., Settings]) -> RegressionDetector:
    return RegressionDetector(make_settings())


def history(p95: list[float], **kwargs: Any) -> list[RunPoint]:
    base = {"error_rate": 0.0, "apdex": 0.95, "ceiling_rps": None}
    return [RunPoint(p95_ms=value, **{**base, **kwargs}) for value in p95]


def test_p95_regression_against_median(detector: RegressionDetector) -> None:
    # медиана [100, 98, 102, 500, 100] = 100: один выброс на базу не влияет
    result = detector.detect(RunPoint(140, 0.0, 0.95, None), history([100, 98, 102, 500, 100]))
    assert result is not None and result.detected
    [finding] = result.findings
    assert (finding.metric, finding.baseline, finding.current) == ("p95_ms", 100, 140)
    assert finding.change_pct == 40.0
    assert finding.baseline_runs == 5
    assert finding.message_key == "regression.p95_ms"
    assert result.to_dict()["detected"] is True


def test_small_change_is_not_regression(detector: RegressionDetector) -> None:
    result = detector.detect(RunPoint(125, 0.0, 0.95, None), history([100, 100, 100]))
    assert result is not None and not result.detected
    assert result.baseline["p95_ms"] == 100


def test_tiny_absolute_change_is_noise(detector: RegressionDetector) -> None:
    """2 → 4 мс — это +100%, но всего 2 мс: шум, а не регрессия."""
    result = detector.detect(RunPoint(4, 0.0, 0.95, None), history([2, 2, 2]))
    assert result is not None and not result.detected


def test_not_enough_history(detector: RegressionDetector) -> None:
    assert detector.detect(RunPoint(999, 0.5, 0.1, None), history([100, 100])) is None


def test_error_rate_apdex_and_ceiling_regressions(detector: RegressionDetector) -> None:
    past = history([100, 100, 100], error_rate=0.01, apdex=0.95, ceiling_rps=200)
    result = detector.detect(RunPoint(100, 0.05, 0.80, 150), past)
    assert result is not None
    metrics = {finding.metric: finding for finding in result.findings}
    assert set(metrics) == {"error_rate", "apdex", "ceiling_rps"}
    assert metrics["error_rate"].change_abs == 0.04
    assert metrics["error_rate"].change_pct is None
    assert metrics["ceiling_rps"].change_pct == -25.0


def test_only_last_runs_are_used(make_settings: Callable[..., Settings]) -> None:
    detector = RegressionDetector(make_settings(regression_baseline_runs=3))
    result = detector.detect(RunPoint(200, 0, None, None), history([190, 190, 190, 10, 10]))
    assert result is not None and not result.detected  # старые быстрые запуски не учитываются
    assert result.baseline_runs == 3


@pytest.mark.parametrize(
    ("p95", "errors", "slo_p95", "slo_err", "expected"),
    [
        (100, 0.0, None, None, None),
        (100, 0.0, 200, None, True),
        (300, 0.0, 200, None, False),
        (100, 0.02, None, 0.01, False),
        (100, 0.01, 200, 0.01, True),
        (250, 0.0, 200, 0.5, False),
    ],
)
def test_slo(
    p95: float, errors: float, slo_p95: int | None, slo_err: float | None, expected: bool | None
) -> None:
    assert slo_met(p95, errors, slo_p95_ms=slo_p95, slo_max_error_rate=slo_err) is expected
