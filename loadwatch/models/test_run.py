"""Запуск теста и его метрики."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from loadwatch.db import Base, TimestampMixin, UtcDateTime, utcnow
from loadwatch.models.scenario import JsonType


class RunStatus(StrEnum):
    """Состояние запуска."""

    QUEUED = "queued"
    RUNNING = "running"
    FINISHED = "finished"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunSource(StrEnum):
    """Кто запустил тест."""

    MANUAL = "manual"
    SCHEDULE = "schedule"
    HOOK = "hook"  # триггер из CI после деплоя


class TestRun(TimestampMixin, Base):
    """Один запуск сценария."""

    __test__ = False  # не тестовый класс pytest, несмотря на имя
    __tablename__ = "test_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'finished', 'failed', 'cancelled')", name="status"
        ),
        CheckConstraint("source IN ('manual', 'schedule', 'hook')", name="source"),
        Index("ix_test_runs_site_id_started_at", "site_id", "started_at"),
        Index("ix_test_runs_status_queued_at", "status", "queued_at"),
        # Не больше одного активного (в очереди или в работе) запуска на сайт — гарантия БД.
        Index(
            "uq_test_runs_one_active_per_site",
            "site_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    site_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("sites.id", ondelete="CASCADE"), nullable=False
    )
    # История запусков остаётся и после удаления сценария.
    scenario_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("scenarios.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=RunStatus.QUEUED.value)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default=RunSource.MANUAL.value)
    label: Mapped[str | None] = mapped_column(String(200))
    # Фактические параметры (после обрезки лимитами) и их хэш для сравнения запусков.
    params: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    params_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    stop_reason: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(String(500))
    generator_saturated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    slo_met: Mapped[bool | None] = mapped_column(Boolean)
    # Итог RegressionDetector: базовая линия и ухудшения (None — сравнивать было не с чем).
    regression: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    queued_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    heartbeat_at: Mapped[datetime | None] = mapped_column(UtcDateTime)

    def __repr__(self) -> str:
        return f"TestRun(id={self.id!s}, status={self.status!r})"


class RunMetrics(Base):
    """Агрегаты запуска: ключевые числа колонками, подробности — в JSON."""

    __tablename__ = "run_metrics"

    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("test_runs.id", ondelete="CASCADE"), primary_key=True
    )
    duration_sec: Mapped[float] = mapped_column(Float, nullable=False)
    requests: Mapped[int] = mapped_column(Integer, nullable=False)
    errors: Mapped[int] = mapped_column(Integer, nullable=False)
    error_rate: Mapped[float] = mapped_column(Float, nullable=False)
    rps: Mapped[float] = mapped_column(Float, nullable=False)
    iterations_per_sec: Mapped[float] = mapped_column(Float, nullable=False)
    latency_avg_ms: Mapped[float] = mapped_column(Float, nullable=False)
    latency_p50_ms: Mapped[float] = mapped_column(Float, nullable=False)
    latency_p90_ms: Mapped[float] = mapped_column(Float, nullable=False)
    latency_p95_ms: Mapped[float] = mapped_column(Float, nullable=False)
    latency_p99_ms: Mapped[float] = mapped_column(Float, nullable=False)
    latency_max_ms: Mapped[float] = mapped_column(Float, nullable=False)
    apdex: Mapped[float | None] = mapped_column(Float)
    ceiling_rps: Mapped[float | None] = mapped_column(Float)  # для ступенчатой нагрузки
    # Сводка: тайминги, коды ответов, ошибки, шаги, генератор, байты.
    summary: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    # Посекундный ряд (до ~300 точек); очищается по RETENTION_DAYS, агрегаты остаются.
    series: Mapped[list[dict[str, Any]] | None] = mapped_column(JsonType)
