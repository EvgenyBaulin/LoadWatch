"""Алерты сайта и каналы уведомлений пользователя."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, String, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from loadwatch.db import Base, TimestampMixin, UtcDateTime, utcnow
from loadwatch.models.scenario import JsonType


class AlertType(StrEnum):
    """Почему открыт алерт (``recovery`` — событие закрытия, а не тип алерта)."""

    REGRESSION = "regression"
    SLO_BREACH = "slo_breach"
    RUN_FAILED = "run_failed"
    SITE_UNVERIFIED = "site_unverified"


class NotificationEvent(StrEnum):
    """События, на которые канал может быть подписан."""

    REGRESSION = "regression"
    SLO_BREACH = "slo_breach"
    RUN_FAILED = "run_failed"
    SITE_UNVERIFIED = "site_unverified"
    RECOVERY = "recovery"
    DIGEST = "digest"


class ChannelType(StrEnum):
    """Тип канала уведомлений."""

    TELEGRAM = "telegram"
    EMAIL = "email"
    SLACK = "slack"
    DISCORD = "discord"
    WEBHOOK = "webhook"


class Alert(TimestampMixin, Base):
    """Открытый или закрытый алерт; по одному открытому на сайт и тип."""

    __tablename__ = "alerts"
    __table_args__ = (
        CheckConstraint("status IN ('open', 'resolved')", name="status"),
        CheckConstraint(
            "type IN ('regression', 'slo_breach', 'run_failed', 'site_unverified')", name="type"
        ),
        Index(
            "uq_alerts_one_open_per_site_type",
            "site_id",
            "type",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
        Index("ix_alerts_site_id_opened_at", "site_id", "opened_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    site_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("sites.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    # Последний запуск, подтвердивший проблему, и её подробности (для текста уведомления).
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("test_runs.id", ondelete="SET NULL")
    )
    details: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    opened_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    last_notified_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    resolved_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class NotificationChannel(TimestampMixin, Base):
    """Канал уведомлений пользователя (их может быть несколько)."""

    __tablename__ = "notification_channels"
    __table_args__ = (
        CheckConstraint(
            "type IN ('telegram', 'email', 'slack', 'discord', 'webhook')", name="type"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # Открытые параметры: chat id Telegram, адрес почты.
    config: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, default=dict)
    # Зашифрованные параметры (Fernet): URL вебхука с токеном, секрет подписи.
    secret_config: Mapped[str | None] = mapped_column(Text)
    events: Mapped[list[str]] = mapped_column(JsonType, nullable=False, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Annotation(TimestampMixin, Base):
    """Метка на графиках: деплой (из триггера CI) или комментарий."""

    __tablename__ = "annotations"
    __table_args__ = (
        CheckConstraint("kind IN ('deploy', 'comment')", name="kind"),
        Index("ix_annotations_site_id_at", "site_id", "at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    site_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("sites.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="comment")
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("test_runs.id", ondelete="SET NULL")
    )
