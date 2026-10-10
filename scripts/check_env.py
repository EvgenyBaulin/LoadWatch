"""Check the LoadWatch configuration without printing secret values.

Usage: ``uv run python scripts/check_env.py`` (reads ``.env`` unless
``LOADWATCH_ENV_FILE`` points elsewhere). Exit code 1 if something required is wrong.
"""

from __future__ import annotations

import sys

from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from loadwatch.config import Settings


def main() -> int:
    """Validate settings, report optional features and try the database."""
    try:
        settings = Settings()
    except ValidationError as exc:
        print("Настройки с ошибками (значения не показываются):")
        for error in exc.errors(include_input=False, include_url=False):
            name = ".".join(str(part) for part in error["loc"]).upper() or "(общая проверка)"
            print(f"  ✗ {name}: {error['msg']}")
        return 1

    print("✓ Обязательные настройки в порядке")
    print(
        f"  профиль ресурсов: {settings.resource_profile.value}, "
        f"регистрация: {settings.registration_mode.value}, "
        f"окружение: {settings.environment.value}"
    )
    optional = {
        "SECRETS_ENCRYPTION_KEY (секреты сценариев, с фазы 4)": settings.secrets_encryption_key,
        "TELEGRAM_BOT_TOKEN (уведомления в Telegram)": settings.telegram_bot_token,
        "SMTP_HOST + SMTP_PASSWORD (уведомления на почту)": settings.smtp_host
        and settings.smtp_password,
    }
    for label, value in optional.items():
        print(f"  {'✓' if value else '-'} {label}{'' if value else ': не задано'}")
    if settings.allow_private_targets:
        print("  ! ALLOW_PRIVATE_TARGETS=true — только для локальной разработки")

    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        print(f"✗ База данных недоступна: {type(exc.__cause__ or exc).__name__}")
        return 1
    finally:
        engine.dispose()
    print("✓ База данных отвечает")
    return 0


if __name__ == "__main__":
    sys.exit(main())
