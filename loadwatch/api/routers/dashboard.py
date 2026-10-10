"""Дашборд сайта: ряды метрик для графиков и сводка для stat-панелей."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query

from loadwatch.api.deps import CurrentUser, DashboardServiceDep
from loadwatch.schemas.dashboard import SiteMetricsOut, SiteSummaryOut

router = APIRouter(tags=["dashboard"])


@router.get("/sites/{site_id}/metrics", response_model_by_alias=True)
def site_metrics(
    site_id: uuid.UUID,
    user: CurrentUser,
    service: DashboardServiceDep,
    since: Annotated[datetime | None, Query(alias="from")] = None,
    until: Annotated[datetime | None, Query(alias="to")] = None,
    scenario_id: uuid.UUID | None = None,
) -> SiteMetricsOut:
    """Ряды метрик завершённых запусков за период (по умолчанию 7 дней).

    В каждой точке — p50/p95/p99, RPS, ошибки, Apdex, потолок, коды ответов, тайминги.
    """
    return service.metrics(user, site_id, since=since, until=until, scenario_id=scenario_id)


@router.get("/sites/{site_id}/summary")
def site_summary(
    site_id: uuid.UUID,
    user: CurrentUser,
    service: DashboardServiceDep,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> SiteSummaryOut:
    """Последний запуск, базовая линия, регрессия, потолок, доля запусков в SLO за ``days``."""
    return service.summary(user, site_id, days=days)
