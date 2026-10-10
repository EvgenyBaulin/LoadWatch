"""Экспорт данных владельца: CSV по запуску и сайту, PDF-отчёт запуска."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from loadwatch.config import Settings
from loadwatch.db import utcnow
from loadwatch.errors import InvalidPeriodError, NotFoundError, RunHasNoMetricsError
from loadwatch.models import RunMetrics, Scenario, Site, TestRun, User
from loadwatch.reports.csv_export import run_csv, site_csv
from loadwatch.reports.pdf_report import build_run_report
from loadwatch.repositories.run_repository import RunRepository
from loadwatch.repositories.site_repository import SiteRepository

MAX_EXPORT_ROWS = 10_000
MAX_PERIOD = timedelta(days=400)


class ExportService:
    """Только чтение; только объекты владельца."""

    def __init__(self, session: Session, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._runs = RunRepository(session)
        self._sites = SiteRepository(session)

    def run_csv(self, owner: User, run_id: uuid.UUID) -> tuple[str, str]:
        """(имя файла, CSV) с метриками запуска по шагам."""
        run = self._run(owner, run_id)
        metrics = self._runs.get_metrics(run.id)
        if metrics is None:
            raise RunHasNoMetricsError()
        return f"loadwatch-run-{run.id}.csv", run_csv(metrics)

    def site_csv(
        self, owner: User, site_id: uuid.UUID, *, since: datetime | None, until: datetime | None
    ) -> tuple[str, str]:
        """(имя файла, CSV) со всеми запусками сайта за период (по умолчанию 30 дней)."""
        site = self._sites.get_owned(site_id, owner.id)
        if site is None:
            raise NotFoundError()
        until = until or utcnow()
        since = since or until - timedelta(days=30)
        if (
            since.tzinfo is None
            or until.tzinfo is None
            or since >= until
            or until - since > MAX_PERIOD
        ):
            raise InvalidPeriodError()
        rows = self._session.execute(
            select(TestRun, RunMetrics)
            .outerjoin(RunMetrics, RunMetrics.run_id == TestRun.id)
            .where(
                TestRun.site_id == site.id, TestRun.queued_at >= since, TestRun.queued_at <= until
            )
            .order_by(TestRun.queued_at)
            .limit(MAX_EXPORT_ROWS)
        ).all()
        scenarios = {
            scenario.id: scenario
            for scenario in self._session.scalars(
                select(Scenario).where(Scenario.site_id == site.id)
            )
        }
        name = f"loadwatch-{site.name}-{since:%Y%m%d}-{until:%Y%m%d}.csv"
        return _safe_filename(name), site_csv([(row[0], row[1]) for row in rows], scenarios)

    def run_pdf(self, owner: User, run_id: uuid.UUID, *, lang: str | None) -> tuple[str, bytes]:
        """(имя файла, PDF) отчёта о запуске на языке ``lang`` или языке пользователя."""
        run = self._run(owner, run_id)
        site = self._session.get(Site, run.site_id)
        scenario = self._session.get(Scenario, run.scenario_id) if run.scenario_id else None
        metrics = self._runs.get_metrics(run.id)
        if site is None:  # не бывает: сайт удаляется вместе с запусками
            raise NotFoundError()
        pdf = build_run_report(
            locale=lang or owner.locale,
            site_name=site.name,
            site_url=site.url,
            scenario_name=scenario.name if scenario else None,
            run={
                "id": str(run.id),
                "status": run.status,
                "source": run.source,
                "label": run.label,
                "started_at": run.started_at.strftime("%Y-%m-%d %H:%M:%S")
                if run.started_at
                else None,
                "generator_saturated": run.generator_saturated,
            },
            params=run.params,
            summary=metrics.summary if metrics else None,
            series=metrics.series if metrics else None,
            slo={
                "met": run.slo_met,
                "p95_target": scenario.slo_p95_ms if scenario else None,
                "error_rate_target": scenario.slo_max_error_rate if scenario else None,
            },
            regression=run.regression,
            generated_at=utcnow(),
        )
        return f"loadwatch-run-{run.id}.pdf", pdf

    def _run(self, owner: User, run_id: uuid.UUID) -> TestRun:
        run = self._runs.get_owned(run_id, owner.id)
        if run is None:
            raise NotFoundError()
        return run


def _safe_filename(name: str) -> str:
    """Только безопасные для заголовка Content-Disposition символы."""
    return "".join(ch if ch.isascii() and (ch.isalnum() or ch in "-_.") else "_" for ch in name)
