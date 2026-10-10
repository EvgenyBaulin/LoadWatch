"""Регистрация, вход и настройки пользователя."""

from __future__ import annotations

import hmac

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from loadwatch.config import RegistrationMode, Settings
from loadwatch.errors import (
    EmailTakenError,
    InvalidCredentialsError,
    InvalidInviteCodeError,
    NotAuthenticatedError,
    RegistrationClosedError,
)
from loadwatch.models import User
from loadwatch.repositories import UserRepository
from loadwatch.schemas.user import AuthResponse, PreferencesUpdate, RegisterRequest, UserOut
from loadwatch.security.passwords import hash_password, verify_password
from loadwatch.security.tokens import InvalidTokenError, create_access_token, decode_access_token


def normalize_email(email: str) -> str:
    """Приводит e-mail к нижнему регистру и обрезает пробелы."""
    return email.strip().lower()


class AuthService:
    """Жизненный цикл аккаунта; управляет транзакцией."""

    def __init__(self, session: Session, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._users = UserRepository(session)

    def register(self, data: RegisterRequest) -> AuthResponse:
        """Создаёт аккаунт с учётом ``REGISTRATION_MODE`` и сразу выдаёт токен."""
        self._check_registration_allowed(data.invite_code)
        email = normalize_email(data.email)
        if self._users.get_by_email(email) is not None:
            raise EmailTakenError()
        user = User(email=email, password_hash=hash_password(data.password), locale=data.locale)
        try:
            self._users.add(user)
            self._session.commit()
        except IntegrityError as exc:  # параллельная регистрация с тем же e-mail
            self._session.rollback()
            raise EmailTakenError() from exc
        return self._auth_response(user)

    def login(self, email: str, password: str) -> AuthResponse:
        """Выдаёт токен по e-mail и паролю."""
        user = self._users.get_by_email(normalize_email(email))
        if not verify_password(user.password_hash if user else None, password) or user is None:
            raise InvalidCredentialsError()
        return self._auth_response(user)

    def authenticate(self, token: str) -> User:
        """Пользователь, которому выдан токен."""
        try:
            user_id = decode_access_token(token, self._settings)
        except InvalidTokenError as exc:
            raise NotAuthenticatedError() from exc
        user = self._users.get(user_id)
        if user is None:
            raise NotAuthenticatedError()
        return user

    def update_preferences(self, user: User, data: PreferencesUpdate) -> User:
        """Меняет язык и/или часовой пояс пользователя."""
        if data.locale is not None:
            user.locale = data.locale
        if data.timezone is not None:
            user.timezone = data.timezone
        self._session.commit()
        return user

    def _check_registration_allowed(self, invite_code: str | None) -> None:
        mode = self._settings.registration_mode
        if mode is RegistrationMode.CLOSED:
            raise RegistrationClosedError()
        if mode is RegistrationMode.INVITE:
            expected = self._settings.registration_invite_code
            if (
                expected is None
                or invite_code is None
                or not hmac.compare_digest(
                    invite_code.encode(), expected.get_secret_value().encode()
                )
            ):
                raise InvalidInviteCodeError()

    def _auth_response(self, user: User) -> AuthResponse:
        return AuthResponse(
            access_token=create_access_token(user.id, self._settings),
            expires_in=self._settings.jwt_expire_minutes * 60,
            user=UserOut.model_validate(user),
        )
