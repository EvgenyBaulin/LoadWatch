"""Выполнение запуска: проверки, генератор нагрузки, сохранение метрик.

Очередь, блокировки и расписание добавит CLI runner (фаза 5); здесь — то, что
происходит с одним уже выбранным запуском.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from loadwatch.config import Settings
from loadwatch.db import utcnow
from loadwatch.flow import FlowExecutor, parse_flow
from loadwatch.metrics import MetricsCollector
from loadwatch.models import RunMetrics, RunStatus, Scenario, Site, TestRun
from loadwatch.repositories.run_repository import RunRepository
from loadwatch.runner.generator import Iteration, LoadRunner, RunLimits, RunOutcome
from loadwatch.runner.http import PinnedRequester, TokenBucket, new_load_client
from loadwatch.runner.profiles import LoadProfile, ProfileLimits, profile_from_dict
from loadwatch.security import ssrf
from loadwatch.security.secrets import SecretsUnavailableError
from loadwatch.services.scenario_service import load_secrets

logger = logging.getLogger(__name__)

HEARTBEAT_EVERY_SEC = 5


class RunAbortedError(Exception):
    """Запуск нельзя выполнить; ``code`` уходит в ``test_runs.error``."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class PreparedRun:
    """Всё, что нужно генератору: цель с закреплённым IP, профиль и поток."""

    pinned: ssrf.PinnedTarget
    dns_ms: float
    profile: LoadProfile
    iteration: Iteration
    apdex_t_ms: float
    params: dict[str, Any]


def params_hash(params: dict[str, Any]) -> str:
    """Хэш параметров: одинаковые сценарий и нагрузка — одинаковый хэш."""
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


class RunService:
    """Выполняет один запуск от ``queued`` до ``finished``/``failed``/``cancelled``."""

    def __init__(
        self,
        session: Session,
        settings: Settings,
        resolver: ssrf.Resolver = ssrf.system_resolver,
    ) -> None:
        self._session = session
        self._settings = settings
        self._resolver = resolver
        self._runs = RunRepository(session)

    def execute(self, run_id: uuid.UUID) -> TestRun:
        """Выполняет запуск синхронно (внутри — свой event loop для генератора)."""
        run = self._runs.get(run_id)
        if run is None:
            raise LookupError(f"run {run_id} not found")
        try:
            prepared = self._prepare(run)
        except RunAbortedError as exc:
            return self._fail(run, exc.code)
        run.status = RunStatus.RUNNING.value
        run.started_at = run.heartbeat_at = utcnow()
        run.params = prepared.params
        run.params_hash = params_hash(prepared.params)
        self._session.commit()
        logger.info("Run %s started: %s", run.id, prepared.params["profile"])
        try:
            outcome = asyncio.run(self._generate(run, prepared))
        except Exception:
            logger.exception("Run %s crashed", run.id)
            return self._fail(run, "internal_error")
        self._save(run, outcome, prepared)
        return run

    def _prepare(self, run: TestRun) -> PreparedRun:
        """Проверки перед запуском: владение, SSRF, лимиты."""
        site = self._session.get(Site, run.site_id)
        scenario = self._session.get(Scenario, run.scenario_id) if run.scenario_id else None
        if site is None or scenario is None:
            raise RunAbortedError("scenario_not_found")
        # Повторная проверка в runner: API могло создать запуск до потери владения.
        if not site.is_verified:
            raise RunAbortedError("site_not_verified")
        allow_private = self._settings.allow_private_targets
        try:
            target = ssrf.parse_target_url(site.url, allow_private=allow_private)
            started = time.perf_counter()
            # Резолв и проверка адреса — перед каждым запуском (защита от DNS rebinding).
            pinned = ssrf.resolve_target(
                target, resolver=self._resolver, allow_private=allow_private
            )
            dns_ms = (time.perf_counter() - started) * 1000
        except ssrf.TargetForbiddenError:
            raise RunAbortedError("target_forbidden") from None
        except ssrf.TargetResolutionError:
            raise RunAbortedError("dns_error") from None
        try:
            profile = profile_from_dict(scenario.load_profile)
        except (TypeError, ValueError):
            raise RunAbortedError("invalid_load_profile") from None
        profile = profile.clamp(
            ProfileLimits(
                max_rate=self._settings.max_rps,
                max_duration_sec=self._settings.max_duration_sec,
            )
        )
        try:
            flow = parse_flow(scenario.flow)
        except ValueError:
            raise RunAbortedError("invalid_flow") from None
        try:
            secrets = load_secrets(self._session, self._settings, scenario.id)
        except SecretsUnavailableError:
            raise RunAbortedError("secrets_unavailable") from None
        if flow.secret_names() - secrets.keys():
            raise RunAbortedError("missing_secret")
        # В параметрах только описание потока: значения секретов не попадают в БД открыто.
        params = {"profile": profile.to_dict(), "flow": scenario.flow}
        return PreparedRun(
            pinned=pinned,
            dns_ms=dns_ms,
            profile=profile,
            iteration=FlowExecutor(flow, secrets),
            apdex_t_ms=float(scenario.apdex_t_ms),
            params=params,
        )

    async def _generate(self, run: TestRun, prepared: PreparedRun) -> RunOutcome:
        settings = self._settings
        limits = RunLimits(
            max_concurrency=settings.max_concurrency,
            max_bandwidth_mbit=settings.max_bandwidth_mbit,
            lag_threshold_ms=settings.generator_lag_threshold_ms,
            saturation_seconds=settings.generator_saturation_seconds,
            drain_timeout_sec=settings.request_timeout_sec,
        )
        async with new_load_client(
            max_connections=settings.max_concurrency, timeout_sec=settings.request_timeout_sec
        ) as client:
            requester = PinnedRequester(
                client,
                prepared.pinned,
                dns_ms=prepared.dns_ms,
                max_response_bytes=settings.max_response_bytes,
                rate_limiter=TokenBucket(settings.max_rps),
            )
            runner = LoadRunner(requester, limits)
            return await runner.run(
                prepared.profile,
                prepared.iteration,
                on_second=lambda second: self._on_second(run, second),
            )

    async def _on_second(self, run: TestRun, second: int) -> bool:
        """Heartbeat и проверка отмены; БД — в потоке, чтобы не тормозить генератор."""
        if second % HEARTBEAT_EVERY_SEC:
            return False
        return await asyncio.to_thread(self._heartbeat, run.id)

    def _heartbeat(self, run_id: uuid.UUID) -> bool:
        run = self._runs.get(run_id)
        if run is None:
            return True
        self._session.refresh(run, ["cancel_requested"])
        run.heartbeat_at = utcnow()
        self._session.commit()
        return run.cancel_requested

    def _save(self, run: TestRun, outcome: RunOutcome, prepared: PreparedRun) -> None:
        summary = outcome.collector.summary(outcome.duration_sec, apdex_t_ms=prepared.apdex_t_ms)
        summary["generator"] = outcome.generator
        summary["stop_reason"] = outcome.stop_reason
        self._runs.save_metrics(
            metrics_from_summary(run.id, summary, outcome.collector, outcome.duration_sec)
        )
        run.status = (
            RunStatus.CANCELLED.value
            if outcome.stop_reason == "cancelled"
            else RunStatus.FINISHED.value
        )
        run.stop_reason = outcome.stop_reason
        run.generator_saturated = outcome.generator_saturated
        run.finished_at = utcnow()
        self._session.commit()
        logger.info(
            "Run %s %s: %s requests, p95 %.1f ms, errors %.2f%%, saturated=%s",
            run.id,
            run.status,
            summary["requests"],
            summary["latency_ms"]["p95"],
            summary["error_rate"] * 100,
            outcome.generator_saturated,
        )

    def _fail(self, run: TestRun, code: str) -> TestRun:
        run.status = RunStatus.FAILED.value
        run.error = code
        run.finished_at = utcnow()
        self._session.commit()
        logger.warning("Run %s failed: %s", run.id, code)
        return run


def metrics_from_summary(
    run_id: uuid.UUID, summary: dict[str, Any], collector: MetricsCollector, duration_sec: float
) -> RunMetrics:
    """Раскладывает сводку по колонкам ``run_metrics``."""
    latency = summary["latency_ms"]
    return RunMetrics(
        run_id=run_id,
        duration_sec=summary["duration_sec"],
        requests=summary["requests"],
        errors=summary["errors"],
        error_rate=summary["error_rate"],
        rps=summary["rps"],
        iterations_per_sec=summary["iterations"]["per_sec"],
        latency_avg_ms=latency["avg"],
        latency_p50_ms=latency["p50"],
        latency_p90_ms=latency["p90"],
        latency_p95_ms=latency["p95"],
        latency_p99_ms=latency["p99"],
        latency_max_ms=latency["max"],
        apdex=summary["apdex"],
        summary=summary,
        series=collector.series(duration_sec),
    )
