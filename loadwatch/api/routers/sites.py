"""``/sites``: сайты пользователя и проверка владения."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from loadwatch.api.deps import AlertManagerDep, CurrentUser, SiteServiceDep
from loadwatch.errors import VerificationFailedError
from loadwatch.schemas.common import Page, PageParams
from loadwatch.schemas.site import SiteCreate, SiteOut, VerificationInfoOut, VerifyRequest

router = APIRouter(prefix="/sites", tags=["sites"])


@router.post("", status_code=status.HTTP_201_CREATED)
def create_site(data: SiteCreate, user: CurrentUser, service: SiteServiceDep) -> SiteOut:
    """Добавить сайт (origin вида ``https://example.com``); тесты — после подтверждения."""
    return SiteOut.model_validate(service.create(user, data))


@router.get("")
def list_sites(
    user: CurrentUser, service: SiteServiceDep, page: Annotated[PageParams, Depends()]
) -> Page[SiteOut]:
    """Сайты текущего пользователя, новые сверху."""
    return service.list_page(user, limit=page.limit, offset=page.offset)


@router.get("/{site_id}")
def get_site(site_id: uuid.UUID, user: CurrentUser, service: SiteServiceDep) -> SiteOut:
    """Один сайт."""
    return SiteOut.model_validate(service.get(user, site_id))


@router.delete("/{site_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_site(site_id: uuid.UUID, user: CurrentUser, service: SiteServiceDep) -> Response:
    """Удалить сайт вместе со сценариями и историей запусков."""
    service.delete(user, site_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{site_id}/verification")
def get_verification(
    site_id: uuid.UUID, user: CurrentUser, service: SiteServiceDep
) -> VerificationInfoOut:
    """Токен и инструкции: TXT-запись или файл ``/.well-known``."""
    return service.verification_info(user, site_id)


@router.post("/{site_id}/verify")
def verify_site(
    site_id: uuid.UUID,
    data: VerifyRequest,
    request: Request,
    user: CurrentUser,
    service: SiteServiceDep,
    alerts: AlertManagerDep,
) -> SiteOut:
    """Проверить владение; при неудаче — 422 ``verification_failed`` с причиной."""
    # Каждая проверка — исходящий DNS/HTTP-запрос, поэтому частота ограничена.
    request.app.state.verify_rate_limiter.hit(f"verify:{user.id}")
    try:
        site = service.verify(user, site_id, data.method)
    except VerificationFailedError:
        # Проверка могла снять подтверждение (адрес ушёл во внутреннюю сеть) — это алерт.
        if service.get(user, site_id).verification_status == "unverified":
            alerts.site_lost(site_id)
        raise
    alerts.site_reverified(site.id)  # если был алерт «владение потеряно» — закрываем
    return SiteOut.model_validate(site)
