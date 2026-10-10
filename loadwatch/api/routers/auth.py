"""``/auth``: registration, login and the current user."""

from __future__ import annotations

from fastapi import APIRouter, Request, status

from loadwatch.api.deps import AuthServiceDep, CurrentUser
from loadwatch.api.rate_limit import client_key
from loadwatch.schemas.user import AuthResponse, LoginRequest, RegisterRequest, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(data: RegisterRequest, request: Request, auth: AuthServiceDep) -> AuthResponse:
    """Create an account (subject to ``REGISTRATION_MODE``) and return a token."""
    request.app.state.auth_rate_limiter.hit(f"register:{client_key(request)}")
    return auth.register(data)


@router.post("/login")
def login(data: LoginRequest, request: Request, auth: AuthServiceDep) -> AuthResponse:
    """Exchange e-mail and password for an access token."""
    request.app.state.auth_rate_limiter.hit(f"login:{client_key(request)}")
    return auth.login(data.email, data.password)


@router.get("/me")
def me(user: CurrentUser) -> UserOut:
    """Return the authenticated user."""
    return UserOut.model_validate(user)
