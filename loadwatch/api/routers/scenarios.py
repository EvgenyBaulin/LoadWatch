"""Сценарии сайта, их секреты и пробный проход."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from loadwatch.api.deps import CurrentUser, ScenarioServiceDep
from loadwatch.schemas.common import Page, PageParams
from loadwatch.schemas.scenario import DryRunOut, ScenarioIn, ScenarioOut, SecretsUpdate

router = APIRouter(tags=["scenarios"])


@router.post("/sites/{site_id}/scenarios", status_code=status.HTTP_201_CREATED)
def create_scenario(
    site_id: uuid.UUID, data: ScenarioIn, user: CurrentUser, service: ScenarioServiceDep
) -> ScenarioOut:
    """Создать сценарий: профиль нагрузки, шаги потока, SLO и Apdex."""
    return service.create(user, site_id, data)


@router.get("/sites/{site_id}/scenarios")
def list_scenarios(
    site_id: uuid.UUID,
    user: CurrentUser,
    service: ScenarioServiceDep,
    page: Annotated[PageParams, Depends()],
) -> Page[ScenarioOut]:
    """Сценарии сайта."""
    return service.list_page(user, site_id, limit=page.limit, offset=page.offset)


@router.get("/scenarios/{scenario_id}")
def get_scenario(
    scenario_id: uuid.UUID, user: CurrentUser, service: ScenarioServiceDep
) -> ScenarioOut:
    """Один сценарий; секреты — только маски."""
    return service.to_out(service.get(user, scenario_id))


@router.put("/scenarios/{scenario_id}")
def update_scenario(
    scenario_id: uuid.UUID, data: ScenarioIn, user: CurrentUser, service: ScenarioServiceDep
) -> ScenarioOut:
    """Заменить сценарий целиком (секреты сохраняются)."""
    return service.update(user, scenario_id, data)


@router.delete("/scenarios/{scenario_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_scenario(
    scenario_id: uuid.UUID, user: CurrentUser, service: ScenarioServiceDep
) -> Response:
    """Удалить сценарий; история запусков остаётся."""
    service.delete(user, scenario_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/scenarios/{scenario_id}/secrets")
def set_secrets(
    scenario_id: uuid.UUID, data: SecretsUpdate, user: CurrentUser, service: ScenarioServiceDep
) -> ScenarioOut:
    """Задать секреты (только запись): в шагах — ``{{secret.NAME}}``, ``null`` удаляет."""
    return service.set_secrets(user, scenario_id, data)


@router.post("/scenarios/{scenario_id}/dry-run")
def dry_run(
    scenario_id: uuid.UUID, request: Request, user: CurrentUser, service: ScenarioServiceDep
) -> DryRunOut:
    """Один проход потока одним пользователем: результат по каждому шагу."""
    # Каждый dry-run — реальные запросы к сайту, поэтому отдельный мягкий лимит.
    request.app.state.dry_run_rate_limiter.hit(f"dry-run:{user.id}")
    return service.dry_run(user, scenario_id)
