"""Еженедельная сводка: по каждому сайту — запуски, p95 к прошлой неделе, SLO, ошибки, потолок."""

from __future__ import annotations

import logging
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from loadwatch.config import Settings
from loadwatch.db import utcnow
from loadwatch.models import RunMetrics, RunStatus, Site, TestRun, User
from loadwatch.notifications.messages import Message, t
from loadwatch.services.channel_service import ChannelService

logger = logging.getLogger(__name__)

WEEK = timedelta(days=7)
# Не чаще раза в 6 дней: cron срабатывает каждый час, а час «понедельник 09:00» длится час.
MIN_INTERVAL = timedelta(days=6)


@dataclass(frozen=True, slots=True)
class SiteWeek:
    """Итоги недели по сайту."""

    site_name: str
    runs: int
    p95_median: float | None
    p95_change_pct: float | None
    slo_pct: float | None
    error_rate_pct: float | None
    ceiling_rps: float | None


def is_due(user: User, now: datetime) -> bool:
    """Наступило ли у пользователя выбранное время сводки (по его часовому поясу).

    Подходит выбранный день с выбранного часа до конца суток: если в нужный час
    отправка не удалась (сеть, SMTP), cron повторит её в следующие часы.
    """
    if not user.digest_enabled:
        return False
    if user.last_digest_at is not None and now - user.last_digest_at < MIN_INTERVAL:
        return False
    try:
        local = now.astimezone(ZoneInfo(user.timezone))
    except (ZoneInfoNotFoundError, ValueError):
        local = now
    return local.weekday() == user.digest_weekday and local.hour >= user.digest_hour


class DigestService:
    """Сборка и отправка сводок (``python -m loadwatch.digest``, cron раз в час)."""

    def __init__(self, session: Session, settings: Settings, channels: ChannelService) -> None:
        self._session = session
        self._settings = settings
        self._channels = channels

    def send_due(self, *, now: datetime | None = None) -> int:
        """Отправляет сводки всем, у кого наступило время; возвращает их число."""
        now = now or utcnow()
        sent = 0
        user_ids = self._session.scalars(select(User.id).where(User.digest_enabled.is_(True))).all()
        for user_id in user_ids:
            # Блокировка строки: два одновременных процесса не отправят сводку дважды.
            user = self._session.scalar(
                select(User)
                .where(User.id == user_id)
                .with_for_update(skip_locked=True)
                .execution_options(populate_existing=True)
            )
            if user is None or not is_due(user, now):
                self._session.rollback()
                continue
            deliveries = self._channels.notify(user.id, "digest", self.build(user, now=now))
            # Нет каналов — считаем отправленной (иначе пытались бы каждый час). Все каналы
            # упали — не отмечаем: cron повторит в следующий час того же дня.
            if not deliveries or any(delivery.ok for delivery in deliveries):
                user.last_digest_at = now
                sent += 1
            self._session.commit()
        logger.info("Weekly digests sent: %d", sent)
        return sent

    def build(self, user: User, *, now: datetime) -> Message:
        """Сводка пользователя на его языке."""
        sites = self._session.scalars(
            select(Site).where(Site.owner_id == user.id).order_by(Site.name)
        ).all()
        weeks = [self.site_week(site, now=now) for site in sites]
        lines = [self._line(user.locale, week) for week in weeks]
        return Message(
            event="digest",
            locale=user.locale,
            title=t(user.locale, "digest.title"),
            lines=lines,
            link=self._settings.public_base_url.rstrip("/") + "/sites",
            payload={"sites": [asdict(week) for week in weeks]},
        )

    def site_week(self, site: Site, *, now: datetime) -> SiteWeek:
        """Итоги последних 7 дней и сравнение медианы p95 с предыдущими 7 днями."""
        this_week = self._runs(site.id, now - WEEK, now)
        last_week = self._runs(site.id, now - 2 * WEEK, now - WEEK)
        p95_now = _median([m.latency_p95_ms for _, m in this_week])
        p95_before = _median([m.latency_p95_ms for _, m in last_week])
        change = (
            round((p95_now - p95_before) / p95_before * 100, 1)
            if p95_now is not None and p95_before
            else None
        )
        with_slo = [run.slo_met for run, _ in this_week if run.slo_met is not None]
        ceilings = [m.ceiling_rps for _, m in this_week if m.ceiling_rps is not None]
        errors = [m.error_rate for _, m in this_week]
        return SiteWeek(
            site_name=site.name,
            runs=len(this_week),
            p95_median=round(p95_now, 1) if p95_now is not None else None,
            p95_change_pct=change,
            slo_pct=round(sum(with_slo) / len(with_slo) * 100, 1) if with_slo else None,
            error_rate_pct=round(statistics.fmean(errors) * 100, 2) if errors else None,
            ceiling_rps=ceilings[0] if ceilings else None,
        )

    def _runs(
        self, site_id: object, since: datetime, until: datetime
    ) -> list[tuple[TestRun, RunMetrics]]:
        rows = self._session.execute(
            select(TestRun, RunMetrics)
            .join(RunMetrics, RunMetrics.run_id == TestRun.id)
            .where(
                TestRun.site_id == site_id,
                TestRun.status == RunStatus.FINISHED.value,
                TestRun.generator_saturated.is_(False),
                TestRun.started_at >= since,
                TestRun.started_at < until,
            )
            .order_by(TestRun.started_at.desc())
        ).all()
        return [(row[0], row[1]) for row in rows]

    @staticmethod
    def _line(locale: str, week: SiteWeek) -> str:
        if not week.runs:
            return t(locale, "digest.site_no_runs", site=week.site_name)
        na = t(locale, "digest.na")
        if week.p95_change_pct is None:
            change = na
        elif week.p95_change_pct == 0:
            change = t(locale, "digest.no_change")
        else:
            change = f"{week.p95_change_pct:+g}%"
        ceiling = (
            t(locale, "digest.ceiling", ceiling=f"{week.ceiling_rps:g}")
            if week.ceiling_rps is not None
            else ""
        )
        return t(
            locale,
            "digest.site",
            site=week.site_name,
            runs=week.runs,
            p95=f"{week.p95_median:g}" if week.p95_median is not None else na,
            p95_change=change,
            slo=f"{week.slo_pct:g}%" if week.slo_pct is not None else na,
            errors=f"{week.error_rate_pct:g}" if week.error_rate_pct is not None else na,
            ceiling=ceiling,
        )


def _median(values: list[float]) -> float | None:
    return float(statistics.median(values)) if values else None
