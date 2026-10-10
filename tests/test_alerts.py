"""AlertManager: открытие после N плохих запусков, антиспам, восстановление, владение."""

from collections.abc import Callable, Iterator
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from loadwatch.alerts import AlertManager
from loadwatch.config import Settings
from loadwatch.db import utcnow
from loadwatch.models import Alert, NotificationChannel, RunMetrics, Scenario, Site, TestRun, User
from loadwatch.services.channel_service import ChannelService
from tests.conftest import FakeDns, RecordingNotifiers

REGRESSED = {"detected": True, "baseline": {"p95_ms": 100}, "findings": []}
FINE = {"detected": False, "baseline": {"p95_ms": 100}, "findings": []}


@pytest.fixture
def session(clean_database: str) -> Iterator[Session]:
    engine = create_engine(clean_database)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


@pytest.fixture
def settings(make_settings: Callable[..., Settings]) -> Settings:
    return make_settings(alert_consecutive_runs=2, alert_repeat_minutes=60, recovery_runs=2)


@pytest.fixture
def notifiers(settings: Settings) -> RecordingNotifiers:
    return RecordingNotifiers(settings, FakeDns())


@pytest.fixture
def site(session: Session) -> Site:
    user = User(email="owner@example.com", password_hash="x", locale="ru")
    session.add(user)
    session.flush()
    site = Site(
        owner_id=user.id, name="Магазин", url="https://shop.example", verification_status="verified"
    )
    session.add(site)
    session.flush()
    session.add(
        NotificationChannel(
            user_id=user.id,
            type="telegram",
            name="tg",
            config={"chat_id": "1"},
            events=["regression", "slo_breach", "run_failed", "site_unverified", "recovery"],
        )
    )
    session.commit()
    return site


@pytest.fixture
def manager(session: Session, settings: Settings, notifiers: RecordingNotifiers) -> AlertManager:
    return AlertManager(session, settings, ChannelService(session, settings, notifiers))  # type: ignore[arg-type]


def add_run(session: Session, site: Site, *, minutes_ago: float = 0, **kw: Any) -> TestRun:
    values: dict[str, Any] = {"status": "finished", "regression": FINE, "slo_met": True, **kw}
    finished = utcnow() - timedelta(minutes=minutes_ago)
    run = TestRun(
        site_id=site.id, started_at=finished, finished_at=finished, queued_at=finished, **values
    )
    session.add(run)
    session.commit()
    return run


def titles(notifiers: RecordingNotifiers) -> list[str]:
    return [message.title for _, message, _ in notifiers.sent]


def test_alert_opens_after_two_bad_runs(
    session: Session, site: Site, manager: AlertManager, notifiers: RecordingNotifiers
) -> None:
    first = add_run(session, site, minutes_ago=10, regression=REGRESSED)
    assert manager.process_run(first.id) == []  # одна плохая — ещё не алерт
    second = add_run(session, site, minutes_ago=5, regression=REGRESSED)
    assert manager.process_run(second.id) == ["regression"]
    alert = session.scalar(select(Alert))
    assert alert is not None and (alert.type, alert.status) == ("regression", "open")
    assert alert.details is not None and alert.details["consecutive"] == 2
    assert titles(notifiers) == ["Регрессия на Магазин"]


def test_antispam_and_repeat(
    session: Session, site: Site, manager: AlertManager, notifiers: RecordingNotifiers
) -> None:
    for minutes in (30, 20):
        manager.process_run(add_run(session, site, minutes_ago=minutes, regression=REGRESSED).id)
    # ещё плохие запуски в течение часа — повторов нет
    for minutes in (10, 5):
        assert (
            manager.process_run(
                add_run(session, site, minutes_ago=minutes, regression=REGRESSED).id
            )
            == []
        )
    assert len(notifiers.sent) == 1
    alert = session.scalar(select(Alert))
    assert alert is not None
    alert.last_notified_at = utcnow() - timedelta(minutes=61)
    session.commit()
    assert manager.process_run(add_run(session, site, regression=REGRESSED).id) == [
        "regression:repeat"
    ]
    assert titles(notifiers)[-1] == "Всё ещё: Регрессия на Магазин"


def test_recovery_after_two_good_runs(
    session: Session, site: Site, manager: AlertManager, notifiers: RecordingNotifiers
) -> None:
    for minutes in (40, 30):
        manager.process_run(add_run(session, site, minutes_ago=minutes, regression=REGRESSED).id)
    assert manager.process_run(add_run(session, site, minutes_ago=20).id) == []  # одной мало
    assert manager.process_run(add_run(session, site, minutes_ago=10).id) == ["recovery"]
    alert = session.scalar(select(Alert))
    assert alert is not None and alert.status == "resolved" and alert.resolved_at is not None
    assert notifiers.sent[-1][1].event == "recovery"
    assert titles(notifiers)[-1] == "Восстановлено на Магазин: регрессия"
    # новая проблема после восстановления — новый алерт
    for minutes in (2, 1):
        manager.process_run(add_run(session, site, minutes_ago=minutes, regression=REGRESSED).id)
    assert len(session.scalars(select(Alert)).all()) == 2


def test_run_failed_and_slo_breach(
    session: Session, site: Site, manager: AlertManager, notifiers: RecordingNotifiers
) -> None:
    scenario = Scenario(
        site_id=site.id, name="s", load_profile={}, slo_p95_ms=500, slo_max_error_rate=0.01
    )
    session.add(scenario)
    session.commit()
    for minutes in (10, 5):
        manager.process_run(
            add_run(
                session,
                site,
                minutes_ago=minutes,
                status="failed",
                error="dns_error",
                regression=None,
                slo_met=None,
            ).id
        )
    assert titles(notifiers) == ["Тест не выполнился на Магазин"]
    assert "Причина: dns_error." in notifiers.sent[0][1].lines
    for minutes in (4, 3):
        run = add_run(session, site, minutes_ago=minutes, slo_met=False, scenario_id=scenario.id)
        session.add(
            RunMetrics(
                run_id=run.id,
                duration_sec=1,
                requests=10,
                errors=1,
                error_rate=0.1,
                rps=10,
                iterations_per_sec=10,
                latency_avg_ms=1,
                latency_p50_ms=1,
                latency_p90_ms=1,
                latency_p95_ms=900,
                latency_p99_ms=1,
                latency_max_ms=1,
                summary={},
            )
        )
        session.commit()
        manager.process_run(run.id)
    slo = [m for _, m, _ in notifiers.sent if m.event == "slo_breach"]
    assert slo and slo[0].lines[0] == "p95 900 мс при цели не выше 500 мс."
    # две успешные — сбой «восстановился»
    events = [m.event for _, m, _ in notifiers.sent]
    assert "recovery" in events


def test_unknown_verdicts_do_not_break_streaks(
    session: Session, site: Site, manager: AlertManager, notifiers: RecordingNotifiers
) -> None:
    """Запуск без базовой линии (regression=None) не прерывает серию плохих."""
    manager.process_run(add_run(session, site, minutes_ago=10, regression=REGRESSED).id)
    manager.process_run(add_run(session, site, minutes_ago=5, regression=None).id)
    assert manager.process_run(add_run(session, site, regression=REGRESSED).id) == ["regression"]


def test_cancelled_runs_are_ignored(session: Session, site: Site, manager: AlertManager) -> None:
    run = add_run(session, site, status="cancelled")
    assert manager.process_run(run.id) == []


def test_site_unverified_alert(
    session: Session, site: Site, manager: AlertManager, notifiers: RecordingNotifiers
) -> None:
    assert manager.site_lost(site.id) == ["site_unverified"]
    assert manager.site_lost(site.id) == []  # уже открыт
    assert manager.site_reverified(site.id) == ["recovery"]
    assert manager.site_reverified(site.id) == []
    assert titles(notifiers) == [
        "Владение сайтом Магазин не подтверждено",
        "Восстановлено на Магазин: владение не подтверждено",
    ]


def test_delivery_failure_does_not_break_alerts(
    session: Session, site: Site, manager: AlertManager, notifiers: RecordingNotifiers
) -> None:
    notifiers.fail_with = "Telegram answered HTTP 403"
    for minutes in (10, 5):
        manager.process_run(add_run(session, site, minutes_ago=minutes, regression=REGRESSED).id)
    alert = session.scalar(select(Alert))
    assert alert is not None and alert.status == "open"


def test_unsubscribed_and_disabled_channels(
    session: Session, site: Site, manager: AlertManager, notifiers: RecordingNotifiers
) -> None:
    channel = session.scalar(select(NotificationChannel))
    assert channel is not None
    channel.events = ["recovery"]
    session.commit()
    for minutes in (10, 5):
        manager.process_run(add_run(session, site, minutes_ago=minutes, regression=REGRESSED).id)
    assert notifiers.sent == []
    channel.events = ["regression"]
    channel.enabled = False
    session.commit()
    alert = session.scalar(select(Alert))
    assert alert is not None
    alert.last_notified_at = utcnow() - timedelta(hours=2)
    session.commit()
    manager.process_run(add_run(session, site, regression=REGRESSED).id)
    assert notifiers.sent == []
