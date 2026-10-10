"""``/users/me``: preferences of the current user."""

from __future__ import annotations

from fastapi import APIRouter

from loadwatch.api.deps import AuthServiceDep, CurrentUser
from loadwatch.schemas.user import PreferencesUpdate, UserOut

router = APIRouter(prefix="/users/me", tags=["users"])


@router.put("/preferences")
def update_preferences(data: PreferencesUpdate, user: CurrentUser, auth: AuthServiceDep) -> UserOut:
    """Change interface language and/or time zone."""
    return UserOut.model_validate(auth.update_preferences(user, data))
