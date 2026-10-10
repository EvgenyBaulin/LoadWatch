"""Работа с таблицей ``users``."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from loadwatch.models import User


class UserRepository:
    """Доступ к пользователям; транзакцией управляет сервис, здесь commit нет."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, user_id: uuid.UUID) -> User | None:
        """Пользователь по id или ``None``."""
        return self._session.get(User, user_id)

    def get_by_email(self, email: str) -> User | None:
        """Пользователь по нормализованному e-mail или ``None``."""
        return self._session.scalar(select(User).where(User.email == email))

    def add(self, user: User) -> User:
        """Добавляет пользователя и сразу шлёт INSERT, чтобы поймать нарушение уникальности."""
        self._session.add(user)
        self._session.flush()
        return user
