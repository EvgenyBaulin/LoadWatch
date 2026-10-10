"""Схемы API каналов уведомлений."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

ChannelTypeName = Literal["telegram", "email", "slack", "discord", "webhook"]
EventName = Literal[
    "regression", "slo_breach", "run_failed", "site_unverified", "recovery", "digest"
]
ALL_EVENTS: list[EventName] = [
    "regression",
    "slo_breach",
    "run_failed",
    "site_unverified",
    "recovery",
    "digest",
]


class ChannelIn(BaseModel):
    """Канал: тип, название, параметры и подписки на события.

    Параметры (``config``): telegram — ``{"chat_id": "123456"}``; email —
    ``{"email": "me@example.com"}``; slack/discord — ``{"webhook_url": "https://…"}``;
    webhook — ``{"url": "https://…"}``. При изменении секретные поля можно не
    присылать — останутся прежние.
    """

    type: ChannelTypeName
    name: str = Field(min_length=1, max_length=100)
    config: dict[str, Any] = Field(default_factory=dict)
    events: list[EventName] = Field(default_factory=lambda: list(ALL_EVENTS), max_length=6)
    enabled: bool = True


class ChannelOut(BaseModel):
    """Канал в ответах API: секреты (URL вебхуков, ключ подписи) не возвращаются."""

    id: uuid.UUID
    type: ChannelTypeName
    name: str
    config: dict[str, Any]
    events: list[EventName]
    enabled: bool
    created_at: datetime
    signing_secret: str | None = Field(
        default=None,
        description="Только в ответе на создание webhook-канала: секрет HMAC-подписи. "
        "Сохраните его — больше он не показывается.",
    )


class ChannelTestOut(BaseModel):
    """Результат тестовой отправки."""

    ok: bool
    error: str | None
