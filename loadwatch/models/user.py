"""Модель пользователя."""

from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from loadwatch.db import Base, TimestampMixin

SUPPORTED_LOCALES = ("ru", "en")


class User(TimestampMixin, Base):
    """Аккаунт; владеет сайтами, сценариями и каналами уведомлений."""

    __tablename__ = "users"
    __table_args__ = (CheckConstraint("locale IN ('ru', 'en')", name="locale"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # Хранится в нижнем регистре, поэтому уникальность не зависит от регистра.
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    locale: Mapped[str] = mapped_column(String(2), nullable=False, default="en")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")

    def __repr__(self) -> str:
        return f"User(id={self.id!s}, email={self.email!r})"
