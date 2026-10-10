"""Работа с таблицами ``test_runs`` и ``run_metrics``."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from sqlalchemy import Select, func, or_, select, text
from sqlalchemy.orm import Session

from loadwatch.models import RunMetrics, RunStatus, Site, TestRun

ACTIVE_STATUSES: Final = (RunStatus.QUEUED.value, RunStatus.RUNNING.value)
# Ключ advisory-блокировки PostgreSQL для выбора следующего запуска из очереди.
CLAIM_LOCK_KEY: Final = 0x4C57_0001


@dataclass(frozen=True, slots=True)
class RunFilters:
    """Фильтры истории запусков."""

    status: str | None = None
    scenario_id: uuid.UUID | None = None
    source: str | None = None


class RunRepository:
    """Доступ к запускам; транзакцией управляет сервис."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, run_id: uuid.UUID) -> TestRun | None:
        """Запуск по id."""
        return self._session.get(TestRun, run_id)

    def get_owned(
        self, run_id: uuid.UUID, owner_id: uuid.UUID, *, for_update: bool = False
    ) -> TestRun | None:
        """Запуск, только если его сайт принадлежит ``owner_id``."""
        query = (
            select(TestRun)
            .join(Site, Site.id == TestRun.site_id)
            .where(TestRun.id == run_id, Site.owner_id == owner_id)
        )
        if for_update:
            query = query.with_for_update(of=TestRun)
        return self._session.scalar(query)

    def add(self, run: TestRun) -> TestRun:
        """Добавляет запуск."""
        self._session.add(run)
        self._session.flush()
        return run

    def has_active(self, site_id: uuid.UUID) -> bool:
        """Есть ли у сайта запуск в очереди или в работе."""
        return bool(
            self._session.scalar(
                select(func.count())
                .select_from(TestRun)
                .where(TestRun.site_id == site_id, TestRun.status.in_(ACTIVE_STATUSES))
            )
        )

    def last_finished_at(self, site_id: uuid.UUID) -> datetime | None:
        """Когда закончился последний реально начатый запуск сайта (для паузы между тестами).

        Запуск, отменённый в очереди, нагрузки не давал и паузу не включает.
        """
        return self._session.scalar(
            select(func.max(TestRun.finished_at)).where(
                TestRun.site_id == site_id, TestRun.started_at.is_not(None)
            )
        )

    def list_for_site(
        self, site_id: uuid.UUID, filters: RunFilters, *, limit: int, offset: int
    ) -> Sequence[TestRun]:
        """История запусков сайта, новые сверху."""
        query = self._filtered(site_id, filters).order_by(TestRun.queued_at.desc(), TestRun.id)
        return self._session.scalars(query.limit(limit).offset(offset)).all()

    def count_for_site(self, site_id: uuid.UUID, filters: RunFilters) -> int:
        """Сколько запусков подходит под фильтры."""
        query = self._filtered(site_id, filters).with_only_columns(func.count()).order_by(None)
        return self._session.scalar(query) or 0

    def metrics_for(self, run_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, RunMetrics]:
        """Метрики нескольких запусков одним запросом."""
        if not run_ids:
            return {}
        rows = self._session.scalars(select(RunMetrics).where(RunMetrics.run_id.in_(run_ids)))
        return {row.run_id: row for row in rows}

    def claim_next(self, *, max_running: int, now: datetime) -> TestRun | None:
        """Забирает самый старый запуск из очереди и переводит его в ``running``.

        Advisory-блокировка делает проверку «сколько уже идёт» и захват атомарными
        для всех runner; ``SKIP LOCKED`` не даёт двум процессам взять один запуск.
        Сайт, у которого уже идёт тест, пропускается.
        """
        self._session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": CLAIM_LOCK_KEY})
        running = self._session.scalar(
            select(func.count())
            .select_from(TestRun)
            .where(TestRun.status == RunStatus.RUNNING.value)
        )
        if (running or 0) >= max_running:
            return None
        busy_sites = select(TestRun.site_id).where(TestRun.status == RunStatus.RUNNING.value)
        run = self._session.scalar(
            select(TestRun)
            .where(TestRun.status == RunStatus.QUEUED.value, TestRun.site_id.not_in(busy_sites))
            .order_by(TestRun.queued_at, TestRun.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if run is not None:
            run.status = RunStatus.RUNNING.value
            run.heartbeat_at = now
        return run

    def stale_running(self, heartbeat_before: datetime) -> Sequence[TestRun]:
        """Запуски в работе, чей процесс перестал отмечаться (упал или убит)."""
        return self._session.scalars(
            select(TestRun)
            .where(
                TestRun.status == RunStatus.RUNNING.value,
                or_(TestRun.heartbeat_at.is_(None), TestRun.heartbeat_at < heartbeat_before),
            )
            .with_for_update(skip_locked=True)
        ).all()

    def comparable_history(
        self, run: TestRun, *, limit: int
    ) -> Sequence[tuple[TestRun, RunMetrics]]:
        """Предыдущие сопоставимые запуски, новые первыми.

        Сопоставимые — тот же сценарий и те же параметры, завершённые успешно и без
        насыщения генератора.
        """
        if run.scenario_id is None or run.params_hash is None or run.started_at is None:
            return []
        rows = self._session.execute(
            select(TestRun, RunMetrics)
            .join(RunMetrics, RunMetrics.run_id == TestRun.id)
            .where(
                TestRun.scenario_id == run.scenario_id,
                TestRun.params_hash == run.params_hash,
                TestRun.status == RunStatus.FINISHED.value,
                TestRun.generator_saturated.is_(False),
                # Оборванные лимитом полосы запуски неполны и в базовую линию не идут.
                TestRun.stop_reason.is_distinct_from("bandwidth_limit"),
                TestRun.id != run.id,
                TestRun.started_at < run.started_at,
            )
            .order_by(TestRun.started_at.desc())
            .limit(limit)
        ).all()
        return [(row[0], row[1]) for row in rows]

    def finished_with_metrics(
        self,
        site_id: uuid.UUID,
        *,
        since: datetime,
        until: datetime,
        scenario_id: uuid.UUID | None,
        limit: int,
    ) -> Sequence[tuple[TestRun, RunMetrics]]:
        """Завершённые запуски с метриками за период, новые первыми."""
        query = (
            select(TestRun, RunMetrics)
            .join(RunMetrics, RunMetrics.run_id == TestRun.id)
            .where(
                TestRun.site_id == site_id,
                TestRun.status == RunStatus.FINISHED.value,
                TestRun.started_at >= since,
                TestRun.started_at <= until,
            )
            .order_by(TestRun.started_at.desc())
            .limit(limit)
        )
        if scenario_id is not None:
            query = query.where(TestRun.scenario_id == scenario_id)
        return [(row[0], row[1]) for row in self._session.execute(query).all()]

    def last_finished(self, site_id: uuid.UUID) -> TestRun | None:
        """Последний завершённый запуск сайта."""
        return self._session.scalar(
            select(TestRun)
            .where(TestRun.site_id == site_id, TestRun.status == RunStatus.FINISHED.value)
            .order_by(TestRun.started_at.desc())
            .limit(1)
        )

    def last_ceiling(self, site_id: uuid.UUID) -> tuple[TestRun, RunMetrics] | None:
        """Последний ступенчатый запуск с найденным потолком (без насыщения генератора)."""
        row = self._session.execute(
            select(TestRun, RunMetrics)
            .join(RunMetrics, RunMetrics.run_id == TestRun.id)
            .where(
                TestRun.site_id == site_id,
                TestRun.status == RunStatus.FINISHED.value,
                TestRun.generator_saturated.is_(False),
                RunMetrics.ceiling_rps.is_not(None),
            )
            .order_by(TestRun.started_at.desc())
            .limit(1)
        ).first()
        return (row[0], row[1]) if row is not None else None

    def slo_counts(self, site_id: uuid.UUID, *, since: datetime) -> tuple[int, int, int]:
        """(запусков за период, из них с SLO, из них в SLO)."""
        row = self._session.execute(
            select(
                func.count(),
                func.count(TestRun.slo_met),
                func.count().filter(TestRun.slo_met.is_(True)),
            ).where(
                TestRun.site_id == site_id,
                TestRun.status == RunStatus.FINISHED.value,
                TestRun.started_at >= since,
            )
        ).one()
        return int(row[0]), int(row[1]), int(row[2])

    def save_metrics(self, metrics: RunMetrics) -> None:
        """Сохраняет (или заменяет) метрики запуска."""
        self._session.merge(metrics)

    def get_metrics(self, run_id: uuid.UUID) -> RunMetrics | None:
        """Метрики запуска."""
        return self._session.get(RunMetrics, run_id)

    @staticmethod
    def _filtered(site_id: uuid.UUID, filters: RunFilters) -> Select[Any]:
        query = select(TestRun).where(TestRun.site_id == site_id)
        if filters.status:
            query = query.where(TestRun.status == filters.status)
        if filters.scenario_id:
            query = query.where(TestRun.scenario_id == filters.scenario_id)
        if filters.source:
            query = query.where(TestRun.source == filters.source)
        return query
