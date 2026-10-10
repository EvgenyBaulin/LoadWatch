"""Persistence for :class:`~loadwatch.models.User`."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from loadwatch.models import User


class UserRepository:
    """Database access for users; never commits, the service owns the transaction."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, user_id: uuid.UUID) -> User | None:
        """Return the user with ``user_id`` or ``None``."""
        return self._session.get(User, user_id)

    def get_by_email(self, email: str) -> User | None:
        """Return the user with a (normalised) e-mail or ``None``."""
        return self._session.scalar(select(User).where(User.email == email))

    def add(self, user: User) -> User:
        """Stage a new user and flush to surface constraint violations early."""
        self._session.add(user)
        self._session.flush()
        return user
