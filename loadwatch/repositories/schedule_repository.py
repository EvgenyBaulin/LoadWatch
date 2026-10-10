"""Работа с таблицей ``schedules``."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from loadwatch.models import Schedule


class ScheduleRepository:
    """Доступ к расписаниям; транзакцией управляет сервис."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_for_site(self, site_id: uuid.UUID) -> Schedule | None:
        """Расписание сайта."""
        return self._session.scalar(select(Schedule).where(Schedule.site_id == site_id))

    def add(self, schedule: Schedule) -> Schedule:
        """Добавляет расписание."""
        self._session.add(schedule)
        self._session.flush()
        return schedule

    def due_for_update(self, now: datetime, *, limit: int) -> Sequence[Schedule]:
        """Включённые расписания, которым пора; строки блокируются.

        ``SKIP LOCKED``: если два runner работают одновременно, каждое расписание
        достанется только одному из них.
        """
        return self._session.scalars(
            select(Schedule)
            .where(Schedule.enabled.is_(True), Schedule.next_run_at <= now)
            .order_by(Schedule.next_run_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
