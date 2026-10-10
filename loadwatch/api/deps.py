"""Общие зависимости FastAPI для роутеров."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session, sessionmaker

from loadwatch.config import Settings
from loadwatch.errors import NotAuthenticatedError
from loadwatch.models import User
from loadwatch.services.auth_service import AuthService
from loadwatch.services.dashboard_service import DashboardService
from loadwatch.services.run_queue_service import RunQueueService
from loadwatch.services.scenario_service import ScenarioService
from loadwatch.services.schedule_service import ScheduleService
from loadwatch.services.site_service import SiteService

_bearer = HTTPBearer(auto_error=False, description="Access token from /auth/login")


def get_settings(request: Request) -> Settings:
    """Настройки, с которыми создано приложение."""
    settings: Settings = request.app.state.settings
    return settings


def get_session(request: Request) -> Iterator[Session]:
    """Одна сессия БД на запрос."""
    factory: sessionmaker[Session] = request.app.state.session_factory
    with factory() as session:
        yield session


SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[Session, Depends(get_session)]


def get_auth_service(session: SessionDep, settings: SettingsDep) -> AuthService:
    """Сервис аутентификации на сессии запроса."""
    return AuthService(session, settings)


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


def get_current_user(
    auth: AuthServiceDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    """Текущий пользователь или 401 ``not_authenticated``."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise NotAuthenticatedError()
    return auth.authenticate(credentials.credentials)


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_site_service(request: Request, session: SessionDep, settings: SettingsDep) -> SiteService:
    """Сервис сайтов с верификаторами и резолвером приложения."""
    return SiteService(session, settings, request.app.state.verifiers, request.app.state.resolver)


SiteServiceDep = Annotated[SiteService, Depends(get_site_service)]


def get_scenario_service(
    request: Request, session: SessionDep, settings: SettingsDep
) -> ScenarioService:
    """Сервис сценариев с резолвером приложения."""
    return ScenarioService(session, settings, request.app.state.resolver)


ScenarioServiceDep = Annotated[ScenarioService, Depends(get_scenario_service)]


def get_run_queue_service(session: SessionDep, settings: SettingsDep) -> RunQueueService:
    """Сервис очереди запусков."""
    return RunQueueService(session, settings)


RunQueueServiceDep = Annotated[RunQueueService, Depends(get_run_queue_service)]


def get_schedule_service(session: SessionDep, settings: SettingsDep) -> ScheduleService:
    """Сервис расписаний."""
    return ScheduleService(session, settings)


ScheduleServiceDep = Annotated[ScheduleService, Depends(get_schedule_service)]


def get_dashboard_service(session: SessionDep, settings: SettingsDep) -> DashboardService:
    """Сервис данных дашборда."""
    return DashboardService(session, settings)


DashboardServiceDep = Annotated[DashboardService, Depends(get_dashboard_service)]
