"""Экспорт CSV и PDF (секретов нет, кириллица читается), очистка по сроку хранения."""

import csv
import io
import json
import uuid
from collections.abc import Callable, Iterator
from datetime import timedelta

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pypdf import PdfReader
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from loadwatch.config import Settings
from loadwatch.db import utcnow
from loadwatch.maintenance import purge
from loadwatch.models import Alert, RunMetrics, Site, TestRun, User
from tests.conftest import auth_headers, register
from tests.test_auth_api import assert_error

KEY = Fernet.generate_key().decode()
SECRET = "Sup3r-Secret-Pass"

SUMMARY = {
    "duration_sec": 10.0,
    "requests": 200,
    "errors": 4,
    "error_rate": 0.02,
    "rps": 20.0,
    "latency_ms": {
        "min": 3.0,
        "avg": 40.0,
        "max": 900.0,
        "p50": 30.0,
        "p90": 80.0,
        "p95": 120.0,
        "p99": 400.0,
    },
    "timings": {
        "dns_ms": 0.5,
        "connect_ms": 1.2,
        "tls_ms": 0.0,
        "ttfb_ms": 35.0,
        "transfer_ms": 2.0,
        "ttfb_p95_ms": 100.0,
    },
    "apdex": 0.93,
    "status_codes": {"200": 196, "503": 4},
    "errors_top": [{"type": "http_503", "count": 4}],
    "steps": [
        {
            "name": '=HYPERLINK("http://evil")',
            "requests": 100,
            "errors": 0,
            "error_rate": 0.0,
            "rps": 10.0,
            "latency_ms": {
                "min": 1,
                "avg": 20,
                "max": 50,
                "p50": 18,
                "p90": 30,
                "p95": 40,
                "p99": 45,
            },
            "timings": {"ttfb_ms": 15.0},
            "apdex": 1.0,
        },
        {
            "name": "дашборд",
            "requests": 100,
            "errors": 4,
            "error_rate": 0.04,
            "rps": 10.0,
            "latency_ms": {
                "min": 5,
                "avg": 60,
                "max": 900,
                "p50": 40,
                "p90": 150,
                "p95": 200,
                "p99": 800,
            },
            "timings": {"ttfb_ms": 55.0},
            "apdex": 0.86,
        },
    ],
    "step_load": {
        "ceiling_rps": 18.5,
        "reason": "threshold_exceeded",
        "ceiling_is_lower_bound": False,
        "steps": [
            {
                "step": 0,
                "target_rate": 10,
                "rps": 10,
                "error_rate": 0,
                "p95_ms": 40,
                "passed": True,
            },
            {
                "step": 1,
                "target_rate": 20,
                "rps": 18.5,
                "error_rate": 0.2,
                "p95_ms": 300,
                "passed": False,
            },
        ],
    },
    "generator": {"saturated": False},
}
REGRESSION = {
    "detected": True,
    "baseline": {"p95_ms": 80},
    "baseline_runs": 5,
    "findings": [
        {
            "metric": "p95_ms",
            "current": 120.0,
            "baseline": 80.0,
            "change_pct": 50.0,
            "change_abs": 40.0,
            "baseline_runs": 5,
            "message_key": "regression.p95_ms",
        }
    ],
}


@pytest.fixture
def api(make_client: Callable[..., TestClient]) -> TestClient:
    return make_client(secrets_encryption_key=KEY)


@pytest.fixture
def owner(api: TestClient) -> dict[str, str]:
    return auth_headers(register(api, "owner@example.com", locale="ru")["access_token"])


def setup_run(
    api: TestClient, headers: dict[str, str], *, with_metrics: bool = True
) -> tuple[str, str]:
    site_id = api.post(
        "/api/v1/sites", json={"url": "https://shop.example", "name": "Магазин"}, headers=headers
    ).json()["id"]
    scenario = api.post(
        f"/api/v1/sites/{site_id}/scenarios",
        json={
            "name": "Вход и =cmd",
            "load_profile": {"type": "constant", "rate": 20, "duration_sec": 10},
            "flow": [
                {
                    "name": "login",
                    "method": "POST",
                    "path": "/login",
                    "body": {"type": "json", "content": {"password": "{{secret.PASSWORD}}"}},
                }
            ],
            "slo_p95_ms": 100,
        },
        headers=headers,
    ).json()
    api.put(
        f"/api/v1/scenarios/{scenario['id']}/secrets",
        json={"secrets": {"PASSWORD": SECRET}},
        headers=headers,
    )
    run_id = str(uuid.uuid4())
    started = utcnow() - timedelta(hours=1)
    with api.app.state.engine.begin() as connection:  # type: ignore[attr-defined]
        connection.execute(
            text(
                "INSERT INTO test_runs (id, site_id, scenario_id, status, source, label, params, generator_saturated,"
                " slo_met, regression, cancel_requested, queued_at, started_at, finished_at, created_at, updated_at)"
                " VALUES (:id, :site, :scenario, 'finished', 'hook', '+deploy', CAST(:params AS jsonb), false, false,"
                " CAST(:regression AS jsonb), false, :t, :t, :t, :t, :t)"
            ),
            {
                "id": run_id,
                "site": site_id,
                "scenario": scenario["id"],
                "t": started,
                "params": json.dumps(
                    {"profile": scenario["effective_load_profile"], "flow": scenario["flow"]}
                ),
                "regression": json.dumps(REGRESSION),
            },
        )
        if with_metrics:
            connection.execute(
                text(
                    "INSERT INTO run_metrics (run_id, duration_sec, requests, errors, error_rate, rps,"
                    " iterations_per_sec, latency_avg_ms, latency_p50_ms, latency_p90_ms, latency_p95_ms,"
                    " latency_p99_ms, latency_max_ms, apdex, ceiling_rps, summary, series) VALUES (:id, 10, 200, 4,"
                    " 0.02, 20, 20, 40, 30, 80, 120, 400, 900, 0.93, 18.5, CAST(:summary AS jsonb), CAST(:series AS jsonb))"
                ),
                {
                    "id": run_id,
                    "summary": json.dumps(SUMMARY),
                    "series": json.dumps(
                        [
                            {"t": i, "rps": 20, "p95_ms": 100 + i, "errors": 0, "error_rate": 0}
                            for i in range(10)
                        ]
                    ),
                },
            )
    return site_id, run_id


def test_run_csv(api: TestClient, owner: dict[str, str]) -> None:
    _, run_id = setup_run(api, owner)
    response = api.get(f"/api/v1/runs/{run_id}/export.csv", headers=owner)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert f'filename="loadwatch-run-{run_id}.csv"' in response.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert [row["step"] for row in rows] == [
        '\'=HYPERLINK("http://evil")',
        "дашборд",
        "TOTAL",
    ]  # формула обезврежена
    assert rows[-1]["latency_p95_ms"] == "120.0" and rows[1]["error_rate"] == "0.04"
    assert SECRET not in response.text


def test_site_csv(api: TestClient, owner: dict[str, str]) -> None:
    site_id, run_id = setup_run(api, owner)
    response = api.get(f"/api/v1/sites/{site_id}/export.csv", headers=owner)
    assert response.status_code == 200
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert len(rows) == 1
    row = rows[0]
    assert (row["run_id"], row["scenario"], row["label"], row["source"]) == (
        run_id,
        "Вход и =cmd",
        "'+deploy",
        "hook",
    )
    assert (row["slo_met"], row["regression_detected"], row["ceiling_rps"]) == (
        "false",
        "true",
        "18.5",
    )
    assert SECRET not in response.text
    empty = api.get(
        f"/api/v1/sites/{site_id}/export.csv",
        params={"from": "2020-01-01T00:00:00Z", "to": "2020-02-01T00:00:00Z"},
        headers=owner,
    )
    assert len(list(csv.DictReader(io.StringIO(empty.text)))) == 0
    bad = api.get(
        f"/api/v1/sites/{site_id}/export.csv",
        params={"from": "2026-01-02T00:00:00Z", "to": "2026-01-01T00:00:00Z"},
        headers=owner,
    )
    assert_error(bad, 422, "invalid_period")


def pdf_text(content: bytes) -> str:
    reader = PdfReader(io.BytesIO(content))
    return "\n".join(page.extract_text() for page in reader.pages)


@pytest.mark.parametrize(
    ("lang", "expected"),
    [
        (
            "ru",
            [
                "Отчёт о нагрузочном тесте",
                "Магазин",
                "дашборд",
                "Регрессия",
                "p95 выросла на 50%",
                "нарушен",
            ],
        ),
        ("en", ["Load test report", "Per step", "p95 increased by 50%", "breached"]),
    ],
)
def test_run_pdf(api: TestClient, owner: dict[str, str], lang: str, expected: list[str]) -> None:
    _, run_id = setup_run(api, owner)
    response = api.get(f"/api/v1/runs/{run_id}/report.pdf", params={"lang": lang}, headers=owner)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")
    content = pdf_text(response.content)
    for fragment in expected:
        assert fragment in content, fragment
    assert SECRET not in content
    assert b"DejaVuSans" in response.content  # шрифт с кириллицей встроен


def test_pdf_defaults_to_user_language_and_handles_no_metrics(
    api: TestClient, owner: dict[str, str]
) -> None:
    _, run_id = setup_run(api, owner, with_metrics=False)
    response = api.get(f"/api/v1/runs/{run_id}/report.pdf", headers=owner)
    content = pdf_text(response.content)
    assert "У запуска нет метрик." in content
    assert_error(
        api.get(f"/api/v1/runs/{run_id}/export.csv", headers=owner), 409, "run_has_no_metrics"
    )
    assert_error(
        api.get(f"/api/v1/runs/{run_id}/report.pdf?lang=de", headers=owner), 422, "validation_error"
    )


def test_exports_isolation(api: TestClient, owner: dict[str, str]) -> None:
    site_id, run_id = setup_run(api, owner)
    stranger = auth_headers(register(api, "stranger@example.com")["access_token"])
    for url in (
        f"/api/v1/runs/{run_id}/export.csv",
        f"/api/v1/runs/{run_id}/report.pdf",
        f"/api/v1/sites/{site_id}/export.csv",
    ):
        assert_error(api.get(url, headers=stranger), 404, "not_found")


# --- очистка по сроку хранения -------------------------------------------------------


@pytest.fixture
def session(clean_database: str) -> Iterator[Session]:
    engine = create_engine(clean_database)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


def add_run(session: Session, site: Site, days_ago: int) -> TestRun:
    finished = utcnow() - timedelta(days=days_ago)
    run = TestRun(
        site_id=site.id,
        status="finished",
        queued_at=finished,
        started_at=finished,
        finished_at=finished,
    )
    session.add(run)
    session.flush()
    session.add(
        RunMetrics(
            run_id=run.id,
            duration_sec=1,
            requests=1,
            errors=0,
            error_rate=0,
            rps=1,
            iterations_per_sec=1,
            latency_avg_ms=1,
            latency_p50_ms=1,
            latency_p90_ms=1,
            latency_p95_ms=1,
            latency_p99_ms=1,
            latency_max_ms=1,
            summary={"requests": 1},
            series=[{"t": 0}],
        )
    )
    session.commit()
    return run


def test_retention(session: Session, make_settings: Callable[..., Settings]) -> None:
    user = User(email="a@b.co", password_hash="x")
    session.add(user)
    session.flush()
    site = Site(owner_id=user.id, name="s", url="https://s.example")
    session.add(site)
    session.flush()
    old, fresh = add_run(session, site, 100), add_run(session, site, 10)
    session.add_all(
        [
            Alert(
                site_id=site.id,
                type="regression",
                status="resolved",
                opened_at=utcnow() - timedelta(days=500),
                resolved_at=utcnow() - timedelta(days=400),
            ),
            Alert(
                site_id=site.id,
                type="slo_breach",
                status="resolved",
                opened_at=utcnow(),
                resolved_at=utcnow(),
            ),
        ]
    )
    session.commit()
    result = purge(session, make_settings(retention_days=90))
    assert result == {"series_cleared": 1, "alerts_deleted": 1}
    session.expire_all()
    old_metrics, fresh_metrics = session.get(RunMetrics, old.id), session.get(RunMetrics, fresh.id)
    assert old_metrics is not None and old_metrics.series is None
    assert old_metrics.summary == {"requests": 1}  # агрегаты остаются
    assert fresh_metrics is not None and fresh_metrics.series == [{"t": 0}]
    assert purge(session, make_settings(retention_days=90)) == {
        "series_cleared": 0,
        "alerts_deleted": 0,
    }


def test_maintenance_cli(monkeypatch: pytest.MonkeyPatch, clean_database: str) -> None:
    from loadwatch import config
    from loadwatch.maintenance import main
    from tests.conftest import TEST_JWT_SECRET

    monkeypatch.setenv("DATABASE_URL", clean_database)
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    config.get_settings.cache_clear()
    try:
        assert main() == 0
    finally:
        config.get_settings.cache_clear()


def test_pdf_survives_long_and_multiline_text(api: TestClient, owner: dict[str, str]) -> None:
    site_id = api.post(
        "/api/v1/sites", json={"url": "https://long.example", "name": "x"}, headers=owner
    ).json()["id"]
    long_path = "/" + "a" * 1900
    scenario = api.post(
        f"/api/v1/sites/{site_id}/scenarios",
        json={
            "name": "строка " * 14,
            "load_profile": {"type": "constant", "rate": 1, "duration_sec": 1},
            "flow": [{"name": "<b>шаг</b>", "path": long_path}],
            "apdex_t_ms": 300,
        },
        headers=owner,
    ).json()
    run_id = str(uuid.uuid4())
    with api.app.state.engine.begin() as connection:  # type: ignore[attr-defined]
        connection.execute(
            text(
                "INSERT INTO test_runs (id, site_id, scenario_id, status, source, params, generator_saturated,"
                " cancel_requested, queued_at, created_at, updated_at) VALUES (:id, :site, :sc, 'failed', 'manual',"
                " CAST(:params AS jsonb), false, false, now(), now(), now())"
            ),
            {
                "id": run_id,
                "site": site_id,
                "sc": scenario["id"],
                "params": json.dumps(
                    {
                        "profile": {"type": "constant"},
                        "flow": scenario["flow"] * 10,
                        "apdex_t_ms": 300,
                    }
                ),
            },
        )
    response = api.get(f"/api/v1/runs/{run_id}/report.pdf", headers=owner)
    assert response.status_code == 200 and response.content.startswith(b"%PDF")


def test_pdf_uses_slo_snapshot_and_apdex_t(api: TestClient, owner: dict[str, str]) -> None:
    _, run_id = setup_run(api, owner)
    with api.app.state.engine.begin() as connection:  # type: ignore[attr-defined]
        connection.execute(
            text(
                "UPDATE run_metrics SET summary = summary || CAST(:slo AS jsonb) WHERE run_id = :id"
            ),
            {"id": run_id, "slo": json.dumps({"slo": {"p95_ms": 777, "max_error_rate": None}})},
        )
        connection.execute(
            text("UPDATE test_runs SET params = params || CAST(:p AS jsonb) WHERE id = :id"),
            {"id": run_id, "p": json.dumps({"apdex_t_ms": 250})},
        )
    content = pdf_text(api.get(f"/api/v1/runs/{run_id}/report.pdf", headers=owner).content)
    assert "p95 ≤ 777" in content  # цель на момент запуска, а не текущая (100)
    assert "T = 250" in " ".join(content.split())  # ячейка может перенести «мс» на новую строку


def test_site_csv_too_large(
    api: TestClient, owner: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import loadwatch.services.export_service as export_service

    site_id, _ = setup_run(api, owner)
    monkeypatch.setattr(export_service, "MAX_EXPORT_ROWS", 0)
    assert_error(
        api.get(f"/api/v1/sites/{site_id}/export.csv", headers=owner), 422, "export_too_large"
    )
