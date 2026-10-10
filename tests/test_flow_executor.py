"""FlowExecutor против настоящего сервера: логин → токен → защищённая страница."""

import json
from collections.abc import Iterator
from typing import Any

import pytest

from loadwatch.flow import Flow, FlowExecutor, RequestStep
from loadwatch.runner.http import PinnedRequester, new_load_client
from loadwatch.security.ssrf import parse_target_url, resolve_target
from tests import target_app
from tests.test_load_runner import run_load

SECRETS = {"PASSWORD": "s3cret-pass"}


@pytest.fixture(scope="module")
def target_port() -> Iterator[int]:
    with target_app.serve() as port:
        yield port


def flow(*steps: dict[str, Any]) -> Flow:
    return Flow(steps=[RequestStep.model_validate(step) for step in steps])


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


async def run_once(port: int, executor: FlowExecutor) -> list[Any]:
    target = parse_target_url(f"http://flow.test:{port}", allow_private=True)
    pinned = resolve_target(target, resolver=lambda h, p: ["127.0.0.1"], allow_private=True)
    async with new_load_client(max_connections=1, timeout_sec=5) as client:
        requester = PinnedRequester(client, pinned, dns_ms=0.5, max_response_bytes=256 * 1024)
        return await executor.run(requester)


async def test_login_then_protected_page(target_port: int) -> None:
    executor = FlowExecutor(flow({"name": "home", "path": "/"}, LOGIN, DASHBOARD), SECRETS)
    results = await run_once(target_port, executor)
    assert [(r.name, r.status, r.ok) for r in results] == [
        ("home", 200, True),
        ("login", 200, True),
        ("dashboard", 200, True),
    ]
    assert results[1].extracted["token"].startswith("tok-")
    assert results[2].timings["ttfb"] > 0


async def test_cookies_are_kept_between_steps(target_port: int) -> None:
    steps = flow(LOGIN, {**DASHBOARD, "assert_contains": "session=sess-tok-"})
    results = await run_once(target_port, FlowExecutor(steps, SECRETS))
    assert all(result.ok for result in results)


async def test_wrong_secret_fails_step_and_stops(target_port: int) -> None:
    executor = FlowExecutor(flow(LOGIN, DASHBOARD), {"PASSWORD": "wrong"})
    results = await run_once(target_port, executor)
    assert len(results) == 1
    assert (results[0].status, results[0].error) == (401, "http_401")


async def test_missing_secret(target_port: int) -> None:
    results = await run_once(target_port, FlowExecutor(flow(LOGIN), {}))
    assert results[0].error == "missing_secret"
    assert results[0].status is None


async def test_assert_and_extract_failures(target_port: int) -> None:
    results = await run_once(
        target_port, FlowExecutor(flow({"name": "a", "path": "/", "assert_contains": "nope"}), {})
    )
    assert results[0].error == "assert_failed"
    extract_rule = [{"var": "x", "source": "json", "expr": "a"}]
    results = await run_once(
        target_port, FlowExecutor(flow({"name": "b", "path": "/", "extract": extract_rule}), {})
    )
    assert results[0].error == "extract_failed"
    assert results[0].ok is False


async def test_request_is_built_from_variables(target_port: int) -> None:
    echo = {
        "name": "echo",
        "method": "PATCH",
        "path": "/echo?q={{q}}",
        "headers": {"X-Trace": "{{uuid}}", "Cookie": "user=1"},
        "body": {"type": "form", "content": {"user": "{{secret.USER}}", "q": "{{q}}"}},
    }
    first = {
        "name": "first",
        "path": "/echo?v=a b&c",
        "extract": [{"var": "q", "source": "json", "expr": "query"}],
    }
    results = await run_once(target_port, FlowExecutor(flow(first, echo), {"USER": "ann"}))
    assert results[1].ok, results[1]
    # Секрет в итоге шага замаскирован, а путь содержит закодированное значение.
    assert results[1].path.startswith("/echo?q=v%3Da")


async def test_secrets_reach_server_but_not_results(target_port: int) -> None:
    steps = flow(
        {
            "name": "login",
            "method": "POST",
            "path": "/api/login",
            "body": {
                "type": "json",
                "content": {"username": "demo", "password": "{{secret.PASSWORD}}"},
            },
        },
        {
            "name": "echo",
            "method": "POST",
            "path": "/echo",
            "headers": {"Authorization": "Token {{secret.PASSWORD}}"},
            "body": {"type": "raw", "content": "hello {{secret.PASSWORD}}"},
            "extract": [{"var": "body", "source": "json", "expr": "body"}],
        },
    )

    results = await run_once(target_port, FlowExecutor(steps, SECRETS))
    assert results[1].ok
    # Сервер получил настоящий секрет, а в итог шага он попал только замаскированным.
    assert results[1].extracted == {"body": "hello ***"}
    assert "s3cret" not in repr(results)


async def test_flow_under_load_records_per_step_metrics(target_port: int) -> None:
    executor = FlowExecutor(flow(LOGIN, DASHBOARD), SECRETS)
    outcome = await run_load(target_port, rate=10, duration=1.5, iteration=executor)
    summary = outcome.collector.summary(outcome.duration_sec)
    steps = {step["name"]: step for step in summary["steps"]}
    assert set(steps) == {"login", "dashboard"}
    assert steps["login"]["requests"] == steps["dashboard"]["requests"] > 10
    assert summary["error_rate"] == 0
    assert summary["iterations"]["ok"] == summary["iterations"]["total"]


async def test_virtual_users_do_not_share_cookies(target_port: int) -> None:
    """Второй пользователь без логина не должен получить cookie первого."""
    login_only = FlowExecutor(flow(LOGIN), SECRETS)
    await run_once(target_port, login_only)
    anonymous = flow(
        {
            "name": "echo",
            "path": "/echo",
            "extract": [{"var": "cookie", "source": "json", "expr": "headers.cookie"}],
        }
    )
    results = await run_once(target_port, FlowExecutor(anonymous, {}))
    # Заголовка Cookie в запросе не было — извлекать нечего.
    assert results[0].error == "extract_failed"
    assert "headers.cookie" in (results[0].detail or "")


async def test_think_time(target_port: int) -> None:
    import time

    steps = flow({"name": "a", "path": "/", "think_ms": 300}, {"name": "b", "path": "/"})
    started = time.perf_counter()
    await run_once(target_port, FlowExecutor(steps, {}))
    assert time.perf_counter() - started >= 0.3


def test_json_dump_of_secret_free_params() -> None:
    assert "s3cret" not in json.dumps(flow(LOGIN, DASHBOARD).model_dump())


async def test_secret_only_in_assert_is_reported(target_port: int) -> None:
    steps = flow({"name": "a", "path": "/", "assert_contains": "{{secret.NOPE}}"})
    results = await run_once(target_port, FlowExecutor(steps, {}))
    assert results[0].error == "missing_secret"


async def test_non_ascii_rendered_header(target_port: int) -> None:
    steps = flow({"name": "a", "path": "/", "headers": {"X-User": "{{secret.NAME}}"}})
    results = await run_once(target_port, FlowExecutor(steps, {"NAME": "Иван"}))
    assert results[0].error == "invalid_header"
    assert "Иван" not in repr(results)


async def test_url_encoded_secret_is_masked_in_path(target_port: int) -> None:
    steps = flow({"name": "a", "path": "/echo?p={{secret.P}}"})
    results = await run_once(target_port, FlowExecutor(steps, {"P": "p@ss word"}))
    assert results[0].ok
    assert results[0].path == "/echo?p=***"
