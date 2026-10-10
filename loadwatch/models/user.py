"""Модель пользователя."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, SmallInteger, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from loadwatch.db import Base, TimestampMixin, UtcDateTime

SUPPORTED_LOCALES = ("ru", "en")


class User(TimestampMixin, Base):
    """Аккаунт; владеет сайтами, сценариями и каналами уведомлений."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("locale IN ('ru', 'en')", name="locale"),
        CheckConstraint("digest_weekday BETWEEN 0 AND 6", name="digest_weekday"),
        CheckConstraint("digest_hour BETWEEN 0 AND 23", name="digest_hour"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # Хранится в нижнем регистре, поэтому уникальность не зависит от регистра.
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    locale: Mapped[str] = mapped_column(String(2), nullable=False, default="en")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    # Еженедельная сводка: день недели (0 — понедельник) и час по часовому поясу пользователя.
    digest_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    digest_weekday: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    digest_hour: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=9)
    last_digest_at: Mapped[datetime | None] = mapped_column(UtcDateTime)

    def __repr__(self) -> str:
        return f"User(id={self.id!s}, email={self.email!r})"
