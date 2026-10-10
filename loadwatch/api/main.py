"""FastAPI application factory.

Run with ``uvicorn --factory loadwatch.api.main:create_app``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from loadwatch import __version__
from loadwatch.api.error_handlers import register_error_handlers
from loadwatch.api.rate_limit import SlidingWindowRateLimiter
from loadwatch.api.routers import auth, health, users
from loadwatch.config import Settings, get_settings
from loadwatch.db import create_db_engine, create_session_factory
from loadwatch.logging_setup import configure_logging, warn_about_unsafe_settings

API_PREFIX = "/api/v1"


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application; state lives on ``app.state`` so the API stays stateless."""
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
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    app.state.auth_rate_limiter = SlidingWindowRateLimiter(settings.auth_rate_limit_per_minute)

    register_error_handlers(app)
    # Added last, so it is the outermost middleware and also wraps error responses.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )
    for module in (health, auth, users):
        app.include_router(module.router, prefix=API_PREFIX)
    return app
