"""Domain errors with stable machine-readable codes.

Services raise these; the API layer turns them into
``{"error": {"code": ..., "message": ...}}`` responses. The frontend translates
messages by ``code``, so codes must never change once released.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class for expected, user-facing errors."""

    code: str = "internal_error"
    status_code: int = 500
    message: str = "Internal server error"

    def __init__(self, message: str | None = None, *, details: Any = None) -> None:
        super().__init__(message or self.message)
        self.message = message or self.message
        self.details = details


class NotAuthenticatedError(AppError):
    """No valid credentials were supplied."""

    code = "not_authenticated"
    status_code = 401
    message = "Authentication required"


class InvalidCredentialsError(AppError):
    """E-mail and password do not match."""

    code = "invalid_credentials"
    status_code = 401
    message = "Invalid e-mail or password"


class EmailTakenError(AppError):
    """An account with this e-mail already exists."""

    code = "email_taken"
    status_code = 409
    message = "This e-mail is already registered"


class RegistrationClosedError(AppError):
    """Registration is disabled by ``REGISTRATION_MODE=closed``."""

    code = "registration_closed"
    status_code = 403
    message = "Registration is closed"


class InvalidInviteCodeError(AppError):
    """``REGISTRATION_MODE=invite`` and the code is missing or wrong."""

    code = "invalid_invite_code"
    status_code = 403
    message = "Invalid invite code"


class RateLimitedError(AppError):
    """Too many requests from one client."""

    code = "rate_limited"
    status_code = 429
    message = "Too many requests, try again later"

    def __init__(self, retry_after: int) -> None:
        super().__init__(details={"retry_after": retry_after})
        self.retry_after = retry_after


class NotFoundError(AppError):
    """The object does not exist or belongs to someone else."""

    code = "not_found"
    status_code = 404
    message = "Not found"
