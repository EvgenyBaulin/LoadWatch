"""Схемы API для сайтов и проверки владения."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

VerificationMethodName = Literal["dns", "http"]


class SiteCreate(BaseModel):
    """Новый сайт: origin и необязательное название."""

    url: str = Field(min_length=1, max_length=300, examples=["https://example.com"])
    name: str | None = Field(default=None, max_length=100)


class SiteOut(BaseModel):
    """Сайт в ответах API."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    url: str
    verification_status: Literal["pending", "verified", "unverified"]
    verification_method: VerificationMethodName | None
    verified_at: datetime | None
    last_verified_at: datetime | None
    created_at: datetime


class VerificationInstructionOut(BaseModel):
    """Что разместить для одного способа проверки."""

    method: VerificationMethodName
    record_name: str = Field(description="DNS-имя для TXT-записи или путь файла")
    record_value: str = Field(description="Значение TXT-записи или содержимое файла")


class VerificationInfoOut(BaseModel):
    """Состояние проверки и инструкции по обоим способам."""

    status: Literal["pending", "verified", "unverified"]
    method: VerificationMethodName | None
    token: str
    last_verified_at: datetime | None
    reverify_days: int
    instructions: list[VerificationInstructionOut]


class VerifyRequest(BaseModel):
    """Каким способом проверить владение."""

    method: VerificationMethodName
