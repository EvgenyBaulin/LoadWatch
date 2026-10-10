"""Registration, login, current user, preferences and the error format."""

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from loadwatch.api.main import create_app
from loadwatch.config import Settings
from tests.conftest import auth_headers, register

PASSWORD = "correct horse battery"


def assert_error(response: object, status: int, code: str) -> dict[str, object]:
    assert response.status_code == status, response.text  # type: ignore[attr-defined]
    body = response.json()  # type: ignore[attr-defined]
    assert set(body) == {"error"}
    assert body["error"]["code"] == code
    assert isinstance(body["error"]["message"], str)
    return body["error"]  # type: ignore[no-any-return]


def test_register_returns_token_and_user(client: TestClient) -> None:
    body = register(client, "Owner@Example.com", locale="ru")
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 720 * 60
    assert body["user"]["email"] == "owner@example.com"
    assert body["user"]["locale"] == "ru"
    assert body["user"]["timezone"] == "UTC"
    assert body["user"]["created_at"].endswith(("Z", "+00:00"))  # always UTC
    assert "password" not in str(body)


def test_duplicate_email_is_case_insensitive(client: TestClient) -> None:
    register(client, "owner@example.com")
    response = client.post(
        "/api/v1/auth/register", json={"email": "OWNER@example.com", "password": PASSWORD}
    )
    assert_error(response, 409, "email_taken")


def test_validation_error_does_not_echo_input(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/register", json={"email": "owner@example.com", "password": "short12"}
    )
    error = assert_error(response, 422, "validation_error")
    assert "short12" not in response.text
    assert error["details"][0]["loc"] == ["body", "password"]  # type: ignore[index]


def test_invalid_email_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/register", json={"email": "not-an-email", "password": PASSWORD}
    )
    assert_error(response, 422, "validation_error")


def test_login_and_me(client: TestClient) -> None:
    register(client)
    response = client.post(
        "/api/v1/auth/login", json={"email": "OWNER@example.com", "password": PASSWORD}
    )
    assert response.status_code == 200
    token = response.json()["access_token"]
    me = client.get("/api/v1/auth/me", headers=auth_headers(token))
    assert me.status_code == 200
    assert me.json()["email"] == "owner@example.com"


def test_login_wrong_password_and_unknown_user_look_the_same(client: TestClient) -> None:
    register(client)
    wrong = client.post(
        "/api/v1/auth/login", json={"email": "owner@example.com", "password": "nope-nope"}
    )
    unknown = client.post(
        "/api/v1/auth/login", json={"email": "ghost@example.com", "password": PASSWORD}
    )
    assert assert_error(wrong, 401, "invalid_credentials") == assert_error(
        unknown, 401, "invalid_credentials"
    )


def test_me_requires_token(client: TestClient) -> None:
    assert_error(client.get("/api/v1/auth/me"), 401, "not_authenticated")


def test_me_rejects_bad_token(client: TestClient) -> None:
    response = client.get("/api/v1/auth/me", headers=auth_headers("garbage"))
    assert_error(response, 401, "not_authenticated")


def test_token_of_deleted_user_rejected(client: TestClient) -> None:
    from sqlalchemy import text

    token = register(client)["access_token"]
    with client.app.state.engine.begin() as connection:  # type: ignore[attr-defined]
        connection.execute(text("DELETE FROM users"))
    assert_error(
        client.get("/api/v1/auth/me", headers=auth_headers(token)), 401, "not_authenticated"
    )


def test_update_preferences(client: TestClient) -> None:
    token = register(client)["access_token"]
    response = client.put(
        "/api/v1/users/me/preferences",
        json={"locale": "ru", "timezone": "Europe/Amsterdam"},
        headers=auth_headers(token),
    )
    assert response.status_code == 200
    assert response.json()["locale"] == "ru"
    assert response.json()["timezone"] == "Europe/Amsterdam"
    # partial update keeps the other field
    response = client.put(
        "/api/v1/users/me/preferences", json={"locale": "en"}, headers=auth_headers(token)
    )
    assert response.json() | {"locale": "en"} == response.json()
    assert response.json()["timezone"] == "Europe/Amsterdam"


def test_preferences_reject_unknown_timezone_and_locale(client: TestClient) -> None:
    token = register(client)["access_token"]
    for payload in ({"timezone": "Mars/Olympus"}, {"timezone": "../etc/passwd"}, {"locale": "de"}):
        response = client.put(
            "/api/v1/users/me/preferences", json=payload, headers=auth_headers(token)
        )
        assert_error(response, 422, "validation_error")


def test_preferences_require_auth(client: TestClient) -> None:
    response = client.put("/api/v1/users/me/preferences", json={"locale": "ru"})
    assert_error(response, 401, "not_authenticated")


def test_registration_closed(make_client: Callable[..., TestClient]) -> None:
    client = make_client(registration_mode="closed")
    response = client.post(
        "/api/v1/auth/register", json={"email": "owner@example.com", "password": PASSWORD}
    )
    assert_error(response, 403, "registration_closed")


def test_registration_invite(make_client: Callable[..., TestClient]) -> None:
    client = make_client(registration_mode="invite", registration_invite_code="let-me-in")
    for code in (None, "wrong"):
        payload = {"email": "owner@example.com", "password": PASSWORD, "invite_code": code}
        assert_error(client.post("/api/v1/auth/register", json=payload), 403, "invalid_invite_code")
    register(client, invite_code="let-me-in")


def test_login_rate_limited(make_client: Callable[..., TestClient]) -> None:
    client = make_client(auth_rate_limit_per_minute=3)
    payload = {"email": "ghost@example.com", "password": PASSWORD}
    for _ in range(3):
        assert client.post("/api/v1/auth/login", json=payload).status_code == 401
    response = client.post("/api/v1/auth/login", json=payload)
    error = assert_error(response, 429, "rate_limited")
    assert int(response.headers["Retry-After"]) >= 1
    assert error["details"]["retry_after"] >= 1  # type: ignore[index]


def test_unknown_route_uses_error_format(client: TestClient) -> None:
    assert_error(client.get("/api/v1/nope"), 404, "not_found")
    assert_error(client.delete("/api/v1/auth/me"), 405, "method_not_allowed")


def test_health(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["database"] == "ok"


def test_health_reports_unavailable_database(make_client: Callable[..., TestClient]) -> None:
    client = make_client(database_url="postgresql+psycopg://nobody:nothing@127.0.0.1:1/none")
    response = client.get("/api/v1/health")
    assert response.status_code == 503
    assert response.json()["database"] == "unavailable"


def test_openapi_available(client: TestClient) -> None:
    schema = client.get("/api/v1/openapi.json").json()
    assert "/api/v1/auth/register" in schema["paths"]


def test_unhandled_error_hides_details(
    clean_database: str, make_settings: Callable[..., Settings]
) -> None:
    app = create_app(make_settings())

    @app.get("/api/v1/boom")
    def boom() -> None:
        raise RuntimeError("secret internals")

    response = TestClient(app, raise_server_exceptions=False).get("/api/v1/boom")
    app.state.engine.dispose()
    assert_error(response, 500, "internal_error")
    assert "secret internals" not in response.text


def test_password_whitespace_is_significant(client: TestClient) -> None:
    register(client, " owner@example.com ", password=" spaced password ")
    ok = {"email": "owner@example.com", "password": " spaced password "}
    stripped = {"email": "owner@example.com", "password": "spaced password"}
    assert client.post("/api/v1/auth/login", json=ok).status_code == 200
    assert_error(client.post("/api/v1/auth/login", json=stripped), 401, "invalid_credentials")


def test_cors_headers_on_errors(
    clean_database: str, make_settings: Callable[..., Settings]
) -> None:
    app = create_app(make_settings(cors_origins="https://app.example"))

    @app.get("/api/v1/boom")
    def boom() -> None:
        raise RuntimeError("boom")

    client = TestClient(app, raise_server_exceptions=False)
    origin = {"Origin": "https://app.example"}
    for response in (
        client.get("/api/v1/boom", headers=origin),
        client.get("/api/v1/auth/me", headers=origin),
    ):
        assert response.headers["access-control-allow-origin"] == "https://app.example"
        assert "error" in response.json()
    app.state.engine.dispose()


def test_private_targets_warning_logged(
    make_client: Callable[..., TestClient], caplog: pytest.LogCaptureFixture
) -> None:
    make_client(allow_private_targets=True)
    assert "ALLOW_PRIVATE_TARGETS=true" in caplog.text
