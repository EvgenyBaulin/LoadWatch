"""Расписание регулярных тестов сайта."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from loadwatch.db import Base, TimestampMixin, UtcDateTime


class Schedule(TimestampMixin, Base):
    """Одно расписание на сайт: какой сценарий и когда запускать."""

    __tablename__ = "schedules"
    __table_args__ = (
        # Runner раз в минуту выбирает включённые расписания, которым пора.
        Index("ix_schedules_enabled_next_run_at", "enabled", "next_run_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    site_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("sites.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    # Сценарий удалили — расписание остаётся, но не срабатывает, пока не выберут другой.
    scenario_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("scenarios.id", ondelete="SET NULL"), index=True
    )
    cron: Mapped[str] = mapped_column(String(100), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    next_run_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    last_run_at: Mapped[datetime | None] = mapped_column(UtcDateTime)

    def __repr__(self) -> str:
        return f"Schedule(site_id={self.site_id!s}, cron={self.cron!r}, tz={self.timezone!r})"
