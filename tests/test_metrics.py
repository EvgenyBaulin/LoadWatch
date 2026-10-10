"""Гистограмма и сборщик метрик на известных данных."""

import math

import pytest

from loadwatch.metrics import Histogram, MetricsCollector, RequestRecord
from loadwatch.metrics.collector import MAX_ERROR_TYPES, MAX_SERIES_POINTS
from loadwatch.metrics.histogram import PRECISION, bucket_index, bucket_value


def relative_error(actual: float, expected: float) -> float:
    return abs(actual - expected) / expected


def test_percentiles_on_uniform_data() -> None:
    histogram = Histogram()
    for value in range(1, 10_001):  # 1..10000 мс
        histogram.record(float(value))
    for q, expected in [(50, 5000), (90, 9000), (95, 9500), (99, 9900)]:
        assert relative_error(histogram.percentile(q), expected) <= PRECISION
    assert histogram.min == 1
    assert histogram.max == 10_000
    assert histogram.mean == pytest.approx(5000.5)
    assert histogram.count == 10_000


def test_percentile_of_single_value_is_exact() -> None:
    histogram = Histogram()
    histogram.record(123.4)
    assert histogram.percentile(50) == 123.4  # зажато между min и max
    assert histogram.percentile(99) == 123.4


def test_skewed_distribution() -> None:
    histogram = Histogram()
    histogram.record(10.0, times=990)
    histogram.record(2000.0, times=10)
    assert relative_error(histogram.percentile(99), 10) <= PRECISION
    assert histogram.percentile(100) == 2000.0
    assert relative_error(histogram.percentile(99.5), 2000) <= PRECISION


def test_empty_histogram() -> None:
    histogram = Histogram()
    assert histogram.percentile(95) == 0.0
    assert histogram.mean == 0.0
    assert histogram.summary()["min"] == 0.0


def test_invalid_percentile() -> None:
    histogram = Histogram()
    histogram.record(1.0)
    with pytest.raises(ValueError, match="between"):
        histogram.percentile(101)


def test_tiny_huge_and_negative_values() -> None:
    histogram = Histogram()
    for value in (0.0, -5.0, 0.001, 10_000_000.0):
        histogram.record(value)
    assert histogram.min == 0.0
    assert histogram.count == 4
    assert bucket_index(0.0) == 0
    assert bucket_value(0) > 0


def test_bucket_width_is_one_percent() -> None:
    for value in (0.5, 1.0, 17.3, 250.0, 9999.0):
        assert relative_error(bucket_value(bucket_index(value)), value) <= PRECISION


def test_merge_and_roundtrip() -> None:
    a, b = Histogram(), Histogram()
    for value in range(1, 501):
        a.record(float(value))
    for value in range(501, 1001):
        b.record(float(value))
    a.merge(b)
    assert a.count == 1000
    assert relative_error(a.percentile(50), 500) <= PRECISION
    restored = Histogram.from_dict(a.to_dict())
    assert restored.percentile(90) == a.percentile(90)
    assert (restored.count, restored.min, restored.max) == (1000, 1, 1000)
    assert Histogram.from_dict(Histogram().to_dict()).count == 0


def test_count_at_most() -> None:
    histogram = Histogram()
    for value in (100.0, 400.0, 500.0, 1500.0, 2500.0):
        histogram.record(value)
    assert histogram.count_at_most(500) == 3
    assert histogram.count_at_most(2000) == 4


def ok(step: str = "home", ms: float = 100.0, **kwargs: object) -> RequestRecord:
    return RequestRecord(step=step, total_ms=ms, status=200, **kwargs)  # type: ignore[arg-type]


def test_collector_summary_and_steps() -> None:
    collector = MetricsCollector()
    for i in range(90):
        collector.record_request(ok("home", 100, ttfb_ms=60, bytes_received=1000), i / 30)
    for i in range(10):
        failed = RequestRecord("login", 900, status=500, error="http_500", bytes_sent=200)
        collector.record_request(failed, i / 10)
    collector.record_iteration(250, True, 0.5)
    collector.record_iteration(900, False, 1.5)
    collector.record_dropped_iteration()
    summary = collector.summary(3.0)
    assert summary["requests"] == 100
    assert summary["errors"] == 10
    assert summary["error_rate"] == 0.1
    assert summary["rps"] == pytest.approx(33.33, abs=0.01)
    assert summary["status_codes"] == {"200": 90, "500": 10}
    assert summary["errors_top"] == [{"type": "http_500", "count": 10}]
    assert summary["bytes"] == {"sent": 2000, "received": 90_000}
    assert summary["iterations"]["total"] == 2
    assert summary["iterations"]["dropped"] == 1
    assert summary["timings"]["ttfb_ms"] == pytest.approx(54.0)  # 90×60 / 100 запросов
    steps = {step["name"]: step for step in summary["steps"]}
    assert steps["home"]["error_rate"] == 0
    assert steps["login"]["error_rate"] == 1
    assert relative_error(steps["home"]["latency_ms"]["p95"], 100) <= PRECISION
    assert summary["apdex"] is None


def test_apdex_on_known_data() -> None:
    collector = MetricsCollector()
    # T = 500: 6 satisfied (≤500), 2 tolerating (≤2000), 1 frustrated по времени, 1 ошибка.
    for ms in (100, 200, 300, 400, 450, 500, 1000, 2000, 5000):
        collector.record_request(ok(ms=ms), 0)
    collector.record_request(RequestRecord("home", 50, status=500, error="http_500"), 0)
    assert collector.summary(1, apdex_t_ms=500)["apdex"] == (6 + 2 / 2) / 10


def test_error_types_are_capped() -> None:
    collector = MetricsCollector()
    for i in range(50):
        collector.record_request(RequestRecord("s", 1, error=f"type_{i}"), 0)
    top = collector.summary(1)["errors_top"]
    assert len(top) == MAX_ERROR_TYPES
    assert {"type": "other", "count": 50 - (MAX_ERROR_TYPES - 1)} in top


def test_series_is_downsampled() -> None:
    collector = MetricsCollector()
    for second in range(900):
        collector.record_request(ok(ms=10 + second), second + 0.5)
    series = collector.series(900)
    assert len(series) == MAX_SERIES_POINTS
    assert series[0]["t"] == 0 and series[1]["t"] == 3
    assert series[0]["rps"] == 1.0
    short = MetricsCollector()
    short.record_request(ok(), 9.5)
    assert len(short.series(10)) == 10


def test_series_keeps_late_events_after_duration() -> None:
    collector = MetricsCollector()
    collector.record_request(ok(), 0.5)
    collector.record_request(ok(), 1.01)  # ответ пришёл, пока генератор дожидался
    series = collector.series(1.04)
    assert [point["rps"] for point in series] == [1.0, 1.0]


def test_series_marks_error_rate_and_empty_seconds() -> None:
    collector = MetricsCollector()
    collector.record_request(ok(), 0.1)
    collector.record_request(RequestRecord("s", 1, error="read_timeout"), 0.2)
    series = collector.series(3)
    assert series[0]["error_rate"] == 0.5
    assert series[2] == {
        "t": 2,
        "rps": 0.0,
        "iterations_per_sec": 0.0,
        "p95_ms": 0.0,
        "errors": 0,
        "error_rate": 0.0,
    }


def test_network_error_has_no_status_code() -> None:
    collector = MetricsCollector()
    collector.record_request(RequestRecord("s", 5000, error="connect_timeout"), 0)
    summary = collector.summary(1)
    assert summary["status_codes"] == {}
    assert summary["timings"]["ttfb_p95_ms"] == 0
    assert math.isclose(summary["error_rate"], 1)
