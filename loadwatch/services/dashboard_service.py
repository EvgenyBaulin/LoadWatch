"""Данные для дашборда сайта: ряды метрик по запускам и сводка для stat-панелей."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from loadwatch.config import Settings
from loadwatch.db import utcnow
from loadwatch.errors import InvalidPeriodError, NotFoundError
from loadwatch.models import Site, User
from loadwatch.repositories.run_repository import RunRepository
from loadwatch.repositories.site_repository import SiteRepository
from loadwatch.schemas.dashboard import MetricPoint, SiteMetricsOut, SiteSummaryOut
from loadwatch.services.run_queue_service import run_out

MAX_POINTS = 1000
MAX_PERIOD = timedelta(days=400)
DEFAULT_PERIOD = timedelta(days=7)
TIMING_KEYS = ("dns_ms", "connect_ms", "tls_ms", "ttfb_ms", "transfer_ms")


class DashboardService:
    """Только чтение; данные — только по сайтам владельца."""

    def __init__(self, session: Session, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._runs = RunRepository(session)
        self._sites = SiteRepository(session)

    def metrics(
        self,
        owner: User,
        site_id: uuid.UUID,
        *,
        since: datetime | None,
        until: datetime | None,
        scenario_id: uuid.UUID | None,
    ) -> SiteMetricsOut:
        """Завершённые запуски за период (по умолчанию — последние 7 дней), старые первыми."""
        site = self._site(owner, site_id)
        until = until or utcnow()
        since = since or until - DEFAULT_PERIOD
        if since.tzinfo is None or until.tzinfo is None:
            raise InvalidPeriodError("from and to must include a time zone, e.g. 2026-01-01T00:00Z")
        if since >= until or until - since > MAX_PERIOD:
            raise InvalidPeriodError()
        rows = self._runs.finished_with_metrics(
            site.id, since=since, until=until, scenario_id=scenario_id, limit=MAX_POINTS + 1
        )
        truncated = len(rows) > MAX_POINTS
        points = []
        for run, metrics in reversed(rows[:MAX_POINTS]):
            if run.started_at is None:  # у завершённого запуска быть не может
                continue
            timings = metrics.summary.get("timings", {})
            points.append(
                MetricPoint(
                    run_id=run.id,
                    scenario_id=run.scenario_id,
                    source=run.source,
                    label=run.label,
                    started_at=run.started_at,
                    rps=metrics.rps,
                    error_rate=metrics.error_rate,
                    latency_p50_ms=metrics.latency_p50_ms,
                    latency_p95_ms=metrics.latency_p95_ms,
                    latency_p99_ms=metrics.latency_p99_ms,
                    apdex=metrics.apdex,
                    ceiling_rps=metrics.ceiling_rps,
                    slo_met=run.slo_met,
                    generator_saturated=run.generator_saturated,
                    regression_detected=bool(run.regression and run.regression.get("detected")),
                    status_codes=metrics.summary.get("status_codes", {}),
                    timings={key: float(timings.get(key, 0.0)) for key in TIMING_KEYS},
                )
            )
        return SiteMetricsOut(from_=since, to=until, points=points, truncated=truncated)

    def summary(self, owner: User, site_id: uuid.UUID, *, days: int = 30) -> SiteSummaryOut:
        """Последний запуск, базовая линия, регрессия, потолок и доля запусков в SLO."""
        site = self._site(owner, site_id)
        last = self._runs.last_finished(site.id)
        regression = last.regression if last is not None else None
        ceiling = self._runs.last_ceiling(site.id)
        step_load = ceiling[1].summary.get("step_load", {}) if ceiling else {}
        total, with_slo, met = self._runs.slo_counts(site.id, since=utcnow() - timedelta(days=days))
        return SiteSummaryOut(
            last_run=run_out(last, self._runs.get_metrics(last.id)) if last else None,
            baseline=regression.get("baseline") if regression else None,
            regression=regression,
            ceiling_rps=ceiling[1].ceiling_rps if ceiling else None,
            ceiling_is_lower_bound=step_load.get("ceiling_is_lower_bound") if ceiling else None,
            slo_met_pct=round(met / with_slo * 100, 1) if with_slo else None,
            runs_in_period=total,
            period_days=days,
        )

    def _site(self, owner: User, site_id: uuid.UUID) -> Site:
        site = self._sites.get_owned(site_id, owner.id)
        if site is None:
            raise NotFoundError()
        return site
