"""Расписания: когда запускать тесты (cron в часовом поясе расписания)."""

from __future__ import annotations

import itertools
import logging
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import CroniterBadDateError, croniter
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from loadwatch.config import Settings
from loadwatch.db import utcnow
from loadwatch.errors import InvalidScheduleError, NotFoundError
from loadwatch.models import RunSource, Schedule, Site, TestRun, User
from loadwatch.repositories.run_repository import RunRepository
from loadwatch.repositories.scenario_repository import ScenarioRepository
from loadwatch.repositories.schedule_repository import ScheduleRepository
from loadwatch.repositories.site_repository import SiteRepository
from loadwatch.schemas.schedule import ScheduleIn, ScheduleOut

logger = logging.getLogger(__name__)

PREVIEW_RUNS = 3


def next_runs(cron: str, timezone: str, after: datetime, count: int = 1) -> list[datetime]:
    """Ближайшие ``count`` срабатываний строго после ``after``, в UTC.

    Cron считается в часовом поясе расписания: «0 9 * * *» в Europe/Moscow — это
    09:00 по Москве, то есть 06:00 UTC, и при переходе на летнее время в других
    поясах час срабатывания в UTC сдвигается вместе с ним.
    """
    zone = ZoneInfo(timezone)
    iterator = croniter(cron, after.astimezone(zone))
    return [iterator.get_next(datetime).astimezone(ZoneInfo("UTC")) for _ in range(count)]


def validate_schedule(cron: str, timezone: str, min_interval_sec: int) -> None:
    """Проверяет выражение, пояс и что тесты не чаще ``min_interval_sec``."""
    if not croniter.is_valid(cron) or len(cron.split()) != 5:
        raise InvalidScheduleError("cron must have 5 fields, e.g. '0 */6 * * *'")
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise InvalidScheduleError("unknown time zone") from exc
    # Минимальный интервал по ближайшему дню срабатываний: «*/1 * * * *» не пройдёт.
    try:
        runs = next_runs(cron, timezone, utcnow(), count=50)
    except CroniterBadDateError as exc:  # например, 31 февраля
        raise InvalidScheduleError("this cron expression never fires") from exc
    gaps = [(b - a).total_seconds() for a, b in itertools.pairwise(runs)]
    if gaps and min(gaps) < min_interval_sec:
        raise InvalidScheduleError(
            f"tests cannot run more often than every {min_interval_sec // 60} minutes"
        )


class ScheduleService:
    """Расписания сайтов владельца и создание запусков по ним (для runner)."""

    def __init__(self, session: Session, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._schedules = ScheduleRepository(session)
        self._sites = SiteRepository(session)
        self._scenarios = ScenarioRepository(session)
        self._runs = RunRepository(session)

    def get(self, owner: User, site_id: uuid.UUID) -> ScheduleOut | None:
        """Расписание сайта или ``None``."""
        site = self._site(owner, site_id)
        schedule = self._schedules.get_for_site(site.id)
        return self._out(schedule) if schedule is not None else None

    def put(self, owner: User, site_id: uuid.UUID, data: ScheduleIn) -> ScheduleOut:
        """Создаёт или заменяет расписание сайта."""
        site = self._site(owner, site_id)
        scenario = self._scenarios.get_owned(data.scenario_id, owner.id)
        if scenario is None or scenario.site_id != site.id:
            raise NotFoundError("Scenario not found")
        validate_schedule(data.cron, data.timezone, self._settings.site_cooldown_sec)
        # Блокировка сайта: параллельные PUT не создадут два расписания и не смешают поля.
        self._sites.lock(site.id)
        schedule = self._schedules.get_for_site(site.id) or self._schedules.add(
            Schedule(site_id=site.id, cron=data.cron)
        )
        schedule.scenario_id = scenario.id
        schedule.cron = data.cron
        schedule.timezone = data.timezone
        schedule.enabled = data.enabled
        schedule.next_run_at = (
            next_runs(data.cron, data.timezone, utcnow())[0] if data.enabled else None
        )
        self._session.commit()
        return self._out(schedule)

    def enqueue_due(self, *, now: datetime | None = None, limit: int = 50) -> list[TestRun]:
        """Создаёт запуски для расписаний, которым пора (вызывает runner).

        Пропущенные срабатывания (runner не работал) не накапливаются: создаётся
        один запуск, а следующее время считается от «сейчас».
        """
        now = now or utcnow()
        created: list[TestRun] = []
        for schedule in self._schedules.due_for_update(now, limit=limit):
            schedule.next_run_at = next_runs(schedule.cron, schedule.timezone, now)[0]
            run = self._make_run(schedule, now)
            if run is not None:
                created.append(run)
        self._session.commit()
        return created

    def _make_run(self, schedule: Schedule, now: datetime) -> TestRun | None:
        """Запуск по расписанию, если сайт можно тестировать прямо сейчас."""
        site = self._sites.lock(schedule.site_id)
        if site is None or not site.is_verified or schedule.scenario_id is None:
            logger.info(
                "Schedule of site %s skipped: site or scenario unavailable", schedule.site_id
            )
            return None
        if self._runs.has_active(site.id):
            logger.info("Schedule of site %s skipped: a run is already active", site.id)
            return None
        cooldown = timedelta(seconds=self._settings.site_cooldown_sec)
        last = self._runs.last_finished_at(site.id)
        if last is not None and now - last < cooldown:
            logger.info("Schedule of site %s skipped: cooldown", site.id)
            return None
        schedule.last_run_at = now
        try:
            with self._session.begin_nested():  # точка сохранения: ошибка не губит остальное
                return self._runs.add(
                    TestRun(
                        site_id=site.id,
                        scenario_id=schedule.scenario_id,
                        source=RunSource.SCHEDULE.value,
                        queued_at=now,
                    )
                )
        except IntegrityError:  # запуск сайта уже поставили параллельно
            return None

    def _out(self, schedule: Schedule) -> ScheduleOut:
        preview = (
            next_runs(schedule.cron, schedule.timezone, utcnow(), PREVIEW_RUNS)
            if schedule.enabled
            else []
        )
        return ScheduleOut(
            scenario_id=schedule.scenario_id,
            cron=schedule.cron,
            timezone=schedule.timezone,
            enabled=schedule.enabled,
            next_run_at=schedule.next_run_at,
            last_run_at=schedule.last_run_at,
            upcoming=preview,
        )

    def _site(self, owner: User, site_id: uuid.UUID) -> Site:
        site = self._sites.get_owned(site_id, owner.id)
        if site is None:
            raise NotFoundError()
        return site
