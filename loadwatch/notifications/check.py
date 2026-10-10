"""``python -m loadwatch.notifications.check``: проверка бота Telegram из настроек.

Отправляет тестовое сообщение в ``TELEGRAM_TEST_CHAT_ID`` ботом
``TELEGRAM_BOT_TOKEN`` — без базы, API и регистрации. Токен в вывод не попадает.
"""

from __future__ import annotations

import sys

from loadwatch.config import Settings, get_settings
from loadwatch.notifications.builders import channel_test_message
from loadwatch.notifications.notifiers import (
    ChannelConfigError,
    DeliveryError,
    NotifierRegistry,
)


def check_telegram(settings: Settings) -> tuple[bool, str]:
    """Отправляет тест; возвращает (успех, сообщение для человека)."""
    if settings.telegram_bot_token is None:
        return False, "TELEGRAM_BOT_TOKEN is not set"
    if not settings.telegram_test_chat_id:
        return False, "TELEGRAM_TEST_CHAT_ID is not set"
    notifier = NotifierRegistry(settings).get("telegram")
    try:
        config = notifier.validate({"chat_id": settings.telegram_test_chat_id})
        notifier.send(channel_test_message("ru", "TELEGRAM_TEST_CHAT_ID"), config)
    except (ChannelConfigError, DeliveryError) as exc:
        return False, str(exc)
    return True, f"sent to chat {settings.telegram_test_chat_id}"


def main() -> int:
    """Точка входа: код выхода 0 — сообщение доставлено."""
    ok, text = check_telegram(get_settings())
    print(("OK: " if ok else "FAILED: ") + text)  # вывод CLI
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
