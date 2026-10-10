"""CSV-экспорт: метрики запуска по шагам и история запусков сайта за период.

Секреты сюда не попадают: в CSV только агрегаты, имена шагов, сценариев и метки.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Sequence
from typing import Any

from loadwatch.models import RunMetrics, Scenario, TestRun

# Ячейка, начинающаяся с этих символов, в Excel/LibreOffice станет формулой (CSV injection).
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

RUN_STEP_COLUMNS = (
    "step",
    "requests",
    "errors",
    "error_rate",
    "rps",
    "latency_min_ms",
    "latency_avg_ms",
    "latency_p50_ms",
    "latency_p90_ms",
    "latency_p95_ms",
    "latency_p99_ms",
    "latency_max_ms",
    "apdex",
    "dns_ms",
    "connect_ms",
    "tls_ms",
    "ttfb_ms",
    "transfer_ms",
)

SITE_RUN_COLUMNS = (
    "run_id",
    "started_at",
    "scenario",
    "source",
    "label",
    "status",
    "duration_sec",
    "requests",
    "rps",
    "error_rate",
    "latency_p50_ms",
    "latency_p95_ms",
    "latency_p99_ms",
    "apdex",
    "ceiling_rps",
    "slo_met",
    "generator_saturated",
    "regression_detected",
)


def _safe(value: Any) -> Any:
    """Текст, похожий на формулу, экранируется апострофом; числа не трогаются."""
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def _write(columns: Sequence[str], rows: Iterable[dict[str, Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(columns), extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: _safe(value) for key, value in row.items()})
    return buffer.getvalue()


def _stats_row(name: str, stats: dict[str, Any]) -> dict[str, Any]:
    latency = stats.get("latency_ms", {})
    timings = stats.get("timings", {})
    return {
        "step": name,
        "requests": stats.get("requests"),
        "errors": stats.get("errors"),
        "error_rate": stats.get("error_rate"),
        "rps": stats.get("rps"),
        "latency_min_ms": latency.get("min"),
        "latency_avg_ms": latency.get("avg"),
        "latency_p50_ms": latency.get("p50"),
        "latency_p90_ms": latency.get("p90"),
        "latency_p95_ms": latency.get("p95"),
        "latency_p99_ms": latency.get("p99"),
        "latency_max_ms": latency.get("max"),
        "apdex": stats.get("apdex"),
        **{
            key: timings.get(key)
            for key in ("dns_ms", "connect_ms", "tls_ms", "ttfb_ms", "transfer_ms")
        },
    }


def run_csv(metrics: RunMetrics) -> str:
    """Метрики запуска: строка на каждый шаг и итоговая строка ``TOTAL``."""
    summary = metrics.summary
    rows = [_stats_row(step["name"], step) for step in summary.get("steps", [])]
    rows.append(_stats_row("TOTAL", summary))
    return _write(RUN_STEP_COLUMNS, rows)


def site_csv(
    runs: Sequence[tuple[TestRun, RunMetrics | None]], scenarios: dict[Any, Scenario]
) -> str:
    """История запусков сайта: строка на запуск."""
    rows = []
    for run, metrics in runs:
        scenario = scenarios.get(run.scenario_id)
        rows.append(
            {
                "run_id": str(run.id),
                "started_at": run.started_at.isoformat() if run.started_at else "",
                "scenario": scenario.name if scenario else "",
                "source": run.source,
                "label": run.label or "",
                "status": run.status,
                "duration_sec": metrics.duration_sec if metrics else "",
                "requests": metrics.requests if metrics else "",
                "rps": metrics.rps if metrics else "",
                "error_rate": metrics.error_rate if metrics else "",
                "latency_p50_ms": metrics.latency_p50_ms if metrics else "",
                "latency_p95_ms": metrics.latency_p95_ms if metrics else "",
                "latency_p99_ms": metrics.latency_p99_ms if metrics else "",
                "apdex": metrics.apdex if metrics and metrics.apdex is not None else "",
                "ceiling_rps": metrics.ceiling_rps
                if metrics and metrics.ceiling_rps is not None
                else "",
                "slo_met": "" if run.slo_met is None else str(run.slo_met).lower(),
                "generator_saturated": str(run.generator_saturated).lower(),
                "regression_detected": str(
                    bool(run.regression and run.regression.get("detected"))
                ).lower(),
            }
        )
    return _write(SITE_RUN_COLUMNS, rows)
