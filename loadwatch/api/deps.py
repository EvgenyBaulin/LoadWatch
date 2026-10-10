"""FastAPI dependencies shared by routers."""

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

_bearer = HTTPBearer(auto_error=False, description="Access token from /auth/login")


def get_settings(request: Request) -> Settings:
    """Return the settings the application was created with."""
    settings: Settings = request.app.state.settings
    return settings


def get_session(request: Request) -> Iterator[Session]:
    """One database session per request."""
    factory: sessionmaker[Session] = request.app.state.session_factory
    with factory() as session:
        yield session


SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[Session, Depends(get_session)]


def get_auth_service(session: SessionDep, settings: SettingsDep) -> AuthService:
    """Auth service bound to the request session."""
    return AuthService(session, settings)


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


def get_current_user(
    auth: AuthServiceDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    """Return the authenticated user or raise 401 ``not_authenticated``."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise NotAuthenticatedError()
    return auth.authenticate(credentials.credentials)


CurrentUser = Annotated[User, Depends(get_current_user)]
