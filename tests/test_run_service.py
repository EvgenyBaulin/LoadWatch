"""Выполнение запуска целиком: проверки, генератор, сохранение метрик в БД."""

import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from loadwatch.config import Settings
from loadwatch.db import utcnow
from loadwatch.models import RunMetrics, RunStatus, Scenario, Site, TestRun, User
from loadwatch.services.run_service import RunService, params_hash
from tests import target_app
from tests.conftest import FakeDns

HOST = "shop.loadwatch.test"


@pytest.fixture(scope="module")
def target_port() -> Iterator[int]:
    with target_app.serve() as port:
        yield port


@pytest.fixture
def session(clean_database: str) -> Iterator[Session]:
    engine = create_engine(clean_database)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


@pytest.fixture
def dns() -> FakeDns:
    resolver = FakeDns()
    resolver.records[HOST] = ["127.0.0.1"]
    return resolver


def make_run(
    session: Session,
    port: int,
    *,
    verified: bool = True,
    profile: dict[str, Any] | None = None,
    path: str = "/",
) -> TestRun:
    user = User(email="owner@example.com", password_hash="x")
    session.add(user)
    session.flush()
    site = Site(
        owner_id=user.id,
        name="shop",
        url=f"http://{HOST}:{port}",
        verification_status="verified" if verified else "pending",
        last_verified_at=utcnow(),
    )
    session.add(site)
    session.flush()
    scenario = Scenario(
        site_id=site.id,
        name="home",
        load_profile=profile or {"type": "constant", "rate": 10, "duration_sec": 1.5},
        flow=[{"name": "home", "method": "GET", "path": path}],
        apdex_t_ms=500,
    )
    session.add(scenario)
    session.flush()
    run = TestRun(site_id=site.id, scenario_id=scenario.id)
    session.add(run)
    session.commit()
    return run


def service(
    session: Session, make_settings: Callable[..., Settings], dns: FakeDns, **overrides: Any
) -> RunService:
    settings = make_settings(allow_private_targets=True, **overrides)
    return RunService(session, settings, resolver=dns)


def test_run_is_executed_and_metrics_saved(
    session: Session, make_settings: Callable[..., Settings], dns: FakeDns, target_port: int
) -> None:
    run = make_run(session, target_port)
    service(session, make_settings, dns).execute(run.id)
    session.refresh(run)
    assert run.status == RunStatus.FINISHED
    assert run.stop_reason == "completed"
    assert run.started_at is not None and run.finished_at is not None
    assert run.generator_saturated is False
    assert run.params == {
        "profile": {"type": "constant", "rate": 10.0, "duration_sec": 1.5},
        "flow": [{"name": "home", "method": "GET", "path": "/"}],
    }
    assert run.params_hash == params_hash(run.params)
    metrics = session.get(RunMetrics, run.id)
    assert metrics is not None
    assert 13 <= metrics.requests <= 17
    assert metrics.error_rate == 0
    assert metrics.apdex == 1.0
    assert metrics.latency_p95_ms > 0
    assert metrics.summary["generator"]["saturated"] is False
    assert metrics.series is not None and len(metrics.series) == 2


def test_profile_is_clamped_by_server_limits(
    session: Session, make_settings: Callable[..., Settings], dns: FakeDns, target_port: int
) -> None:
    run = make_run(
        session, target_port, profile={"type": "constant", "rate": 500, "duration_sec": 999}
    )
    service(session, make_settings, dns, max_rps=20, max_duration_sec=1).execute(run.id)
    session.refresh(run)
    assert run.params is not None
    assert run.params["profile"] == {"type": "constant", "rate": 20.0, "duration_sec": 1.0}


def test_unverified_site_is_refused_by_runner(
    session: Session, make_settings: Callable[..., Settings], dns: FakeDns, target_port: int
) -> None:
    run = make_run(session, target_port, verified=False)
    service(session, make_settings, dns).execute(run.id)
    session.refresh(run)
    assert (run.status, run.error) == (RunStatus.FAILED, "site_not_verified")
    assert session.get(RunMetrics, run.id) is None


def test_private_target_is_refused_without_flag(
    session: Session, make_settings: Callable[..., Settings], dns: FakeDns, target_port: int
) -> None:
    run = make_run(session, target_port)
    RunService(session, make_settings(), resolver=dns).execute(run.id)
    session.refresh(run)
    assert (run.status, run.error) == (RunStatus.FAILED, "target_forbidden")


def test_dns_rebinding_before_run_is_refused(
    session: Session, make_settings: Callable[..., Settings], dns: FakeDns, target_port: int
) -> None:
    """Сайт подтверждён, но к запуску DNS указывает на метаданные облака."""
    run = make_run(session, target_port)
    dns.records[HOST] = ["169.254.169.254"]
    RunService(session, make_settings(), resolver=dns).execute(run.id)
    session.refresh(run)
    assert run.error == "target_forbidden"


def test_unresolvable_site(
    session: Session, make_settings: Callable[..., Settings], dns: FakeDns, target_port: int
) -> None:
    run = make_run(session, target_port)
    dns.records[HOST] = []
    service(session, make_settings, dns).execute(run.id)
    session.refresh(run)
    assert run.error == "dns_error"


def test_invalid_profile(
    session: Session, make_settings: Callable[..., Settings], dns: FakeDns, target_port: int
) -> None:
    run = make_run(session, target_port, profile={"type": "nope"})
    service(session, make_settings, dns).execute(run.id)
    session.refresh(run)
    assert run.error == "invalid_load_profile"


def test_deleted_scenario(
    session: Session, make_settings: Callable[..., Settings], dns: FakeDns, target_port: int
) -> None:
    run = make_run(session, target_port)
    run.scenario_id = None
    session.commit()
    service(session, make_settings, dns).execute(run.id)
    session.refresh(run)
    assert run.error == "scenario_not_found"


def test_cancel_requested_stops_run(
    session: Session, make_settings: Callable[..., Settings], dns: FakeDns, target_port: int
) -> None:
    run = make_run(
        session, target_port, profile={"type": "constant", "rate": 5, "duration_sec": 30}
    )
    run.cancel_requested = True
    session.commit()
    service(session, make_settings, dns).execute(run.id)
    session.refresh(run)
    assert run.status == RunStatus.CANCELLED
    assert run.heartbeat_at is not None
    metrics = session.get(RunMetrics, run.id)
    assert metrics is not None and metrics.duration_sec < 3


def test_unknown_run() -> None:
    class NoSession:
        def get(self, *args: object) -> None:
            return None

    service = RunService(NoSession(), None, resolver=FakeDns())  # type: ignore[arg-type]
    with pytest.raises(LookupError):
        service.execute(uuid.uuid4())
