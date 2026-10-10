"""Ошибки предметной области со стабильными машинными кодами.

Их бросают сервисы, а слой API превращает в ответы
``{"error": {"code": ..., "message": ...}}``. Фронтенд переводит сообщения по
``code``, поэтому выпущенные коды менять нельзя.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Базовый класс ожидаемых ошибок, которые видит пользователь."""

    code: str = "internal_error"
    status_code: int = 500
    message: str = "Internal server error"

    def __init__(self, message: str | None = None, *, details: Any = None) -> None:
        super().__init__(message or self.message)
        self.message = message or self.message
        self.details = details


class NotAuthenticatedError(AppError):
    """Не передан действительный токен."""

    code = "not_authenticated"
    status_code = 401
    message = "Authentication required"


class InvalidCredentialsError(AppError):
    """E-mail и пароль не совпадают."""

    code = "invalid_credentials"
    status_code = 401
    message = "Invalid e-mail or password"


class EmailTakenError(AppError):
    """Аккаунт с таким e-mail уже есть."""

    code = "email_taken"
    status_code = 409
    message = "This e-mail is already registered"


class RegistrationClosedError(AppError):
    """Регистрация выключена: ``REGISTRATION_MODE=closed``."""

    code = "registration_closed"
    status_code = 403
    message = "Registration is closed"


class InvalidInviteCodeError(AppError):
    """Включён ``REGISTRATION_MODE=invite``, а код не передан или неверен."""

    code = "invalid_invite_code"
    status_code = 403
    message = "Invalid invite code"


class RateLimitedError(AppError):
    """Слишком много запросов от одного клиента."""

    code = "rate_limited"
    status_code = 429
    message = "Too many requests, try again later"

    def __init__(self, retry_after: int) -> None:
        super().__init__(details={"retry_after": retry_after})
        self.retry_after = retry_after


class NotFoundError(AppError):
    """Объекта нет или он принадлежит другому пользователю."""

    code = "not_found"
    status_code = 404
    message = "Not found"


class InvalidSiteUrlError(AppError):
    """URL сайта не подходит: нужен origin вида ``https://example.com``."""

    code = "invalid_site_url"
    status_code = 422
    message = "Site URL must be an http(s) origin without path, query or credentials"


class ForbiddenTargetError(AppError):
    """Адрес ведёт во внутреннюю сеть или на localhost (защита от SSRF)."""

    code = "target_forbidden"
    status_code = 422
    message = "This address is not allowed: it points to a private or local network"


class SiteExistsError(AppError):
    """У пользователя уже есть сайт с таким адресом."""

    code = "site_exists"
    status_code = 409
    message = "This site has already been added"


class VerificationFailedError(AppError):
    """Владение не подтверждено; причина — в ``details.reason``."""

    code = "verification_failed"
    status_code = 422
    message = "Ownership verification failed"
