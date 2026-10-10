"""Потоковый сбор метрик нагрузочного теста.

Ничего не хранится по каждому запросу: задержки идут в гистограммы, всё
остальное — в счётчики. Память зависит от числа шагов и секунд теста, а не от
числа запросов.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Final

from loadwatch.metrics.histogram import Histogram

MAX_ERROR_TYPES: Final = 20
OTHER_ERRORS: Final = "other"
MAX_SERIES_POINTS: Final = 300
TIMING_NAMES: Final = ("dns", "connect", "tls", "ttfb", "transfer")


@dataclass(slots=True)
class RequestRecord:
    """Итог одного HTTP-запроса."""

    step: str
    total_ms: float
    status: int | None = None  # None — ответа не было (сетевая ошибка)
    error: str | None = None  # тип ошибки или None, если запрос успешен
    dns_ms: float = 0.0
    connect_ms: float = 0.0
    tls_ms: float = 0.0
    ttfb_ms: float = 0.0
    transfer_ms: float = 0.0
    bytes_sent: int = 0
    bytes_received: int = 0


class _ErrorCounter:
    """Счётчик типов ошибок: не больше 20 разных, остальное — в ``other``."""

    __slots__ = ("_counts",)

    def __init__(self) -> None:
        self._counts: Counter[str] = Counter()

    def add(self, error: str) -> None:
        if error in self._counts or len(self._counts) < MAX_ERROR_TYPES - 1:
            self._counts[error] += 1
        else:
            self._counts[OTHER_ERRORS] += 1

    def top(self) -> list[dict[str, Any]]:
        return [{"type": name, "count": count} for name, count in self._counts.most_common()]


@dataclass(slots=True)
class RequestStats:
    """Агрегаты по набору запросов (весь тест или один шаг)."""

    latency: Histogram = field(default_factory=Histogram)
    ok_latency: Histogram = field(default_factory=Histogram)  # для Apdex: только успешные
    ttfb: Histogram = field(default_factory=Histogram)
    timing_sums: dict[str, float] = field(default_factory=lambda: dict.fromkeys(TIMING_NAMES, 0.0))
    requests: int = 0
    errors: int = 0
    status_codes: Counter[int] = field(default_factory=Counter)
    error_types: _ErrorCounter = field(default_factory=_ErrorCounter)

    def add(self, record: RequestRecord) -> None:
        """Учитывает запрос."""
        self.requests += 1
        self.latency.record(record.total_ms)
        if record.status is not None:
            self.status_codes[record.status] += 1
            self.ttfb.record(record.ttfb_ms)
        if record.error is None:
            self.ok_latency.record(record.total_ms)
        else:
            self.errors += 1
            self.error_types.add(record.error)
        for name in TIMING_NAMES:
            self.timing_sums[name] += getattr(record, f"{name}_ms")

    def apdex(self, threshold_ms: float) -> float | None:
        """Apdex(T): (satisfied + tolerating / 2) / total; ошибки — «frustrated»."""
        if not self.requests:
            return None
        satisfied = self.ok_latency.count_at_most(threshold_ms)
        tolerating = self.ok_latency.count_at_most(4 * threshold_ms) - satisfied
        return round((satisfied + tolerating / 2) / self.requests, 4)

    def summary(self, duration_sec: float, apdex_t_ms: float | None) -> dict[str, Any]:
        """Сводка для JSON: счётчики, задержки, тайминги, коды, ошибки, Apdex."""
        timings = {
            f"{name}_ms": round(self.timing_sums[name] / self.requests, 2) if self.requests else 0.0
            for name in TIMING_NAMES
        }
        timings["ttfb_p95_ms"] = round(self.ttfb.percentile(95), 2)
        return {
            "requests": self.requests,
            "errors": self.errors,
            "error_rate": round(self.errors / self.requests, 4) if self.requests else 0.0,
            "rps": round(self.requests / duration_sec, 2) if duration_sec > 0 else 0.0,
            "latency_ms": self.latency.summary(),
            "timings": timings,
            "apdex": self.apdex(apdex_t_ms) if apdex_t_ms else None,
            "status_codes": {str(code): count for code, count in sorted(self.status_codes.items())},
            "errors_top": self.error_types.top(),
        }


@dataclass(slots=True)
class SecondStats:
    """Счётчики одной секунды теста (для графиков и проверок на лету)."""

    requests: int = 0
    errors: int = 0
    iterations: int = 0
    bytes_sent: int = 0
    bytes_received: int = 0
    latency: Histogram = field(default_factory=Histogram)

    def merge(self, other: SecondStats) -> None:
        """Добавляет счётчики другой секунды (для даунсэмплинга ряда)."""
        self.requests += other.requests
        self.errors += other.errors
        self.iterations += other.iterations
        self.bytes_sent += other.bytes_sent
        self.bytes_received += other.bytes_received
        self.latency.merge(other.latency)


class MetricsCollector:
    """Собирает запросы и проходы; в конце отдаёт сводку для сохранения в БД.

    Время передаётся явно (секунды от начала теста), поэтому сборщик не зависит
    от часов и легко проверяется на известных данных.
    """

    def __init__(self) -> None:
        self.overall = RequestStats()
        self.steps: dict[str, RequestStats] = {}
        self.seconds: dict[int, SecondStats] = {}
        self.iteration_latency = Histogram()
        self.iterations_ok = 0
        self.iterations_failed = 0
        self.iterations_dropped = 0  # не запущены: достигнут лимит конкурентности
        self.iterations_aborted = 0  # прерваны: тест закончился или отменён
        self.bytes_sent = 0
        self.bytes_received = 0

    def second(self, index: int) -> SecondStats:
        """Счётчики секунды ``index`` (создаются при первом обращении)."""
        stats = self.seconds.get(index)
        if stats is None:
            stats = self.seconds[index] = SecondStats()
        return stats

    def record_request(self, record: RequestRecord, at_sec: float) -> None:
        """Учитывает запрос, завершившийся в момент ``at_sec`` от начала теста."""
        self.overall.add(record)
        step = self.steps.get(record.step)
        if step is None:
            step = self.steps[record.step] = RequestStats()
        step.add(record)
        second = self.second(int(at_sec))
        second.requests += 1
        second.errors += record.error is not None
        second.bytes_sent += record.bytes_sent
        second.bytes_received += record.bytes_received
        second.latency.record(record.total_ms)
        self.bytes_sent += record.bytes_sent
        self.bytes_received += record.bytes_received

    def record_iteration(self, duration_ms: float, ok: bool, at_sec: float) -> None:
        """Учитывает завершённый проход потока одним виртуальным пользователем."""
        self.iteration_latency.record(duration_ms)
        if ok:
            self.iterations_ok += 1
        else:
            self.iterations_failed += 1
        self.second(int(at_sec)).iterations += 1

    def record_aborted_iteration(self) -> None:
        """Проход прерван генератором до завершения."""
        self.iterations_aborted += 1

    def record_dropped_iteration(self) -> None:
        """Проход не запущен: все слоты конкурентности заняты."""
        self.iterations_dropped += 1

    def series(self, duration_sec: float) -> list[dict[str, Any]]:
        """Посекундный ряд, ужатый до ``MAX_SERIES_POINTS`` точек."""
        # Хвост в доли секунды без событий не даёт пустой точки, а секунды с событиями
        # (ответы, пришедшие в самом конце) не теряются.
        populated = [
            sec for sec, stats in self.seconds.items() if stats.requests or stats.iterations
        ]
        total_seconds = max(1, math.ceil(round(duration_sec, 1)), max(populated, default=0) + 1)
        step = max(1, math.ceil(total_seconds / MAX_SERIES_POINTS))
        points = []
        for start in range(0, total_seconds, step):
            window = SecondStats()
            for sec in range(start, min(start + step, total_seconds)):
                if sec in self.seconds:
                    window.merge(self.seconds[sec])
            width = min(step, total_seconds - start)
            points.append(
                {
                    "t": start,
                    "rps": round(window.requests / width, 2),
                    "iterations_per_sec": round(window.iterations / width, 2),
                    "p95_ms": round(window.latency.percentile(95), 2),
                    "errors": window.errors,
                    "error_rate": round(window.errors / window.requests, 4)
                    if window.requests
                    else 0.0,
                }
            )
        return points

    def summary(self, duration_sec: float, *, apdex_t_ms: float | None = None) -> dict[str, Any]:
        """Итоговая сводка запуска (JSON-совместимая)."""
        iterations = self.iterations_ok + self.iterations_failed
        overall = self.overall.summary(duration_sec, apdex_t_ms)
        return {
            "duration_sec": round(duration_sec, 3),
            **overall,
            "iterations": {
                "total": iterations,
                "ok": self.iterations_ok,
                "failed": self.iterations_failed,
                "dropped": self.iterations_dropped,
                "aborted": self.iterations_aborted,
                "per_sec": round(iterations / duration_sec, 2) if duration_sec > 0 else 0.0,
                "duration_ms": self.iteration_latency.summary(),
            },
            "bytes": {"sent": self.bytes_sent, "received": self.bytes_received},
            "steps": [
                {"name": name, **stats.summary(duration_sec, apdex_t_ms)}
                for name, stats in self.steps.items()
            ],
        }
