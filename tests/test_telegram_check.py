"""Команда проверки бота Telegram из настроек."""

from collections.abc import Callable

import httpx
import respx

from loadwatch.config import Settings
from loadwatch.notifications.check import check_telegram


def test_missing_settings(make_settings: Callable[..., Settings]) -> None:
    assert check_telegram(make_settings()) == (False, "TELEGRAM_BOT_TOKEN is not set")
    settings = make_settings(telegram_bot_token="1:T", telegram_test_chat_id="")
    assert check_telegram(settings) == (False, "TELEGRAM_TEST_CHAT_ID is not set")


@respx.mock
def test_sends_to_test_chat(make_settings: Callable[..., Settings]) -> None:
    route = respx.post("https://api.telegram.org/bot1:TOKEN/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    settings = make_settings(telegram_bot_token="1:TOKEN", telegram_test_chat_id="424242")
    ok, text = check_telegram(settings)
    assert (ok, text) == (True, "sent to chat 424242")
    assert b'"chat_id":"424242"' in route.calls.last.request.content.replace(b" ", b"")


@respx.mock
def test_reports_errors_without_token(make_settings: Callable[..., Settings]) -> None:
    respx.post(url__regex=r"https://api\.telegram\.org/.*").mock(
        return_value=httpx.Response(400, json={"description": "Bad Request: chat not found"})
    )
    settings = make_settings(telegram_bot_token="1:TOKEN", telegram_test_chat_id="424242")
    ok, text = check_telegram(settings)
    assert not ok and "chat not found" in text and "TOKEN" not in text
    bad = make_settings(telegram_bot_token="1:TOKEN", telegram_test_chat_id="not-a-chat")
    assert check_telegram(bad)[0] is False
