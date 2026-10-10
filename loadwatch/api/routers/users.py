"""``/users/me``: настройки текущего пользователя."""

from __future__ import annotations

from fastapi import APIRouter

from loadwatch.api.deps import AuthServiceDep, CurrentUser
from loadwatch.schemas.user import PreferencesUpdate, UserOut

router = APIRouter(prefix="/users/me", tags=["users"])


@router.put("/preferences")
def update_preferences(data: PreferencesUpdate, user: CurrentUser, auth: AuthServiceDep) -> UserOut:
    """Сменить язык интерфейса и/или часовой пояс."""
    return UserOut.model_validate(auth.update_preferences(user, data))
