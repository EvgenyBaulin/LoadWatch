"""Фабрика приложения FastAPI.

Запуск: ``uvicorn --factory loadwatch.api.main:create_app``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from loadwatch import __version__
from loadwatch.api.error_handlers import register_error_handlers
from loadwatch.api.rate_limit import SlidingWindowRateLimiter
from loadwatch.api.routers import (
    auth,
    dashboard,
    exports,
    health,
    notifications,
    runs,
    scenarios,
    sites,
    users,
)
from loadwatch.config import Settings, get_settings
from loadwatch.db import create_db_engine, create_session_factory
from loadwatch.logging_setup import configure_logging, warn_about_unsafe_settings
from loadwatch.notifications import NotifierRegistry
from loadwatch.security.ssrf import system_resolver
from loadwatch.verification import default_registry

API_PREFIX = "/api/v1"


def create_app(settings: Settings | None = None) -> FastAPI:
    """Собирает приложение; состояние лежит в ``app.state``, сам API без состояния."""
    settings = settings or get_settings()
    configure_logging(settings)
    warn_about_unsafe_settings(settings)
    engine = create_db_engine(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        engine.dispose()

    app = FastAPI(
        title="LoadWatch API",
        version=__version__,
        description="Website performance monitoring through scheduled load tests.",
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url=f"{API_PREFIX}/docs",
        # ReDoc — основная документация API (по просьбе владельца), Swagger — для проб запросов.
        redoc_url=f"{API_PREFIX}/redoc",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    app.state.auth_rate_limiter = SlidingWindowRateLimiter(settings.auth_rate_limit_per_minute)
    app.state.verify_rate_limiter = SlidingWindowRateLimiter(settings.verify_rate_limit_per_minute)
    app.state.dry_run_rate_limiter = SlidingWindowRateLimiter(
        settings.dry_run_rate_limit_per_minute
    )
    # Подменяются в тестах: сеть и DNS в тестах не используются.
    app.state.verifiers = default_registry(settings)
    app.state.resolver = system_resolver
    app.state.notifiers = NotifierRegistry(settings)
    app.state.hook_rate_limiter = SlidingWindowRateLimiter(settings.hook_rate_limit_per_minute)
    app.state.hook_ip_rate_limiter = SlidingWindowRateLimiter(
        settings.hook_rate_limit_per_minute * 3
    )
    app.state.channel_test_rate_limiter = SlidingWindowRateLimiter(
        settings.channel_test_rate_limit_per_minute
    )

    register_error_handlers(app)
    # Добавляется последним — значит, самый внешний и оборачивает и ответы с ошибками.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )
    for module in (health, auth, users, sites, scenarios, runs, dashboard, notifications, exports):
        app.include_router(module.router, prefix=API_PREFIX)
    return app
