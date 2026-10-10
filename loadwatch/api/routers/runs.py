"""Запуски тестов: постановка в очередь, история, отмена; расписание сайта."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from loadwatch.api.deps import CurrentUser, RunQueueServiceDep, ScheduleServiceDep
from loadwatch.repositories.run_repository import RunFilters
from loadwatch.schemas.common import Page, PageParams
from loadwatch.schemas.run import RunCreate, RunDetailOut, RunOut, RunSourceName, RunStatusName
from loadwatch.schemas.schedule import ScheduleIn, ScheduleOut

router = APIRouter(tags=["runs"])


@router.post("/sites/{site_id}/runs", status_code=status.HTTP_201_CREATED)
def create_run(
    site_id: uuid.UUID, data: RunCreate, user: CurrentUser, service: RunQueueServiceDep
) -> RunOut:
    """Запустить тест сейчас: запуск встаёт в очередь, его выполнит runner."""
    return service.create_manual(user, site_id, data)


@router.get("/sites/{site_id}/runs")
def list_runs(
    site_id: uuid.UUID,
    user: CurrentUser,
    service: RunQueueServiceDep,
    page: Annotated[PageParams, Depends()],
    status_filter: Annotated[RunStatusName | None, Query(alias="status")] = None,
    scenario_id: uuid.UUID | None = None,
    source: RunSourceName | None = None,
) -> Page[RunOut]:
    """История запусков сайта (новые сверху) с фильтрами по статусу, сценарию и источнику."""
    filters = RunFilters(status=status_filter, scenario_id=scenario_id, source=source)
    return service.list_page(user, site_id, filters, limit=page.limit, offset=page.offset)


@router.get("/runs/{run_id}")
def get_run(run_id: uuid.UUID, user: CurrentUser, service: RunQueueServiceDep) -> RunDetailOut:
    """Запуск со сводкой метрик и посекундным рядом."""
    return service.get(user, run_id)


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: uuid.UUID, user: CurrentUser, service: RunQueueServiceDep) -> RunOut:
    """Отменить запуск в очереди или остановить идущий."""
    return service.cancel(user, run_id)


@router.get("/sites/{site_id}/schedule")
def get_schedule(
    site_id: uuid.UUID, user: CurrentUser, service: ScheduleServiceDep
) -> ScheduleOut | None:
    """Расписание сайта; ``null``, если его нет."""
    return service.get(user, site_id)


@router.put("/sites/{site_id}/schedule")
def put_schedule(
    site_id: uuid.UUID, data: ScheduleIn, user: CurrentUser, service: ScheduleServiceDep
) -> ScheduleOut:
    """Задать расписание: cron в выбранном часовом поясе (по умолчанию UTC)."""
    return service.put(user, site_id, data)
