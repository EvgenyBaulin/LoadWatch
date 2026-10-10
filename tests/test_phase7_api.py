"""API каналов, триггера из CI, меток и алертов; еженедельная сводка; алерты в runner."""

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from loadwatch.config import Settings
from loadwatch.db import utcnow
from loadwatch.models import RunMetrics, Site, TestRun, User
from loadwatch.services.channel_service import ChannelService
from loadwatch.services.digest_service import DigestService, is_due
from tests.conftest import FakeDns, RecordingNotifiers, auth_headers, register
from tests.test_auth_api import assert_error

KEY = Fernet.generate_key().decode()


@pytest.fixture
def api(make_client: Callable[..., TestClient]) -> TestClient:
    return make_client(secrets_encryption_key=KEY, site_cooldown_sec=0)


@pytest.fixture
def owner(api: TestClient) -> dict[str, str]:
    return auth_headers(register(api, "owner@example.com", locale="ru")["access_token"])


@pytest.fixture
def stranger(api: TestClient) -> dict[str, str]:
    return auth_headers(register(api, "stranger@example.com")["access_token"])


def recorder(notifications: list[RecordingNotifiers]) -> RecordingNotifiers:
    return notifications[-1]


# --- каналы -----------------------------------------------------------------------


def test_channel_crud_and_masking(api: TestClient, owner: dict[str, str]) -> None:
    url = "/api/v1/users/me/channels"
    slack = api.post(
        url,
        json={
            "type": "slack",
            "name": "Ops",
            "config": {"webhook_url": "https://hooks.slack.com/services/T/B/SECRET"},
        },
        headers=owner,
    )
    assert slack.status_code == 201, slack.text
    assert slack.json()["config"] == {"host": "hooks.slack.com"}
    assert "SECRET" not in slack.text
    assert len(slack.json()["events"]) == 6  # по умолчанию — все события
    webhook = api.post(
        url,
        json={
            "type": "webhook",
            "name": "CI",
            "config": {"url": "https://hooks.example/x"},
            "events": ["regression"],
        },
        headers=owner,
    ).json()
    assert len(webhook["signing_secret"]) == 64  # показывается один раз
    listed = api.get(url, headers=owner)
    assert [c["name"] for c in listed.json()] == ["Ops", "CI"]
    assert all(c["signing_secret"] is None for c in listed.json())
    assert webhook["signing_secret"] not in listed.text
    # изменение без секретных полей — прежний URL остаётся
    updated = api.put(
        f"{url}/{slack.json()['id']}",
        json={
            "type": "slack",
            "name": "Ops 2",
            "config": {},
            "events": ["recovery"],
            "enabled": False,
        },
        headers=owner,
    ).json()
    assert (updated["name"], updated["events"], updated["enabled"]) == (
        "Ops 2",
        ["recovery"],
        False,
    )
    wrong_type = api.put(
        f"{url}/{slack.json()['id']}",
        json={"type": "discord", "name": "x", "config": {}},
        headers=owner,
    )
    assert_error(wrong_type, 422, "invalid_channel")
    assert api.delete(f"{url}/{slack.json()['id']}", headers=owner).status_code == 204
    assert len(api.get(url, headers=owner).json()) == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "telegram", "name": "t", "config": {"chat_id": "abc"}},
        {"type": "email", "name": "e", "config": {"email": "nope"}},
        {"type": "slack", "name": "s", "config": {"webhook_url": "https://evil.example/x"}},
        {"type": "webhook", "name": "w", "config": {"url": "http://127.0.0.1/hook"}},
    ],
)
def test_invalid_channels(api: TestClient, owner: dict[str, str], payload: dict[str, Any]) -> None:
    assert_error(
        api.post("/api/v1/users/me/channels", json=payload, headers=owner), 422, "invalid_channel"
    )


def test_webhook_channels_need_encryption_key(make_client: Callable[..., TestClient]) -> None:
    client = make_client()
    headers = auth_headers(register(client)["access_token"])
    payload = {
        "type": "discord",
        "name": "d",
        "config": {"webhook_url": "https://discord.com/api/webhooks/1/x"},
    }
    assert_error(
        client.post("/api/v1/users/me/channels", json=payload, headers=headers),
        503,
        "secrets_unavailable",
    )
    ok = {"type": "telegram", "name": "t", "config": {"chat_id": "42"}}
    assert client.post("/api/v1/users/me/channels", json=ok, headers=headers).status_code == 201


def test_test_button(
    api: TestClient, owner: dict[str, str], notifications: list[RecordingNotifiers]
) -> None:
    channel = api.post(
        "/api/v1/users/me/channels",
        json={"type": "telegram", "name": "Мой", "config": {"chat_id": "42"}},
        headers=owner,
    ).json()
    result = api.post(f"/api/v1/users/me/channels/{channel['id']}/test", headers=owner).json()
    assert result == {"ok": True, "error": None}
    channel_type, message, config = recorder(notifications).sent[0]
    assert channel_type == "telegram" and config.public == {"chat_id": "42"}
    assert message.title == "Тестовое уведомление LoadWatch"  # на языке пользователя
    recorder(notifications).fail_with = "Telegram answered HTTP 403 Forbidden"
    failed = api.post(f"/api/v1/users/me/channels/{channel['id']}/test", headers=owner).json()
    assert failed == {"ok": False, "error": "Telegram answered HTTP 403 Forbidden"}


def test_channel_isolation(
    api: TestClient, owner: dict[str, str], stranger: dict[str, str]
) -> None:
    channel = api.post(
        "/api/v1/users/me/channels",
        json={"type": "telegram", "name": "t", "config": {"chat_id": "1"}},
        headers=owner,
    ).json()
    url = f"/api/v1/users/me/channels/{channel['id']}"
    assert api.get("/api/v1/users/me/channels", headers=stranger).json() == []
    assert_error(api.delete(url, headers=stranger), 404, "not_found")
    assert_error(api.post(f"{url}/test", headers=stranger), 404, "not_found")
    assert_error(
        api.put(
            url,
            json={"type": "telegram", "name": "x", "config": {"chat_id": "2"}},
            headers=stranger,
        ),
        404,
        "not_found",
    )


def test_digest_preferences(api: TestClient, owner: dict[str, str]) -> None:
    me = api.get("/api/v1/auth/me", headers=owner).json()
    assert (me["digest_enabled"], me["digest_weekday"], me["digest_hour"]) == (True, 0, 9)
    updated = api.put(
        "/api/v1/users/me/preferences", json={"digest_weekday": 4, "digest_hour": 18}, headers=owner
    ).json()
    assert (updated["digest_weekday"], updated["digest_hour"]) == (4, 18)
    bad = api.put("/api/v1/users/me/preferences", json={"digest_hour": 24}, headers=owner)
    assert_error(bad, 422, "validation_error")


# --- триггер из CI, метки, алерты --------------------------------------------------------


def make_site(
    api: TestClient, headers: dict[str, str], *, scenarios: int = 1
) -> tuple[str, list[str]]:
    site_id = api.post(
        "/api/v1/sites", json={"url": "https://shop.example"}, headers=headers
    ).json()["id"]
    api.post(f"/api/v1/sites/{site_id}/verify", json={"method": "dns"}, headers=headers)
    ids = []
    for index in range(scenarios):
        ids.append(
            api.post(
                f"/api/v1/sites/{site_id}/scenarios",
                json={
                    "name": f"s{index}",
                    "load_profile": {"type": "constant", "rate": 1, "duration_sec": 1},
                    "flow": [{"name": "a", "path": "/"}],
                },
                headers=headers,
            ).json()["id"]
        )
    return site_id, ids


def test_trigger_from_ci_creates_run_and_deploy_mark(
    api: TestClient, owner: dict[str, str]
) -> None:
    site_id, [scenario_id] = make_site(api, owner)
    issued = api.post(f"/api/v1/sites/{site_id}/trigger-token", headers=owner).json()
    token = issued["token"]
    assert token.startswith("lwt_") and "Authorization: Bearer" in issued["example"]
    hook = f"/api/v1/hooks/sites/{site_id}/run"
    response = api.post(
        hook, json={"label": "v1.4.2"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["run"]["source"], body["run"]["label"], body["run"]["scenario_id"]) == (
        "hook",
        "v1.4.2",
        scenario_id,
    )
    assert (
        body["annotation"]["kind"] == "deploy" and body["annotation"]["run_id"] == body["run"]["id"]
    )
    marks = api.get(f"/api/v1/sites/{site_id}/annotations", headers=owner).json()
    assert [(m["kind"], m["label"]) for m in marks] == [("deploy", "v1.4.2")]
    # второй вызов, пока первый тест в очереди: тест не ставится, но метка деплоя сохраняется
    again = api.post(hook, json={"label": "v1.4.3"}, headers={"Authorization": f"Bearer {token}"})
    assert_error(again, 409, "run_in_progress")
    assert len(api.get(f"/api/v1/sites/{site_id}/annotations", headers=owner).json()) == 2
    # перевыпуск — старый токен больше не работает
    api.post(f"/api/v1/sites/{site_id}/trigger-token", headers=owner)
    assert_error(
        api.post(hook, headers={"Authorization": f"Bearer {token}"}), 401, "invalid_trigger_token"
    )


def test_trigger_auth_errors(api: TestClient, owner: dict[str, str]) -> None:
    site_id, _ = make_site(api, owner)
    hook = f"/api/v1/hooks/sites/{site_id}/run"
    assert_error(api.post(hook), 401, "invalid_trigger_token")  # токен не выпускался
    api.post(f"/api/v1/sites/{site_id}/trigger-token", headers=owner)
    assert_error(
        api.post(hook, headers={"Authorization": "Bearer lwt_wrong"}), 401, "invalid_trigger_token"
    )
    assert_error(api.post(hook), 401, "invalid_trigger_token")
    missing = "/api/v1/hooks/sites/00000000-0000-0000-0000-000000000000/run"
    assert_error(
        api.post(missing, headers={"Authorization": "Bearer lwt_x"}), 401, "invalid_trigger_token"
    )


def test_trigger_scenario_choice(api: TestClient, owner: dict[str, str]) -> None:
    site_id, scenario_ids = make_site(api, owner, scenarios=2)
    token = api.post(f"/api/v1/sites/{site_id}/trigger-token", headers=owner).json()["token"]
    auth = {"Authorization": f"Bearer {token}"}
    hook = f"/api/v1/hooks/sites/{site_id}/run"
    assert_error(api.post(hook, headers=auth), 422, "scenario_required")
    api.put(
        f"/api/v1/sites/{site_id}/schedule",
        json={"scenario_id": scenario_ids[1], "cron": "0 9 * * *"},
        headers=owner,
    )
    assert api.post(hook, headers=auth).json()["run"]["scenario_id"] == scenario_ids[1]


def test_hook_rate_limits(make_client: Callable[..., TestClient]) -> None:
    client = make_client(hook_rate_limit_per_minute=1, site_cooldown_sec=0)
    headers = auth_headers(register(client)["access_token"])
    site_id, _ = make_site(client, headers)
    token = client.post(f"/api/v1/sites/{site_id}/trigger-token", headers=headers).json()["token"]
    hook = f"/api/v1/hooks/sites/{site_id}/run"
    auth = {"Authorization": f"Bearer {token}"}
    assert client.post(hook, headers=auth).status_code == 201
    assert_error(client.post(hook, headers=auth), 429, "rate_limited")  # лимит сайта
    # IP-лимит втрое больше лимита сайта: два вызова выше + один мусорный — дальше 429
    garbage = "/api/v1/hooks/sites/00000000-0000-0000-0000-000000000000/run"
    assert_error(client.post(garbage), 401, "invalid_trigger_token")
    assert_error(client.post(hook), 429, "rate_limited")


def test_annotations_and_alerts_api(
    api: TestClient, owner: dict[str, str], stranger: dict[str, str]
) -> None:
    site_id, _ = make_site(api, owner)
    url = f"/api/v1/sites/{site_id}/annotations"
    mark = api.post(url, json={"label": "Включили CDN"}, headers=owner).json()
    assert mark["kind"] == "comment"
    old = api.post(url, json={"label": "старое", "at": "2020-01-01T00:00:00Z"}, headers=owner)
    assert old.status_code == 201
    assert [m["label"] for m in api.get(url, headers=owner).json()] == ["Включили CDN"]
    assert_error(api.get(url, headers=stranger), 404, "not_found")
    assert_error(api.delete(f"{url}/{mark['id']}", headers=stranger), 404, "not_found")
    assert api.delete(f"{url}/{mark['id']}", headers=owner).status_code == 204
    with api.app.state.engine.begin() as connection:  # type: ignore[attr-defined]
        connection.execute(
            text(
                "INSERT INTO alerts (id, site_id, type, status, opened_at, created_at, updated_at)"
                " VALUES (gen_random_uuid(), :site, 'regression', 'open', now(), now(), now())"
            ),
            {"site": site_id},
        )
    alerts = api.get(f"/api/v1/sites/{site_id}/alerts?status=open", headers=owner).json()
    assert [a["type"] for a in alerts] == ["regression"]
    assert api.get(f"/api/v1/sites/{site_id}/alerts?status=resolved", headers=owner).json() == []
    assert_error(api.get(f"/api/v1/sites/{site_id}/alerts", headers=stranger), 404, "not_found")


def test_reverification_resolves_ownership_alert(
    api: TestClient, owner: dict[str, str], notifications: list[RecordingNotifiers]
) -> None:
    site_id, _ = make_site(api, owner)
    api.post(
        "/api/v1/users/me/channels",
        json={"type": "telegram", "name": "t", "config": {"chat_id": "1"}},
        headers=owner,
    )
    with api.app.state.engine.begin() as connection:  # type: ignore[attr-defined]
        connection.execute(
            text(
                "INSERT INTO alerts (id, site_id, type, status, opened_at, created_at, updated_at)"
                " VALUES (gen_random_uuid(), :site, 'site_unverified', 'open', now(), now(), now())"
            ),
            {"site": site_id},
        )
    api.post(f"/api/v1/sites/{site_id}/verify", json={"method": "dns"}, headers=owner)
    assert api.get(f"/api/v1/sites/{site_id}/alerts?status=open", headers=owner).json() == []
    assert recorder(notifications).sent[-1][1].event == "recovery"


# --- еженедельная сводка ----------------------------------------------------------------


@pytest.fixture
def session(clean_database: str) -> Iterator[Session]:
    engine = create_engine(clean_database)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


def test_digest_timing_in_user_time_zone() -> None:
    user = User(
        email="a@b.c",
        password_hash="x",
        timezone="Europe/Moscow",
        digest_enabled=True,
        digest_weekday=0,
        digest_hour=9,
    )
    monday_9_msk = datetime(2026, 10, 12, 6, 30, tzinfo=UTC)  # понедельник 09:30 по Москве
    assert is_due(user, monday_9_msk)
    assert is_due(user, monday_9_msk + timedelta(hours=5))  # повтор в тот же день
    assert not is_due(user, monday_9_msk - timedelta(hours=1))  # 08:30 — ещё рано
    assert not is_due(user, monday_9_msk + timedelta(days=1))  # вторник
    user.last_digest_at = monday_9_msk - timedelta(days=2)
    assert not is_due(user, monday_9_msk)  # недавно уже отправляли
    user.last_digest_at = None
    user.digest_enabled = False
    assert not is_due(user, monday_9_msk)


def add_week_run(
    session: Session,
    site: Site,
    *,
    days_ago: float,
    p95: float,
    slo: bool | None,
    ceiling: float | None = None,
) -> None:
    started = utcnow() - timedelta(days=days_ago)
    run = TestRun(
        site_id=site.id,
        status="finished",
        started_at=started,
        finished_at=started,
        queued_at=started,
        slo_met=slo,
    )
    session.add(run)
    session.flush()
    session.add(
        RunMetrics(
            run_id=run.id,
            duration_sec=1,
            requests=100,
            errors=1,
            error_rate=0.01,
            rps=10,
            iterations_per_sec=10,
            latency_avg_ms=1,
            latency_p50_ms=1,
            latency_p90_ms=1,
            latency_p95_ms=p95,
            latency_p99_ms=1,
            latency_max_ms=1,
            ceiling_rps=ceiling,
            summary={},
        )
    )
    session.commit()


def test_digest_content_and_sending(
    session: Session, make_settings: Callable[..., Settings]
) -> None:
    settings = make_settings(public_base_url="https://lw.example")
    user = User(
        email="o@example.com",
        password_hash="x",
        locale="ru",
        digest_weekday=utcnow().weekday(),
        digest_hour=utcnow().hour,
    )
    session.add(user)
    session.flush()
    shop = Site(owner_id=user.id, name="Магазин", url="https://shop.example")
    blog = Site(owner_id=user.id, name="Блог", url="https://blog.example")
    session.add_all([shop, blog])
    session.flush()
    from loadwatch.models import NotificationChannel

    session.add(
        NotificationChannel(
            user_id=user.id,
            type="email",
            name="e",
            config={"email": "o@example.com"},
            events=["digest"],
        )
    )
    session.commit()
    for days, p95, slo in (
        (1, 120, True),
        (2, 110, False),
        (3, 130, True),
        (9, 100, True),
        (10, 100, True),
    ):
        add_week_run(
            session, shop, days_ago=days, p95=p95, slo=slo, ceiling=80 if days == 1 else None
        )
    notifiers = RecordingNotifiers(settings, FakeDns())
    service = DigestService(session, settings, ChannelService(session, settings, notifiers))  # type: ignore[arg-type]
    week = service.site_week(shop, now=utcnow())
    assert (week.runs, week.p95_median, week.p95_change_pct, week.slo_pct, week.ceiling_rps) == (
        3,
        120,
        20.0,
        66.7,
        80,
    )
    assert service.send_due() == 1
    [(channel_type, message, _)] = notifiers.sent
    assert channel_type == "email" and message.title == "LoadWatch: сводка за неделю"
    assert message.lines == [
        "Блог: за неделю запусков не было.",
        "Магазин: запусков 3, медиана p95 120 мс (+20%), в SLO 66.7%, ошибки 1%, потолок 80 RPS.",
    ]
    assert message.link == "https://lw.example/sites"
    assert service.send_due() == 0  # повторно — не раньше чем через 6 дней


def test_digest_cli(monkeypatch: pytest.MonkeyPatch, clean_database: str) -> None:
    from loadwatch import config
    from loadwatch.digest.__main__ import main
    from tests.conftest import TEST_JWT_SECRET

    monkeypatch.setenv("DATABASE_URL", clean_database)
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    config.get_settings.cache_clear()
    try:
        assert main() == 0
    finally:
        config.get_settings.cache_clear()


# --- алерты из runner ---------------------------------------------------------------------


def test_runner_sends_alerts(clean_database: str, make_settings: Callable[..., Settings]) -> None:
    from loadwatch.models import NotificationChannel, Scenario
    from loadwatch.runner.cli import tick
    from loadwatch.verification import VerifierRegistry

    settings = make_settings(alert_consecutive_runs=1)
    engine = create_engine(clean_database)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as session:
        user = User(email="o@example.com", password_hash="x")
        session.add(user)
        session.flush()
        site = Site(
            owner_id=user.id,
            name="s",
            url="https://10.0.0.1",
            verification_status="verified",
            last_verified_at=utcnow(),
        )
        session.add(site)
        session.flush()
        scenario = Scenario(
            site_id=site.id,
            name="s",
            load_profile={"type": "constant", "rate": 1, "duration_sec": 1},
        )
        session.add(scenario)
        session.add(
            NotificationChannel(
                user_id=user.id,
                type="telegram",
                name="t",
                config={"chat_id": "1"},
                events=["run_failed"],
            )
        )
        session.flush()
        session.add(TestRun(site_id=site.id, scenario_id=scenario.id))
        session.commit()
    notifiers = RecordingNotifiers(settings, FakeDns())
    # Адрес сайта внутренний и флаг ALLOW_PRIVATE_TARGETS выключен — запуск упадёт.
    report = tick(factory, settings, verifiers=VerifierRegistry([]), notifiers=notifiers)  # type: ignore[arg-type]
    engine.dispose()
    assert report.notifications == ["run_failed"]
    assert notifiers.sent[0][1].event == "run_failed"
