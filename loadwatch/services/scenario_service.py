"""Сценарии: создание, изменение, секреты и пробный проход (dry-run)."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from loadwatch.config import Settings
from loadwatch.errors import (
    ForbiddenTargetError,
    NotFoundError,
    ScenarioExistsError,
    SecretsUnavailableError,
    SiteNotVerifiedError,
    TargetUnresolvableError,
)
from loadwatch.flow import FlowExecutor, StepResult, parse_flow
from loadwatch.models import Scenario, ScenarioSecret, Site, User
from loadwatch.repositories.scenario_repository import ScenarioRepository
from loadwatch.repositories.site_repository import SiteRepository
from loadwatch.runner.http import PinnedRequester, new_load_client
from loadwatch.runner.profiles import ProfileLimits, profile_from_dict
from loadwatch.schemas.common import Page
from loadwatch.schemas.scenario import (
    DryRunOut,
    ScenarioIn,
    ScenarioOut,
    SecretOut,
    SecretsUpdate,
    StepResultOut,
)
from loadwatch.security import secrets as secret_box
from loadwatch.security import ssrf

logger = logging.getLogger(__name__)

DRY_RUN_DEADLINE_SEC = 60.0


def load_secrets(session: Session, settings: Settings, scenario_id: uuid.UUID) -> dict[str, str]:
    """Расшифрованные секреты сценария (только для выполнения, наружу не отдаются)."""
    rows = ScenarioRepository(session).secrets(scenario_id)
    if not rows:
        return {}
    box = secret_box.SecretBox(_key(settings))
    return {row.name: box.decrypt(row.value_encrypted) for row in rows}


def _key(settings: Settings) -> str | None:
    key = settings.secrets_encryption_key
    return key.get_secret_value() if key is not None else None


class ScenarioService:
    """Сценарии сайтов владельца."""

    def __init__(
        self,
        session: Session,
        settings: Settings,
        resolver: ssrf.Resolver = ssrf.system_resolver,
    ) -> None:
        self._session = session
        self._settings = settings
        self._resolver = resolver
        self._scenarios = ScenarioRepository(session)
        self._sites = SiteRepository(session)

    def create(self, owner: User, site_id: uuid.UUID, data: ScenarioIn) -> ScenarioOut:
        """Новый сценарий сайта."""
        site = self._site(owner, site_id)
        if self._scenarios.get_by_name(site.id, data.name) is not None:
            raise ScenarioExistsError()
        scenario = Scenario(site_id=site.id)
        self._apply(scenario, data)
        try:
            self._scenarios.add(scenario)
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            raise ScenarioExistsError() from exc
        return self.to_out(scenario)

    def list_page(
        self, owner: User, site_id: uuid.UUID, *, limit: int, offset: int
    ) -> Page[ScenarioOut]:
        """Сценарии сайта."""
        site = self._site(owner, site_id)
        items = self._scenarios.list_for_site(site.id, limit=limit, offset=offset)
        return Page[ScenarioOut](
            items=[self.to_out(item) for item in items],
            total=self._scenarios.count_for_site(site.id),
            limit=limit,
            offset=offset,
        )

    def get(self, owner: User, scenario_id: uuid.UUID) -> Scenario:
        """Сценарий владельца; чужой — 404."""
        scenario = self._scenarios.get_owned(scenario_id, owner.id)
        if scenario is None:
            raise NotFoundError()
        return scenario

    def update(self, owner: User, scenario_id: uuid.UUID, data: ScenarioIn) -> ScenarioOut:
        """Полная замена сценария (секреты не меняются)."""
        scenario = self.get(owner, scenario_id)
        other = self._scenarios.get_by_name(scenario.site_id, data.name)
        if other is not None and other.id != scenario.id:
            raise ScenarioExistsError()
        self._apply(scenario, data)
        self._session.commit()
        return self.to_out(scenario)

    def delete(self, owner: User, scenario_id: uuid.UUID) -> None:
        """Удаляет сценарий; история его запусков остаётся."""
        self._scenarios.delete(self.get(owner, scenario_id))
        self._session.commit()

    def set_secrets(self, owner: User, scenario_id: uuid.UUID, data: SecretsUpdate) -> ScenarioOut:
        """Задаёт или удаляет секреты; значения шифруются и больше не показываются."""
        scenario = self.get(owner, scenario_id)
        box = secret_box.SecretBox(_key(self._settings))
        if not box.available and any(value is not None for value in data.secrets.values()):
            raise SecretsUnavailableError()
        existing = {row.name: row for row in self._scenarios.secrets(scenario.id)}
        for name, value in data.secrets.items():
            row = existing.get(name)
            if value is None:
                if row is not None:
                    self._scenarios.delete_secret(row)
            elif row is None:
                self._scenarios.add_secret(
                    ScenarioSecret(
                        scenario_id=scenario.id, name=name, value_encrypted=box.encrypt(value)
                    )
                )
            else:
                row.value_encrypted = box.encrypt(value)
        self._session.commit()
        # Имена секретов в лог можно, значения — никогда.
        logger.info("Secrets of scenario %s updated: %s", scenario.id, sorted(data.secrets))
        return self.to_out(scenario)

    def dry_run(self, owner: User, scenario_id: uuid.UUID) -> DryRunOut:
        """Один проход потока одним пользователем, синхронно и с общим таймаутом."""
        scenario = self.get(owner, scenario_id)
        site = self._session.get(Site, scenario.site_id)
        if site is None or not site.is_verified:
            raise SiteNotVerifiedError()
        try:
            secrets = load_secrets(self._session, self._settings, scenario.id)
        except secret_box.SecretsUnavailableError as exc:
            raise SecretsUnavailableError() from exc
        flow = parse_flow(scenario.flow)
        started = time.perf_counter()
        results: list[StepResult] = []
        error = None
        try:
            asyncio.run(self._run_once(site, FlowExecutor(flow, secrets), results))
        except TimeoutError:
            error = "dry_run_timeout"
        except ssrf.TargetForbiddenError as exc:
            raise ForbiddenTargetError() from exc
        except ssrf.TargetResolutionError as exc:
            raise TargetUnresolvableError() from exc
        done = {result.name for result in results}
        return DryRunOut(
            ok=error is None and len(results) == len(flow.steps) and all(r.ok for r in results),
            error=error,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
            steps=[
                StepResultOut.model_validate(result, from_attributes=True) for result in results
            ],
            skipped_steps=[step.name for step in flow.steps if step.name not in done],
        )

    async def _run_once(
        self, site: Site, executor: FlowExecutor, results: list[StepResult]
    ) -> None:
        """Резолв с SSRF-проверкой и проход потока — под одним общим дедлайном."""
        settings = self._settings
        allow_private = settings.allow_private_targets
        async with asyncio.timeout(DRY_RUN_DEADLINE_SEC):
            target = ssrf.parse_target_url(site.url, allow_private=allow_private)
            started = time.perf_counter()
            pinned = await asyncio.to_thread(
                ssrf.resolve_target, target, resolver=self._resolver, allow_private=allow_private
            )
            dns_ms = (time.perf_counter() - started) * 1000
            async with new_load_client(
                max_connections=1, timeout_sec=settings.request_timeout_sec
            ) as client:
                requester = PinnedRequester(
                    client,
                    pinned,
                    dns_ms=dns_ms,
                    max_response_bytes=settings.max_response_bytes,
                    deadline_sec=settings.request_timeout_sec,
                )
                await executor.run(requester, skip_think=True, results=results)

    def to_out(self, scenario: Scenario) -> ScenarioOut:
        """Сценарий для ответа API: секреты — только имена и маски."""
        secret_names = [row.name for row in self._scenarios.secrets(scenario.id)]
        flow = parse_flow(scenario.flow)
        return ScenarioOut(
            id=scenario.id,
            site_id=scenario.site_id,
            name=scenario.name,
            load_profile=scenario.load_profile,
            effective_load_profile=profile_from_dict(scenario.load_profile)
            .clamp(self._limits())
            .to_dict(),
            flow=scenario.flow,
            apdex_t_ms=scenario.apdex_t_ms,
            slo_p95_ms=scenario.slo_p95_ms,
            slo_max_error_rate=scenario.slo_max_error_rate,
            secrets=[SecretOut(name=name, value=secret_box.MASK) for name in secret_names],
            missing_secrets=sorted(flow.secret_names() - set(secret_names)),
            created_at=scenario.created_at,
            updated_at=scenario.updated_at,
        )

    def _limits(self) -> ProfileLimits:
        return ProfileLimits(
            max_rate=self._settings.max_rps, max_duration_sec=self._settings.max_duration_sec
        )

    def _apply(self, scenario: Scenario, data: ScenarioIn) -> None:
        scenario.name = data.name
        scenario.load_profile = data.load_profile.model_dump()
        scenario.flow = [step.model_dump(mode="json") for step in data.flow]
        scenario.apdex_t_ms = data.apdex_t_ms
        scenario.slo_p95_ms = data.slo_p95_ms
        scenario.slo_max_error_rate = data.slo_max_error_rate

    def _site(self, owner: User, site_id: uuid.UUID) -> Site:
        site = self._sites.get_owned(site_id, owner.id)
        if site is None:
            raise NotFoundError()
        return site
