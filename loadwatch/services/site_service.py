"""Сайты пользователя: добавление, удаление, проверка и перепроверка владения."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from loadwatch.config import Settings
from loadwatch.db import utcnow
from loadwatch.errors import (
    ForbiddenTargetError,
    InvalidSiteUrlError,
    NotFoundError,
    SiteExistsError,
    VerificationFailedError,
)
from loadwatch.models import Site, User, VerificationStatus
from loadwatch.repositories.site_repository import SiteRepository
from loadwatch.schemas.common import Page
from loadwatch.schemas.site import (
    SiteCreate,
    SiteOut,
    VerificationInfoOut,
    VerificationInstructionOut,
)
from loadwatch.security import ssrf
from loadwatch.verification import (
    VerificationOutcome,
    VerificationResult,
    VerifierRegistry,
)

logger = logging.getLogger(__name__)

NAME_MAX_LENGTH = 100
URL_MAX_LENGTH = 300
# Если перепроверка не удалась из-за сети, сайт остаётся подтверждённым ещё столько.
REVERIFY_GRACE = timedelta(days=1)
# Повторная попытка после неудачной (сетевой) перепроверки — не чаще.
REVERIFY_RETRY_INTERVAL = timedelta(hours=1)


@dataclass(frozen=True, slots=True)
class ReverificationReport:
    """Итог перепроверки одного сайта (для runner, логов и будущих алертов)."""

    site_id: uuid.UUID
    owner_id: uuid.UUID
    outcome: VerificationOutcome
    lost_ownership: bool


def target_of(site: Site, settings: Settings) -> ssrf.TargetUrl:
    """Разбирает сохранённый URL сайта (он уже нормализован при добавлении)."""
    return ssrf.parse_target_url(site.url, allow_private=settings.allow_private_targets)


class SiteService:
    """Бизнес-логика сайтов; все операции — только над сайтами владельца."""

    def __init__(
        self,
        session: Session,
        settings: Settings,
        verifiers: VerifierRegistry,
        resolver: ssrf.Resolver = ssrf.system_resolver,
    ) -> None:
        self._session = session
        self._settings = settings
        self._verifiers = verifiers
        self._resolver = resolver
        self._sites = SiteRepository(session)

    def create(self, owner: User, data: SiteCreate) -> Site:
        """Добавляет сайт в статусе ``pending``."""
        target = self._parse_origin(data.url)
        if len(target.origin) > URL_MAX_LENGTH:  # punycode длиннее исходного IDN
            raise InvalidSiteUrlError()
        if self._sites.get_by_url(owner.id, target.origin) is not None:
            raise SiteExistsError()
        site = Site(
            owner_id=owner.id,
            url=target.origin,
            name=((data.name or "").strip() or target.host)[:NAME_MAX_LENGTH],
            verification_status=VerificationStatus.PENDING.value,
        )
        try:
            self._sites.add(site)
            self._session.commit()
        except IntegrityError as exc:  # параллельное добавление того же адреса
            self._session.rollback()
            raise SiteExistsError() from exc
        logger.info("Site %s added by user %s", site.id, owner.id)
        return site

    def list_page(self, owner: User, *, limit: int, offset: int) -> Page[SiteOut]:
        """Страница сайтов пользователя."""
        items = self._sites.list_owned(owner.id, limit=limit, offset=offset)
        return Page[SiteOut](
            items=[SiteOut.model_validate(site) for site in items],
            total=self._sites.count_owned(owner.id),
            limit=limit,
            offset=offset,
        )

    def get(self, owner: User, site_id: uuid.UUID) -> Site:
        """Сайт владельца; чужой и несуществующий неразличимы (404)."""
        site = self._sites.get_owned(site_id, owner.id)
        if site is None:
            raise NotFoundError()
        return site

    def delete(self, owner: User, site_id: uuid.UUID) -> None:
        """Удаляет сайт владельца."""
        self._sites.delete(self.get(owner, site_id))
        self._session.commit()
        logger.info("Site %s deleted by user %s", site_id, owner.id)

    def verification_info(self, owner: User, site_id: uuid.UUID) -> VerificationInfoOut:
        """Токен, статус и инструкции для обоих способов."""
        site = self.get(owner, site_id)
        target = target_of(site, self._settings)
        instructions = [
            verifier.instructions(target, site.verification_token)
            for verifier in self._verifiers.all()
        ]
        # model_validate проверяет, что строки из БД — допустимые значения Literal.
        return VerificationInfoOut.model_validate(
            {
                "status": site.verification_status,
                "method": site.verification_method,
                "token": site.verification_token,
                "last_verified_at": site.last_verified_at,
                "reverify_days": self._settings.reverify_days,
                "instructions": [
                    VerificationInstructionOut.model_validate(item, from_attributes=True)
                    for item in instructions
                ],
            }
        )

    def verify(self, owner: User, site_id: uuid.UUID, method: str) -> Site:
        """Проверяет владение выбранным способом; неудача — ``verification_failed``."""
        site = self.get(owner, site_id)
        result = self._check(site, method)
        if result.outcome is VerificationOutcome.FORBIDDEN and site.is_verified:
            # Сайт теперь смотрит во внутреннюю сеть — тестировать его больше нельзя.
            site.verification_status = VerificationStatus.UNVERIFIED.value
            self._session.commit()
        if not result.ok:
            # Остальные неудачи статус не трогают: неудачная ручная попытка
            # (например, выбран не тот способ) не отзывает прежнее подтверждение.
            raise VerificationFailedError(
                details={"reason": result.outcome.value, "detail": result.detail}
            )
        now = utcnow()
        if not site.is_verified:
            site.verified_at = now
        site.verification_status = VerificationStatus.VERIFIED.value
        site.verification_method = method
        site.last_verified_at = now
        site.verification_checked_at = now
        self._session.commit()
        logger.info("Site %s verified via %s", site.id, method)
        return site

    def reverify_due(
        self, *, now: datetime | None = None, limit: int = 20
    ) -> list[ReverificationReport]:
        """Перепроверяет сайты, подтверждённые больше ``REVERIFY_DAYS`` назад.

        Токен пропал или сайт смотрит во внутреннюю сеть — сайт сразу становится
        ``unverified`` и тесты останавливаются. Сетевая ошибка — повтор через час;
        если проверить не удаётся дольше суток сверх срока, сайт тоже снимается.
        """
        now = now or utcnow()
        period = timedelta(days=self._settings.reverify_days)
        reports: list[ReverificationReport] = []
        due = self._sites.due_for_reverification(
            verified_before=now - period,
            checked_before=now - REVERIFY_RETRY_INTERVAL,
            limit=limit,
        )
        for site in due:
            try:
                result = self._check(site, site.verification_method or "http")
            except Exception:  # ошибка одного сайта не должна останавливать всю пачку
                logger.exception("Reverification of site %s crashed", site.id)
                result = VerificationResult(VerificationOutcome.ERROR, "internal error")
            site.verification_checked_at = now
            lost = False
            if result.ok:
                site.last_verified_at = now
            elif result.outcome is not VerificationOutcome.ERROR or (
                site.last_verified_at is not None
                and now - site.last_verified_at > period + REVERIFY_GRACE
            ):
                site.verification_status = VerificationStatus.UNVERIFIED.value
                lost = True
                logger.warning(
                    "Site %s lost verification: %s (%s)", site.id, result.outcome, result.detail
                )
            self._session.commit()
            reports.append(ReverificationReport(site.id, site.owner_id, result.outcome, lost))
        return reports

    def _check(self, site: Site, method: str) -> VerificationResult:
        """SSRF-проверка адреса сайта, затем проверка токена выбранным способом."""
        allow_private = self._settings.allow_private_targets
        try:
            target = ssrf.parse_target_url(site.url, allow_private=allow_private)
            ssrf.resolve_target(target, resolver=self._resolver, allow_private=allow_private)
        except ssrf.TargetForbiddenError as exc:
            return VerificationResult(VerificationOutcome.FORBIDDEN, str(exc))
        except ssrf.TargetResolutionError as exc:
            return VerificationResult(VerificationOutcome.ERROR, str(exc))
        return self._verifiers.get(method).verify(target, site.verification_token)

    def _parse_origin(self, url: str) -> ssrf.TargetUrl:
        """Принимает только origin: схема, хост, порт; путь — пустой или ``/``."""
        try:
            parts = urlsplit(url.strip())
        except ValueError as exc:
            raise InvalidSiteUrlError() from exc
        if parts.path not in ("", "/") or parts.query or parts.fragment:
            raise InvalidSiteUrlError()
        try:
            return ssrf.parse_target_url(url, allow_private=self._settings.allow_private_targets)
        except ssrf.InvalidTargetUrlError as exc:
            raise InvalidSiteUrlError() from exc
        except ssrf.TargetForbiddenError as exc:
            raise ForbiddenTargetError() from exc
