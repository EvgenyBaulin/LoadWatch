"""Схемы API запусков."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

RunStatusName = Literal["queued", "running", "finished", "failed", "cancelled"]
RunSourceName = Literal["manual", "schedule", "hook"]


class RunCreate(BaseModel):
    """Ручной запуск сценария."""

    scenario_id: uuid.UUID
    label: str | None = Field(default=None, max_length=200)


class RunMetricsBrief(BaseModel):
    """Ключевые цифры запуска для списков."""

    requests: int
    rps: float
    error_rate: float
    latency_p50_ms: float
    latency_p95_ms: float
    latency_p99_ms: float
    apdex: float | None
    ceiling_rps: float | None


class RunOut(BaseModel):
    """Запуск в списках."""

    id: uuid.UUID
    site_id: uuid.UUID
    scenario_id: uuid.UUID | None
    status: RunStatusName
    source: RunSourceName
    label: str | None
    queued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    stop_reason: str | None
    error: str | None
    generator_saturated: bool
    slo_met: bool | None
    cancel_requested: bool
    metrics: RunMetricsBrief | None
    regression: dict[str, Any] | None = Field(
        default=None, description="Базовая линия и ухудшения; null — сравнивать было не с чем"
    )


class RunDetailOut(RunOut):
    """Запуск со сводкой, рядом и параметрами."""

    params: dict[str, Any] | None
    summary: dict[str, Any] | None
    series: list[dict[str, Any]] | None
