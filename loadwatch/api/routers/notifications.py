"""Каналы уведомлений, триггер из CI, метки на графиках и алерты сайта."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from loadwatch.api.deps import ChannelServiceDep, CurrentUser, HookServiceDep
from loadwatch.api.rate_limit import client_key
from loadwatch.schemas.channel import ChannelIn, ChannelOut, ChannelTestOut
from loadwatch.schemas.hooks import (
    AlertOut,
    AnnotationIn,
    AnnotationOut,
    HookRunIn,
    HookRunOut,
    TriggerTokenOut,
)

router = APIRouter()
_trigger_bearer = HTTPBearer(auto_error=False, description="Токен триггера сайта")


@router.get("/users/me/channels", tags=["notifications"])
def list_channels(user: CurrentUser, service: ChannelServiceDep) -> list[ChannelOut]:
    """Каналы уведомлений текущего пользователя."""
    return service.list_for(user)


@router.post("/users/me/channels", status_code=status.HTTP_201_CREATED, tags=["notifications"])
def create_channel(data: ChannelIn, user: CurrentUser, service: ChannelServiceDep) -> ChannelOut:
    """Добавить канал: Telegram, Email, Slack, Discord или webhook с подписью."""
    return service.create(user, data)


@router.put("/users/me/channels/{channel_id}", tags=["notifications"])
def update_channel(
    channel_id: uuid.UUID, data: ChannelIn, user: CurrentUser, service: ChannelServiceDep
) -> ChannelOut:
    """Изменить канал (тип менять нельзя)."""
    return service.update(user, channel_id, data)


@router.delete(
    "/users/me/channels/{channel_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["notifications"],
)
def delete_channel(
    channel_id: uuid.UUID, user: CurrentUser, service: ChannelServiceDep
) -> Response:
    """Удалить канал."""
    service.delete(user, channel_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/users/me/channels/{channel_id}/test", tags=["notifications"])
def test_channel(
    channel_id: uuid.UUID, request: Request, user: CurrentUser, service: ChannelServiceDep
) -> ChannelTestOut:
    """Отправить тестовое уведомление и сразу узнать результат."""
    request.app.state.channel_test_rate_limiter.hit(f"channel-test:{user.id}")
    return service.send_test(user, channel_id)


@router.post("/sites/{site_id}/trigger-token", tags=["hooks"])
def issue_trigger_token(
    site_id: uuid.UUID, user: CurrentUser, service: HookServiceDep
) -> TriggerTokenOut:
    """Выпустить или перевыпустить токен для запуска тестов из CI."""
    return service.issue_token(user, site_id)


@router.post("/hooks/sites/{site_id}/run", status_code=status.HTTP_201_CREATED, tags=["hooks"])
def hook_run(
    site_id: uuid.UUID,
    request: Request,
    service: HookServiceDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_trigger_bearer)],
    data: HookRunIn | None = None,
) -> HookRunOut:
    """Запуск теста из CI после деплоя: ``Authorization: Bearer <токен триггера>``.

    Необязательный ``label`` ставит метку деплоя на графиках. Проверки те же, что у
    ручного запуска: подтверждённый сайт, пауза между тестами, один тест за раз.
    """
    # До проверки токена — лимит по IP: перебор токенов и мусорные id не исчерпают
    # лимит настоящего сайта. После проверки — лимит на сайт.
    request.app.state.hook_ip_rate_limiter.hit(f"hook-ip:{client_key(request)}")
    token = credentials.credentials if credentials is not None else None
    site = service.authenticate(site_id, token)
    request.app.state.hook_rate_limiter.hit(f"hook:{site.id}")
    return service.trigger(site, data or HookRunIn())


@router.get("/sites/{site_id}/annotations", tags=["dashboard"])
def list_annotations(
    site_id: uuid.UUID,
    user: CurrentUser,
    service: HookServiceDep,
    since: Annotated[datetime | None, Query(alias="from")] = None,
    until: Annotated[datetime | None, Query(alias="to")] = None,
) -> list[AnnotationOut]:
    """Метки на графиках за период (по умолчанию 30 дней)."""
    return service.annotations(user, site_id, since=since, until=until)


@router.post(
    "/sites/{site_id}/annotations", status_code=status.HTTP_201_CREATED, tags=["dashboard"]
)
def add_annotation(
    site_id: uuid.UUID, data: AnnotationIn, user: CurrentUser, service: HookServiceDep
) -> AnnotationOut:
    """Добавить комментарий на графики."""
    return service.add_annotation(user, site_id, data)


@router.delete(
    "/sites/{site_id}/annotations/{annotation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["dashboard"],
)
def delete_annotation(
    site_id: uuid.UUID, annotation_id: uuid.UUID, user: CurrentUser, service: HookServiceDep
) -> Response:
    """Удалить метку."""
    service.delete_annotation(user, site_id, annotation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/sites/{site_id}/alerts", tags=["dashboard"])
def list_alerts(
    site_id: uuid.UUID,
    user: CurrentUser,
    service: HookServiceDep,
    status_filter: Annotated[Literal["open", "resolved"] | None, Query(alias="status")] = None,
) -> list[AlertOut]:
    """Алерты сайта (открытые и закрытые), новые сверху."""
    return service.alerts(user, site_id, status=status_filter)
