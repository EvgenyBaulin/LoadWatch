"""Ступенчатый тест против перегружаемого сервера, регрессия «после деплоя», дашборд."""

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from loadwatch.config import Settings
from loadwatch.db import utcnow
from loadwatch.models import RunMetrics, RunStatus, Scenario, Site, TestRun, User
from loadwatch.runner.profiles import StepLoad
from loadwatch.services.run_service import RunService
from tests import target_app
from tests.conftest import FakeDns, auth_headers, register
from tests.test_auth_api import assert_error

HOST = "shop.test"


@pytest.fixture(scope="module")
def target_port() -> Iterator[int]:
    with target_app.serve() as port:
        yield port


async def test_step_load_finds_server_ceiling(target_port: int) -> None:
    """Сервер держит 30 запросов в секунду — потолок должен оказаться около 30."""
    from loadwatch.runner.generator import LoadRunner, RunLimits, simple_get
    from loadwatch.runner.http import PinnedRequester, new_load_client
    from loadwatch.security.ssrf import parse_target_url, resolve_target

    target = parse_target_url(f"http://{HOST}:{target_port}", allow_private=True)
    pinned = resolve_target(target, resolver=lambda h, p: ["127.0.0.1"], allow_private=True)
    profile = StepLoad(start_rate=10, step_rate=10, step_duration_sec=2, max_steps=8)
    async with new_load_client(max_connections=50, timeout_sec=5) as client:
        requester = PinnedRequester(client, pinned, dns_ms=0, max_response_bytes=1024)
        runner = LoadRunner(requester, RunLimits(max_concurrency=50, max_bandwidth_mbit=1000))
        outcome = await runner.run(profile, simple_get("/limited?rps=30"))
    assert outcome.stop_reason == "profile:threshold_exceeded"
    result = outcome.profile_result
    assert result is not None
    assert 18 <= result["ceiling_rps"] <= 32
    assert result["steps"][-1]["passed"] is False
    assert outcome.duration_sec < 12  # остановились, не дойдя до 8 ступеней


@pytest.fixture
def factory(clean_database: str) -> Iterator[Any]:
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(clean_database)
    yield sessionmaker(engine, expire_on_commit=False)
    engine.dispose()


def make_scenario(factory: Any, port: int, **scenario_kw: Any) -> tuple[Site, Scenario]:
    with factory() as session:
        user = User(email="owner@example.com", password_hash="x")
        session.add(user)
        session.flush()
        site = Site(
            owner_id=user.id,
            name="shop",
            url=f"http://{HOST}:{port}",
            verification_status="verified",
            last_verified_at=utcnow(),
        )
        session.add(site)
        session.flush()
        values = {
            "name": "tunable",
            "load_profile": {"type": "constant", "rate": 20, "duration_sec": 1},
            "flow": [{"name": "page", "path": "/tunable"}],
            **scenario_kw,
        }
        scenario = Scenario(site_id=site.id, **values)
        session.add(scenario)
        session.commit()
        return site, scenario


def execute(factory: Any, settings: Settings, site: Site, scenario: Scenario) -> TestRun:
    dns = FakeDns()
    dns.records[HOST] = ["127.0.0.1"]
    with factory() as session:
        run = TestRun(site_id=site.id, scenario_id=scenario.id)
        session.add(run)
        session.commit()
        RunService(session, settings, resolver=dns).execute(run.id)
        session.refresh(run)
        return run


def test_regression_after_slowdown_and_slo(
    factory: Any, make_settings: Callable[..., Settings], target_port: int
) -> None:
    settings = make_settings(allow_private_targets=True, regression_min_baseline_runs=3)
    site, scenario = make_scenario(factory, target_port, slo_p95_ms=50, slo_max_error_rate=0.01)
    target_app.TUNABLE_DELAY_MS["value"] = 0
    try:
        first = execute(factory, settings, site, scenario)
        assert first.regression is None  # сравнивать не с чем
        assert first.slo_met is True
        for _ in range(2):
            run = execute(factory, settings, site, scenario)
            assert run.status == RunStatus.FINISHED
        baseline_run = execute(factory, settings, site, scenario)
        assert baseline_run.regression is not None
        assert baseline_run.regression["detected"] is False
        target_app.TUNABLE_DELAY_MS["value"] = 150  # «деплой» сделал страницу медленнее
        slow = execute(factory, settings, site, scenario)
    finally:
        target_app.TUNABLE_DELAY_MS["value"] = 0
    assert slow.regression is not None and slow.regression["detected"] is True
    finding = slow.regression["findings"][0]
    assert finding["metric"] == "p95_ms"
    assert finding["change_pct"] > 100
    assert slow.slo_met is False


def test_ceiling_is_saved_for_step_scenario(
    factory: Any, make_settings: Callable[..., Settings], target_port: int
) -> None:
    settings = make_settings(allow_private_targets=True)
    site, scenario = make_scenario(
        factory,
        target_port,
        name="step",
        load_profile={
            "type": "step",
            "start_rate": 10,
            "step_rate": 10,
            "step_duration_sec": 2,
            "max_steps": 6,
        },
        flow=[{"name": "page", "path": "/limited?rps=25"}],
    )
    run = execute(factory, settings, site, scenario)
    assert run.status == RunStatus.FINISHED
    assert run.stop_reason == "profile:threshold_exceeded"
    with factory() as session:
        metrics = session.get(RunMetrics, run.id)
        assert metrics is not None
        assert metrics.ceiling_rps is not None and 10 <= metrics.ceiling_rps <= 27
        assert metrics.summary["step_load"]["steps"][0]["passed"] is True


# --- API дашборда -------------------------------------------------------------------


def insert_run(client: TestClient, site_id: str, **overrides: Any) -> str:
    import uuid

    from sqlalchemy import text

    run_id = str(uuid.uuid4())
    values = {
        "status": "finished",
        "slo_met": True,
        "regression": None,
        "saturated": False,
        "p95": 100.0,
        "ceiling": None,
        "started": utcnow(),
        "summary": '{"status_codes": {"200": 10}, "timings": {"ttfb_ms": 12.5}, '
        '"step_load": {"ceiling_is_lower_bound": false}}',
        **overrides,
    }
    with client.app.state.engine.begin() as connection:  # type: ignore[attr-defined]
        connection.execute(
            text(
                "INSERT INTO test_runs (id, site_id, status, source, generator_saturated, slo_met,"
                " cancel_requested, queued_at, started_at, finished_at, regression, created_at,"
                " updated_at) VALUES (:id, :site, :status, 'manual', :saturated, :slo, false,"
                " :started, :started, :started, CAST(:regression AS jsonb), :started, :started)"
            ),
            {
                "id": run_id,
                "site": site_id,
                "status": values["status"],
                "saturated": values["saturated"],
                "slo": values["slo_met"],
                "started": values["started"],
                "regression": values["regression"],
            },
        )
        connection.execute(
            text(
                "INSERT INTO run_metrics (run_id, duration_sec, requests, errors, error_rate, rps,"
                " iterations_per_sec, latency_avg_ms, latency_p50_ms, latency_p90_ms,"
                " latency_p95_ms, latency_p99_ms, latency_max_ms, apdex, ceiling_rps, summary)"
                " VALUES (:id, 10, 100, 0, 0, 10, 10, 50, 40, 80, :p95, 120, 200, 0.9, :ceiling,"
                " CAST(:summary AS jsonb))"
            ),
            {
                "id": run_id,
                "p95": values["p95"],
                "ceiling": values["ceiling"],
                "summary": values["summary"],
            },
        )
    return run_id


@pytest.fixture
def owner(client: TestClient) -> dict[str, str]:
    return auth_headers(register(client)["access_token"])


def add_site(client: TestClient, headers: dict[str, str]) -> str:
    return str(
        client.post("/api/v1/sites", json={"url": "https://a.example"}, headers=headers).json()[
            "id"
        ]
    )


def test_dashboard_metrics_and_summary(client: TestClient, owner: dict[str, str]) -> None:
    from datetime import timedelta

    site_id = add_site(client, owner)
    now = utcnow()
    insert_run(client, site_id, started=now - timedelta(days=2), p95=90.0, slo_met=False)
    insert_run(client, site_id, started=now - timedelta(days=1), p95=110.0, ceiling=55.0)
    last = insert_run(
        client,
        site_id,
        started=now - timedelta(hours=1),
        p95=200.0,
        regression='{"detected": true, "baseline": {"p95_ms": 100}, "findings": []}',
    )
    insert_run(client, site_id, started=now - timedelta(days=20), p95=10.0)  # вне 7 дней
    insert_run(client, site_id, status="failed", started=now)  # не завершён успешно

    metrics = client.get(f"/api/v1/sites/{site_id}/metrics", headers=owner).json()
    assert [point["latency_p95_ms"] for point in metrics["points"]] == [90, 110, 200]
    assert metrics["points"][-1]["regression_detected"] is True
    assert metrics["points"][0]["status_codes"] == {"200": 10}
    assert metrics["points"][0]["timings"]["ttfb_ms"] == 12.5
    assert metrics["truncated"] is False
    long_range = client.get(
        f"/api/v1/sites/{site_id}/metrics",
        params={"from": (now - timedelta(days=30)).isoformat()},
        headers=owner,
    ).json()
    assert len(long_range["points"]) == 4

    summary = client.get(f"/api/v1/sites/{site_id}/summary", headers=owner).json()
    assert summary["last_run"]["id"] == last
    assert summary["baseline"] == {"p95_ms": 100}
    assert summary["regression"]["detected"] is True
    assert summary["ceiling_rps"] == 55.0
    assert summary["ceiling_is_lower_bound"] is False
    assert summary["slo_met_pct"] == 75.0  # 3 из 4 завершённых за 30 дней
    assert summary["runs_in_period"] == 4


def test_dashboard_empty_site(client: TestClient, owner: dict[str, str]) -> None:
    site_id = add_site(client, owner)
    summary = client.get(f"/api/v1/sites/{site_id}/summary", headers=owner).json()
    assert summary["last_run"] is None and summary["slo_met_pct"] is None
    assert client.get(f"/api/v1/sites/{site_id}/metrics", headers=owner).json()["points"] == []


def test_dashboard_validation_and_isolation(
    client: TestClient, owner: dict[str, str], make_client: Callable[..., TestClient]
) -> None:
    site_id = add_site(client, owner)
    url = f"/api/v1/sites/{site_id}/metrics"
    bad_order = {"from": "2026-02-01T00:00:00Z", "to": "2026-01-01T00:00:00Z"}
    assert_error(client.get(url, params=bad_order, headers=owner), 422, "invalid_period")
    too_long = {"from": "2024-01-01T00:00:00Z", "to": "2026-01-01T00:00:00Z"}
    assert_error(client.get(url, params=too_long, headers=owner), 422, "invalid_period")
    naive = {"from": "2026-01-01T00:00:00", "to": "2026-01-02T00:00:00"}
    assert_error(client.get(url, params=naive, headers=owner), 422, "invalid_period")
    stranger = auth_headers(register(client, "stranger@example.com")["access_token"])
    assert_error(client.get(url, headers=stranger), 404, "not_found")
    assert_error(client.get(f"/api/v1/sites/{site_id}/summary", headers=stranger), 404, "not_found")


def test_scenario_api_accepts_new_profiles(client: TestClient, owner: dict[str, str]) -> None:
    site_id = add_site(client, owner)
    for profile in (
        {"type": "ramp_up", "start_rate": 1, "end_rate": 500, "ramp_sec": 600, "hold_sec": 60},
        {
            "type": "step",
            "start_rate": 10,
            "step_rate": 10,
            "step_duration_sec": 30,
            "max_steps": 20,
        },
    ):
        response = client.post(
            f"/api/v1/sites/{site_id}/scenarios",
            json={
                "name": profile["type"],
                "load_profile": profile,
                "flow": [{"name": "a", "path": "/"}],
            },
            headers=owner,
        )
        assert response.status_code == 201, response.text
        effective = response.json()["effective_load_profile"]
        assert effective["type"] == profile["type"]
    bad = {
        "type": "step",
        "start_rate": 10,
        "step_rate": 10,
        "step_duration_sec": 1,
        "max_steps": 5,
    }
    response = client.post(
        f"/api/v1/sites/{site_id}/scenarios",
        json={"name": "bad", "load_profile": bad, "flow": [{"name": "a", "path": "/"}]},
        headers=owner,
    )
    assert_error(response, 422, "validation_error")


async def test_single_healthy_step_gets_ceiling(target_port: int) -> None:
    """Последняя ступень оценивается и тогда, когда тест закончился по длительности."""
    from loadwatch.runner.generator import LoadRunner, RunLimits, simple_get
    from loadwatch.runner.http import PinnedRequester, new_load_client
    from loadwatch.security.ssrf import parse_target_url, resolve_target

    target = parse_target_url(f"http://{HOST}:{target_port}", allow_private=True)
    pinned = resolve_target(target, resolver=lambda h, p: ["127.0.0.1"], allow_private=True)
    profile = StepLoad(start_rate=10, step_rate=10, step_duration_sec=2, max_steps=1)
    async with new_load_client(max_connections=10, timeout_sec=5) as client:
        requester = PinnedRequester(client, pinned, dns_ms=0, max_response_bytes=1024)
        runner = LoadRunner(requester, RunLimits(max_concurrency=10, max_bandwidth_mbit=1000))
        outcome = await runner.run(profile, simple_get("/"))
    result = outcome.profile_result
    assert result is not None
    assert result["reason"] == "max_steps_reached"
    assert result["ceiling_rps"] is not None and result["ceiling_is_lower_bound"] is True


def test_ramp_up_must_grow(client: TestClient, owner: dict[str, str]) -> None:
    site_id = add_site(client, owner)
    response = client.post(
        f"/api/v1/sites/{site_id}/scenarios",
        json={
            "name": "down",
            "load_profile": {"type": "ramp_up", "start_rate": 50, "end_rate": 10, "ramp_sec": 10},
            "flow": [{"name": "a", "path": "/"}],
        },
        headers=owner,
    )
    assert_error(response, 422, "validation_error")
