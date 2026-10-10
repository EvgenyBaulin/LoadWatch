"""Работа с таблицами ``test_runs`` и ``run_metrics``."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from loadwatch.models import RunMetrics, TestRun


class RunRepository:
    """Доступ к запускам; транзакцией управляет сервис."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, run_id: uuid.UUID) -> TestRun | None:
        """Запуск по id."""
        return self._session.get(TestRun, run_id)

    def add(self, run: TestRun) -> TestRun:
        """Добавляет запуск."""
        self._session.add(run)
        self._session.flush()
        return run

    def save_metrics(self, metrics: RunMetrics) -> None:
        """Сохраняет (или заменяет) метрики запуска."""
        self._session.merge(metrics)

    def get_metrics(self, run_id: uuid.UUID) -> RunMetrics | None:
        """Метрики запуска."""
        return self._session.get(RunMetrics, run_id)
