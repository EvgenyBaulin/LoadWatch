"""Каналы уведомлений пользователя и доставка уведомлений по ним."""

from __future__ import annotations

import json
import logging
import secrets
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from loadwatch.config import Settings
from loadwatch.errors import (
    InvalidChannelError,
    NotFoundError,
    SecretsUnavailableError,
)
from loadwatch.models import NotificationChannel, User
from loadwatch.notifications.builders import channel_test_message
from loadwatch.notifications.messages import Message
from loadwatch.notifications.notifiers import (
    ChannelConfig,
    ChannelConfigError,
    DeliveryError,
    NotifierRegistry,
)
from loadwatch.schemas.channel import ChannelIn, ChannelOut, ChannelTestOut
from loadwatch.security import secrets as secret_box

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Delivery:
    """Итог отправки в один канал."""

    channel_id: uuid.UUID
    ok: bool
    error: str | None = None


class ChannelService:
    """CRUD каналов и отправка; всё — только по каналам владельца."""

    def __init__(self, session: Session, settings: Settings, notifiers: NotifierRegistry) -> None:
        self._session = session
        self._settings = settings
        self._notifiers = notifiers
        key = settings.secrets_encryption_key
        self._box = secret_box.SecretBox(key.get_secret_value() if key else None)

    def list_for(self, owner: User) -> list[ChannelOut]:
        """Каналы пользователя."""
        return [self._out(channel) for channel in self._channels(owner.id)]

    def create(self, owner: User, data: ChannelIn) -> ChannelOut:
        """Новый канал; для webhook генерируется секрет подписи (показывается один раз)."""
        raw = dict(data.config)
        signing_secret = None
        if data.type == "webhook":
            signing_secret = secrets.token_hex(32)
            raw["signing_secret"] = signing_secret
        config = self._validate(data.type, raw)
        channel = NotificationChannel(
            user_id=owner.id,
            type=data.type,
            name=data.name,
            config=config.public,
            secret_config=self._encrypt(config),
            events=sorted(set(data.events)),
            enabled=data.enabled,
        )
        self._session.add(channel)
        self._session.commit()
        out = self._out(channel)
        out.signing_secret = signing_secret
        return out

    def update(self, owner: User, channel_id: uuid.UUID, data: ChannelIn) -> ChannelOut:
        """Замена канала. Пустые секретные поля — оставить прежние (их не показывают)."""
        channel = self._owned(owner, channel_id)
        if data.type != channel.type:
            raise InvalidChannelError(details={"config": "channel type cannot be changed"})
        raw = dict(data.config)
        old = self._decrypt(channel)
        for key, value in old.secret.items():
            raw.setdefault(key, value)
        config = self._validate(channel.type, raw)
        channel.name = data.name
        channel.config = config.public
        channel.secret_config = self._encrypt(config)
        channel.events = sorted(set(data.events))
        channel.enabled = data.enabled
        self._session.commit()
        return self._out(channel)

    def delete(self, owner: User, channel_id: uuid.UUID) -> None:
        """Удаляет канал."""
        self._session.delete(self._owned(owner, channel_id))
        self._session.commit()

    def send_test(self, owner: User, channel_id: uuid.UUID) -> ChannelTestOut:
        """Кнопка «отправить тест»: синхронная отправка, результат — сразу."""
        channel = self._owned(owner, channel_id)
        delivery = self._deliver(channel, channel_test_message(owner.locale, channel.name))
        return ChannelTestOut(ok=delivery.ok, error=delivery.error)

    def notify(self, user_id: uuid.UUID, event: str, message: Message) -> list[Delivery]:
        """Отправляет событие во все включённые каналы пользователя, подписанные на него."""
        results = []
        for channel in self._channels(user_id):
            if channel.enabled and event in channel.events:
                results.append(self._deliver(channel, message))
        return results

    def _deliver(self, channel: NotificationChannel, message: Message) -> Delivery:
        try:
            config = self._decrypt(channel)
            self._notifiers.get(channel.type).send(message, config)
        except (DeliveryError, secret_box.SecretsUnavailableError) as exc:
            logger.warning(
                "Notification via %s channel %s failed: %s", channel.type, channel.id, exc
            )
            return Delivery(channel.id, ok=False, error=str(exc))
        logger.info(
            "Notification %s sent via %s channel %s", message.event, channel.type, channel.id
        )
        return Delivery(channel.id, ok=True)

    def _validate(self, channel_type: str, raw: dict[str, object]) -> ChannelConfig:
        try:
            config = self._notifiers.get(channel_type).validate(raw)
        except ChannelConfigError as exc:
            raise InvalidChannelError(details={"config": str(exc)}) from exc
        if config.secret and not self._box.available:
            raise SecretsUnavailableError()
        return config

    def _encrypt(self, config: ChannelConfig) -> str | None:
        return self._box.encrypt(json.dumps(config.secret)) if config.secret else None

    def _decrypt(self, channel: NotificationChannel) -> ChannelConfig:
        secret = (
            json.loads(self._box.decrypt(channel.secret_config)) if channel.secret_config else {}
        )
        return ChannelConfig(public=dict(channel.config), secret=secret)

    def _channels(self, user_id: uuid.UUID) -> Sequence[NotificationChannel]:
        return self._session.scalars(
            select(NotificationChannel)
            .where(NotificationChannel.user_id == user_id)
            .order_by(NotificationChannel.created_at, NotificationChannel.id)
        ).all()

    def _owned(self, owner: User, channel_id: uuid.UUID) -> NotificationChannel:
        channel = self._session.get(NotificationChannel, channel_id)
        if channel is None or channel.user_id != owner.id:
            raise NotFoundError()
        return channel

    @staticmethod
    def _out(channel: NotificationChannel) -> ChannelOut:
        return ChannelOut.model_validate(
            {
                "id": channel.id,
                "type": channel.type,
                "name": channel.name,
                "config": channel.config,
                "events": channel.events,
                "enabled": channel.enabled,
                "created_at": channel.created_at,
            }
        )
