"""CLI runner: ``python -m loadwatch.runner`` (cron раз в минуту, под ``flock``).

За один вызов:

1. зависшие запуски (процесс упал, heartbeat протух) переводятся в ``failed``;
2. перепроверяется владение сайтами, которым пора (раз в ``REVERIFY_DAYS``);
3. по расписаниям создаются запуски в ``queued``;
4. запуски из очереди выполняются по одному, пока очередь не опустеет или не
   выйдет ``RUNNER_MAX_RUNTIME_SEC`` (не больше ``MAX_GLOBAL_CONCURRENT_RUNS``
   одновременно на все процессы).
"""

from __future__ import annotations

import argparse
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from logging.handlers import RotatingFileHandler

from sqlalchemy.orm import Session, sessionmaker

from loadwatch.alerts import AlertManager
from loadwatch.config import Settings, get_settings
from loadwatch.db import create_db_engine, create_session_factory, utcnow
from loadwatch.logging_setup import LOG_FORMAT, configure_logging, warn_about_unsafe_settings
from loadwatch.notifications import NotifierRegistry
from loadwatch.repositories.run_repository import RunRepository
from loadwatch.security import ssrf
from loadwatch.services.channel_service import ChannelService
from loadwatch.services.run_service import RunService
from loadwatch.services.schedule_service import ScheduleService
from loadwatch.services.site_service import SiteService
from loadwatch.verification import VerifierRegistry, default_registry

logger = logging.getLogger("loadwatch.runner")


@dataclass(slots=True)
class TickReport:
    """Что сделал один вызов runner (для логов и тестов)."""

    stale_failed: int = 0
    reverified: int = 0
    lost_ownership: int = 0
    scheduled: int = 0
    executed: list[str] = field(default_factory=list)
    notifications: list[str] = field(default_factory=list)


def _process_alerts(
    factory: sessionmaker[Session],
    settings: Settings,
    notifiers: NotifierRegistry,
    run_id: uuid.UUID,
    report: TickReport,
) -> None:
    """Алерты по итогам запуска; сбой уведомлений не должен ронять runner."""
    try:
        with factory() as session:
            channels = ChannelService(session, settings, notifiers)
            report.notifications += AlertManager(session, settings, channels).process_run(run_id)
    except Exception:
        logger.exception("Alert processing for run %s failed", run_id)


def recover_stale(factory: sessionmaker[Session], settings: Settings) -> list[uuid.UUID]:
    """Запуски без heartbeat дольше ``RUNNER_STALE_AFTER_SEC`` → ``failed``."""
    with factory() as session:
        now = utcnow()
        before = now - timedelta(seconds=settings.runner_stale_after_sec)
        stale = RunRepository(session).stale_running(before)
        for run in stale:
            run.status = "failed"
            run.error = "stale_heartbeat"
            run.finished_at = now
            logger.warning("Run %s marked failed: no heartbeat since %s", run.id, run.heartbeat_at)
        session.commit()
        return [run.id for run in stale]


def tick(
    factory: sessionmaker[Session],
    settings: Settings,
    *,
    resolver: ssrf.Resolver = ssrf.system_resolver,
    verifiers: VerifierRegistry | None = None,
    max_runs: int | None = None,
    notifiers: NotifierRegistry | None = None,
) -> TickReport:
    """Один проход runner (см. описание модуля)."""
    report = TickReport()
    notifiers = notifiers or NotifierRegistry(settings)
    stale_ids = recover_stale(factory, settings)
    report.stale_failed = len(stale_ids)
    for run_id in stale_ids:
        _process_alerts(factory, settings, notifiers, run_id, report)

    with factory() as session:
        registry = verifiers or default_registry(settings)
        results = SiteService(session, settings, registry, resolver).reverify_due()
        report.reverified = len(results)
        report.lost_ownership = sum(result.lost_ownership for result in results)
        alerts = AlertManager(session, settings, ChannelService(session, settings, notifiers))
        for result in results:
            if result.lost_ownership:
                report.notifications += alerts.site_lost(result.site_id)

    with factory() as session:
        report.scheduled = len(ScheduleService(session, settings).enqueue_due())

    deadline = time.monotonic() + settings.runner_max_runtime_sec
    while time.monotonic() < deadline and (max_runs is None or len(report.executed) < max_runs):
        with factory() as session:
            run = RunRepository(session).claim_next(
                max_running=settings.max_global_concurrent_runs, now=utcnow()
            )
            session.commit()
            if run is None:
                break
            run_id = run.id
        with factory() as session:
            RunService(session, settings, resolver).execute(run_id)
        report.executed.append(str(run_id))
        _process_alerts(factory, settings, notifiers, run_id, report)
    return report


def _configure_file_logging(settings: Settings) -> None:
    if settings.runner_log_file:
        handler = RotatingFileHandler(
            settings.runner_log_file, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        logging.getLogger().addHandler(handler)


def main(argv: list[str] | None = None) -> int:
    """Точка входа CLI."""
    parser = argparse.ArgumentParser(prog="python -m loadwatch.runner", description=__doc__)
    parser.add_argument("--once", action="store_true", help="выполнить не больше одного запуска")
    args = parser.parse_args(argv)
    settings = get_settings()
    configure_logging(settings)
    _configure_file_logging(settings)
    warn_about_unsafe_settings(settings)
    engine = create_db_engine(settings)
    try:
        report = tick(create_session_factory(engine), settings, max_runs=1 if args.once else None)
    finally:
        engine.dispose()
    logger.info(
        "Runner tick: stale=%d reverified=%d lost=%d scheduled=%d executed=%d notified=%d",
        report.stale_failed,
        report.reverified,
        report.lost_ownership,
        report.scheduled,
        len(report.executed),
        len(report.notifications),
    )
    return 0
