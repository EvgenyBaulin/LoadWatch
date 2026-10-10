"""API сценариев: CRUD, изоляция, секреты только на запись, dry-run."""

import logging
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from tests import target_app
from tests.conftest import FakeDns, auth_headers, register
from tests.test_auth_api import assert_error

KEY = Fernet.generate_key().decode()
HOST = "shop.example"

LOGIN = {
    "name": "login",
    "method": "POST",
    "path": "/api/login",
    "body": {"type": "json", "content": {"username": "demo", "password": "{{secret.PASSWORD}}"}},
    "extract": [{"var": "token", "source": "json", "expr": "data.token"}],
}
DASHBOARD = {
    "name": "dashboard",
    "path": "/dashboard",
    "headers": {"Authorization": "Bearer {{token}}"},
    "assert_contains": "Welcome",
}


def scenario_payload(**overrides: Any) -> dict[str, Any]:
    return {
        "name": "login flow",
        "load_profile": {"type": "constant", "rate": 5, "duration_sec": 30},
        "flow": [{"name": "home", "path": "/"}, LOGIN, DASHBOARD],
        "slo_p95_ms": 800,
        "slo_max_error_rate": 0.01,
        **overrides,
    }


@pytest.fixture(scope="module")
def target_port() -> Iterator[int]:
    with target_app.serve() as port:
        yield port


@pytest.fixture
def api(make_client: Callable[..., TestClient], fake_dns: FakeDns) -> TestClient:
    fake_dns.records[HOST] = ["127.0.0.1"]
    return make_client(secrets_encryption_key=KEY, allow_private_targets=True)


@pytest.fixture
def owner(api: TestClient) -> dict[str, str]:
    return auth_headers(register(api, "owner@example.com")["access_token"])


@pytest.fixture
def stranger(api: TestClient) -> dict[str, str]:
    return auth_headers(register(api, "stranger@example.com")["access_token"])


def add_site(api: TestClient, headers: dict[str, str], port: int = 80, verify: bool = True) -> str:
    url = f"http://{HOST}:{port}" if port != 80 else f"http://{HOST}"
    site = api.post("/api/v1/sites", json={"url": url}, headers=headers).json()
    if verify:
        response = api.post(
            f"/api/v1/sites/{site['id']}/verify", json={"method": "http"}, headers=headers
        )
        assert response.status_code == 200, response.text
    return str(site["id"])


def add_scenario(
    api: TestClient, headers: dict[str, str], site_id: str, **kw: Any
) -> dict[str, Any]:
    response = api.post(
        f"/api/v1/sites/{site_id}/scenarios", json=scenario_payload(**kw), headers=headers
    )
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


def test_create_and_read_scenario(api: TestClient, owner: dict[str, str]) -> None:
    site_id = add_site(api, owner)
    scenario = add_scenario(api, owner, site_id)
    assert scenario["name"] == "login flow"
    assert [step["name"] for step in scenario["flow"]] == ["home", "login", "dashboard"]
    assert scenario["flow"][0]["expected_status"] == ["2xx", "3xx"]  # значения по умолчанию
    assert scenario["missing_secrets"] == ["PASSWORD"]
    assert scenario["secrets"] == []
    assert scenario["apdex_t_ms"] == 500
    fetched = api.get(f"/api/v1/scenarios/{scenario['id']}", headers=owner).json()
    assert fetched == scenario
    page = api.get(f"/api/v1/sites/{site_id}/scenarios", headers=owner).json()
    assert page["total"] == 1


def test_effective_profile_is_clamped(api: TestClient, owner: dict[str, str]) -> None:
    site_id = add_site(api, owner)
    scenario = add_scenario(
        api, owner, site_id, load_profile={"type": "constant", "rate": 5000, "duration_sec": 3600}
    )
    assert scenario["load_profile"]["rate"] == 5000
    assert scenario["effective_load_profile"] == {
        "type": "constant",
        "rate": 150.0,
        "duration_sec": 120.0,
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"flow": [{"name": "x", "path": "https://evil.example/"}]},
        {"flow": [{"name": "x", "path": "/", "headers": {"Host": "evil.example"}}]},
        {"flow": [DASHBOARD, LOGIN]},
        {"flow": []},
        {"flow": [{"name": f"s{i}", "path": "/"} for i in range(11)]},
        {"load_profile": {"type": "constant", "rate": 0, "duration_sec": 10}},
        {"load_profile": {"type": "spike", "rate": 1, "duration_sec": 10}},
        {"slo_max_error_rate": 2},
        {"name": ""},
    ],
)
def test_invalid_scenarios(
    api: TestClient, owner: dict[str, str], overrides: dict[str, Any]
) -> None:
    site_id = add_site(api, owner)
    response = api.post(
        f"/api/v1/sites/{site_id}/scenarios", json=scenario_payload(**overrides), headers=owner
    )
    assert_error(response, 422, "validation_error")


def test_duplicate_name(api: TestClient, owner: dict[str, str]) -> None:
    site_id = add_site(api, owner)
    add_scenario(api, owner, site_id)
    response = api.post(
        f"/api/v1/sites/{site_id}/scenarios", json=scenario_payload(), headers=owner
    )
    assert_error(response, 409, "scenario_exists")


def test_update_and_delete(api: TestClient, owner: dict[str, str]) -> None:
    site_id = add_site(api, owner)
    first = add_scenario(api, owner, site_id)
    second = add_scenario(api, owner, site_id, name="other")
    url = f"/api/v1/scenarios/{first['id']}"
    updated = api.put(url, json=scenario_payload(name="renamed", apdex_t_ms=300), headers=owner)
    assert updated.status_code == 200
    assert (updated.json()["name"], updated.json()["apdex_t_ms"]) == ("renamed", 300)
    clash = api.put(url, json=scenario_payload(name="other"), headers=owner)
    assert_error(clash, 409, "scenario_exists")
    assert api.delete(url, headers=owner).status_code == 204
    assert_error(api.get(url, headers=owner), 404, "not_found")
    assert api.get(f"/api/v1/scenarios/{second['id']}", headers=owner).status_code == 200


def test_isolation(api: TestClient, owner: dict[str, str], stranger: dict[str, str]) -> None:
    site_id = add_site(api, owner)
    scenario = add_scenario(api, owner, site_id)
    url = f"/api/v1/scenarios/{scenario['id']}"
    assert_error(api.get(url, headers=stranger), 404, "not_found")
    assert_error(api.put(url, json=scenario_payload(), headers=stranger), 404, "not_found")
    assert_error(api.delete(url, headers=stranger), 404, "not_found")
    assert_error(
        api.put(f"{url}/secrets", json={"secrets": {"PASSWORD": "x"}}, headers=stranger),
        404,
        "not_found",
    )
    assert_error(api.post(f"{url}/dry-run", headers=stranger), 404, "not_found")
    assert_error(api.get(f"/api/v1/sites/{site_id}/scenarios", headers=stranger), 404, "not_found")
    assert_error(
        api.post(f"/api/v1/sites/{site_id}/scenarios", json=scenario_payload(), headers=stranger),
        404,
        "not_found",
    )


def test_secrets_are_write_only(
    api: TestClient, owner: dict[str, str], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    site_id = add_site(api, owner)
    scenario = add_scenario(api, owner, site_id)
    url = f"/api/v1/scenarios/{scenario['id']}/secrets"
    response = api.put(
        url, json={"secrets": {"PASSWORD": "s3cret-pass", "EXTRA": "x1"}}, headers=owner
    )
    assert response.status_code == 200
    body = response.json()
    assert body["secrets"] == [
        {"name": "EXTRA", "value": "********"},
        {"name": "PASSWORD", "value": "********"},
    ]
    assert body["missing_secrets"] == []
    fetched = api.get(f"/api/v1/scenarios/{scenario['id']}", headers=owner)
    assert "s3cret-pass" not in fetched.text
    assert "s3cret-pass" not in caplog.text
    # null удаляет секрет
    removed = api.put(url, json={"secrets": {"EXTRA": None}}, headers=owner).json()
    assert [secret["name"] for secret in removed["secrets"]] == ["PASSWORD"]


def test_secrets_are_encrypted_in_database(api: TestClient, owner: dict[str, str]) -> None:
    from sqlalchemy import text

    site_id = add_site(api, owner)
    scenario = add_scenario(api, owner, site_id)
    api.put(
        f"/api/v1/scenarios/{scenario['id']}/secrets",
        json={"secrets": {"PASSWORD": "s3cret-pass"}},
        headers=owner,
    )
    with api.app.state.engine.connect() as connection:  # type: ignore[attr-defined]
        stored = connection.scalar(text("SELECT value_encrypted FROM scenario_secrets"))
    assert stored and "s3cret-pass" not in stored


def test_invalid_secret_names(api: TestClient, owner: dict[str, str]) -> None:
    site_id = add_site(api, owner)
    scenario = add_scenario(api, owner, site_id)
    url = f"/api/v1/scenarios/{scenario['id']}/secrets"
    assert_error(
        api.put(url, json={"secrets": {"bad name": "x"}}, headers=owner), 422, "validation_error"
    )
    too_many = {f"S{i}": "x" for i in range(21)}
    assert_error(api.put(url, json={"secrets": too_many}, headers=owner), 422, "validation_error")


def test_secrets_unavailable_without_key(
    make_client: Callable[..., TestClient], fake_dns: FakeDns
) -> None:
    client = make_client()  # SECRETS_ENCRYPTION_KEY не задан
    headers = auth_headers(register(client)["access_token"])
    site_id = add_site(client, headers)
    scenario = add_scenario(client, headers, site_id)
    response = client.put(
        f"/api/v1/scenarios/{scenario['id']}/secrets",
        json={"secrets": {"PASSWORD": "x"}},
        headers=headers,
    )
    assert_error(response, 503, "secrets_unavailable")


def test_dry_run_login_flow(
    api: TestClient, owner: dict[str, str], target_port: int, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    site_id = add_site(api, owner, target_port)
    scenario = add_scenario(api, owner, site_id)
    api.put(
        f"/api/v1/scenarios/{scenario['id']}/secrets",
        json={"secrets": {"PASSWORD": "s3cret-pass"}},
        headers=owner,
    )
    response = api.post(f"/api/v1/scenarios/{scenario['id']}/dry-run", headers=owner)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["ok"] is True
    assert [(s["name"], s["status"], s["ok"]) for s in result["steps"]] == [
        ("home", 200, True),
        ("login", 200, True),
        ("dashboard", 200, True),
    ]
    assert result["steps"][1]["extracted"]["token"].startswith("tok-")
    assert result["skipped_steps"] == []
    assert "s3cret-pass" not in response.text
    assert "s3cret-pass" not in caplog.text


def test_dry_run_reports_failed_step(
    api: TestClient, owner: dict[str, str], target_port: int
) -> None:
    site_id = add_site(api, owner, target_port)
    scenario = add_scenario(api, owner, site_id)  # секрет не задан
    result = api.post(f"/api/v1/scenarios/{scenario['id']}/dry-run", headers=owner).json()
    assert result["ok"] is False
    assert result["steps"][-1]["name"] == "login"
    assert result["steps"][-1]["error"] == "missing_secret"
    assert result["skipped_steps"] == ["dashboard"]


def test_dry_run_requires_verified_site(api: TestClient, owner: dict[str, str]) -> None:
    site_id = add_site(api, owner, verify=False)
    scenario = add_scenario(api, owner, site_id)
    response = api.post(f"/api/v1/scenarios/{scenario['id']}/dry-run", headers=owner)
    assert_error(response, 409, "site_not_verified")


def test_dry_run_rechecks_ssrf(api: TestClient, owner: dict[str, str], fake_dns: FakeDns) -> None:
    site_id = add_site(api, owner)
    scenario = add_scenario(api, owner, site_id)
    fake_dns.records[HOST] = []
    response = api.post(f"/api/v1/scenarios/{scenario['id']}/dry-run", headers=owner)
    assert_error(response, 422, "target_unresolvable")


def test_dry_run_forbidden_target(
    make_client: Callable[..., TestClient], fake_dns: FakeDns
) -> None:
    client = make_client(secrets_encryption_key=KEY)  # без ALLOW_PRIVATE_TARGETS
    headers = auth_headers(register(client)["access_token"])
    site_id = add_site(client, headers)
    scenario = add_scenario(client, headers, site_id)
    fake_dns.records[HOST] = ["10.0.0.7"]
    response = client.post(f"/api/v1/scenarios/{scenario['id']}/dry-run", headers=headers)
    assert_error(response, 422, "target_forbidden")


def test_dry_run_rate_limited(
    make_client: Callable[..., TestClient], fake_dns: FakeDns, target_port: int
) -> None:
    fake_dns.records[HOST] = ["127.0.0.1"]
    client = make_client(
        secrets_encryption_key=KEY, allow_private_targets=True, dry_run_rate_limit_per_minute=1
    )
    headers = auth_headers(register(client)["access_token"])
    site_id = add_site(client, headers, target_port)
    scenario = add_scenario(client, headers, site_id, flow=[{"name": "home", "path": "/"}])
    url = f"/api/v1/scenarios/{scenario['id']}/dry-run"
    assert client.post(url, headers=headers).status_code == 200
    assert_error(client.post(url, headers=headers), 429, "rate_limited")
