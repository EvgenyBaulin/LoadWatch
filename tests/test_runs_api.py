"""API запусков и расписания: очередь, пауза между тестами, отмена, история, изоляция."""

from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from loadwatch.db import utcnow
from tests.conftest import auth_headers, register
from tests.test_auth_api import assert_error


@pytest.fixture
def owner(client: TestClient) -> dict[str, str]:
    return auth_headers(register(client, "owner@example.com")["access_token"])


@pytest.fixture
def stranger(client: TestClient) -> dict[str, str]:
    return auth_headers(register(client, "stranger@example.com")["access_token"])


def setup_site(
    client: TestClient, headers: dict[str, str], *, verify: bool = True, flow: Any = None
) -> tuple[str, str]:
    site = client.post("/api/v1/sites", json={"url": "https://shop.example"}, headers=headers)
    site_id = site.json()["id"]
    if verify:
        client.post(f"/api/v1/sites/{site_id}/verify", json={"method": "dns"}, headers=headers)
    scenario = client.post(
        f"/api/v1/sites/{site_id}/scenarios",
        json={
            "name": "home",
            "load_profile": {"type": "constant", "rate": 5, "duration_sec": 10},
            "flow": flow or [{"name": "home", "path": "/"}],
        },
        headers=headers,
    )
    assert scenario.status_code == 201, scenario.text
    return site_id, scenario.json()["id"]


def start(
    client: TestClient, headers: dict[str, str], site_id: str, scenario_id: str, **kw: Any
) -> Any:
    return client.post(
        f"/api/v1/sites/{site_id}/runs", json={"scenario_id": scenario_id, **kw}, headers=headers
    )


def sql(client: TestClient, statement: str, **params: Any) -> None:
    with client.app.state.engine.begin() as connection:  # type: ignore[attr-defined]
        connection.execute(text(statement), params)


def test_manual_run_is_queued(client: TestClient, owner: dict[str, str]) -> None:
    site_id, scenario_id = setup_site(client, owner)
    response = start(client, owner, site_id, scenario_id, label="v1.2.3")
    assert response.status_code == 201, response.text
    run = response.json()
    assert (run["status"], run["source"], run["label"]) == ("queued", "manual", "v1.2.3")
    assert run["metrics"] is None and run["started_at"] is None


def test_one_active_run_per_site(client: TestClient, owner: dict[str, str]) -> None:
    site_id, scenario_id = setup_site(client, owner)
    start(client, owner, site_id, scenario_id)
    assert_error(start(client, owner, site_id, scenario_id), 409, "run_in_progress")


def test_cooldown_between_runs(make_client: Callable[..., TestClient]) -> None:
    client = make_client(site_cooldown_sec=300)
    headers = auth_headers(register(client)["access_token"])
    site_id, scenario_id = setup_site(client, headers)
    run_id = start(client, headers, site_id, scenario_id).json()["id"]
    sql(
        client,
        "UPDATE test_runs SET status='finished', started_at=:t, finished_at=:t WHERE id=:id",
        t=utcnow() - timedelta(seconds=100),
        id=run_id,
    )
    response = start(client, headers, site_id, scenario_id)
    error = assert_error(response, 429, "cooldown_active")
    assert 195 <= error["details"]["retry_after"] <= 200  # type: ignore[index]
    assert response.headers["Retry-After"] == str(error["details"]["retry_after"])  # type: ignore[index]


def test_unverified_site_cannot_run(client: TestClient, owner: dict[str, str]) -> None:
    site_id, scenario_id = setup_site(client, owner, verify=False)
    assert_error(start(client, owner, site_id, scenario_id), 409, "site_not_verified")


def test_missing_secrets_block_run(client: TestClient, owner: dict[str, str]) -> None:
    flow = [{"name": "a", "path": "/", "headers": {"X-Key": "{{secret.KEY}}"}}]
    site_id, scenario_id = setup_site(client, owner, flow=flow)
    assert_error(start(client, owner, site_id, scenario_id), 409, "missing_secrets")


def test_scenario_of_another_site(client: TestClient, owner: dict[str, str]) -> None:
    site_id, _ = setup_site(client, owner)
    other = client.post("/api/v1/sites", json={"url": "https://other.example"}, headers=owner)
    other_id = other.json()["id"]
    client.post(f"/api/v1/sites/{other_id}/verify", json={"method": "dns"}, headers=owner)
    foreign = client.post(
        f"/api/v1/sites/{other_id}/scenarios",
        json={
            "name": "x",
            "load_profile": {"type": "constant", "rate": 1, "duration_sec": 1},
            "flow": [{"name": "a", "path": "/"}],
        },
        headers=owner,
    ).json()["id"]
    assert_error(start(client, owner, site_id, foreign), 404, "not_found")


def test_cancel(client: TestClient, owner: dict[str, str]) -> None:
    site_id, scenario_id = setup_site(client, owner)
    run_id = start(client, owner, site_id, scenario_id).json()["id"]
    cancelled = client.post(f"/api/v1/runs/{run_id}/cancel", headers=owner).json()
    assert cancelled["status"] == "cancelled" and cancelled["finished_at"] is not None
    assert_error(
        client.post(f"/api/v1/runs/{run_id}/cancel", headers=owner), 409, "run_not_cancellable"
    )
    run_id = start(client, owner, site_id, scenario_id).json()["id"]
    sql(client, "UPDATE test_runs SET status='running' WHERE id=:id", id=run_id)
    running = client.post(f"/api/v1/runs/{run_id}/cancel", headers=owner).json()
    assert (running["status"], running["cancel_requested"]) == ("running", True)


def test_history_with_filters_and_metrics(client: TestClient, owner: dict[str, str]) -> None:
    site_id, scenario_id = setup_site(client, owner)
    ids = []
    for status, source in (("finished", "manual"), ("failed", "schedule"), ("finished", "hook")):
        run_id = start(client, owner, site_id, scenario_id).json()["id"]
        sql(
            client,
            "UPDATE test_runs SET status=:s, source=:src, finished_at=:t - interval '1 hour' "
            "WHERE id=:id",
            s=status,
            src=source,
            t=utcnow(),
            id=run_id,
        )
        ids.append(run_id)
    sql(
        client,
        "INSERT INTO run_metrics (run_id, duration_sec, requests, errors, error_rate, rps, "
        "iterations_per_sec, latency_avg_ms, latency_p50_ms, latency_p90_ms, latency_p95_ms, "
        "latency_p99_ms, latency_max_ms, apdex, summary, series) VALUES (:id, 10, 100, 1, 0.01, "
        "10, 10, 50, 40, 80, 90, 120, 200, 0.95, '{\"requests\": 100}', '[{\"t\": 0}]')",
        id=ids[0],
    )
    page = client.get(f"/api/v1/sites/{site_id}/runs", headers=owner).json()
    assert page["total"] == 3
    assert [item["id"] for item in page["items"]] == list(reversed(ids))
    finished = client.get(f"/api/v1/sites/{site_id}/runs?status=finished", headers=owner).json()
    assert finished["total"] == 2
    hooks = client.get(f"/api/v1/sites/{site_id}/runs?source=hook", headers=owner).json()
    assert [item["id"] for item in hooks["items"]] == [ids[2]]
    by_scenario = client.get(
        f"/api/v1/sites/{site_id}/runs?scenario_id={scenario_id}&limit=1", headers=owner
    ).json()
    assert (by_scenario["total"], len(by_scenario["items"])) == (3, 1)
    assert_error(
        client.get(f"/api/v1/sites/{site_id}/runs?status=bogus", headers=owner),
        422,
        "validation_error",
    )
    detail = client.get(f"/api/v1/runs/{ids[0]}", headers=owner).json()
    assert detail["metrics"]["latency_p95_ms"] == 90
    assert detail["summary"] == {"requests": 100}
    assert detail["series"] == [{"t": 0}]


def test_runs_isolation(
    client: TestClient, owner: dict[str, str], stranger: dict[str, str]
) -> None:
    site_id, scenario_id = setup_site(client, owner)
    run_id = start(client, owner, site_id, scenario_id).json()["id"]
    assert_error(client.get(f"/api/v1/runs/{run_id}", headers=stranger), 404, "not_found")
    assert_error(client.post(f"/api/v1/runs/{run_id}/cancel", headers=stranger), 404, "not_found")
    assert_error(client.get(f"/api/v1/sites/{site_id}/runs", headers=stranger), 404, "not_found")
    assert_error(start(client, stranger, site_id, scenario_id), 404, "not_found")
    assert_error(
        client.get(f"/api/v1/sites/{site_id}/schedule", headers=stranger), 404, "not_found"
    )
    payload = {"scenario_id": scenario_id, "cron": "0 9 * * *"}
    assert_error(
        client.put(f"/api/v1/sites/{site_id}/schedule", json=payload, headers=stranger),
        404,
        "not_found",
    )


def test_schedule_api(client: TestClient, owner: dict[str, str]) -> None:
    site_id, scenario_id = setup_site(client, owner)
    url = f"/api/v1/sites/{site_id}/schedule"
    assert client.get(url, headers=owner).json() is None
    payload = {"scenario_id": scenario_id, "cron": "0 9 * * *", "timezone": "Europe/Moscow"}
    schedule = client.put(url, json=payload, headers=owner).json()
    assert schedule["enabled"] is True
    assert len(schedule["upcoming"]) == 3
    assert schedule["next_run_at"] == schedule["upcoming"][0]
    assert schedule["next_run_at"].endswith(("06:00:00Z", "06:00:00+00:00"))
    assert client.get(url, headers=owner).json()["cron"] == "0 9 * * *"
    disabled = client.put(url, json={**payload, "enabled": False}, headers=owner).json()
    assert (disabled["next_run_at"], disabled["upcoming"]) == (None, [])
    for bad in ({"cron": "* * * * *"}, {"cron": "nope nope"}, {"timezone": "Nowhere/City"}):
        assert_error(
            client.put(url, json={**payload, **bad}, headers=owner), 422, "invalid_schedule"
        )
    missing = {**payload, "scenario_id": "00000000-0000-0000-0000-000000000000"}
    assert_error(client.put(url, json=missing, headers=owner), 404, "not_found")
