"""Демо-цель: все эндпоинты через TestClient, без сети и без реального времени."""

from __future__ import annotations

import random
import time

import pytest
from fastapi.testclient import TestClient

from demo_target import DemoConfig, create_app
from demo_target.app import MAX_OVERLOAD_DELAY_MS, MAX_SESSIONS, CapacityWindow


class FakeClock:
    """Часы, которые двигает тест."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make_client(config: DemoConfig | None = None, **kwargs: object) -> TestClient:
    return TestClient(create_app(config or DemoConfig(), **kwargs))  # type: ignore[arg-type]


def login(client: TestClient) -> str:
    response = client.post("/api/login", json={"username": "demo", "password": "demo-password"})
    assert response.status_code == 200
    token: str = response.json()["data"]["token"]
    return token


def test_config_from_env() -> None:
    config = DemoConfig.from_env(
        {
            "DEMO_LATENCY_MS": "15",
            "DEMO_CAPACITY_RPS": "7",
            "DEMO_USERNAME": "alice",
            "DEMO_PASSWORD": "pw",
            "DEMO_VERIFY_TOKEN": "tok",
            "DEMO_ADMIN_TOKEN": "",
        }
    )
    assert config == DemoConfig(15, 7, "alice", "pw", "tok", None)
    assert DemoConfig.from_env({}) == DemoConfig()


def test_index_and_health() -> None:
    client = make_client()
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "LoadWatch demo target" in response.text
    assert client.get("/health").json() == {"status": "ok"}


def test_base_latency_applies_to_index() -> None:
    client = make_client(DemoConfig(latency_ms=120))
    started = time.perf_counter()
    assert client.get("/").status_code == 200
    assert time.perf_counter() - started >= 0.11


def test_delay_param() -> None:
    client = make_client()
    started = time.perf_counter()
    response = client.get("/delay", params={"ms": 100})
    assert response.status_code == 200
    assert time.perf_counter() - started >= 0.09
    assert client.get("/delay", params={"ms": -1}).status_code == 422
    assert client.get("/delay", params={"ms": 10**6}).status_code == 422


def test_login_and_dashboard_flow() -> None:
    client = make_client()
    token = login(client)
    assert client.cookies.get("session") == token
    client.cookies.clear()  # страница требует именно Bearer, cookie недостаточно
    response = client.get("/dashboard", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert "Welcome" in response.text


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer nope"}, {"Authorization": "Basic abc"}, {"Authorization": ""}],
)
def test_dashboard_requires_valid_bearer(headers: dict[str, str]) -> None:
    client = make_client()
    login(client)
    client.cookies.clear()
    response = client.get("/dashboard", headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_login_wrong_credentials() -> None:
    client = make_client(DemoConfig(username="u", password="secret-pw"))
    bad = client.post("/api/login", json={"username": "u", "password": "wrong"})
    assert bad.status_code == 401
    assert "set-cookie" not in bad.headers
    assert client.post("/api/login", json={"username": "u"}).status_code == 422
    ok = client.post("/api/login", json={"username": "u", "password": "secret-pw"})
    assert ok.status_code == 200


def test_sessions_are_bounded() -> None:
    app = create_app(DemoConfig())
    state = app.state.demo
    first = state.add_session()
    for _ in range(MAX_SESSIONS):
        state.add_session()
    assert len(state.sessions) == MAX_SESSIONS
    assert first not in state.sessions


def test_verification_file() -> None:
    client = make_client(DemoConfig(verify_token="abc123"))
    response = client.get("/.well-known/loadwatch-abc123.txt")
    assert response.status_code == 200
    assert response.text == "abc123"
    assert client.get("/.well-known/loadwatch-other.txt").status_code == 404


def test_verification_file_disabled_without_token() -> None:
    client = make_client(DemoConfig(verify_token=None))
    assert client.get("/.well-known/loadwatch-abc123.txt").status_code == 404


def test_capacity_limit_returns_503_above_capacity() -> None:
    clock = FakeClock()
    client = make_client(DemoConfig(capacity_rps=3), clock=clock)
    statuses = [client.get("/limited").status_code for _ in range(5)]
    assert statuses == [200, 200, 200, 503, 503]
    overloaded = client.get("/limited")
    assert overloaded.headers["retry-after"] == "1"
    clock.now += 1.0  # окно сдвинулось — сайт снова отвечает
    assert client.get("/limited").status_code == 200


def test_capacity_window_slides() -> None:
    clock = FakeClock()
    window = CapacityWindow(capacity=3, clock=clock)
    assert [window.arrive() for _ in range(3)] == [(True, 1), (True, 2), (True, 3)]
    clock.now += 0.5
    assert window.arrive() == (False, 4)
    clock.now += 0.6  # первые три вышли из окна, четвёртый (отказ) ещё в нём
    assert window.arrive() == (True, 2)


def test_sustained_overload_still_serves_capacity() -> None:
    clock = FakeClock()
    window = CapacityWindow(capacity=10, clock=clock)
    served = 0
    for _ in range(30):  # 30 секунд по 16 запросов в секунду при ёмкости 10
        for _ in range(16):
            served += window.arrive()[0]
            clock.now += 0.0625  # 1/16 с точно представима в float
    assert served == 300  # каждую секунду обслужено ровно 10, остальным — отказ


def test_capacity_window_memory_is_bounded() -> None:
    window = CapacityWindow(capacity=2, clock=FakeClock())
    results = [window.arrive() for _ in range(100)]
    assert results[-1] == (False, 41)  # 40 хранится + текущий
    assert len(window._arrivals) == 40
    assert len(window._accepted) == 2


def test_overload_delay_grows_with_overload() -> None:
    clock = FakeClock()
    client = make_client(DemoConfig(capacity_rps=1), clock=clock)
    assert client.get("/limited").status_code == 200
    started = time.perf_counter()
    assert client.get("/limited").status_code == 503  # перегрузка 100 % → 200 мс
    small = time.perf_counter() - started
    for _ in range(3):
        client.get("/limited")
    started = time.perf_counter()
    assert client.get("/limited").status_code == 503  # перегрузка 500 % → 1000 мс
    large = time.perf_counter() - started
    assert 0.18 <= small < large
    assert large >= 0.9
    assert MAX_OVERLOAD_DELAY_MS == 2000


def test_slow_latency_range(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("demo_target.app.asyncio.sleep", fake_sleep)
    client = make_client(rng=random.Random(1))  # noqa: S311
    for _ in range(20):
        assert client.get("/slow").status_code == 200
    assert len(slept) == 20
    assert all(0.5 <= value <= 1.5 for value in slept)


def test_flaky_error_rate() -> None:
    client = make_client(rng=random.Random(42))  # noqa: S311
    assert all(client.get("/flaky", params={"error_rate": 0}).status_code == 200 for _ in range(20))
    assert all(client.get("/flaky", params={"error_rate": 1}).status_code == 500 for _ in range(20))
    errors = sum(client.get("/flaky").status_code == 500 for _ in range(400))
    assert 20 <= errors <= 65  # ≈10 %
    assert client.get("/flaky", params={"error_rate": 2}).status_code == 422


def test_admin_latency_requires_token() -> None:
    client = make_client(DemoConfig(admin_token="admin-secret"))
    assert client.post("/admin/latency", json={"ms": 50}).status_code == 403
    wrong = client.post("/admin/latency", json={"ms": 50}, headers={"X-Demo-Admin": "nope"})
    assert wrong.status_code == 403
    ok = client.post("/admin/latency", json={"ms": 50}, headers={"X-Demo-Admin": "admin-secret"})
    assert ok.status_code == 200
    assert ok.json() == {"latency_ms": 50}
    assert client.get("/admin/latency").json() == {"latency_ms": 50}
    started = time.perf_counter()
    client.get("/")
    assert time.perf_counter() - started >= 0.045
    invalid = client.post(
        "/admin/latency", json={"ms": -5}, headers={"X-Demo-Admin": "admin-secret"}
    )
    assert invalid.status_code == 422


def test_admin_latency_disabled_without_token() -> None:
    client = make_client(DemoConfig(admin_token=None))
    response = client.post("/admin/latency", json={"ms": 50}, headers={"X-Demo-Admin": "x"})
    assert response.status_code == 404
