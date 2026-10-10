"""Триггер из CI, метки на графиках и список алертов сайта."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from loadwatch.config import Settings
from loadwatch.db import utcnow
from loadwatch.errors import (
    InvalidPeriodError,
    InvalidTriggerTokenError,
    NotFoundError,
    ScenarioRequiredError,
)
from loadwatch.models import Alert, Annotation, RunSource, Scenario, Schedule, Site, User
from loadwatch.repositories.site_repository import SiteRepository
from loadwatch.schemas.hooks import (
    AlertOut,
    AnnotationIn,
    AnnotationOut,
    HookRunIn,
    HookRunOut,
    TriggerTokenOut,
)
from loadwatch.services.run_queue_service import RunQueueService, run_out

TOKEN_PREFIX = "lwt_"  # noqa: S105  # префикс, а не секрет


def token_hash(token: str) -> str:
    """SHA-256 токена: в базе хранится только он."""
    return hashlib.sha256(token.encode()).hexdigest()


class HookService:
    """Всё, что связано с деплоями: токен, запуск из CI, метки; плюс алерты сайта."""

    def __init__(self, session: Session, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._sites = SiteRepository(session)

    def issue_token(self, owner: User, site_id: uuid.UUID) -> TriggerTokenOut:
        """Выпускает (или перевыпускает) токен; старый сразу перестаёт работать."""
        site = self._site(owner, site_id)
        token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        site.trigger_token_hash = token_hash(token)
        self._session.commit()
        url = f"{self._settings.public_base_url.rstrip('/')}/api/v1/hooks/sites/{site.id}/run"
        example = (
            f'curl -X POST {url} -H "Authorization: Bearer $LOADWATCH_TOKEN" '
            '-H "Content-Type: application/json" -d \'{"label": "v1.2.3"}\''
        )
        return TriggerTokenOut(token=token, example=example)

    def authenticate(self, site_id: uuid.UUID, token: str | None) -> Site:
        """Сайт, если токен триггера верный; иначе 401."""
        site = self._session.get(Site, site_id)
        if (
            site is None
            or site.trigger_token_hash is None
            or token is None
            or not hmac.compare_digest(token_hash(token), site.trigger_token_hash)
        ):
            # Нет сайта и неверный токен неразличимы: перебором не узнать, какие сайты есть.
            raise InvalidTriggerTokenError()
        return site

    def trigger(self, site: Site, data: HookRunIn) -> HookRunOut:
        """Запуск из CI: метка деплоя и запуск в очередь (те же проверки, что у ручного)."""
        scenario = self._scenario_for(site, data.scenario_id)
        annotation = None
        if data.label:
            annotation = Annotation(site_id=site.id, kind="deploy", label=data.label, at=utcnow())
            self._session.add(annotation)
            # Деплой состоялся, даже если тест сейчас поставить нельзя (пауза, идёт другой).
            self._session.commit()
        queue = RunQueueService(self._session, self._settings)
        run = queue.enqueue(site, scenario, source=RunSource.HOOK, label=data.label)
        if annotation is not None:
            annotation.run_id = run.id
            self._session.commit()
        return HookRunOut(
            run=run_out(run, None),
            annotation=_annotation_out(annotation) if annotation else None,
        )

    def annotations(
        self, owner: User, site_id: uuid.UUID, *, since: datetime | None, until: datetime | None
    ) -> list[AnnotationOut]:
        """Метки за период (по умолчанию — последние 30 дней)."""
        site = self._site(owner, site_id)
        until = until or utcnow()
        since = since or until - timedelta(days=30)
        if since >= until:
            raise InvalidPeriodError()
        rows = self._session.scalars(
            select(Annotation)
            .where(Annotation.site_id == site.id, Annotation.at >= since, Annotation.at <= until)
            .order_by(Annotation.at)
            .limit(1000)
        ).all()
        return [_annotation_out(row) for row in rows]

    def add_annotation(self, owner: User, site_id: uuid.UUID, data: AnnotationIn) -> AnnotationOut:
        """Комментарий на графиках."""
        site = self._site(owner, site_id)
        annotation = Annotation(
            site_id=site.id, kind="comment", label=data.label, at=data.at or utcnow()
        )
        self._session.add(annotation)
        self._session.commit()
        return _annotation_out(annotation)

    def delete_annotation(self, owner: User, site_id: uuid.UUID, annotation_id: uuid.UUID) -> None:
        """Удаляет метку сайта."""
        site = self._site(owner, site_id)
        annotation = self._session.get(Annotation, annotation_id)
        if annotation is None or annotation.site_id != site.id:
            raise NotFoundError()
        self._session.delete(annotation)
        self._session.commit()

    def alerts(self, owner: User, site_id: uuid.UUID, *, status: str | None) -> list[AlertOut]:
        """Алерты сайта, новые сверху (не больше 200)."""
        site = self._site(owner, site_id)
        query = select(Alert).where(Alert.site_id == site.id)
        if status:
            query = query.where(Alert.status == status)
        rows: Sequence[Alert] = self._session.scalars(
            query.order_by(Alert.opened_at.desc()).limit(200)
        ).all()
        return [AlertOut.model_validate(row, from_attributes=True) for row in rows]

    def _scenario_for(self, site: Site, scenario_id: uuid.UUID | None) -> Scenario:
        if scenario_id is not None:
            scenario = self._session.get(Scenario, scenario_id)
            if scenario is None or scenario.site_id != site.id:
                raise NotFoundError("Scenario not found")
            return scenario
        schedule = self._session.scalar(select(Schedule).where(Schedule.site_id == site.id))
        if schedule is not None and schedule.scenario_id is not None:
            found = self._session.get(Scenario, schedule.scenario_id)
            if found is not None:
                return found
        scenarios = self._session.scalars(
            select(Scenario).where(Scenario.site_id == site.id).limit(2)
        ).all()
        if len(scenarios) != 1:
            raise ScenarioRequiredError()
        return scenarios[0]

    def _site(self, owner: User, site_id: uuid.UUID) -> Site:
        site = self._sites.get_owned(site_id, owner.id)
        if site is None:
            raise NotFoundError()
        return site


def _annotation_out(annotation: Annotation) -> AnnotationOut:
    return AnnotationOut.model_validate(annotation, from_attributes=True)
