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
