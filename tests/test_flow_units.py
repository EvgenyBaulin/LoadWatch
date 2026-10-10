"""Шаги потока: проверка при сохранении, переменные, извлечение, секреты."""

from typing import Any

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from loadwatch.flow import Flow, RequestStep, redact
from loadwatch.flow.extract import ExtractError, extract, json_path, response_cookies
from loadwatch.flow.steps import MAX_BODY_BYTES, ExtractRule
from loadwatch.flow.templating import (
    UndefinedVariableError,
    referenced_variables,
    render,
    render_value,
)
from loadwatch.security.secrets import MASK, SecretBox, SecretsUnavailableError, mask


def step(**kwargs: Any) -> RequestStep:
    return RequestStep.model_validate({"name": "s", "path": "/", **kwargs})


# --- проверка шагов -------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "https://evil.example/",
        "//evil.example/x",
        "evil.example",
        "",
        "/a\\b",
        "/a\r\nHost: x",
        "/redirect?to=x&u=http://ok",  # ок: схема только в запросе
    ],
)
def test_paths(path: str) -> None:
    if path == "/redirect?to=x&u=http://ok":
        assert step(path=path).path == path
        return
    with pytest.raises(ValidationError):
        step(path=path)


@pytest.mark.parametrize(
    "header",
    [
        "Host",
        "host",
        "Content-Length",
        "Transfer-Encoding",
        "Connection",
        "Keep-Alive",
        "TE",
        "Upgrade",
        "Proxy-Authorization",
        "Proxy-Anything",
        "Bad Header",
        "X:Y",
    ],
)
def test_forbidden_or_invalid_headers(header: str) -> None:
    with pytest.raises(ValidationError):
        step(headers={header: "x"})


def test_header_limits() -> None:
    with pytest.raises(ValidationError, match="line break"):
        step(headers={"X-A": "a\r\nInjected: 1"})
    with pytest.raises(ValidationError, match="no more than"):
        step(headers={f"X-{i}": "v" for i in range(21)})
    with pytest.raises(ValidationError, match="longer"):
        step(headers={"X-Big": "a" * 5000})
    with pytest.raises(ValidationError, match="duplicate"):
        step(headers={"X-A": "1", "x-a": "2"})
    assert step(headers={"Authorization": "Bearer {{token}}"}).headers


def test_body_rules() -> None:
    with pytest.raises(ValidationError, match="cannot have a body"):
        step(method="GET", body={"type": "raw", "content": "x"})
    with pytest.raises(ValidationError, match="larger"):
        step(method="POST", body={"type": "raw", "content": "x" * (MAX_BODY_BYTES + 1)})
    with pytest.raises(ValidationError, match="object of strings"):
        step(method="POST", body={"type": "form", "content": {"a": 1}})
    with pytest.raises(ValidationError, match="raw body"):
        step(method="POST", body={"type": "raw", "content": {"a": "b"}})
    assert step(method="POST", body={"type": "json", "content": {"a": [1, 2]}}).body


def test_expected_status() -> None:
    default = step()
    assert default.status_matches(200) and default.status_matches(301)
    assert not default.status_matches(404)
    custom = step(expected_status=["401", "5XX"])
    assert custom.expected_status == ["401", "5xx"]
    assert custom.status_matches(401) and custom.status_matches(503)
    assert not custom.status_matches(200)
    with pytest.raises(ValidationError):
        step(expected_status=["2x"])
    with pytest.raises(ValidationError):
        step(expected_status=[])


def test_extract_rules() -> None:
    with pytest.raises(ValidationError, match="reserved"):
        ExtractRule(var="uuid", source="json", expr="a")
    with pytest.raises(ValidationError, match="invalid regex"):
        ExtractRule(var="x", source="regex", expr="(")
    with pytest.raises(ValidationError):
        ExtractRule(var="1bad", source="json", expr="a")
    assert ExtractRule(var="token", source="regex", expr=r"token=(\w+)")


def test_flow_rules() -> None:
    login = step(
        name="login",
        method="POST",
        path="/login",
        body={"type": "json", "content": {"password": "{{secret.PASSWORD}}"}},
        extract=[{"var": "token", "source": "json", "expr": "data.token"}],
    )
    dashboard = step(name="dash", headers={"Authorization": "Bearer {{token}}"})
    flow = Flow(steps=[login, dashboard])
    assert flow.secret_names() == {"PASSWORD"}
    with pytest.raises(ValidationError, match="before it is defined"):
        Flow(steps=[dashboard, login])  # токен используется раньше, чем извлечён
    with pytest.raises(ValidationError, match="unique"):
        Flow(steps=[step(name="a"), step(name="a")])
    with pytest.raises(ValidationError):
        Flow(steps=[step(name=f"s{i}") for i in range(11)])
    with pytest.raises(ValidationError):
        Flow(steps=[])
    assert Flow(steps=[step(path="/u/{{uuid}}?t={{timestamp}}&r={{rand_int}}")])


def test_assert_contains_variables_are_checked() -> None:
    with pytest.raises(ValidationError, match="before it is defined"):
        Flow(steps=[step(assert_contains="hello {{name}}")])


# --- переменные -----------------------------------------------------------------


def test_render() -> None:
    variables = {"token": "abc", "q": "a b/c?d"}
    secrets = {"PASS": "p@ss"}
    assert render("Bearer {{token}}", variables, secrets) == "Bearer abc"
    assert render("{{ token }}-{{secret.PASS}}", variables, secrets) == "abc-p@ss"
    assert render("/search/{{q}}", variables, secrets, url_encode=True) == "/search/a%20b%2Fc%3Fd"
    assert len(render("{{uuid}}", {}, {})) == 36
    assert render("{{timestamp}}", {}, {}).isdigit()
    assert 0 <= int(render("{{rand_int}}", {}, {})) <= 1_000_000
    assert render("no vars {{ }}", {}, {}) == "no vars {{ }}"


def test_render_missing() -> None:
    with pytest.raises(UndefinedVariableError) as info:
        render("{{secret.NOPE}}", {}, {})
    assert info.value.name == "secret.NOPE"
    with pytest.raises(UndefinedVariableError):
        render("{{token}}", {}, {})


def test_render_value_and_references() -> None:
    value = {"user": "{{name}}", "list": ["{{name}}", 1, None], "{{key}}": True}
    rendered = render_value(value, {"name": "Ann"}, {})
    assert rendered == {"user": "Ann", "list": ["Ann", 1, None], "{{key}}": True}
    assert referenced_variables("{{a}} {{secret.B}} {{ c }}") == ["a", "secret.B", "c"]


# --- извлечение -----------------------------------------------------------------

HEADERS = httpx.Headers(
    [
        ("X-Request-Id", "rid-1"),
        ("Set-Cookie", "session=abc; Path=/; HttpOnly"),
        ("Set-Cookie", "theme=dark"),
    ]
)


def rule(source: str, expr: str) -> ExtractRule:
    return ExtractRule(var="v", source=source, expr=expr)


def test_json_path() -> None:
    doc = {"data": {"items": [{"id": 7}, {"id": 8}], "ok": True}}
    assert json_path(doc, "data.items[1].id") == 8
    assert json_path(doc, "$.data.ok") is True
    for bad in ("data.missing", "data.items[5].id", "data.items.id"):
        with pytest.raises(ExtractError):
            json_path(doc, bad)


def test_extract_sources() -> None:
    body = b'{"data": {"token": "t-1", "n": 5, "flag": false, "obj": {}}} csrf=xyz;'
    assert extract(rule("header", "x-request-id"), HEADERS, body) == "rid-1"
    assert extract(rule("cookie", "session"), HEADERS, body) == "abc"
    assert extract(rule("regex", r"csrf=(\w+)"), HEADERS, body) == "xyz"
    assert extract(rule("regex", r"csrf=\w+"), HEADERS, body) == "csrf=xyz"
    json_body = b'{"data": {"token": "t-1", "n": 5, "flag": false, "obj": {}, "none": null}}'
    assert extract(rule("json", "data.token"), HEADERS, json_body) == "t-1"
    assert extract(rule("json", "data.n"), HEADERS, json_body) == "5"
    assert extract(rule("json", "data.flag"), HEADERS, json_body) == "false"
    assert extract(rule("json", "data.none"), HEADERS, json_body) == ""
    assert response_cookies(HEADERS) == {"session": "abc", "theme": "dark"}


@pytest.mark.parametrize(
    ("source", "expr", "body"),
    [
        ("header", "x-missing", b""),
        ("cookie", "nope", b""),
        ("regex", r"nothing(\d+)", b"text"),
        ("json", "a", b"not json"),
        ("json", "data", b'{"data": {"a": 1}}'),
        ("json", "a", b'{"a": "' + b"x" * 5000 + b'"}'),
    ],
)
def test_extract_failures(source: str, expr: str, body: bytes) -> None:
    with pytest.raises(ExtractError):
        extract(rule(source, expr), HEADERS, body)


def test_broken_set_cookie_is_ignored() -> None:
    assert response_cookies(httpx.Headers([("Set-Cookie", '"broken')])) == {}


# --- секреты --------------------------------------------------------------------


def test_secret_box_roundtrip() -> None:
    box = SecretBox(Fernet.generate_key().decode())
    token = box.encrypt("hunter2")
    assert "hunter2" not in token
    assert box.decrypt(token) == "hunter2"
    assert box.available


def test_secret_box_wrong_key_and_missing_key() -> None:
    token = SecretBox(Fernet.generate_key().decode()).encrypt("v")
    with pytest.raises(SecretsUnavailableError, match="cannot be decrypted"):
        SecretBox(Fernet.generate_key().decode()).decrypt(token)
    empty = SecretBox(None)
    assert not empty.available
    with pytest.raises(SecretsUnavailableError):
        empty.encrypt("v")
    with pytest.raises(SecretsUnavailableError):
        empty.decrypt(token)


def test_mask_and_redact() -> None:
    assert mask("anything") == MASK
    assert mask("") == ""
    secrets = {"A": "pass", "B": "password123", "C": ""}
    assert redact("x password123 y pass", secrets) == "x *** y ***"


def test_catastrophic_regex_times_out() -> None:
    body = b"a" * 40 + b"b"
    with pytest.raises(ExtractError, match="too long"):
        extract(rule("regex", "(a|aa)+$"), HEADERS, body)


def test_redact_url_encoded_secret() -> None:
    assert redact("/login?p=p%40ss&x=p@ss", {"P": "p@ss"}) == "/login?p=***&x=***"


def test_non_ascii_static_header_rejected() -> None:
    with pytest.raises(ValidationError, match="ASCII"):
        step(headers={"X-Name": "Привет"})


# --- cookie виртуального пользователя -----------------------------------------------


def cookie_headers(*values: str) -> httpx.Headers:
    return httpx.Headers([("Set-Cookie", value) for value in values])


def test_cookie_store_path_secure_and_domain() -> None:
    from loadwatch.flow.cookies import CookieStore

    store = CookieStore("shop.example.com", https=False)
    store.update(
        cookie_headers(
            "a=1",
            "admin=2; Path=/admin",
            "sec=3; Secure",
            "parent=4; Domain=example.com",
            "foreign=5; Domain=evil.example",
        ),
        "/login",
    )
    assert store.header_for("/") == "a=1; parent=4"
    assert store.header_for("/admin/users?x=1") == "a=1; admin=2; parent=4"
    assert store.header_for("/administrator") == "a=1; parent=4"
    https_store = CookieStore("shop.example.com", https=True)
    https_store.update(cookie_headers("sec=3; Secure"), "/")
    assert https_store.header_for("/") == "sec=3"


def test_cookie_store_expiry_and_deletion() -> None:
    from loadwatch.flow.cookies import CookieStore

    store = CookieStore("example.com", https=True)
    store.update(cookie_headers("s=1", "t=2; Max-Age=3600"), "/")
    assert store.header_for("/") == "s=1; t=2"
    store.update(cookie_headers("s=deleted; Max-Age=0"), "/")
    assert store.header_for("/") == "t=2"
    store.update(cookie_headers("t=x; Expires=Thu, 01 Jan 1970 00:00:00 GMT"), "/")
    assert store.header_for("/") is None
    store.update(cookie_headers("u=1; Max-Age=abc", "v=1; Expires=garbage", '"broken'), "/")
    assert store.header_for("/") == "u=1; v=1"


def test_cookie_default_path() -> None:
    from loadwatch.flow.cookies import CookieStore

    store = CookieStore("example.com", https=True)
    store.update(cookie_headers("d=1"), "/account/login?next=/")
    assert store.header_for("/account/profile") == "d=1"
    assert store.header_for("/") is None


def test_httpcore_debug_logs_are_silenced(make_settings: Any) -> None:
    import logging

    from loadwatch.logging_setup import configure_logging

    configure_logging(make_settings())
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING
