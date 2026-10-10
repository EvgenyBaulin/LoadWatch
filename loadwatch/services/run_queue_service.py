"""Запуски со стороны API: постановка в очередь, отмена, история.

API тесты не выполняет — только создаёт запуски в статусе ``queued``. Выполняет
их CLI runner (``python -m loadwatch.runner``).
"""

from __future__ import annotations

import logging
import math
import uuid
from datetime import timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from loadwatch.config import Settings
from loadwatch.db import utcnow
from loadwatch.errors import (
    CooldownActiveError,
    MissingSecretsError,
    NotFoundError,
    RunInProgressError,
    RunNotCancellableError,
    SiteNotVerifiedError,
)
from loadwatch.flow import parse_flow
from loadwatch.models import RunMetrics, RunSource, RunStatus, Scenario, Site, TestRun, User
from loadwatch.repositories.run_repository import RunFilters, RunRepository
from loadwatch.repositories.scenario_repository import ScenarioRepository
from loadwatch.repositories.site_repository import SiteRepository
from loadwatch.schemas.common import Page
from loadwatch.schemas.run import RunCreate, RunDetailOut, RunMetricsBrief, RunOut

logger = logging.getLogger(__name__)


class RunQueueService:
    """Очередь запусков; все операции — только над сайтами владельца."""

    def __init__(self, session: Session, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._runs = RunRepository(session)
        self._sites = SiteRepository(session)
        self._scenarios = ScenarioRepository(session)

    def enqueue(
        self,
        site: Site,
        scenario: Scenario,
        *,
        source: RunSource,
        label: str | None = None,
    ) -> TestRun:
        """Ставит запуск в очередь после всех проверок (общая часть для API и хуков)."""
        # Блокировка строки сайта: два параллельных запроса не поставят два запуска.
        self._sites.lock(site.id)
        if not site.is_verified:
            raise SiteNotVerifiedError()
        if self._runs.has_active(site.id):
            raise RunInProgressError()
        last = self._runs.last_finished_at(site.id)
        cooldown = timedelta(seconds=self._settings.site_cooldown_sec)
        now = utcnow()
        if last is not None and now - last < cooldown:
            raise CooldownActiveError(math.ceil((cooldown - (now - last)).total_seconds()))
        secret_names = {row.name for row in self._scenarios.secrets(scenario.id)}
        if parse_flow(scenario.flow).secret_names() - secret_names:
            raise MissingSecretsError()
        try:
            run = self._runs.add(
                TestRun(
                    site_id=site.id,
                    scenario_id=scenario.id,
                    source=source.value,
                    label=label,
                    queued_at=now,
                )
            )
            self._session.commit()
        except IntegrityError as exc:  # уникальный индекс «один активный запуск на сайт»
            self._session.rollback()
            raise RunInProgressError() from exc
        logger.info("Run %s queued for site %s (%s)", run.id, site.id, source.value)
        return run

    def create_manual(self, owner: User, site_id: uuid.UUID, data: RunCreate) -> RunOut:
        """Ручной запуск («запустить сейчас»)."""
        site = self._site(owner, site_id)
        scenario = self._scenarios.get_owned(data.scenario_id, owner.id)
        if scenario is None or scenario.site_id != site.id:
            raise NotFoundError("Scenario not found")
        run = self.enqueue(site, scenario, source=RunSource.MANUAL, label=data.label)
        return self._out(run, None)

    def list_page(
        self,
        owner: User,
        site_id: uuid.UUID,
        filters: RunFilters,
        *,
        limit: int,
        offset: int,
    ) -> Page[RunOut]:
        """История запусков сайта с фильтрами."""
        site = self._site(owner, site_id)
        runs = self._runs.list_for_site(site.id, filters, limit=limit, offset=offset)
        metrics = self._runs.metrics_for([run.id for run in runs])
        return Page[RunOut](
            items=[self._out(run, metrics.get(run.id)) for run in runs],
            total=self._runs.count_for_site(site.id, filters),
            limit=limit,
            offset=offset,
        )

    def get(self, owner: User, run_id: uuid.UUID) -> RunDetailOut:
        """Запуск со сводкой и посекундным рядом."""
        run = self._owned(owner, run_id)
        metrics = self._runs.get_metrics(run.id)
        brief = self._out(run, metrics)
        return RunDetailOut(
            **brief.model_dump(),
            params=run.params,
            summary=metrics.summary if metrics else None,
            series=metrics.series if metrics else None,
        )

    def cancel(self, owner: User, run_id: uuid.UUID) -> RunOut:
        """Отмена: из очереди — сразу, идущий тест — по флагу (runner увидит за секунды)."""
        # Блокировка: runner в этот момент может забирать запуск из очереди.
        run = self._runs.get_owned(run_id, owner.id, for_update=True)
        if run is None:
            raise NotFoundError()
        if run.status == RunStatus.QUEUED.value:
            run.status = RunStatus.CANCELLED.value
            run.finished_at = utcnow()
        elif run.status == RunStatus.RUNNING.value:
            run.cancel_requested = True
        else:
            raise RunNotCancellableError()
        self._session.commit()
        return self._out(run, self._runs.get_metrics(run.id))

    def _out(self, run: TestRun, metrics: RunMetrics | None) -> RunOut:
        brief = (
            RunMetricsBrief(
                requests=metrics.requests,
                rps=metrics.rps,
                error_rate=metrics.error_rate,
                latency_p50_ms=metrics.latency_p50_ms,
                latency_p95_ms=metrics.latency_p95_ms,
                latency_p99_ms=metrics.latency_p99_ms,
                apdex=metrics.apdex,
                ceiling_rps=metrics.ceiling_rps,
            )
            if metrics is not None
            else None
        )
        return RunOut.model_validate(
            {
                "id": run.id,
                "site_id": run.site_id,
                "scenario_id": run.scenario_id,
                "status": run.status,
                "source": run.source,
                "label": run.label,
                "queued_at": run.queued_at,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
                "stop_reason": run.stop_reason,
                "error": run.error,
                "generator_saturated": run.generator_saturated,
                "slo_met": run.slo_met,
                "cancel_requested": run.cancel_requested,
                "metrics": brief,
            }
        )

    def _owned(self, owner: User, run_id: uuid.UUID) -> TestRun:
        run = self._runs.get_owned(run_id, owner.id)
        if run is None:
            raise NotFoundError()
        return run

    def _site(self, owner: User, site_id: uuid.UUID) -> Site:
        site = self._sites.get_owned(site_id, owner.id)
        if site is None:
            raise NotFoundError()
        return site
