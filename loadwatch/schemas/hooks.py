"""Схемы API триггера из CI, меток на графиках и алертов."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from loadwatch.schemas.run import RunOut


class TriggerTokenOut(BaseModel):
    """Новый токен триггера — показывается один раз."""

    token: str = Field(description="Сохраните в секретах CI: больше он не показывается")
    example: str = Field(description="Пример вызова из CI")


class HookRunIn(BaseModel):
    """Запуск из CI после деплоя."""

    label: str | None = Field(default=None, max_length=200, examples=["v1.4.2"])
    scenario_id: uuid.UUID | None = Field(
        default=None, description="По умолчанию — сценарий из расписания или единственный сценарий"
    )


class AnnotationIn(BaseModel):
    """Комментарий на графиках."""

    label: str = Field(min_length=1, max_length=200)
    at: datetime | None = Field(default=None, description="По умолчанию — сейчас")


class AnnotationOut(BaseModel):
    """Метка на графиках."""

    id: uuid.UUID
    kind: Literal["deploy", "comment"]
    label: str
    at: datetime
    run_id: uuid.UUID | None


class HookRunOut(BaseModel):
    """Результат вызова триггера."""

    run: RunOut
    annotation: AnnotationOut | None


class AlertOut(BaseModel):
    """Алерт сайта."""

    id: uuid.UUID
    type: Literal["regression", "slo_breach", "run_failed", "site_unverified"]
    status: Literal["open", "resolved"]
    run_id: uuid.UUID | None
    details: dict[str, Any] | None
    opened_at: datetime
    last_notified_at: datetime | None
    resolved_at: datetime | None
