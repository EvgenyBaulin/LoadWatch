"""Движок БД, фабрика сессий и базовый класс моделей."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Dialect, Engine, MetaData, TypeDecorator, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from loadwatch.config import Settings

# Стабильные имена ограничений делают миграции Alembic воспроизводимыми.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    """Текущее время в UTC (aware datetime)."""
    return datetime.now(UTC)


class UtcDateTime(TypeDecorator[datetime]):
    """``timestamptz``, который всегда возвращается в UTC.

    PostgreSQL отдаёт ``timestamptz`` в часовом поясе сессии, а он зависит от
    настроек сервера или пулера; нормализация здесь держит API в UTC.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        """Отклоняет наивные datetime, aware сохраняет в UTC."""
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime; use loadwatch.db.utcnow()")
        return value.astimezone(UTC)

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:
        """Переводит прочитанные значения в UTC."""
        return value.astimezone(UTC) if isinstance(value, datetime) else None


class Base(DeclarativeBase):
    """Базовый класс всех ORM-моделей."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    """Колонки ``created_at`` / ``updated_at`` в UTC."""

    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


def create_db_engine(settings: Settings) -> Engine:
    """Создаёт движок SQLAlchemy с пулом по профилю ресурсов."""
    return create_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_pool_size,
        pool_pre_ping=True,
    )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Создаёт фабрику сессий для ``engine``."""
    return sessionmaker(bind=engine, expire_on_commit=False)
