"""Перепроверка владения раз в ``REVERIFY_DAYS``."""

from collections.abc import Callable, Iterator
from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from loadwatch.config import Settings
from loadwatch.db import utcnow
from loadwatch.models import Site, User, VerificationStatus
from loadwatch.services.site_service import SiteService
from loadwatch.verification import VerificationOutcome, VerifierRegistry
from tests.conftest import FakeDns, FakeVerifier

DAY = timedelta(days=1)


@pytest.fixture
def session(clean_database: str) -> Iterator[Session]:
    engine = create_engine(clean_database)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


@pytest.fixture
def service(
    session: Session,
    make_settings: Callable[..., Settings],
    fake_verifiers: dict[str, FakeVerifier],
    fake_dns: FakeDns,
) -> SiteService:
    registry = VerifierRegistry(list(fake_verifiers.values()))
    return SiteService(session, make_settings(reverify_days=7), registry, fake_dns)


def make_site(session: Session, *, verified_days_ago: float, host: str = "example.com") -> Site:
    user = User(email=f"{host}@example.com", password_hash="x")
    session.add(user)
    session.flush()
    verified = utcnow() - timedelta(days=verified_days_ago)
    site = Site(
        owner_id=user.id,
        name=host,
        url=f"https://{host}",
        verification_status=VerificationStatus.VERIFIED.value,
        verification_method="dns",
        verified_at=verified,
        last_verified_at=verified,
    )
    session.add(site)
    session.commit()
    return site


def test_fresh_site_is_not_rechecked(
    session: Session, service: SiteService, fake_verifiers: dict[str, FakeVerifier]
) -> None:
    make_site(session, verified_days_ago=3)
    assert service.reverify_due() == []
    assert fake_verifiers["dns"].calls == []


def test_due_site_still_owned(session: Session, service: SiteService) -> None:
    site = make_site(session, verified_days_ago=8)
    now = utcnow()
    [report] = service.reverify_due(now=now)
    assert report.outcome is VerificationOutcome.VERIFIED
    assert not report.lost_ownership
    session.refresh(site)
    assert site.is_verified
    assert site.last_verified_at == now
    assert service.reverify_due() == []  # следующая — через 7 дней


def test_token_removed_stops_tests(
    session: Session, service: SiteService, fake_verifiers: dict[str, FakeVerifier]
) -> None:
    site = make_site(session, verified_days_ago=8)
    fake_verifiers["dns"].outcome = VerificationOutcome.TOKEN_NOT_FOUND
    [report] = service.reverify_due()
    assert report.lost_ownership
    session.refresh(site)
    assert site.verification_status == "unverified"
    assert not site.is_verified


def test_site_now_resolving_to_private_network_is_unverified(
    session: Session, service: SiteService, fake_dns: FakeDns
) -> None:
    site = make_site(session, verified_days_ago=8, host="rebind.example")
    fake_dns.records["rebind.example"] = ["10.0.0.1"]
    [report] = service.reverify_due()
    assert report.outcome is VerificationOutcome.FORBIDDEN
    session.refresh(site)
    assert site.verification_status == "unverified"


def test_network_error_keeps_site_within_grace_and_retries_later(
    session: Session, service: SiteService, fake_verifiers: dict[str, FakeVerifier]
) -> None:
    site = make_site(session, verified_days_ago=7.5)
    fake_verifiers["dns"].outcome = VerificationOutcome.ERROR
    now = utcnow()
    [report] = service.reverify_due(now=now)
    assert not report.lost_ownership
    session.refresh(site)
    assert site.is_verified
    assert service.reverify_due(now=now + timedelta(minutes=30)) == []  # не долбим сайт
    assert len(service.reverify_due(now=now + timedelta(hours=2))) == 1


def test_network_error_beyond_grace_stops_tests(
    session: Session, service: SiteService, fake_verifiers: dict[str, FakeVerifier]
) -> None:
    site = make_site(session, verified_days_ago=9)
    fake_verifiers["dns"].outcome = VerificationOutcome.ERROR
    [report] = service.reverify_due()
    assert report.lost_ownership
    session.refresh(site)
    assert site.verification_status == "unverified"


def test_unverified_and_pending_sites_are_ignored(session: Session, service: SiteService) -> None:
    site = make_site(session, verified_days_ago=30)
    site.verification_status = VerificationStatus.UNVERIFIED.value
    session.commit()
    assert service.reverify_due() == []


def test_batch_limit(session: Session, service: SiteService) -> None:
    for i in range(3):
        make_site(session, verified_days_ago=8 + i, host=f"s{i}.example")
    assert len(service.reverify_due(limit=2)) == 2
    assert len(service.reverify_due(limit=2)) == 1


def test_crash_on_one_site_does_not_stop_batch(
    session: Session, service: SiteService, fake_verifiers: dict[str, FakeVerifier]
) -> None:
    make_site(session, verified_days_ago=8, host="a.example")
    make_site(session, verified_days_ago=8.5, host="b.example")
    original = fake_verifiers["dns"].verify
    calls = {"n": 0}

    def flaky(target: object, token: str) -> object:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return original(target, token)  # type: ignore[arg-type]

    fake_verifiers["dns"].verify = flaky  # type: ignore[method-assign, assignment]
    reports = service.reverify_due()
    assert [r.outcome for r in reports] == [VerificationOutcome.ERROR, VerificationOutcome.VERIFIED]
