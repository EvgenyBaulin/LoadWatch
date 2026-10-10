"""Жизненный цикл алертов и защита от спама.

Правила (числа — из конфига):

* алерт открывается после ``ALERT_CONSECUTIVE_RUNS`` плохих запусков подряд;
* пока он открыт, повторное уведомление — не чаще раза в ``ALERT_REPEAT_MINUTES``;
* после ``RECOVERY_RUNS`` хороших запусков подряд алерт закрывается и уходит
  уведомление о восстановлении.

Состояние «сколько плохих подряд» не хранится отдельно, а вычисляется по истории
запусков сайта: так оно не может разойтись с данными.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Sequence
from datetime import timedelta
from enum import Enum
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from loadwatch.config import Settings
from loadwatch.db import utcnow
from loadwatch.models import Alert, AlertType, RunMetrics, RunStatus, Scenario, Site, TestRun, User
from loadwatch.notifications.builders import alert_message, recovery_message
from loadwatch.services.channel_service import ChannelService

logger = logging.getLogger(__name__)


class Verdict(Enum):
    """Оценка запуска по одному типу проблем."""

    BAD = "bad"
    GOOD = "good"
    UNKNOWN = "unknown"  # запуск ничего не говорит об этой проблеме


def regression_verdict(run: TestRun) -> Verdict:
    """Регрессия: оценивается только у завершённых запусков, где было с чем сравнить."""
    if run.status != RunStatus.FINISHED.value or run.regression is None:
        return Verdict.UNKNOWN
    return Verdict.BAD if run.regression.get("detected") else Verdict.GOOD


def slo_verdict(run: TestRun) -> Verdict:
    """SLO: только у завершённых запусков с заданным SLO."""
    if run.status != RunStatus.FINISHED.value or run.slo_met is None:
        return Verdict.UNKNOWN
    return Verdict.GOOD if run.slo_met else Verdict.BAD


def failure_verdict(run: TestRun) -> Verdict:
    """Сбой: запуск упал (не отменён)."""
    if run.status == RunStatus.FAILED.value:
        return Verdict.BAD
    return Verdict.GOOD if run.status == RunStatus.FINISHED.value else Verdict.UNKNOWN


VERDICTS: dict[AlertType, Callable[[TestRun], Verdict]] = {
    AlertType.REGRESSION: regression_verdict,
    AlertType.SLO_BREACH: slo_verdict,
    AlertType.RUN_FAILED: failure_verdict,
}


class AlertManager:
    """Открывает, повторяет и закрывает алерты; шлёт уведомления владельцу сайта."""

    def __init__(self, session: Session, settings: Settings, channels: ChannelService) -> None:
        self._session = session
        self._settings = settings
        self._channels = channels

    def process_run(self, run_id: uuid.UUID) -> list[str]:
        """Учитывает завершившийся запуск; возвращает отправленные события (для логов и тестов)."""
        run = self._session.get(TestRun, run_id)
        if run is None or run.status not in (RunStatus.FINISHED.value, RunStatus.FAILED.value):
            return []
        site = self._session.get(Site, run.site_id)
        if site is None:
            return []
        history = self._history(site.id)
        sent: list[str] = []
        for alert_type, verdict_of in VERDICTS.items():
            verdict = verdict_of(run)
            if verdict is Verdict.UNKNOWN:
                continue
            known = [v for v in (verdict_of(item) for item in history) if v is not Verdict.UNKNOWN]
            sent += self._apply(site, alert_type, verdict, known, run)
        self._session.commit()
        return sent

    def site_lost(self, site_id: uuid.UUID) -> list[str]:
        """Перепроверка владения не прошла — алерт сразу, без накопления."""
        site = self._session.get(Site, site_id)
        if site is None or self._open(site.id, AlertType.SITE_UNVERIFIED) is not None:
            return []
        alert = self._open_alert(site, AlertType.SITE_UNVERIFIED, None, {})
        if alert is None:
            return []
        self._notify(site, alert, repeat=False)
        self._session.commit()
        return [AlertType.SITE_UNVERIFIED.value]

    def site_reverified(self, site_id: uuid.UUID) -> list[str]:
        """Владение снова подтверждено — закрываем алерт и сообщаем о восстановлении."""
        site = self._session.get(Site, site_id)
        alert = self._open(site_id, AlertType.SITE_UNVERIFIED) if site else None
        if site is None or alert is None:
            return []
        self._resolve(site, alert, runs=0)
        self._session.commit()
        return ["recovery"]

    def _apply(
        self,
        site: Site,
        alert_type: AlertType,
        verdict: Verdict,
        known: Sequence[Verdict],
        run: TestRun,
    ) -> list[str]:
        settings = self._settings
        alert = self._open(site.id, alert_type)
        if alert is None:
            needed = settings.alert_consecutive_runs
            if (
                verdict is Verdict.BAD
                and len(known) >= needed
                and all(v is Verdict.BAD for v in known[:needed])
            ):
                details = self._details(alert_type, run, consecutive=needed)
                alert = self._open_alert(site, alert_type, run, details)
                if alert is not None:
                    self._notify(site, alert, repeat=False)
                    return [alert_type.value]
            return []
        if verdict is Verdict.BAD:
            alert.run_id = run.id
            alert.details = self._details(alert_type, run, consecutive=_streak(known))
            repeat_after = timedelta(minutes=settings.alert_repeat_minutes)
            if alert.last_notified_at is None or utcnow() - alert.last_notified_at >= repeat_after:
                self._notify(site, alert, repeat=True)
                return [f"{alert_type.value}:repeat"]
            return []  # антиспам: уведомляли недавно
        needed = settings.recovery_runs
        if len(known) >= needed and all(v is Verdict.GOOD for v in known[:needed]):
            self._resolve(site, alert, runs=needed)
            return ["recovery"]
        return []

    def _open_alert(
        self, site: Site, alert_type: AlertType, run: TestRun | None, details: dict[str, Any]
    ) -> Alert | None:
        alert = Alert(
            site_id=site.id,
            type=alert_type.value,
            status="open",
            run_id=run.id if run else None,
            details=details,
            opened_at=utcnow(),
        )
        try:
            with self._session.begin_nested():
                self._session.add(alert)
        except IntegrityError:  # открытый алерт этого типа уже создал другой процесс
            return None
        logger.info("Alert %s opened for site %s", alert_type.value, site.id)
        return alert

    def _resolve(self, site: Site, alert: Alert, *, runs: int) -> None:
        alert.status = "resolved"
        alert.resolved_at = utcnow()
        owner = self._session.get(User, site.owner_id)
        if owner is not None:
            message = recovery_message(
                owner.locale, alert.type, site_name=site.name, runs=runs, link=self._link(site)
            )
            self._channels.notify(owner.id, "recovery", message)
        logger.info("Alert %s resolved for site %s", alert.type, site.id)

    def _notify(self, site: Site, alert: Alert, *, repeat: bool) -> None:
        alert.last_notified_at = utcnow()
        owner = self._session.get(User, site.owner_id)
        if owner is None:
            return
        message = alert_message(
            owner.locale,
            alert.type,
            site_name=site.name,
            details=alert.details or {},
            link=self._link(site),
            repeat=repeat,
        )
        self._channels.notify(owner.id, alert.type, message)

    def _details(self, alert_type: AlertType, run: TestRun, *, consecutive: int) -> dict[str, Any]:
        details: dict[str, Any] = {"run_id": str(run.id), "consecutive": consecutive}
        if alert_type is AlertType.REGRESSION:
            details["regression"] = run.regression
        elif alert_type is AlertType.RUN_FAILED:
            details["error"] = run.error
        elif alert_type is AlertType.SLO_BREACH:
            metrics = self._session.get(RunMetrics, run.id)
            scenario = self._session.get(Scenario, run.scenario_id) if run.scenario_id else None
            details["slo"] = {
                "p95": metrics.latency_p95_ms if metrics else None,
                "error_rate": metrics.error_rate if metrics else None,
                "p95_target": scenario.slo_p95_ms if scenario else None,
                "error_rate_target": scenario.slo_max_error_rate if scenario else None,
            }
        return details

    def _history(self, site_id: uuid.UUID) -> Sequence[TestRun]:
        # Запас на запуски, которые ничего не говорят о проблеме (UNKNOWN), — их отсеивают позже.
        depth = max(self._settings.alert_consecutive_runs, self._settings.recovery_runs) * 10 + 20
        return self._session.scalars(
            select(TestRun)
            .where(
                TestRun.site_id == site_id,
                TestRun.status.in_((RunStatus.FINISHED.value, RunStatus.FAILED.value)),
            )
            .order_by(TestRun.finished_at.desc().nulls_last(), TestRun.queued_at.desc())
            .limit(depth)
        ).all()

    def _open(self, site_id: uuid.UUID, alert_type: AlertType) -> Alert | None:
        return self._session.scalar(
            select(Alert)
            .where(Alert.site_id == site_id, Alert.type == alert_type.value, Alert.status == "open")
            .with_for_update()
        )

    def _link(self, site: Site) -> str:
        return f"{self._settings.public_base_url.rstrip('/')}/sites/{site.id}"


def _streak(known: Sequence[Verdict]) -> int:
    count = 0
    for verdict in known:
        if verdict is not Verdict.BAD:
            break
        count += 1
    return count
