"""Схемы API сценариев, секретов и dry-run."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, model_validator

from loadwatch.flow.steps import Flow, RequestStep, VarName

MAX_SECRETS = 20
MAX_SECRET_LENGTH = 4096


class ConstantProfileIn(BaseModel):
    """Постоянная нагрузка: ``rate`` проходов в секунду в течение ``duration_sec``."""

    type: Literal["constant"]
    rate: float = Field(gt=0, le=10_000)
    duration_sec: float = Field(gt=0, le=86_400)


# Остальные профили (рост, ступени) добавятся в фазе 6 в этот же Union.
LoadProfileIn = Annotated[ConstantProfileIn, Field(discriminator="type")]


class ScenarioIn(BaseModel):
    """Сценарий целиком (создание и полная замена)."""

    name: str = Field(min_length=1, max_length=100)
    load_profile: LoadProfileIn
    flow: list[RequestStep] = Field(min_length=1)
    apdex_t_ms: int = Field(default=500, ge=10, le=60_000)
    slo_p95_ms: int | None = Field(default=None, ge=1, le=600_000)
    slo_max_error_rate: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def _check_flow(self) -> ScenarioIn:
        Flow(steps=self.flow)  # проверки между шагами: имена, порядок переменных
        return self


class SecretOut(BaseModel):
    """Секрет в ответах API: только имя и маска."""

    name: str
    value: str = Field(description="Всегда маска: значение после сохранения не показывается")


class ScenarioOut(BaseModel):
    """Сценарий в ответах API."""

    id: uuid.UUID
    site_id: uuid.UUID
    name: str
    load_profile: dict[str, Any]
    effective_load_profile: dict[str, Any] = Field(
        description="Профиль после обрезки лимитами сервера — с ним и пойдёт тест"
    )
    flow: list[dict[str, Any]]
    apdex_t_ms: int
    slo_p95_ms: int | None
    slo_max_error_rate: float | None
    secrets: list[SecretOut]
    missing_secrets: list[str] = Field(
        description="Секреты, которые поток использует, но не заданы"
    )
    created_at: datetime
    updated_at: datetime


class SecretsUpdate(BaseModel):
    """Значения секретов: строка — задать, ``null`` — удалить."""

    secrets: dict[VarName, Annotated[str, Field(max_length=MAX_SECRET_LENGTH)] | None] = Field(
        max_length=MAX_SECRETS
    )


class StepResultOut(BaseModel):
    """Итог шага dry-run; секреты замаскированы."""

    name: str
    method: str
    path: str
    ok: bool
    status: int | None
    error: str | None
    detail: str | None
    duration_ms: float
    timings: dict[str, float]
    response_bytes: int
    truncated: bool
    extracted: dict[str, str]


class DryRunOut(BaseModel):
    """Результат одного прохода потока."""

    ok: bool
    error: str | None = Field(default=None, description="dry_run_timeout, если не уложились")
    duration_ms: float
    steps: list[StepResultOut]
    skipped_steps: list[str] = Field(description="Шаги, до которых не дошло из-за ошибки")


__all__ = [
    "ConstantProfileIn",
    "DryRunOut",
    "RequestStep",
    "ScenarioIn",
    "ScenarioOut",
    "SecretOut",
    "SecretsUpdate",
    "StepResultOut",
]
