"""Работа с таблицами ``scenarios`` и ``scenario_secrets``."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from loadwatch.models import Scenario, ScenarioSecret, Site


class ScenarioRepository:
    """Доступ к сценариям; транзакцией управляет сервис."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_owned(self, scenario_id: uuid.UUID, owner_id: uuid.UUID) -> Scenario | None:
        """Сценарий, только если его сайт принадлежит ``owner_id``."""
        return self._session.scalar(
            select(Scenario)
            .join(Site, Site.id == Scenario.site_id)
            .where(Scenario.id == scenario_id, Site.owner_id == owner_id)
        )

    def get_by_name(self, site_id: uuid.UUID, name: str) -> Scenario | None:
        """Сценарий сайта с таким названием."""
        return self._session.scalar(
            select(Scenario).where(Scenario.site_id == site_id, Scenario.name == name)
        )

    def list_for_site(self, site_id: uuid.UUID, *, limit: int, offset: int) -> Sequence[Scenario]:
        """Сценарии сайта по названию."""
        return self._session.scalars(
            select(Scenario)
            .where(Scenario.site_id == site_id)
            .order_by(Scenario.name, Scenario.id)
            .limit(limit)
            .offset(offset)
        ).all()

    def count_for_site(self, site_id: uuid.UUID) -> int:
        """Сколько сценариев у сайта."""
        return (
            self._session.scalar(
                select(func.count()).select_from(Scenario).where(Scenario.site_id == site_id)
            )
            or 0
        )

    def add(self, scenario: Scenario) -> Scenario:
        """Добавляет сценарий."""
        self._session.add(scenario)
        self._session.flush()
        return scenario

    def delete(self, scenario: Scenario) -> None:
        """Удаляет сценарий (секреты — каскадом, запуски остаются)."""
        self._session.delete(scenario)

    def secrets(self, scenario_id: uuid.UUID) -> Sequence[ScenarioSecret]:
        """Зашифрованные секреты сценария по имени."""
        return self._session.scalars(
            select(ScenarioSecret)
            .where(ScenarioSecret.scenario_id == scenario_id)
            .order_by(ScenarioSecret.name)
        ).all()

    def add_secret(self, secret: ScenarioSecret) -> None:
        """Добавляет секрет."""
        self._session.add(secret)

    def delete_secret(self, secret: ScenarioSecret) -> None:
        """Удаляет секрет."""
        self._session.delete(secret)
