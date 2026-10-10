"""Гистограмма задержек на логарифмических корзинах.

Память не зависит от числа запросов: значение попадает в корзину с относительной
шириной ``PRECISION`` (1%), хранятся только непустые корзины. Перцентиль
возвращается как середина корзины, поэтому погрешность — не больше половины
ширины корзины (~0,5%). Минимум, максимум, сумма и количество — точные.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any, Final

MIN_VALUE_MS: Final = 0.01  # всё, что быстрее, попадает в первую корзину
MAX_VALUE_MS: Final = 3_600_000.0  # час — заведомо больше любого таймаута
PRECISION: Final = 0.01
_LOG_BASE: Final = math.log1p(PRECISION)
_MAX_INDEX: Final = math.ceil(math.log(MAX_VALUE_MS / MIN_VALUE_MS) / _LOG_BASE)


def bucket_index(value_ms: float) -> int:
    """Номер корзины для значения в миллисекундах."""
    if value_ms <= MIN_VALUE_MS:
        return 0
    index = int(math.log(value_ms / MIN_VALUE_MS) / _LOG_BASE) + 1
    return min(index, _MAX_INDEX)


def bucket_value(index: int) -> float:
    """Представитель корзины: геометрическая середина её границ."""
    if index == 0:
        return MIN_VALUE_MS
    low = MIN_VALUE_MS * math.exp((index - 1) * _LOG_BASE)
    return low * math.sqrt(1 + PRECISION)


class Histogram:
    """Потоковая гистограмма: ``record`` за O(1), перцентили без хранения значений."""

    __slots__ = ("_buckets", "count", "max", "min", "total")

    def __init__(self) -> None:
        self._buckets: dict[int, int] = {}
        self.count = 0
        self.total = 0.0
        self.min = math.inf
        self.max = 0.0

    def record(self, value_ms: float, times: int = 1) -> None:
        """Добавляет значение (``times`` раз)."""
        value_ms = max(value_ms, 0.0)
        index = bucket_index(value_ms)
        self._buckets[index] = self._buckets.get(index, 0) + times
        self.count += times
        self.total += value_ms * times
        self.min = min(self.min, value_ms)
        self.max = max(self.max, value_ms)

    def merge(self, other: Histogram) -> None:
        """Добавляет к этой гистограмме другую."""
        for index, count in other._buckets.items():
            self._buckets[index] = self._buckets.get(index, 0) + count
        self.count += other.count
        self.total += other.total
        self.min = min(self.min, other.min)
        self.max = max(self.max, other.max)

    @property
    def mean(self) -> float:
        """Среднее (точное)."""
        return self.total / self.count if self.count else 0.0

    def percentile(self, q: float) -> float:
        """Перцентиль ``q`` (0–100); для пустой гистограммы — 0."""
        if not self.count:
            return 0.0
        if not 0 <= q <= 100:
            raise ValueError("percentile must be between 0 and 100")
        if q == 100:
            return self.max
        rank = max(1, math.ceil(self.count * q / 100))
        seen = 0
        for index in sorted(self._buckets):
            seen += self._buckets[index]
            if seen >= rank:
                # Середина корзины не может выйти за реально наблюдавшиеся min/max.
                return min(max(bucket_value(index), self.min), self.max)
        return self.max  # недостижимо: rank <= count

    def count_at_most(self, threshold_ms: float) -> int:
        """Сколько значений не больше порога (с точностью до корзины)."""
        limit = bucket_index(threshold_ms)
        return sum(count for index, count in self._buckets.items() if index <= limit)

    def summary(self, percentiles: Iterable[float] = (50, 90, 95, 99)) -> dict[str, float]:
        """Сводка: min/avg/max и перцентили, округлённые до 0,01 мс."""
        result = {
            "min": round(self.min if self.count else 0.0, 2),
            "avg": round(self.mean, 2),
            "max": round(self.max, 2),
        }
        for q in percentiles:
            result[f"p{q:g}"] = round(self.percentile(q), 2)
        return result

    def to_dict(self) -> dict[str, Any]:
        """Компактная форма для JSON (корзины — списком пар)."""
        return {
            "buckets": sorted(self._buckets.items()),
            "count": self.count,
            "total": self.total,
            "min": self.min if self.count else 0.0,
            "max": self.max,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Histogram:
        """Восстанавливает гистограмму из :meth:`to_dict`."""
        histogram = cls()
        histogram._buckets = {int(index): int(count) for index, count in data["buckets"]}
        histogram.count = int(data["count"])
        histogram.total = float(data["total"])
        histogram.min = float(data["min"]) if histogram.count else math.inf
        histogram.max = float(data["max"])
        return histogram
