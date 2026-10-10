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


class ScenarioExistsError(AppError):
    """У сайта уже есть сценарий с таким названием."""

    code = "scenario_exists"
    status_code = 409
    message = "A scenario with this name already exists for the site"


class SiteNotVerifiedError(AppError):
    """Действие доступно только для подтверждённого сайта."""

    code = "site_not_verified"
    status_code = 409
    message = "Verify site ownership first"


class TargetUnresolvableError(AppError):
    """Имя сайта не резолвится."""

    code = "target_unresolvable"
    status_code = 422
    message = "The site host name cannot be resolved"


class SecretsUnavailableError(AppError):
    """Ключ шифрования секретов не настроен на сервере."""

    code = "secrets_unavailable"
    status_code = 503
    message = "Secrets storage is not configured on the server"


class InvalidScheduleError(AppError):
    """Неверное выражение cron, часовой пояс или слишком частые запуски."""

    code = "invalid_schedule"
    status_code = 422
    message = "Invalid schedule"


class RunInProgressError(AppError):
    """У сайта уже есть запуск в очереди или в работе."""

    code = "run_in_progress"
    status_code = 409
    message = "A test of this site is already queued or running"


class CooldownActiveError(AppError):
    """Между тестами сайта должна пройти пауза ``SITE_COOLDOWN_SEC``."""

    code = "cooldown_active"
    status_code = 429
    message = "Too soon after the previous test of this site"

    def __init__(self, retry_after: int) -> None:
        super().__init__(details={"retry_after": retry_after})
        self.retry_after = retry_after


class MissingSecretsError(AppError):
    """Сценарий использует секреты, которые не заданы."""

    code = "missing_secrets"
    status_code = 409
    message = "The scenario uses secrets that are not set"


class RunNotCancellableError(AppError):
    """Запуск уже завершён."""

    code = "run_not_cancellable"
    status_code = 409
    message = "The run has already finished"


class InvalidPeriodError(AppError):
    """Неверный период: ``from`` позже ``to`` или период длиннее 400 дней."""

    code = "invalid_period"
    status_code = 422
    message = "Invalid period: 'from' must be before 'to' and the period at most 400 days"


class InvalidChannelError(AppError):
    """Неверные параметры канала уведомлений; подробности — в ``details.config``."""

    code = "invalid_channel"
    status_code = 422
    message = "Invalid notification channel settings"


class InvalidTriggerTokenError(AppError):
    """Токен триггера неверный или не выпущен."""

    code = "invalid_trigger_token"
    status_code = 401
    message = "Invalid trigger token"


class ScenarioRequiredError(AppError):
    """У сайта несколько сценариев и нет расписания — нужно указать ``scenario_id``."""

    code = "scenario_required"
    status_code = 422
    message = "Specify scenario_id: the site has several scenarios and no schedule"


class RunHasNoMetricsError(AppError):
    """У запуска ещё нет метрик (в очереди, упал до начала или отменён)."""

    code = "run_has_no_metrics"
    status_code = 409
    message = "The run has no metrics to export"


class ExportTooLargeError(AppError):
    """За период слишком много запусков для одного CSV — нужно сузить период."""

    code = "export_too_large"
    status_code = 422
    message = "Too many runs in the period, choose a shorter period"
