"""Работа с таблицей ``sites``."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from loadwatch.models import Site, VerificationStatus


class SiteRepository:
    """Доступ к сайтам; транзакцией управляет сервис."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_owned(self, site_id: uuid.UUID, owner_id: uuid.UUID) -> Site | None:
        """Сайт по id, только если он принадлежит ``owner_id``."""
        return self._session.scalar(
            select(Site).where(Site.id == site_id, Site.owner_id == owner_id)
        )

    def get_by_url(self, owner_id: uuid.UUID, url: str) -> Site | None:
        """Сайт пользователя с таким нормализованным URL."""
        return self._session.scalar(select(Site).where(Site.owner_id == owner_id, Site.url == url))

    def list_owned(self, owner_id: uuid.UUID, *, limit: int, offset: int) -> Sequence[Site]:
        """Страница сайтов пользователя, новые сверху."""
        return self._session.scalars(
            select(Site)
            .where(Site.owner_id == owner_id)
            .order_by(Site.created_at.desc(), Site.id)
            .limit(limit)
            .offset(offset)
        ).all()

    def count_owned(self, owner_id: uuid.UUID) -> int:
        """Сколько всего сайтов у пользователя."""
        return (
            self._session.scalar(
                select(func.count()).select_from(Site).where(Site.owner_id == owner_id)
            )
            or 0
        )

    def due_for_reverification(
        self, *, verified_before: datetime, checked_before: datetime, limit: int
    ) -> Sequence[Site]:
        """Подтверждённые сайты, которым пора на перепроверку."""
        return self._session.scalars(
            select(Site)
            .where(
                Site.verification_status == VerificationStatus.VERIFIED.value,
                Site.last_verified_at <= verified_before,
                or_(
                    Site.verification_checked_at.is_(None),
                    Site.verification_checked_at <= checked_before,
                ),
            )
            .order_by(Site.last_verified_at)
            .limit(limit)
        ).all()

    def add(self, site: Site) -> Site:
        """Добавляет сайт и сразу отправляет INSERT, чтобы поймать нарушение уникальности."""
        self._session.add(site)
        self._session.flush()
        return site

    def delete(self, site: Site) -> None:
        """Удаляет сайт (связанные объекты удалятся каскадом)."""
        self._session.delete(site)
