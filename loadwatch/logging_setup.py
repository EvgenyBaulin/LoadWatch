"""Настройка логирования процесса."""

from __future__ import annotations

import logging

from loadwatch.config import Environment, Settings

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_logging(settings: Settings) -> None:
    """Настраивает корневой логгер; повторный вызов безвреден."""
    level = logging.DEBUG if settings.environment is Environment.DEVELOPMENT else logging.INFO
    logging.basicConfig(level=level, format=LOG_FORMAT)
    # SQL и строки доступа на уровне DEBUG слишком шумные.
    logging.getLogger("sqlalchemy").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # httpcore на DEBUG пишет заголовки запросов и ответов — там могут быть секреты.
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def warn_about_unsafe_settings(settings: Settings) -> None:
    """Громко предупреждает о настройках, ослабляющих безопасность."""
    log = logging.getLogger("loadwatch")
    if settings.allow_private_targets:
        log.warning(
            "ALLOW_PRIVATE_TARGETS=true: SSRF protection for private and loopback "
            "addresses is OFF. Use only for local demos."
        )
