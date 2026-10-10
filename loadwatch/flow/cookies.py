"""Cookie одного виртуального пользователя.

Клиент подключается к IP, а не к имени хоста, поэтому стандартный cookie jar
httpx здесь не годится: домены сравнивать не с чем. Сайт один, поэтому
достаточно RFC 6265 в части Path, Secure, Max-Age/Expires и Domain (чужой домен
отклоняется).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from http.cookies import CookieError, SimpleCookie

import httpx


@dataclass(slots=True)
class _Cookie:
    value: str
    path: str
    secure: bool
    expires_at: float | None


class CookieStore:
    """Хранилище cookie виртуального пользователя для одного сайта."""

    def __init__(self, host: str, *, https: bool) -> None:
        self._host = host.lower()
        self._https = https
        self._cookies: dict[str, _Cookie] = {}

    def update(self, headers: httpx.Headers, request_path: str) -> None:
        """Учитывает все ``Set-Cookie`` ответа на запрос к ``request_path``."""
        for raw in headers.get_list("set-cookie"):
            parsed: SimpleCookie = SimpleCookie()
            try:
                parsed.load(raw)
            except CookieError:
                continue
            for name, morsel in parsed.items():
                domain = morsel["domain"].lstrip(".").lower()
                if domain and not (self._host == domain or self._host.endswith(f".{domain}")):
                    continue  # cookie для чужого домена браузер бы не принял
                expires_at = self._expiry(morsel["max-age"], morsel["expires"])
                if expires_at is not None and expires_at <= time.time():
                    self._cookies.pop(name, None)  # сайт удалил cookie
                    continue
                self._cookies[name] = _Cookie(
                    value=morsel.value,
                    path=morsel["path"] or _default_path(request_path),
                    secure=bool(morsel["secure"]),
                    expires_at=expires_at,
                )

    def header_for(self, path: str) -> str | None:
        """Значение заголовка ``Cookie`` для запроса к ``path`` или ``None``."""
        now = time.time()
        request_path = path.split("?", 1)[0] or "/"
        pairs = [
            f"{name}={cookie.value}"
            for name, cookie in self._cookies.items()
            if (cookie.expires_at is None or cookie.expires_at > now)
            and (self._https or not cookie.secure)
            and _path_matches(request_path, cookie.path)
        ]
        return "; ".join(pairs) or None

    @staticmethod
    def _expiry(max_age: str, expires: str) -> float | None:
        if max_age:
            try:
                return time.time() + int(max_age)
            except ValueError:
                return None
        if expires:
            try:
                return parsedate_to_datetime(expires).timestamp()
            except (TypeError, ValueError):
                return None
        return None


def _default_path(request_path: str) -> str:
    """Путь cookie по умолчанию (RFC 6265, 5.1.4): «каталог» запроса."""
    path = request_path.split("?", 1)[0]
    if not path.startswith("/") or path.count("/") == 1:
        return "/"
    return path[: path.rfind("/")]


def _path_matches(request_path: str, cookie_path: str) -> bool:
    """Совпадение путей по RFC 6265, 5.1.4."""
    if request_path == cookie_path:
        return True
    if request_path.startswith(cookie_path):
        return cookie_path.endswith("/") or request_path[len(cookie_path)] == "/"
    return False
