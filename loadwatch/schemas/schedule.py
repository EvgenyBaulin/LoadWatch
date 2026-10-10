"""Схемы API расписания."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ScheduleIn(BaseModel):
    """Расписание сайта: сценарий, cron и часовой пояс."""

    scenario_id: uuid.UUID
    cron: str = Field(min_length=9, max_length=100, examples=["0 */6 * * *"])
    timezone: str = Field(default="UTC", max_length=64, examples=["Europe/Moscow"])
    enabled: bool = True


class ScheduleOut(BaseModel):
    """Расписание в ответах API; все времена — в UTC."""

    scenario_id: uuid.UUID | None
    cron: str
    timezone: str
    enabled: bool
    next_run_at: datetime | None
    last_run_at: datetime | None
    upcoming: list[datetime] = Field(description="Ближайшие срабатывания (UTC)")
