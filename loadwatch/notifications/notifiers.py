"""Каналы уведомлений: Telegram, Email, Slack, Discord, generic webhook."""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import html
import json
import re
import smtplib
import ssl
from abc import ABC, abstractmethod
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any, ClassVar
from urllib.parse import urlsplit

import httpx

from loadwatch.config import Settings
from loadwatch.notifications.messages import Message, render_email
from loadwatch.security.ssrf import (
    SafeHttpClient,
    TargetForbiddenError,
    TargetResolutionError,
    parse_target_url,
)

WEBHOOK_TIMEOUT_SEC = 5.0
SIGNATURE_HEADER = "X-LoadWatch-Signature"
EVENT_HEADER = "X-LoadWatch-Event"
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_CHAT_ID_RE = re.compile(r"^-?\d{1,20}$|^@[A-Za-z0-9_]{5,32}$")


class ChannelConfigError(ValueError):
    """Неверные параметры канала (текст — для поля ``details`` ошибки API)."""


class DeliveryError(Exception):
    """Уведомление не доставлено; текст — без секретов (токенов, URL вебхуков)."""


@dataclass(frozen=True, slots=True)
class ChannelConfig:
    """Параметры канала: открытые (``public``) и секретные (``secret``, хранятся зашифрованно)."""

    public: dict[str, Any]
    secret: dict[str, str]


class Notifier(ABC):
    """Отправка уведомления одним способом."""

    type: ClassVar[str]

    def __init__(self, settings: Settings, http: SafeHttpClient) -> None:
        self._settings = settings
        self._http = http

    @abstractmethod
    def validate(self, raw: dict[str, Any]) -> ChannelConfig:
        """Проверяет параметры канала при сохранении."""

    @abstractmethod
    def send(self, message: Message, config: ChannelConfig) -> None:
        """Отправляет; при ошибке — :class:`DeliveryError`."""

    @staticmethod
    def masked(config: ChannelConfig) -> dict[str, Any]:
        """Параметры для ответа API: секреты не возвращаются."""
        return dict(config.public)

    def _post_json(self, url: str, payload: dict[str, Any], headers: dict[str, str]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        try:
            status = self._http.post(
                url, content=body, headers={"Content-Type": "application/json", **headers}
            )
        except TargetForbiddenError as exc:
            raise DeliveryError("webhook address is not allowed") from exc
        except TargetResolutionError as exc:
            raise DeliveryError("webhook host cannot be resolved") from exc
        except httpx.HTTPError as exc:
            raise DeliveryError(f"webhook request failed: {type(exc).__name__}") from exc
        if not 200 <= status < 300:
            raise DeliveryError(f"webhook answered HTTP {status}")

    def _webhook_url(self, raw: dict[str, Any], *, host_suffix: str | None) -> str:
        """Проверка URL вебхука при сохранении: синтаксис, домен сервиса и DNS (SSRF)."""
        url = _webhook_url(raw, self._settings, host_suffix=host_suffix)
        try:
            self._http.check(url)
        except TargetForbiddenError as exc:
            raise ChannelConfigError(f"webhook URL is not allowed: {exc}") from exc
        except TargetResolutionError as exc:
            raise ChannelConfigError("webhook host cannot be resolved") from exc
        return url


def _webhook_url(raw: dict[str, Any], settings: Settings, *, host_suffix: str | None) -> str:
    url = str(raw.get("webhook_url") or raw.get("url") or "").strip()
    try:
        target = parse_target_url(url, allow_private=settings.allow_private_targets)
    except TargetForbiddenError as exc:
        raise ChannelConfigError(f"webhook URL is not allowed: {exc}") from exc
    if host_suffix is not None and (
        target.scheme != "https"
        or not (target.host == host_suffix or target.host.endswith(f".{host_suffix}"))
    ):
        raise ChannelConfigError(f"webhook URL must be https://…{host_suffix}/…")
    return url


def _host_hint(url: str) -> str:
    """Безопасная подсказка «куда отправляем»: только хост, без пути с токеном."""
    return urlsplit(url).hostname or ""


class TelegramNotifier(Notifier):
    """Бот LoadWatch (токен в ``TELEGRAM_BOT_TOKEN``) пишет в чат пользователя."""

    type = "telegram"
    api_base = "https://api.telegram.org"

    def validate(self, raw: dict[str, Any]) -> ChannelConfig:
        """Нужен ``chat_id``: число (личный чат или группа) или ``@channel``."""
        chat_id = str(raw.get("chat_id", "")).strip()
        if not _CHAT_ID_RE.match(chat_id):
            raise ChannelConfigError("chat_id must be a number or @channel_name")
        return ChannelConfig(public={"chat_id": chat_id}, secret={})

    def send(self, message: Message, config: ChannelConfig) -> None:
        """``sendMessage`` Bot API; текст экранирован для HTML-разметки."""
        token = self._settings.telegram_bot_token
        if token is None:
            raise DeliveryError("TELEGRAM_BOT_TOKEN is not configured on the server")
        text = f"<b>{html.escape(message.title)}</b>\n" + "\n".join(
            html.escape(line) for line in message.lines
        )
        if message.link:
            href = html.escape(message.link, quote=True)
            text += f'\n<a href="{href}">{html.escape(message.link)}</a>'
        url = f"{self.api_base}/bot{token.get_secret_value()}/sendMessage"
        try:
            with httpx.Client(timeout=WEBHOOK_TIMEOUT_SEC, trust_env=False) as client:
                response = client.post(
                    url,
                    json={
                        "chat_id": config.public["chat_id"],
                        "text": text,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                    },
                )
        except httpx.HTTPError as exc:  # в тексте исключения httpx может быть URL с токеном
            raise DeliveryError(f"Telegram request failed: {type(exc).__name__}") from None
        if response.status_code != 200:
            description = ""
            with contextlib.suppress(ValueError):
                description = str(response.json().get("description", ""))[:200]
            raise DeliveryError(
                f"Telegram answered HTTP {response.status_code} {description}".strip()
            )


class EmailNotifier(Notifier):
    """Письмо через SMTP из конфига (``SMTP_*``), HTML и текст из шаблонов Jinja2."""

    type = "email"

    def validate(self, raw: dict[str, Any]) -> ChannelConfig:
        """Нужен адрес ``email``."""
        email = str(raw.get("email", "")).strip()
        if not _EMAIL_RE.match(email) or len(email) > 320:
            raise ChannelConfigError("invalid e-mail address")
        return ChannelConfig(public={"email": email}, secret={})

    def send(self, message: Message, config: ChannelConfig) -> None:
        """Отправка через SMTP (STARTTLS по умолчанию)."""
        settings = self._settings
        if not settings.smtp_host:
            raise DeliveryError("SMTP is not configured on the server")
        text, html_body = render_email(message)
        try:
            mail = EmailMessage()
            # Перевод строки в заголовке письма — инъекция заголовков; заменяем пробелом.
            mail["Subject"] = " ".join(message.title.split())
            mail["From"] = settings.smtp_from
            mail["To"] = config.public["email"]
            mail.set_content(text)
            mail.add_alternative(html_body, subtype="html")
        except ValueError as exc:
            raise DeliveryError("e-mail cannot be built") from exc
        try:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
                if settings.smtp_starttls:
                    # Проверка сертификата и имени сервера: иначе пароль SMTP уязвим для MITM.
                    smtp.starttls(context=ssl.create_default_context())
                if settings.smtp_user and settings.smtp_password:
                    smtp.login(settings.smtp_user, settings.smtp_password.get_secret_value())
                smtp.send_message(mail)
        except (OSError, smtplib.SMTPException) as exc:
            raise DeliveryError(f"SMTP delivery failed: {type(exc).__name__}") from None


class SlackNotifier(Notifier):
    """Incoming webhook Slack."""

    type = "slack"

    def validate(self, raw: dict[str, Any]) -> ChannelConfig:
        """Нужен ``webhook_url`` вида ``https://hooks.slack.com/services/…``."""
        url = self._webhook_url(raw, host_suffix="hooks.slack.com")
        return ChannelConfig(public={"host": _host_hint(url)}, secret={"webhook_url": url})

    def send(self, message: Message, config: ChannelConfig) -> None:
        """``{"text": …}``."""
        self._post_json(config.secret["webhook_url"], {"text": message.text}, {})


class DiscordNotifier(Notifier):
    """Webhook Discord."""

    type = "discord"

    def validate(self, raw: dict[str, Any]) -> ChannelConfig:
        """Нужен ``webhook_url`` вида ``https://discord.com/api/webhooks/…``."""
        url = self._webhook_url(raw, host_suffix="discord.com")
        return ChannelConfig(public={"host": _host_hint(url)}, secret={"webhook_url": url})

    def send(self, message: Message, config: ChannelConfig) -> None:
        """``{"content": …}`` (Discord ограничивает текст 2000 символами)."""
        self._post_json(config.secret["webhook_url"], {"content": message.text[:2000]}, {})


def sign(body: bytes, secret: str) -> str:
    """Подпись тела вебхука: ``sha256=<hex HMAC-SHA256>``."""
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class WebhookNotifier(Notifier):
    """Generic webhook: JSON с событием, подпись HMAC-SHA256 в ``X-LoadWatch-Signature``."""

    type = "webhook"

    def validate(self, raw: dict[str, Any]) -> ChannelConfig:
        """Нужен ``url``; секрет подписи генерирует сервер (см. сервис каналов)."""
        url = self._webhook_url(raw, host_suffix=None)
        secret = str(raw.get("signing_secret", ""))
        if len(secret) < 32:
            raise ChannelConfigError("signing secret is missing")
        return ChannelConfig(
            public={"host": _host_hint(url)}, secret={"url": url, "signing_secret": secret}
        )

    def send(self, message: Message, config: ChannelConfig) -> None:
        """Тело подписывается тем же байтовым представлением, что и отправляется."""
        payload = {
            "event": message.event,
            "title": message.title,
            "text": message.text,
            "link": message.link,
            "data": message.payload,
        }
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
        headers = {
            "Content-Type": "application/json",
            SIGNATURE_HEADER: sign(body, config.secret["signing_secret"]),
            EVENT_HEADER: message.event,
        }
        try:
            status = self._http.post(config.secret["url"], content=body, headers=headers)
        except TargetForbiddenError as exc:
            raise DeliveryError("webhook address is not allowed") from exc
        except TargetResolutionError as exc:
            raise DeliveryError("webhook host cannot be resolved") from exc
        except httpx.HTTPError as exc:
            raise DeliveryError(f"webhook request failed: {type(exc).__name__}") from exc
        if not 200 <= status < 300:
            raise DeliveryError(f"webhook answered HTTP {status}")


NOTIFIER_TYPES: dict[str, type[Notifier]] = {
    cls.type: cls
    for cls in (TelegramNotifier, EmailNotifier, SlackNotifier, DiscordNotifier, WebhookNotifier)
}


class NotifierRegistry:
    """Нотификаторы по типу канала (фабрика; в тестах подменяется)."""

    def __init__(self, settings: Settings, http: SafeHttpClient | None = None) -> None:
        http = http or SafeHttpClient(
            allow_private=settings.allow_private_targets,
            timeout=WEBHOOK_TIMEOUT_SEC,
            deadline=WEBHOOK_TIMEOUT_SEC,
            max_redirects=0,
        )
        self._notifiers = {name: cls(settings, http) for name, cls in NOTIFIER_TYPES.items()}

    def get(self, channel_type: str) -> Notifier:
        """Нотификатор по типу."""
        return self._notifiers[channel_type]
