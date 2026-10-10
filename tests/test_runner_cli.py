"""CLI runner: очередь, расписания, зависшие запуски, глобальный лимит, перепроверка."""

from collections.abc import Callable, Iterator
from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from loadwatch.config import Settings
from loadwatch.db import utcnow
from loadwatch.models import RunMetrics, RunStatus, Scenario, Schedule, Site, TestRun, User
from loadwatch.repositories.run_repository import RunRepository
from loadwatch.runner.cli import main, tick
from loadwatch.verification import VerificationOutcome, VerifierRegistry
from tests import target_app
from tests.conftest import TEST_JWT_SECRET, FakeDns, FakeVerifier


@pytest.fixture(scope="module")
def target_port() -> Iterator[int]:
    with target_app.serve() as port:
        yield port


@pytest.fixture
def factory(clean_database: str) -> Iterator[sessionmaker[Session]]:
    engine = create_engine(clean_database)
    yield sessionmaker(engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def dns() -> FakeDns:
    resolver = FakeDns()
    resolver.records["shop.test"] = ["127.0.0.1"]
    resolver.records["blog.test"] = ["127.0.0.1"]
    return resolver


@pytest.fixture
def settings(make_settings: Callable[..., Settings]) -> Settings:
    return make_settings(allow_private_targets=True, site_cooldown_sec=0)


def make_site(
    factory: sessionmaker[Session], port: int, host: str = "shop.test", **site_kw: object
) -> tuple[Site, Scenario]:
    with factory() as session:
        user = User(email=f"{host}@example.com", password_hash="x")
        session.add(user)
        session.flush()
        site = Site(
            owner_id=user.id,
            name=host,
            url=f"http://{host}:{port}",
            verification_status="verified",
            verification_method="http",
            last_verified_at=utcnow(),
            **site_kw,
        )
        session.add(site)
        session.flush()
        scenario = Scenario(
            site_id=site.id,
            name="home",
            load_profile={"type": "constant", "rate": 5, "duration_sec": 1},
            flow=[{"name": "home", "path": "/"}],
        )
        session.add(scenario)
        session.commit()
        return site, scenario


def queue(factory: sessionmaker[Session], site: Site, scenario: Scenario, **kw: object) -> TestRun:
    with factory() as session:
        run = TestRun(site_id=site.id, scenario_id=scenario.id, **kw)
        session.add(run)
        session.commit()
        return run


def status_of(factory: sessionmaker[Session], run: TestRun) -> TestRun:
    with factory() as session:
        found = session.get(TestRun, run.id)
        assert found is not None
        return found


def test_queued_run_is_executed(
    factory: sessionmaker[Session], settings: Settings, dns: FakeDns, target_port: int
) -> None:
    site, scenario = make_site(factory, target_port)
    run = queue(factory, site, scenario)
    report = tick(factory, settings, resolver=dns, verifiers=VerifierRegistry([]))
    assert report.executed == [str(run.id)]
    finished = status_of(factory, run)
    assert finished.status == RunStatus.FINISHED
    with factory() as session:
        assert session.get(RunMetrics, run.id) is not None


def test_schedule_creates_and_runs(
    factory: sessionmaker[Session], settings: Settings, dns: FakeDns, target_port: int
) -> None:
    site, scenario = make_site(factory, target_port)
    with factory() as session:
        session.add(
            Schedule(
                site_id=site.id,
                scenario_id=scenario.id,
                cron="0 * * * *",
                next_run_at=utcnow() - timedelta(minutes=1),
            )
        )
        session.commit()
    report = tick(factory, settings, resolver=dns, verifiers=VerifierRegistry([]))
    assert report.scheduled == 1
    assert len(report.executed) == 1
    with factory() as session:
        run = session.get(TestRun, report.executed[0])
        assert run is not None
        assert (run.source, run.status) == ("schedule", RunStatus.FINISHED)


def test_stale_running_run_is_failed(
    factory: sessionmaker[Session], settings: Settings, dns: FakeDns, target_port: int
) -> None:
    site, scenario = make_site(factory, target_port)
    stale = queue(
        factory,
        site,
        scenario,
        status="running",
        heartbeat_at=utcnow() - timedelta(minutes=10),
        started_at=utcnow() - timedelta(minutes=11),
    )
    report = tick(factory, settings, resolver=dns, verifiers=VerifierRegistry([]))
    assert report.stale_failed == 1
    failed = status_of(factory, stale)
    assert (failed.status, failed.error) == (RunStatus.FAILED, "stale_heartbeat")


def test_global_limit(factory: sessionmaker[Session], target_port: int) -> None:
    site, scenario = make_site(factory, target_port)
    other_site, other_scenario = make_site(factory, target_port, host="blog.test")
    running = queue(factory, site, scenario, status="running", heartbeat_at=utcnow())
    waiting = queue(factory, other_site, other_scenario)
    with factory() as session:  # глобальный лимит 1 и один уже идёт — ничего не берём
        assert RunRepository(session).claim_next(max_running=1, now=utcnow()) is None
    with factory() as session:
        claimed = RunRepository(session).claim_next(max_running=2, now=utcnow())
        session.commit()
        assert claimed is not None and claimed.id == waiting.id
    assert status_of(factory, running).status == RunStatus.RUNNING


def test_database_allows_one_active_run_per_site(
    factory: sessionmaker[Session], target_port: int
) -> None:
    from sqlalchemy.exc import IntegrityError

    site, scenario = make_site(factory, target_port)
    queue(factory, site, scenario)
    with pytest.raises(IntegrityError):
        queue(factory, site, scenario, status="running")
    queue(factory, site, scenario, status="finished")  # завершённых — сколько угодно


def test_stale_worker_is_fenced(
    factory: sessionmaker[Session], settings: Settings, dns: FakeDns, target_port: int
) -> None:
    """Запуск признали зависшим, пока процесс ещё работал: он должен остановиться
    и не перезаписать статус failed."""
    from loadwatch.services.run_service import RunService

    site, scenario = make_site(factory, target_port)
    run = queue(factory, site, scenario, status="running", heartbeat_at=utcnow())
    with factory() as worker_session, factory() as other:
        service = RunService(worker_session, settings, resolver=dns)
        assert service._heartbeat(run.id) is False  # пока всё штатно
        stored = other.get(TestRun, run.id)
        assert stored is not None
        stored.status, stored.error = "failed", "stale_heartbeat"
        other.commit()
        assert service._heartbeat(run.id) is True  # процесс узнаёт, что запуск не его


def test_claim_order_is_fifo(factory: sessionmaker[Session], target_port: int) -> None:
    site, scenario = make_site(factory, target_port)
    other, other_scenario = make_site(factory, target_port, host="blog.test")
    first = queue(factory, other, other_scenario, queued_at=utcnow() - timedelta(minutes=5))
    queue(factory, site, scenario, queued_at=utcnow())
    with factory() as session:
        claimed = RunRepository(session).claim_next(max_running=5, now=utcnow())
        assert claimed is not None and claimed.id == first.id


def test_lost_ownership_stops_queued_run(
    factory: sessionmaker[Session], settings: Settings, dns: FakeDns, target_port: int
) -> None:
    site, scenario = make_site(factory, target_port)
    with factory() as session:
        stored = session.get(Site, site.id)
        assert stored is not None
        stored.last_verified_at = utcnow() - timedelta(days=8)
        session.commit()
    run = queue(factory, site, scenario)
    verifier = FakeVerifier("http")
    verifier.outcome = VerificationOutcome.TOKEN_NOT_FOUND
    report = tick(factory, settings, resolver=dns, verifiers=VerifierRegistry([verifier]))
    assert (report.reverified, report.lost_ownership) == (1, 1)
    failed = status_of(factory, run)
    assert (failed.status, failed.error) == (RunStatus.FAILED, "site_not_verified")


def test_cancelled_while_queued_is_not_executed(
    factory: sessionmaker[Session], settings: Settings, dns: FakeDns, target_port: int
) -> None:
    site, scenario = make_site(factory, target_port)
    run = queue(factory, site, scenario, status="cancelled")
    report = tick(factory, settings, resolver=dns, verifiers=VerifierRegistry([]))
    assert report.executed == []
    assert status_of(factory, run).status == RunStatus.CANCELLED


def test_main_entry_point(
    monkeypatch: pytest.MonkeyPatch, clean_database: str, tmp_path: object
) -> None:
    from loadwatch import config

    log_file = f"{tmp_path}/runner.log"
    monkeypatch.setenv("DATABASE_URL", clean_database)
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("RUNNER_LOG_FILE", log_file)
    config.get_settings.cache_clear()
    try:
        assert main(["--once"]) == 0
    finally:
        config.get_settings.cache_clear()
    with open(log_file, encoding="utf-8") as handle:
        assert "Runner tick" in handle.read()
