"""Генератор нагрузки: запускает проходы потока по профилю и следит за собой.

Модель открытая: проходы стартуют по расписанию профиля независимо от того,
успели ли завершиться предыдущие (так ведут себя настоящие посетители). Если все
слоты конкурентности заняты, проход не запускается и считается «сброшенным» —
сайт не успевает, это и есть результат теста.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import statistics
import time
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any, Protocol

from loadwatch.metrics import Histogram, MetricsCollector
from loadwatch.runner.http import PinnedRequester
from loadwatch.runner.profiles import LoadProfile

logger = logging.getLogger(__name__)

TICK_SEC = 0.005  # шаг планировщика: 5 мс — точность расписания до 200 проходов в секунду
LAG_SAMPLE_SEC = 0.1


@dataclass(frozen=True, slots=True)
class RunLimits:
    """Лимиты генератора из конфига."""

    max_concurrency: int
    max_bandwidth_mbit: float
    lag_threshold_ms: float = 3.0
    saturation_seconds: int = 3  # столько «плохих» секунд подряд — генератор насыщен
    cpu_threshold: float = 0.9  # доля одного ядра, занятая процессом генератора за секунду
    drain_timeout_sec: float = 10.0  # сколько ждать незавершённые проходы после конца теста


@dataclass(slots=True)
class VirtualUser:
    """Виртуальный пользователь: один проход потока со своим состоянием."""

    index: int
    requester: PinnedRequester
    collector: MetricsCollector
    started_at: float  # perf_counter начала теста
    state: dict[str, Any] = field(default_factory=dict)

    def now_sec(self) -> float:
        """Секунды от начала теста."""
        return time.perf_counter() - self.started_at


class Iteration(Protocol):
    """Один проход потока; возвращает ``True``, если все шаги успешны."""

    def __call__(self, user: VirtualUser) -> Awaitable[bool]:
        """Выполняет проход."""
        ...


class LagMonitor:
    """Самоконтроль генератора: успевает ли он сам.

    Два признака «плохой» секунды:

    * event loop опаздывает: задача, уснувшая на 100 мс, просыпается заметно
      позже (медиана опоздания за секунду выше порога);
    * процесс генератора занял почти всё ядро (``cpu_threshold``). Event loop при
      этом может просыпаться вовремя, но новые проходы уже не успевают
      запускаться и сбрасываются — замер показал, что одного лага для этого мало.

    Несколько плохих секунд подряд — тормозит уже генератор, а не сайт, и цифрам
    теста верить нельзя.
    """

    def __init__(
        self,
        threshold_ms: float,
        saturation_seconds: int,
        cpu_threshold: float = 0.9,
        cpu_clock: Callable[[], float] = time.process_time,
        wall_clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.threshold_ms = threshold_ms
        self.saturation_seconds = saturation_seconds
        self.cpu_threshold = cpu_threshold
        self.lag = Histogram()
        self.saturated = False
        self.cpu_max = 0.0
        self._current: list[float] = []
        self._bad_streak = 0
        self._cpu_clock = cpu_clock
        self._wall_clock = wall_clock
        self._cpu_mark = cpu_clock()
        self._wall_mark = wall_clock()

    async def run(self) -> None:
        """Фоновая задача: меряет опоздание каждые 100 мс."""
        while True:
            expected = time.perf_counter() + LAG_SAMPLE_SEC
            await asyncio.sleep(LAG_SAMPLE_SEC)
            lag_ms = max(0.0, (time.perf_counter() - expected) * 1000)
            self._current.append(lag_ms)
            self.lag.record(lag_ms)

    def close_second(self) -> bool:
        """Итог прошедшей секунды; ``True``, если секунда «плохая»."""
        samples, self._current = self._current, []
        cpu_now, wall_now = self._cpu_clock(), self._wall_clock()
        wall = wall_now - self._wall_mark
        # На коротком окне доля CPU бессмысленна (шум), поэтому меряем от полсекунды.
        cpu_share = (cpu_now - self._cpu_mark) / wall if wall >= 0.5 else 0.0
        self._cpu_mark, self._wall_mark = cpu_now, wall_now
        self.cpu_max = max(self.cpu_max, cpu_share)
        # Ни одного замера за секунду — event loop был заблокирован всю секунду.
        bad = (
            not samples
            or statistics.median(samples) > self.threshold_ms
            or cpu_share >= self.cpu_threshold
        )
        self._bad_streak = self._bad_streak + 1 if bad else 0
        if self._bad_streak >= self.saturation_seconds:
            self.saturated = True
        return bad

    def summary(self) -> dict[str, Any]:
        """Статистика опоздания для отчёта."""
        return {
            "saturated": self.saturated,
            "lag_p50_ms": round(self.lag.percentile(50), 2),
            "lag_p95_ms": round(self.lag.percentile(95), 2),
            "lag_max_ms": round(self.lag.max, 2),
            "threshold_ms": self.threshold_ms,
            "cpu_max": round(self.cpu_max, 2),
            "cpu_threshold": self.cpu_threshold,
        }


@dataclass(slots=True)
class RunOutcome:
    """Результат работы генератора."""

    collector: MetricsCollector
    duration_sec: float
    stop_reason: str  # completed | cancelled | bandwidth_limit | profile:<причина>
    generator: dict[str, Any]
    profile: dict[str, Any]
    profile_result: dict[str, Any] | None = None  # например, потолок ступенчатой нагрузки

    @property
    def generator_saturated(self) -> bool:
        """Генератор не справлялся — результат недостоверен."""
        return bool(self.generator["saturated"])


SecondHook = Callable[[int], Coroutine[Any, Any, bool]]


class LoadRunner:
    """Исполняет профиль нагрузки, вызывая ``iteration`` для каждого прохода."""

    def __init__(self, requester: PinnedRequester, limits: RunLimits) -> None:
        self._requester = requester
        self._limits = limits

    async def run(
        self,
        profile: LoadProfile,
        iteration: Iteration,
        *,
        on_second: SecondHook | None = None,
    ) -> RunOutcome:
        """Запускает тест и возвращает собранные метрики.

        ``on_second`` вызывается раз в секунду и может вернуть ``True``, чтобы
        отменить тест (heartbeat и проверка отмены у runner).
        """
        collector = MetricsCollector()
        monitor = LagMonitor(
            self._limits.lag_threshold_ms,
            self._limits.saturation_seconds,
            cpu_threshold=self._limits.cpu_threshold,
        )
        monitor_task = asyncio.create_task(monitor.run())
        active: set[asyncio.Task[None]] = set()
        started = time.perf_counter()
        stop_reason = "completed"
        credit = 0.0
        last = started
        next_second = 1
        user_index = 0
        hook = _SecondHookRunner(on_second)
        try:
            while True:
                now = time.perf_counter()
                elapsed = now - started
                if elapsed >= profile.duration_sec:
                    break
                while elapsed >= next_second:  # закрываем прошедшие секунды
                    reason = self._close_second(next_second - 1, collector, monitor, profile, hook)
                    next_second += 1
                    if reason:
                        stop_reason = reason
                        break
                if stop_reason != "completed":
                    break
                credit += profile.rate_at(elapsed) * (now - last)
                last = now
                while credit >= 1:
                    credit -= 1
                    if len(active) >= self._limits.max_concurrency:
                        collector.record_dropped_iteration()
                        continue
                    user = VirtualUser(user_index, self._requester, collector, started)
                    user_index += 1
                    task = asyncio.create_task(self._run_iteration(iteration, user))
                    active.add(task)
                    task.add_done_callback(active.discard)
                await asyncio.sleep(TICK_SEC)
            if stop_reason in ("cancelled", "bandwidth_limit"):
                # Отмена и превышение полосы — прекращаем трафик сразу, без дожидания.
                await self._abort(active, collector)
            else:
                await self._drain(active, collector)
                # Последние секунды профиль ещё не видел (цикл вышел по длительности):
                # без них последняя ступень StepLoad осталась бы без оценки.
                for last_second in range(next_second - 1, int(profile.duration_sec)):
                    profile.observe(
                        last_second, collector.second(last_second), saturated=monitor.saturated
                    )
            # Длительность — до последнего ответа: иначе RPS завышался бы на время дожидания.
            duration = time.perf_counter() - started
        finally:
            await hook.close()
            monitor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await monitor_task
            for task in active:
                task.cancel()
        return RunOutcome(
            collector=collector,
            duration_sec=duration,
            stop_reason=stop_reason,
            generator=monitor.summary(),
            profile=profile.to_dict(),
            profile_result=profile.result(),
        )

    def _close_second(
        self,
        second: int,
        collector: MetricsCollector,
        monitor: LagMonitor,
        profile: LoadProfile,
        hook: _SecondHookRunner,
    ) -> str | None:
        """Проверки раз в секунду; возвращает причину остановки или ``None``."""
        monitor.close_second()
        stats = collector.second(second)
        profile.observe(second, stats, saturated=monitor.saturated)
        if self._over_bandwidth(collector, second):
            return "bandwidth_limit"
        if (reason := profile.stop_reason()) is not None:
            return f"profile:{reason}"
        if hook.tick(second):
            return "cancelled"
        return None

    def _over_bandwidth(self, collector: MetricsCollector, second: int) -> bool:
        """Средняя полоса за две последние секунды выше ``MAX_BANDWIDTH_MBIT``."""
        window = [collector.second(s) for s in (second - 1, second) if s >= 0]
        total_bytes = sum(s.bytes_sent + s.bytes_received for s in window)
        mbit = total_bytes * 8 / 1_000_000 / len(window)
        return mbit > self._limits.max_bandwidth_mbit

    async def _run_iteration(self, iteration: Iteration, user: VirtualUser) -> None:
        begin = time.perf_counter()
        try:
            ok = await iteration(user)
        except asyncio.CancelledError:
            raise
        except Exception:  # ошибка в коде потока не должна ронять весь тест
            logger.exception("Iteration crashed")
            ok = False
        end = time.perf_counter()
        user.collector.record_iteration((end - begin) * 1000, ok, end - user.started_at)

    async def _drain(self, active: set[asyncio.Task[None]], collector: MetricsCollector) -> None:
        """Даёт незавершённым проходам доработать, остальные прерывает."""
        if not active:
            return
        _, pending = await asyncio.wait(set(active), timeout=self._limits.drain_timeout_sec)
        await self._abort(pending, collector)

    async def _abort(self, tasks: set[asyncio.Task[None]], collector: MetricsCollector) -> None:
        """Прерывает проходы и учитывает их как прерванные (не теряются из отчёта)."""
        tasks = {task for task in tasks if not task.done()}
        for task in tasks:
            task.cancel()
            collector.record_aborted_iteration()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


class _SecondHookRunner:
    """Вызывает ``on_second`` в фоне, чтобы запись в БД не тормозила расписание.

    Результат (просьба отменить тест) читается на следующих секундах.
    """

    def __init__(self, on_second: SecondHook | None) -> None:
        self._on_second = on_second
        self._task: asyncio.Task[bool] | None = None
        self._cancel = False

    def tick(self, second: int) -> bool:
        """Запускает хук, если прошлый вызов завершён; ``True`` — пора отменять."""
        if self._on_second is None:
            return False
        if self._task is not None and self._task.done():
            if not self._task.cancelled() and self._task.exception() is None:
                self._cancel = self._cancel or self._task.result()
            elif not self._task.cancelled():
                logger.error("Second hook failed", exc_info=self._task.exception())
            self._task = None
        if self._task is None and not self._cancel:
            self._task = asyncio.create_task(self._on_second(second))
        return self._cancel

    async def close(self) -> None:
        """Дожидается последнего вызова хука."""
        if self._task is not None:
            with contextlib.suppress(Exception):
                await self._task


def simple_get(path: str = "/") -> Iteration:
    """Простейший поток из одного GET — до появления многошаговых сценариев."""

    async def iteration(user: VirtualUser) -> bool:
        record, _ = await user.requester.request(f"GET {path}", "GET", path)
        user.collector.record_request(record, user.now_sec())
        return record.error is None

    return iteration
