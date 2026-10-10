"""Пять нотификаторов с подменой сети, подпись вебхука, SSRF, тексты на двух языках."""

import hashlib
import hmac
import json
from collections.abc import Callable
from typing import Any, ClassVar

import httpx
import pytest
import respx

from loadwatch.config import Settings
from loadwatch.notifications.builders import alert_message, channel_test_message, recovery_message
from loadwatch.notifications.messages import LOCALES_DIR, Message, render_email, t
from loadwatch.notifications.notifiers import (
    ChannelConfig,
    ChannelConfigError,
    DeliveryError,
    DiscordNotifier,
    EmailNotifier,
    SlackNotifier,
    TelegramNotifier,
    WebhookNotifier,
    sign,
)
from loadwatch.security.ssrf import SafeHttpClient

REGRESSION = {
    "detected": True,
    "findings": [
        {
            "metric": "p95_ms",
            "current": 140.0,
            "baseline": 100.0,
            "change_pct": 40.0,
            "change_abs": 40.0,
            "baseline_runs": 5,
            "message_key": "regression.p95_ms",
        }
    ],
}


class Captured:
    def __init__(self, status: int = 200) -> None:
        self.requests: list[httpx.Request] = []
        self.status = status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status)


def http(captured: Captured, *, resolve_to: str = "93.184.216.34") -> SafeHttpClient:
    return SafeHttpClient(
        resolver=lambda host, port: [resolve_to], transport=httpx.MockTransport(captured)
    )


@pytest.fixture
def settings(make_settings: Callable[..., Settings]) -> Settings:
    return make_settings(
        telegram_bot_token="123456:TEST-TOKEN",
        smtp_host="smtp.example.com",
        smtp_user="bot@example.com",
        smtp_password="smtp-pass",
        smtp_from="LoadWatch <bot@example.com>",
    )


def message(locale: str = "ru") -> Message:
    return alert_message(
        locale,
        "regression",
        site_name="Магазин <shop>",
        details={"regression": REGRESSION, "consecutive": 2},
        link="https://app.example/sites/1",
    )


# --- тексты ---------------------------------------------------------------------


def test_locales_have_identical_keys() -> None:
    ru = json.loads((LOCALES_DIR / "ru.json").read_text(encoding="utf-8"))
    en = json.loads((LOCALES_DIR / "en.json").read_text(encoding="utf-8"))
    assert set(ru) == set(en)


def test_regression_text_in_both_languages() -> None:
    ru, en = message("ru"), message("en")
    assert ru.title == "Регрессия на Магазин <shop>"
    assert ru.lines[0] == (
        "p95 выросла на 40% относительно медианы последних 5 запусков (100 → 140 мс)."
    )
    assert ru.lines[1] == "Подряд проблемных запусков: 2."
    assert en.lines[0].startswith("p95 increased by 40% compared to the median of the last 5 runs")
    assert "Открыть: https://app.example/sites/1" in ru.text


def test_other_messages() -> None:
    repeat = alert_message(
        "en",
        "slo_breach",
        site_name="s",
        details={
            "slo": {"p95": 900, "p95_target": 500, "error_rate": 0.02, "error_rate_target": 0.01}
        },
        link=None,
        repeat=True,
    )
    assert repeat.title == "Still: SLO breached on s"
    assert repeat.lines == [
        "p95 is 900 ms, target is at most 500 ms.",
        "Errors are 2%, target is at most 1%.",
    ]
    failed = alert_message(
        "ru", "run_failed", site_name="s", details={"error": "dns_error"}, link=None
    )
    assert failed.lines == ["Причина: dns_error."]
    lost = alert_message("ru", "site_unverified", site_name="s", details={}, link=None)
    assert "тесты сайта остановлены" in lost.lines[0]
    recovered = recovery_message("ru", "regression", site_name="s", runs=2, link=None)
    assert recovered.title == "Восстановлено на s: регрессия"
    assert recovered.event == "recovery"
    assert channel_test_message("en", "Ops").lines == ["Channel “Ops” is configured correctly."]
    assert t("de", "test.title") == "LoadWatch test notification"  # нет языка — английский
    assert t("ru", "no.such.key") == "no.such.key"


def test_email_templates_escape_html() -> None:
    text, html_body = render_email(message("ru"))
    assert "Регрессия на Магазин <shop>" in text
    assert "Магазин &lt;shop&gt;" in html_body
    assert '<html lang="ru">' in html_body
    assert "https://app.example/sites/1" in html_body


# --- Telegram -------------------------------------------------------------------


@pytest.mark.parametrize("chat_id", ["123456", "-1001234567890", "@loadwatch_alerts"])
def test_telegram_chat_ids(settings: Settings, chat_id: str) -> None:
    config = TelegramNotifier(settings, http(Captured())).validate({"chat_id": chat_id})
    assert config.public == {"chat_id": chat_id} and config.secret == {}


@pytest.mark.parametrize("chat_id", ["", "abc", "@x", "12 34"])
def test_telegram_bad_chat_ids(settings: Settings, chat_id: str) -> None:
    with pytest.raises(ChannelConfigError):
        TelegramNotifier(settings, http(Captured())).validate({"chat_id": chat_id})


@respx.mock
def test_telegram_send(settings: Settings) -> None:
    route = respx.post("https://api.telegram.org/bot123456:TEST-TOKEN/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    notifier = TelegramNotifier(settings, http(Captured()))
    notifier.send(message(), ChannelConfig(public={"chat_id": "42"}, secret={}))
    body = json.loads(route.calls.last.request.content)
    assert body["chat_id"] == "42" and body["parse_mode"] == "HTML"
    assert "<b>Регрессия на Магазин &lt;shop&gt;</b>" in body["text"]  # HTML экранирован


@respx.mock
def test_telegram_errors_do_not_leak_token(settings: Settings) -> None:
    respx.post(url__regex=r"https://api.telegram.org/.*").mock(
        return_value=httpx.Response(
            400, json={"ok": False, "description": "Bad Request: chat not found"}
        )
    )
    notifier = TelegramNotifier(settings, http(Captured()))
    with pytest.raises(DeliveryError) as info:
        notifier.send(message(), ChannelConfig(public={"chat_id": "42"}, secret={}))
    assert "chat not found" in str(info.value)
    assert "TEST-TOKEN" not in str(info.value)
    respx.post(url__regex=r"https://api.telegram.org/.*").mock(side_effect=httpx.ConnectError("x"))
    with pytest.raises(DeliveryError) as info:
        notifier.send(message(), ChannelConfig(public={"chat_id": "42"}, secret={}))
    assert "TEST-TOKEN" not in str(info.value)


def test_telegram_without_token(make_settings: Callable[..., Settings]) -> None:
    notifier = TelegramNotifier(make_settings(), http(Captured()))
    with pytest.raises(DeliveryError, match="TELEGRAM_BOT_TOKEN"):
        notifier.send(message(), ChannelConfig(public={"chat_id": "42"}, secret={}))


# --- Email ----------------------------------------------------------------------


class FakeSMTP:
    instances: ClassVar[list["FakeSMTP"]] = []

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.host, self.port = host, port
        self.started_tls = False
        self.login_args: tuple[str, str] | None = None
        self.sent: list[Any] = []
        FakeSMTP.instances.append(self)

    def __enter__(self) -> "FakeSMTP":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def starttls(self, context: Any = None) -> None:
        import ssl

        assert isinstance(context, ssl.SSLContext)  # сертификат сервера проверяется
        assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
        self.started_tls = True

    def login(self, user: str, password: str) -> None:
        self.login_args = (user, password)

    def send_message(self, mail: Any) -> None:
        self.sent.append(mail)


def test_email_send(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    FakeSMTP.instances.clear()
    monkeypatch.setattr("smtplib.SMTP", FakeSMTP)
    notifier = EmailNotifier(settings, http(Captured()))
    config = notifier.validate({"email": "me@example.com"})
    notifier.send(message("en"), config)
    smtp = FakeSMTP.instances[0]
    assert (smtp.host, smtp.port, smtp.started_tls) == ("smtp.example.com", 587, True)
    assert smtp.login_args == ("bot@example.com", "smtp-pass")
    mail = smtp.sent[0]
    assert mail["To"] == "me@example.com"
    assert mail["Subject"] == "Regression on Магазин <shop>"
    parts = [part.get_content_type() for part in mail.walk()]
    assert "text/plain" in parts and "text/html" in parts


def test_email_validation_and_failures(
    settings: Settings, make_settings: Callable[..., Settings], monkeypatch: pytest.MonkeyPatch
) -> None:
    notifier = EmailNotifier(settings, http(Captured()))
    with pytest.raises(ChannelConfigError):
        notifier.validate({"email": "not-an-email"})

    def broken(*args: object, **kwargs: object) -> None:
        raise OSError("connection refused")

    monkeypatch.setattr("smtplib.SMTP", broken)
    with pytest.raises(DeliveryError, match="SMTP"):
        notifier.send(message(), ChannelConfig(public={"email": "a@b.co"}, secret={}))
    unconfigured = EmailNotifier(make_settings(), http(Captured()))
    with pytest.raises(DeliveryError, match="not configured"):
        unconfigured.send(message(), ChannelConfig(public={"email": "a@b.co"}, secret={}))


# --- Slack, Discord ---------------------------------------------------------------


def test_subject_line_breaks_are_removed(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeSMTP.instances.clear()
    monkeypatch.setattr("smtplib.SMTP", FakeSMTP)
    notifier = EmailNotifier(settings, http(Captured()))
    evil = Message(event="test", locale="en", title="Hi\r\nBcc: victim@example.com", lines=[])
    notifier.send(evil, ChannelConfig(public={"email": "a@b.co"}, secret={}))
    assert FakeSMTP.instances[0].sent[0]["Subject"] == "Hi Bcc: victim@example.com"
    assert FakeSMTP.instances[0].sent[0]["Bcc"] is None


def test_webhook_host_resolving_to_private_is_rejected_at_save(settings: Settings) -> None:
    notifier = WebhookNotifier(settings, http(Captured(), resolve_to="192.168.1.5"))
    with pytest.raises(ChannelConfigError, match="not allowed"):
        notifier.validate({"url": "https://internal.example/hook", "signing_secret": "s" * 64})


def test_unresolvable_webhook(settings: Settings) -> None:
    from loadwatch.security.ssrf import TargetResolutionError

    def no_dns(host: str, port: int) -> list[str]:
        raise TargetResolutionError(host)

    client = SafeHttpClient(resolver=no_dns, transport=httpx.MockTransport(Captured()))
    notifier = WebhookNotifier(settings, client)
    with pytest.raises(ChannelConfigError, match="resolved"):
        notifier.validate({"url": "https://gone.example/hook", "signing_secret": "s" * 64})
    config = ChannelConfig(
        public={}, secret={"url": "https://gone.example/hook", "signing_secret": "s" * 64}
    )
    with pytest.raises(DeliveryError, match="resolved"):
        notifier.send(message(), config)
    slack = ChannelConfig(public={}, secret={"webhook_url": "https://hooks.slack.com/x"})
    with pytest.raises(DeliveryError, match="resolved"):
        SlackNotifier(settings, client).send(message(), slack)


def test_slack_and_discord(settings: Settings) -> None:
    captured = Captured()
    slack = SlackNotifier(settings, http(captured))
    config = slack.validate({"webhook_url": "https://hooks.slack.com/services/T/B/secret"})
    assert config.public == {"host": "hooks.slack.com"}  # токен из пути наружу не отдаётся
    slack.send(message("en"), config)
    assert json.loads(captured.requests[0].content)["text"].startswith("Regression on")
    discord = DiscordNotifier(settings, http(captured))
    config = discord.validate({"webhook_url": "https://discord.com/api/webhooks/1/abc"})
    discord.send(message("en"), config)
    assert "content" in json.loads(captured.requests[1].content)


@pytest.mark.parametrize(
    ("cls", "url"),
    [
        (SlackNotifier, "https://evil.example/services/x"),
        (SlackNotifier, "http://hooks.slack.com/services/x"),
        (DiscordNotifier, "https://discord.com.evil.example/api/webhooks/1"),
        (WebhookNotifier, "http://169.254.169.254/latest"),
        (WebhookNotifier, "http://localhost:8080/hook"),
        (WebhookNotifier, "ftp://example.com/hook"),
    ],
)
def test_webhook_urls_are_validated(settings: Settings, cls: Any, url: str) -> None:
    with pytest.raises(ChannelConfigError):
        cls(settings, http(Captured())).validate(
            {"webhook_url": url, "url": url, "signing_secret": "x" * 64}
        )


def test_webhook_send_failures(settings: Settings) -> None:
    config = ChannelConfig(public={}, secret={"webhook_url": "https://hooks.slack.com/services/x"})
    with pytest.raises(DeliveryError, match="HTTP 500"):
        SlackNotifier(settings, http(Captured(status=500))).send(message(), config)
    # DNS сменился после сохранения канала: адрес теперь внутренний — отправки нет
    captured = Captured()
    with pytest.raises(DeliveryError, match="not allowed"):
        SlackNotifier(settings, http(captured, resolve_to="10.0.0.5")).send(message(), config)
    assert captured.requests == []


# --- generic webhook ----------------------------------------------------------------


def test_generic_webhook_is_signed(settings: Settings) -> None:
    captured = Captured()
    notifier = WebhookNotifier(settings, http(captured))
    secret = "s" * 64
    config = notifier.validate({"url": "https://hooks.example/loadwatch", "signing_secret": secret})
    assert config.public == {"host": "hooks.example"}
    notifier.send(message("en"), config)
    request = captured.requests[0]
    body = request.content
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert request.headers["X-LoadWatch-Signature"] == expected == sign(body, secret)
    assert request.headers["X-LoadWatch-Event"] == "regression"
    payload = json.loads(body)
    assert payload["event"] == "regression"
    assert payload["data"]["details"]["regression"]["findings"][0]["change_pct"] == 40.0


def test_generic_webhook_needs_secret(settings: Settings) -> None:
    with pytest.raises(ChannelConfigError, match="secret"):
        WebhookNotifier(settings, http(Captured())).validate({"url": "https://hooks.example/x"})


def test_webhook_does_not_follow_redirects(settings: Settings) -> None:
    def redirect(request: httpx.Request) -> httpx.Response:
        return httpx.Response(307, headers={"Location": "http://10.0.0.1/"})

    client = SafeHttpClient(
        resolver=lambda h, p: ["93.184.216.34"], transport=httpx.MockTransport(redirect)
    )
    notifier = WebhookNotifier(settings, client)
    config = notifier.validate({"url": "https://hooks.example/x", "signing_secret": "s" * 64})
    with pytest.raises(DeliveryError, match="HTTP 307"):
        notifier.send(message(), config)
