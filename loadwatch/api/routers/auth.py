"""``/auth``: регистрация, вход и текущий пользователь."""

from __future__ import annotations

from fastapi import APIRouter, Request, status

from loadwatch.api.deps import AuthServiceDep, CurrentUser
from loadwatch.api.rate_limit import client_key
from loadwatch.schemas.user import AuthResponse, LoginRequest, RegisterRequest, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(data: RegisterRequest, request: Request, auth: AuthServiceDep) -> AuthResponse:
    """Создать аккаунт (с учётом ``REGISTRATION_MODE``) и получить токен."""
    request.app.state.auth_rate_limiter.hit(f"register:{client_key(request)}")
    return auth.register(data)


@router.post("/login")
def login(data: LoginRequest, request: Request, auth: AuthServiceDep) -> AuthResponse:
    """Получить токен по e-mail и паролю."""
    request.app.state.auth_rate_limiter.hit(f"login:{client_key(request)}")
    return auth.login(data.email, data.password)


@router.get("/me")
def me(user: CurrentUser) -> UserOut:
    """Текущий пользователь."""
    return UserOut.model_validate(user)
