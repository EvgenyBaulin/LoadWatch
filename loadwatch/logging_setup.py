"""Process-wide logging configuration."""

from __future__ import annotations

import logging

from loadwatch.config import Environment, Settings

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_logging(settings: Settings) -> None:
    """Configure the root logger once; repeated calls are harmless."""
    level = logging.DEBUG if settings.environment is Environment.DEVELOPMENT else logging.INFO
    logging.basicConfig(level=level, format=LOG_FORMAT)
    # SQL echo and per-request access lines are noisy at DEBUG.
    logging.getLogger("sqlalchemy").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def warn_about_unsafe_settings(settings: Settings) -> None:
    """Log loud warnings for settings that weaken security."""
    log = logging.getLogger("loadwatch")
    if settings.allow_private_targets:
        log.warning(
            "ALLOW_PRIVATE_TARGETS=true: SSRF protection for private and loopback "
            "addresses is OFF. Use only for local demos."
        )
