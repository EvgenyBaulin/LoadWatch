"""Профили нагрузки: сколько проходов потока запускать в секунду в каждый момент."""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar

from loadwatch.metrics import SecondStats


@dataclass(frozen=True, slots=True)
class ProfileLimits:
    """Жёсткие лимиты из конфига, которыми обрезаются параметры пользователя."""

    max_rate: float  # проходов в секунду (не больше MAX_RPS)
    max_duration_sec: int


class LoadProfile(ABC):
    """Форма нагрузки во времени.

    Генератор раз в такт спрашивает ``rate_at`` и раз в секунду передаёт
    статистику прошедшей секунды в ``observe``; профили с обратной связью
    (ступенчатый) могут по ней решить остановиться через ``stop_reason``.
    """

    type: ClassVar[str]

    @property
    @abstractmethod
    def duration_sec(self) -> float:
        """Сколько длится тест."""

    @abstractmethod
    def rate_at(self, elapsed_sec: float) -> float:
        """Целевая частота запуска проходов (в секунду) в момент ``elapsed_sec``."""

    @abstractmethod
    def clamp(self, limits: ProfileLimits) -> LoadProfile:
        """Копия профиля, обрезанная лимитами сервера."""

    @abstractmethod
    def to_dict(self) -> dict[str, Any]:
        """Параметры для сохранения и сравнения запусков."""

    def observe(self, second: int, stats: SecondStats, *, saturated: bool) -> None:  # noqa: B027
        """Статистика очередной секунды; по умолчанию не нужна."""

    def stop_reason(self) -> str | None:
        """Причина досрочной остановки или ``None``."""
        return None


class ConstantLoad(LoadProfile):
    """Постоянная частота проходов на всё время теста."""

    type = "constant"

    def __init__(self, rate: float, duration_sec: float) -> None:
        if not (math.isfinite(rate) and math.isfinite(duration_sec)):
            raise ValueError("rate and duration must be finite numbers")
        if rate <= 0 or duration_sec <= 0:
            raise ValueError("rate and duration must be positive")
        self.rate = float(rate)
        self._duration = float(duration_sec)

    @property
    def duration_sec(self) -> float:
        """Длительность теста."""
        return self._duration

    def rate_at(self, elapsed_sec: float) -> float:
        """Одна и та же частота всё время теста."""
        return self.rate if elapsed_sec < self._duration else 0.0

    def clamp(self, limits: ProfileLimits) -> ConstantLoad:
        """Частота и длительность не выше лимитов сервера."""
        return ConstantLoad(
            min(self.rate, limits.max_rate), min(self._duration, limits.max_duration_sec)
        )

    def to_dict(self) -> dict[str, Any]:
        """``{"type": "constant", "rate": …, "duration_sec": …}``."""
        return {"type": self.type, "rate": self.rate, "duration_sec": self._duration}


PROFILE_TYPES: dict[str, type[LoadProfile]] = {ConstantLoad.type: ConstantLoad}


def profile_from_dict(data: dict[str, Any]) -> LoadProfile:
    """Создаёт профиль по сохранённому описанию (фабрика по полю ``type``)."""
    params = dict(data)
    kind = params.pop("type", None)
    cls = PROFILE_TYPES.get(str(kind))
    if cls is None:
        raise ValueError(f"unknown load profile type: {kind!r}")
    return cls(**params)
