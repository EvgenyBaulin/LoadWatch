"""Модель сайта, который пользователь тестирует."""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from loadwatch.db import Base, TimestampMixin, UtcDateTime


class VerificationStatus(StrEnum):
    """Состояние подтверждения владения."""

    PENDING = "pending"  # добавлен, ещё не подтверждён
    VERIFIED = "verified"
    UNVERIFIED = "unverified"  # был подтверждён, но перепроверка не прошла


class VerificationMethod(StrEnum):
    """Способ подтверждения владения."""

    DNS = "dns"
    HTTP = "http"


def new_verification_token() -> str:
    """Генерирует токен верификации (в URL и DNS безопасные символы)."""
    return secrets.token_hex(16)


class Site(TimestampMixin, Base):
    """Сайт пользователя; тестировать можно только подтверждённый."""

    __tablename__ = "sites"
    __table_args__ = (
        UniqueConstraint("owner_id", "url", name="uq_sites_owner_id_url"),
        CheckConstraint(
            "verification_status IN ('pending', 'verified', 'unverified')",
            name="verification_status",
        ),
        CheckConstraint(
            "verification_method IS NULL OR verification_method IN ('dns', 'http')",
            name="verification_method",
        ),
        # Для выборки сайтов, которым пора на перепроверку.
        Index("ix_sites_status_last_verified_at", "verification_status", "last_verified_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # Нормализованный origin: схема, хост в нижнем регистре, порт только нестандартный.
    url: Mapped[str] = mapped_column(String(300), nullable=False)
    verification_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=VerificationStatus.PENDING.value
    )
    verification_method: Mapped[str | None] = mapped_column(String(8))
    # Токен публичный по смыслу (лежит в DNS или в файле на сайте), поэтому не хэшируется.
    verification_token: Mapped[str] = mapped_column(
        String(64), nullable=False, default=new_verification_token
    )
    verified_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    last_verified_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    # Время последней попытки перепроверки (успешной или нет), чтобы не долбить сайт.
    verification_checked_at: Mapped[datetime | None] = mapped_column(UtcDateTime)

    @property
    def is_verified(self) -> bool:
        """Можно ли запускать тесты."""
        return self.verification_status == VerificationStatus.VERIFIED.value

    def __repr__(self) -> str:
        return f"Site(id={self.id!s}, url={self.url!r}, status={self.verification_status!r})"
