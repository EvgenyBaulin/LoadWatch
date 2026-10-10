"""Профили нагрузки: сколько проходов потока запускать в секунду в каждый момент."""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

from loadwatch.metrics import Histogram, SecondStats


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

    def result(self) -> dict[str, Any] | None:
        """Итог профиля для отчёта (например, найденный потолок); по умолчанию нет."""
        return None


def _check_finite(*values: float) -> None:
    if not all(math.isfinite(value) for value in values):
        raise ValueError("profile parameters must be finite numbers")


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


class RampUpLoad(LoadProfile):
    """Плавный рост от ``start_rate`` до ``end_rate`` за ``ramp_sec``, затем удержание."""

    type = "ramp_up"

    def __init__(
        self, start_rate: float, end_rate: float, ramp_sec: float, hold_sec: float = 0
    ) -> None:
        _check_finite(start_rate, end_rate, ramp_sec, hold_sec)
        if start_rate < 0 or end_rate <= 0 or ramp_sec <= 0 or hold_sec < 0:
            raise ValueError("rates and ramp duration must be positive")
        if start_rate > end_rate:
            raise ValueError("ramp-up must not decrease: start_rate > end_rate")
        self.start_rate = float(start_rate)
        self.end_rate = float(end_rate)
        self.ramp_sec = float(ramp_sec)
        self.hold_sec = float(hold_sec)

    @property
    def duration_sec(self) -> float:
        """Рост плюс удержание."""
        return self.ramp_sec + self.hold_sec

    def rate_at(self, elapsed_sec: float) -> float:
        """Линейная интерполяция на росте, постоянная частота на удержании."""
        if elapsed_sec >= self.duration_sec:
            return 0.0
        if elapsed_sec >= self.ramp_sec:
            return self.end_rate
        share = elapsed_sec / self.ramp_sec
        return self.start_rate + (self.end_rate - self.start_rate) * share

    def clamp(self, limits: ProfileLimits) -> RampUpLoad:
        """Частоты не выше лимита; сначала укорачивается удержание, потом рост."""
        ramp = min(self.ramp_sec, limits.max_duration_sec)
        hold = min(self.hold_sec, limits.max_duration_sec - ramp)
        return RampUpLoad(
            min(self.start_rate, limits.max_rate), min(self.end_rate, limits.max_rate), ramp, hold
        )

    def to_dict(self) -> dict[str, Any]:
        """Параметры профиля."""
        return {
            "type": self.type,
            "start_rate": self.start_rate,
            "end_rate": self.end_rate,
            "ramp_sec": self.ramp_sec,
            "hold_sec": self.hold_sec,
        }


@dataclass(slots=True)
class _StepStats:
    rate: float
    seconds: int = 0
    requests: int = 0
    errors: int = 0
    latency: Histogram = field(default_factory=Histogram)


class StepLoad(LoadProfile):
    """Ступени нагрузки: каждая следующая выше на ``step_rate``, пока сайт держит.

    После каждой ступени проверяется доля ошибок и p95. Первая ступень, на
    которой сайт не уложился, останавливает тест, а потолком (``ceiling_rps``)
    считается фактический RPS последней «хорошей» ступени. Если не справляется
    сам генератор, тест останавливается без потолка: принять насыщение
    генератора за предел сайта было бы ошибкой.
    """

    type = "step"

    def __init__(
        self,
        start_rate: float,
        step_rate: float,
        step_duration_sec: float,
        max_steps: int,
        max_error_rate: float = 0.05,
        max_p95_ms: float | None = None,
    ) -> None:
        _check_finite(start_rate, step_rate, step_duration_sec, max_error_rate)
        if max_p95_ms is not None:
            _check_finite(max_p95_ms)
        if start_rate <= 0 or step_rate <= 0 or step_duration_sec < 1 or max_steps < 1:
            raise ValueError("start rate, step and duration must be positive")
        if not 0 <= max_error_rate <= 1 or (max_p95_ms is not None and max_p95_ms <= 0):
            raise ValueError("thresholds are out of range")
        self.start_rate = float(start_rate)
        self.step_rate = float(step_rate)
        self.step_duration_sec = int(step_duration_sec)
        self.max_steps = int(max_steps)
        self.max_error_rate = float(max_error_rate)
        self.max_p95_ms = float(max_p95_ms) if max_p95_ms is not None else None
        self._steps: list[_StepStats] = []
        self._stop: str | None = None
        self._ceiling: float | None = None

    @property
    def duration_sec(self) -> float:
        """Все ступени, если ни одна не остановит тест раньше."""
        return float(self.step_duration_sec * self.max_steps)

    def step_rate_of(self, index: int) -> float:
        """Частота проходов на ступени ``index`` (с нуля)."""
        return self.start_rate + self.step_rate * index

    def rate_at(self, elapsed_sec: float) -> float:
        """Частота текущей ступени."""
        index = int(elapsed_sec // self.step_duration_sec)
        if self._stop is not None or index >= self.max_steps:
            return 0.0
        return self.step_rate_of(index)

    def clamp(self, limits: ProfileLimits) -> StepLoad:
        """Ступени выше лимита и сверх длительности не выполняются."""
        steps = self.max_steps
        if self.step_rate_of(steps - 1) > limits.max_rate:
            steps = max(1, int((limits.max_rate - self.start_rate) // self.step_rate) + 1)
        steps = max(1, min(steps, int(limits.max_duration_sec // self.step_duration_sec)))
        return StepLoad(
            min(self.start_rate, limits.max_rate),
            self.step_rate,
            min(self.step_duration_sec, limits.max_duration_sec),
            steps,
            self.max_error_rate,
            self.max_p95_ms,
        )

    def to_dict(self) -> dict[str, Any]:
        """Параметры профиля."""
        return {
            "type": self.type,
            "start_rate": self.start_rate,
            "step_rate": self.step_rate,
            "step_duration_sec": self.step_duration_sec,
            "max_steps": self.max_steps,
            "max_error_rate": self.max_error_rate,
            "max_p95_ms": self.max_p95_ms,
        }

    def observe(self, second: int, stats: SecondStats, *, saturated: bool) -> None:
        """Копит статистику ступени и в конце ступени решает, идти ли дальше."""
        if self._stop is not None:
            return
        if saturated:
            self._stop = "generator_saturated"
            return
        index = second // self.step_duration_sec
        while len(self._steps) <= index:
            self._steps.append(_StepStats(rate=self.step_rate_of(len(self._steps))))
        step = self._steps[index]
        step.seconds += 1
        step.requests += stats.requests
        step.errors += stats.errors
        step.latency.merge(stats.latency)
        if step.seconds < self.step_duration_sec:
            return
        if not self._passed(step):
            self._stop = "threshold_exceeded"
        else:
            self._ceiling = step.requests / step.seconds
            if index + 1 >= self.max_steps:
                self._stop = "max_steps_reached"

    def stop_reason(self) -> str | None:
        """Порог превышен или генератор насыщен — останавливаемся."""
        return self._stop if self._stop in ("threshold_exceeded", "generator_saturated") else None

    def result(self) -> dict[str, Any]:
        """Потолок и таблица ступеней для графика «RPS по ступеням vs ошибки»."""
        saturated = self._stop == "generator_saturated"
        return {
            # При насыщении генератора потолок не определён — цифрам верить нельзя.
            "ceiling_rps": None if saturated else _round(self._ceiling),
            "reason": self._stop or "incomplete",
            # Все ступени пройдены — настоящий потолок выше найденного.
            "ceiling_is_lower_bound": self._stop == "max_steps_reached",
            "steps": [
                {
                    "step": number,
                    "target_rate": round(step.rate, 2),
                    "rps": round(step.requests / step.seconds, 2) if step.seconds else 0.0,
                    "error_rate": round(step.errors / step.requests, 4) if step.requests else 0.0,
                    "p95_ms": round(step.latency.percentile(95), 2),
                    "passed": step.seconds >= self.step_duration_sec and self._passed(step),
                }
                for number, step in enumerate(self._steps)
            ],
        }

    def _passed(self, step: _StepStats) -> bool:
        if not step.requests:
            return False  # ни одного ответа за ступень — сайт не держит
        if step.errors / step.requests > self.max_error_rate:
            return False
        return self.max_p95_ms is None or step.latency.percentile(95) <= self.max_p95_ms


def _round(value: float | None) -> float | None:
    return round(value, 2) if value is not None else None


PROFILE_TYPES: dict[str, type[LoadProfile]] = {
    ConstantLoad.type: ConstantLoad,
    RampUpLoad.type: RampUpLoad,
    StepLoad.type: StepLoad,
}


def profile_from_dict(data: dict[str, Any]) -> LoadProfile:
    """Создаёт профиль по сохранённому описанию (фабрика по полю ``type``)."""
    params = dict(data)
    kind = params.pop("type", None)
    cls = PROFILE_TYPES.get(str(kind))
    if cls is None:
        raise ValueError(f"unknown load profile type: {kind!r}")
    return cls(**params)
