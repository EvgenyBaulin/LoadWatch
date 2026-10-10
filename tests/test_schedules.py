"""Расписания: часовые пояса, проверка cron, создание запусков по расписанию."""

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from loadwatch.config import Settings
from loadwatch.db import utcnow
from loadwatch.errors import InvalidScheduleError
from loadwatch.models import RunStatus, Scenario, Schedule, Site, TestRun, User
from loadwatch.services.schedule_service import ScheduleService, next_runs, validate_schedule


def utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def test_cron_in_moscow_time() -> None:
    [run] = next_runs("0 9 * * *", "Europe/Moscow", utc(2026, 1, 10, 12, 0))
    assert run == utc(2026, 1, 11, 6, 0)  # 09:00 МСК = 06:00 UTC
    assert run.tzinfo is not None and run.utcoffset() == timedelta(0)


def test_cron_follows_daylight_saving_time() -> None:
    """В Амстердаме 09:00 зимой — 08:00 UTC, летом — 07:00 UTC."""
    winter, summer = (
        next_runs("0 9 * * *", "Europe/Amsterdam", utc(2026, 3, 27, 12))[0],
        next_runs("0 9 * * *", "Europe/Amsterdam", utc(2026, 3, 29, 12))[0],
    )
    assert winter == utc(2026, 3, 28, 8)
    assert summer == utc(2026, 3, 30, 7)


def test_cron_in_utc_and_several_runs() -> None:
    runs = next_runs("0 */6 * * *", "UTC", utc(2026, 1, 1, 0, 0), count=3)
    assert runs == [utc(2026, 1, 1, 6), utc(2026, 1, 1, 12), utc(2026, 1, 1, 18)]


@pytest.mark.parametrize(
    ("cron", "timezone", "message"),
    [
        ("not a cron", "UTC", "5 fields"),
        ("0 9 * * * *", "UTC", "5 fields"),
        ("0 9 * * *", "Mars/Base", "time zone"),
        ("* * * * *", "UTC", "more often"),
        ("*/2 * * * *", "UTC", "more often"),
        ("0 0 31 2 *", "UTC", "never fires"),
    ],
)
def test_invalid_schedules(cron: str, timezone: str, message: str) -> None:
    with pytest.raises(InvalidScheduleError, match=message):
        validate_schedule(cron, timezone, min_interval_sec=300)


def test_valid_schedule() -> None:
    validate_schedule("*/5 * * * *", "UTC", min_interval_sec=300)
    validate_schedule("30 3 * * 1", "Asia/Tokyo", min_interval_sec=300)


@pytest.fixture
def session(clean_database: str) -> Iterator[Session]:
    engine = create_engine(clean_database)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


def make_schedule(
    session: Session,
    *,
    next_run_at: datetime,
    verified: bool = True,
    enabled: bool = True,
    host: str = "example.com",
) -> Schedule:
    user = User(email=f"{host}@example.com", password_hash="x")
    session.add(user)
    session.flush()
    site = Site(
        owner_id=user.id,
        name=host,
        url=f"https://{host}",
        verification_status="verified" if verified else "pending",
    )
    session.add(site)
    session.flush()
    scenario = Scenario(
        site_id=site.id, name="s", load_profile={"type": "constant", "rate": 1, "duration_sec": 1}
    )
    session.add(scenario)
    session.flush()
    schedule = Schedule(
        site_id=site.id,
        scenario_id=scenario.id,
        cron="0 * * * *",
        timezone="UTC",
        enabled=enabled,
        next_run_at=next_run_at,
    )
    session.add(schedule)
    session.commit()
    return schedule


@pytest.fixture
def service(session: Session, make_settings: Callable[..., Settings]) -> ScheduleService:
    return ScheduleService(session, make_settings(site_cooldown_sec=300))


def test_due_schedule_creates_one_run_and_skips_missed(
    session: Session, service: ScheduleService
) -> None:
    now = utc(2026, 5, 1, 10, 30)
    # runner не работал три часа: срабатывания 08:00, 09:00, 10:00 пропущены
    schedule = make_schedule(session, next_run_at=utc(2026, 5, 1, 8, 0))
    [run] = service.enqueue_due(now=now)
    assert (run.status, run.source) == (RunStatus.QUEUED, "schedule")
    session.refresh(schedule)
    assert schedule.next_run_at == utc(2026, 5, 1, 11, 0)
    assert schedule.last_run_at == now
    assert service.enqueue_due(now=now) == []


def test_not_due_and_disabled_schedules(session: Session, service: ScheduleService) -> None:
    make_schedule(session, next_run_at=utcnow() + timedelta(hours=1))
    make_schedule(
        session, next_run_at=utcnow() - timedelta(minutes=1), enabled=False, host="b.example"
    )
    assert service.enqueue_due() == []


def test_unverified_site_is_skipped_but_schedule_advances(
    session: Session, service: ScheduleService
) -> None:
    schedule = make_schedule(session, next_run_at=utcnow() - timedelta(minutes=1), verified=False)
    assert service.enqueue_due() == []
    session.refresh(schedule)
    assert schedule.next_run_at is not None and schedule.next_run_at > utcnow()


def test_active_run_and_cooldown_block_new_run(session: Session, service: ScheduleService) -> None:
    schedule = make_schedule(session, next_run_at=utcnow() - timedelta(minutes=1))
    session.add(TestRun(site_id=schedule.site_id, scenario_id=schedule.scenario_id))
    session.commit()
    assert service.enqueue_due() == []  # уже есть запуск в очереди

    other = make_schedule(session, next_run_at=utcnow() - timedelta(minutes=1), host="c.example")
    session.add(
        TestRun(
            site_id=other.site_id,
            scenario_id=other.scenario_id,
            status="finished",
            started_at=utcnow() - timedelta(seconds=90),
            finished_at=utcnow() - timedelta(seconds=60),
        )
    )
    session.commit()
    assert service.enqueue_due() == []  # пауза между тестами ещё не прошла


def test_deleted_scenario_is_skipped(session: Session, service: ScheduleService) -> None:
    schedule = make_schedule(session, next_run_at=utcnow() - timedelta(minutes=1))
    schedule.scenario_id = None
    session.commit()
    assert service.enqueue_due() == []


def test_two_runners_do_not_double_schedule(
    session: Session, make_settings: Callable[..., Settings], clean_database: str
) -> None:
    """Пока первый runner держит строку расписания, второй её пропускает (SKIP LOCKED)."""
    from loadwatch.repositories.schedule_repository import ScheduleRepository

    make_schedule(session, next_run_at=utcnow() - timedelta(minutes=1))
    engine = create_engine(clean_database)
    with Session(engine) as first, Session(engine) as second:
        locked = ScheduleRepository(first).due_for_update(utcnow(), limit=10)
        assert len(locked) == 1
        assert ScheduleRepository(second).due_for_update(utcnow(), limit=10) == []
        first.rollback()
    engine.dispose()
