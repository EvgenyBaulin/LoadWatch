"""API сайтов: добавление, список, удаление, проверка владения, изоляция данных."""

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from loadwatch.verification import VerificationOutcome
from tests.conftest import FakeDns, FakeVerifier, auth_headers, register
from tests.test_auth_api import assert_error


@pytest.fixture
def owner(client: TestClient) -> dict[str, str]:
    return auth_headers(register(client, "owner@example.com")["access_token"])


@pytest.fixture
def stranger(client: TestClient) -> dict[str, str]:
    return auth_headers(register(client, "stranger@example.com")["access_token"])


def add_site(
    client: TestClient, headers: dict[str, str], url: str = "https://example.com"
) -> dict[str, Any]:
    response = client.post("/api/v1/sites", json={"url": url}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


def test_create_site_normalizes_url(client: TestClient, owner: dict[str, str]) -> None:
    site = add_site(client, owner, "HTTPS://Example.COM/")
    assert site["url"] == "https://example.com"
    assert site["name"] == "example.com"
    assert site["verification_status"] == "pending"
    assert site["verification_method"] is None


def test_create_site_with_name_and_port(client: TestClient, owner: dict[str, str]) -> None:
    response = client.post(
        "/api/v1/sites",
        json={"url": "http://shop.example:8080", "name": "  Shop  "},
        headers=owner,
    )
    assert response.json()["url"] == "http://shop.example:8080"
    assert response.json()["name"] == "Shop"


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com",
        "example.com",
        "https://example.com/path",
        "https://example.com/?q=1",
        "https://user:pw@example.com",
        "https://example.com:0x50",
    ],
)
def test_create_site_rejects_non_origin(
    client: TestClient, owner: dict[str, str], url: str
) -> None:
    response = client.post("/api/v1/sites", json={"url": url}, headers=owner)
    assert_error(response, 422, "invalid_site_url")


@pytest.mark.parametrize(
    "url", ["http://localhost:8081", "http://127.0.0.1", "http://[::1]", "http://10.0.0.1"]
)
def test_create_site_rejects_private(client: TestClient, owner: dict[str, str], url: str) -> None:
    response = client.post("/api/v1/sites", json={"url": url}, headers=owner)
    assert_error(response, 422, "target_forbidden")


def test_private_site_allowed_with_flag(
    make_client: Callable[..., TestClient],
) -> None:
    client = make_client(allow_private_targets=True)
    headers = auth_headers(register(client)["access_token"])
    assert add_site(client, headers, "http://localhost:8081")["url"] == "http://localhost:8081"


def test_duplicate_site(
    client: TestClient, owner: dict[str, str], stranger: dict[str, str]
) -> None:
    add_site(client, owner)
    response = client.post("/api/v1/sites", json={"url": "https://EXAMPLE.com"}, headers=owner)
    assert_error(response, 409, "site_exists")
    add_site(client, stranger)  # у другого пользователя тот же сайт — можно


def test_list_sites_paginated(client: TestClient, owner: dict[str, str]) -> None:
    for i in range(3):
        add_site(client, owner, f"https://s{i}.example")
    page = client.get("/api/v1/sites?limit=2&offset=0", headers=owner).json()
    assert page["total"] == 3
    assert len(page["items"]) == 2
    assert (page["limit"], page["offset"]) == (2, 0)
    rest = client.get("/api/v1/sites?limit=2&offset=2", headers=owner).json()
    assert len(rest["items"]) == 1
    urls = {item["url"] for item in page["items"] + rest["items"]}
    assert urls == {"https://s0.example", "https://s1.example", "https://s2.example"}
    assert_error(client.get("/api/v1/sites?limit=500", headers=owner), 422, "validation_error")


def test_sites_require_auth(client: TestClient) -> None:
    assert_error(client.get("/api/v1/sites"), 401, "not_authenticated")
    assert_error(
        client.post("/api/v1/sites", json={"url": "https://a.example"}), 401, "not_authenticated"
    )


def test_data_isolation(
    client: TestClient,
    owner: dict[str, str],
    stranger: dict[str, str],
    fake_verifiers: dict[str, FakeVerifier],
) -> None:
    site = add_site(client, owner)
    base = f"/api/v1/sites/{site['id']}"
    assert client.get("/api/v1/sites", headers=stranger).json()["total"] == 0
    assert_error(client.get(base, headers=stranger), 404, "not_found")
    assert_error(client.get(f"{base}/verification", headers=stranger), 404, "not_found")
    assert_error(
        client.post(f"{base}/verify", json={"method": "dns"}, headers=stranger), 404, "not_found"
    )
    assert_error(client.delete(base, headers=stranger), 404, "not_found")
    assert fake_verifiers["dns"].calls == []  # чужой сайт даже не проверялся
    assert client.get(base, headers=owner).status_code == 200


def test_unknown_and_malformed_ids(client: TestClient, owner: dict[str, str]) -> None:
    missing = "/api/v1/sites/00000000-0000-0000-0000-000000000000"
    assert_error(client.get(missing, headers=owner), 404, "not_found")
    assert_error(client.get("/api/v1/sites/not-a-uuid", headers=owner), 422, "validation_error")


def test_delete_site(client: TestClient, owner: dict[str, str]) -> None:
    site = add_site(client, owner)
    response = client.delete(f"/api/v1/sites/{site['id']}", headers=owner)
    assert response.status_code == 204
    assert_error(client.get(f"/api/v1/sites/{site['id']}", headers=owner), 404, "not_found")


def test_verification_info(client: TestClient, owner: dict[str, str]) -> None:
    site = add_site(client, owner)
    info = client.get(f"/api/v1/sites/{site['id']}/verification", headers=owner).json()
    assert info["status"] == "pending"
    assert len(info["token"]) == 32
    assert info["reverify_days"] == 7
    assert [item["method"] for item in info["instructions"]] == ["dns", "http"]
    assert info["instructions"][0]["record_value"] == info["token"]


def test_verify_success(
    client: TestClient, owner: dict[str, str], fake_verifiers: dict[str, FakeVerifier]
) -> None:
    site = add_site(client, owner)
    token = client.get(f"/api/v1/sites/{site['id']}/verification", headers=owner).json()["token"]
    response = client.post(
        f"/api/v1/sites/{site['id']}/verify", json={"method": "http"}, headers=owner
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["verification_status"] == "verified"
    assert body["verification_method"] == "http"
    assert body["verified_at"] is not None
    assert body["last_verified_at"] == body["verified_at"]
    assert fake_verifiers["http"].calls == [("example.com", token)]


def test_verify_failure_keeps_status(
    client: TestClient, owner: dict[str, str], fake_verifiers: dict[str, FakeVerifier]
) -> None:
    site = add_site(client, owner)
    fake_verifiers["dns"].outcome = VerificationOutcome.TOKEN_NOT_FOUND
    response = client.post(
        f"/api/v1/sites/{site['id']}/verify", json={"method": "dns"}, headers=owner
    )
    error = assert_error(response, 422, "verification_failed")
    assert error["details"]["reason"] == "token_not_found"  # type: ignore[index]
    current = client.get(f"/api/v1/sites/{site['id']}", headers=owner).json()
    assert current["verification_status"] == "pending"


def test_verify_blocks_site_resolving_to_private_network(
    client: TestClient,
    owner: dict[str, str],
    fake_dns: FakeDns,
    fake_verifiers: dict[str, FakeVerifier],
) -> None:
    site = add_site(client, owner, "https://rebind.example")
    fake_dns.records["rebind.example"] = ["127.0.0.1"]  # DNS сменился после добавления
    response = client.post(
        f"/api/v1/sites/{site['id']}/verify", json={"method": "dns"}, headers=owner
    )
    error = assert_error(response, 422, "verification_failed")
    assert error["details"]["reason"] == "target_forbidden"  # type: ignore[index]
    assert fake_verifiers["dns"].calls == []


def test_verify_unresolvable_site(
    client: TestClient, owner: dict[str, str], fake_dns: FakeDns
) -> None:
    site = add_site(client, owner, "https://gone.example")
    fake_dns.records["gone.example"] = []
    response = client.post(
        f"/api/v1/sites/{site['id']}/verify", json={"method": "http"}, headers=owner
    )
    error = assert_error(response, 422, "verification_failed")
    assert error["details"]["reason"] == "error"  # type: ignore[index]


def test_verify_unknown_method(client: TestClient, owner: dict[str, str]) -> None:
    site = add_site(client, owner)
    response = client.post(
        f"/api/v1/sites/{site['id']}/verify", json={"method": "email"}, headers=owner
    )
    assert_error(response, 422, "validation_error")


def test_verify_rate_limited(make_client: Callable[..., TestClient]) -> None:
    client = make_client(verify_rate_limit_per_minute=2)
    headers = auth_headers(register(client)["access_token"])
    site = add_site(client, headers)
    url = f"/api/v1/sites/{site['id']}/verify"
    for _ in range(2):
        assert client.post(url, json={"method": "dns"}, headers=headers).status_code == 200
    assert_error(client.post(url, json={"method": "dns"}, headers=headers), 429, "rate_limited")


def test_long_hostname_name_is_truncated(client: TestClient, owner: dict[str, str]) -> None:
    host = ".".join(["a" * 60] * 3) + ".example"
    site = add_site(client, owner, f"https://{host}")
    assert len(site["name"]) == 100


def test_manual_verify_of_private_destination_revokes_verification(
    client: TestClient, owner: dict[str, str], fake_dns: FakeDns
) -> None:
    site = add_site(client, owner, "https://moved.example")
    url = f"/api/v1/sites/{site['id']}/verify"
    assert client.post(url, json={"method": "dns"}, headers=owner).status_code == 200
    fake_dns.records["moved.example"] = ["192.168.1.10"]
    assert_error(
        client.post(url, json={"method": "dns"}, headers=owner), 422, "verification_failed"
    )
    current = client.get(f"/api/v1/sites/{site['id']}", headers=owner).json()
    assert current["verification_status"] == "unverified"


def test_failed_manual_verify_does_not_revoke(
    client: TestClient, owner: dict[str, str], fake_verifiers: dict[str, FakeVerifier]
) -> None:
    site = add_site(client, owner)
    url = f"/api/v1/sites/{site['id']}/verify"
    assert client.post(url, json={"method": "dns"}, headers=owner).status_code == 200
    fake_verifiers["http"].outcome = VerificationOutcome.TOKEN_NOT_FOUND
    assert_error(
        client.post(url, json={"method": "http"}, headers=owner), 422, "verification_failed"
    )
    current = client.get(f"/api/v1/sites/{site['id']}", headers=owner).json()
    assert (current["verification_status"], current["verification_method"]) == ("verified", "dns")
