"""Поиск регрессий и проверка SLO для завершённого запуска."""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any, Literal

from loadwatch.config import Settings

Direction = Literal["higher_is_worse", "lower_is_worse"]


@dataclass(frozen=True, slots=True)
class RunPoint:
    """Ключевые метрики одного запуска для сравнения."""

    p95_ms: float
    error_rate: float
    apdex: float | None
    ceiling_rps: float | None


@dataclass(frozen=True, slots=True)
class Finding:
    """Одна ухудшившаяся метрика.

    ``message_key`` и числа — для локализованного текста (фронтенд, уведомления):
    «p95 выросла на 40% относительно медианы последних 5 запусков».
    """

    metric: Literal["p95_ms", "error_rate", "apdex", "ceiling_rps"]
    current: float
    baseline: float
    change_pct: float | None  # для долей (ошибки, Apdex) — None, смотрим абсолютную разницу
    change_abs: float
    baseline_runs: int
    message_key: str


@dataclass(frozen=True, slots=True)
class RegressionResult:
    """Итог сравнения: базовая линия и найденные ухудшения."""

    baseline: dict[str, float | None]
    baseline_runs: int
    findings: list[Finding]

    @property
    def detected(self) -> bool:
        """Есть ли регрессия."""
        return bool(self.findings)

    def to_dict(self) -> dict[str, Any]:
        """Для JSON-колонки ``test_runs.regression``."""
        return {
            "detected": self.detected,
            "baseline": self.baseline,
            "baseline_runs": self.baseline_runs,
            "findings": [asdict(finding) for finding in self.findings],
        }


def _median(values: Sequence[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return float(statistics.median(present)) if present else None


class RegressionDetector:
    """Сравнивает запуск с медианой предыдущих сопоставимых запусков.

    Сопоставимые — тот же сценарий с теми же параметрами (хэш), завершённые и без
    ``generator_saturated``. Подбор таких запусков делает репозиторий, здесь —
    только арифметика, чтобы её было легко проверить.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def detect(self, current: RunPoint, history: Sequence[RunPoint]) -> RegressionResult | None:
        """``None`` — истории слишком мало, сравнивать не с чем."""
        settings = self._settings
        history = list(history)[: settings.regression_baseline_runs]
        if len(history) < settings.regression_min_baseline_runs:
            return None
        baseline = {
            "p95_ms": _median([run.p95_ms for run in history]),
            "error_rate": _median([run.error_rate for run in history]),
            "apdex": _median([run.apdex for run in history]),
            "ceiling_rps": _median([run.ceiling_rps for run in history]),
        }
        findings: list[Finding] = []
        count = len(history)
        p95 = baseline["p95_ms"]
        if p95 and current.p95_ms > p95 * (1 + settings.regression_p95_increase_pct / 100):
            findings.append(self._finding("p95_ms", current.p95_ms, p95, count, relative=True))
        errors = baseline["error_rate"]
        if (
            errors is not None
            and current.error_rate - errors > settings.regression_error_rate_increase
        ):
            findings.append(self._finding("error_rate", current.error_rate, errors, count))
        apdex = baseline["apdex"]
        apdex_now = current.apdex
        if (
            apdex is not None
            and apdex_now is not None
            and apdex - apdex_now > settings.regression_apdex_drop
        ):
            findings.append(self._finding("apdex", apdex_now, apdex, count))
        ceiling = baseline["ceiling_rps"]
        ceiling_now = current.ceiling_rps
        ceiling_limit = (ceiling or 0) * (1 - settings.regression_ceiling_drop_pct / 100)
        if ceiling and ceiling_now is not None and ceiling_now < ceiling_limit:
            findings.append(
                self._finding("ceiling_rps", ceiling_now, ceiling, count, relative=True)
            )
        rounded = {
            key: round(value, 4) if value is not None else None for key, value in baseline.items()
        }
        return RegressionResult(baseline=rounded, baseline_runs=count, findings=findings)

    @staticmethod
    def _finding(
        metric: Literal["p95_ms", "error_rate", "apdex", "ceiling_rps"],
        current: float,
        baseline: float,
        count: int,
        *,
        relative: bool = False,
    ) -> Finding:
        change_pct = (
            round((current - baseline) / baseline * 100, 1) if relative and baseline else None
        )
        return Finding(
            metric=metric,
            current=round(current, 4),
            baseline=round(baseline, 4),
            change_pct=change_pct,
            change_abs=round(current - baseline, 4),
            baseline_runs=count,
            message_key=f"regression.{metric}",
        )


def slo_met(
    p95_ms: float, error_rate: float, *, slo_p95_ms: int | None, slo_max_error_rate: float | None
) -> bool | None:
    """Уложился ли запуск в SLO сценария; ``None`` — SLO не задан."""
    if slo_p95_ms is None and slo_max_error_rate is None:
        return None
    if slo_p95_ms is not None and p95_ms > slo_p95_ms:
        return False
    return slo_max_error_rate is None or error_rate <= slo_max_error_rate
