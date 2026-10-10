"""Схемы API дашборда сайта: ряды метрик по запускам и сводка."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from loadwatch.schemas.run import RunOut


class MetricPoint(BaseModel):
    """Один завершённый запуск на графиках дашборда."""

    run_id: uuid.UUID
    scenario_id: uuid.UUID | None
    source: str
    label: str | None
    started_at: datetime
    rps: float
    error_rate: float
    latency_p50_ms: float
    latency_p95_ms: float
    latency_p99_ms: float
    apdex: float | None
    ceiling_rps: float | None
    slo_met: bool | None
    generator_saturated: bool
    regression_detected: bool
    status_codes: dict[str, int]
    timings: dict[str, float] = Field(description="Средние DNS, connect, TLS, TTFB, transfer (мс)")


class SiteMetricsOut(BaseModel):
    """Ряды для графиков за период."""

    from_: datetime = Field(alias="from")
    to: datetime
    points: list[MetricPoint]
    truncated: bool = Field(description="Точек больше лимита — показаны последние")

    model_config = {"populate_by_name": True}


class SiteSummaryOut(BaseModel):
    """Сводка для stat-панелей дашборда."""

    last_run: RunOut | None
    baseline: dict[str, Any] | None = Field(description="Медиана последних сопоставимых запусков")
    regression: dict[str, Any] | None
    ceiling_rps: float | None = Field(description="Потолок из последнего ступенчатого теста")
    ceiling_is_lower_bound: bool | None
    slo_met_pct: float | None = Field(description="Доля запусков в SLO за период, %")
    runs_in_period: int
    period_days: int
