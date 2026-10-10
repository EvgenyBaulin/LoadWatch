"""Извлечение значений из ответа: JSON-путь, заголовок, cookie, регулярное выражение."""

from __future__ import annotations

import json
import re
from http.cookies import CookieError, SimpleCookie
from typing import Any, Final

import httpx
import regex

from loadwatch.flow.steps import ExtractRule

MAX_VALUE_LENGTH: Final = 4096  # извлечённое значение потом идёт в заголовки и пути
_PATH_TOKEN: Final = re.compile(r"([^.\[\]]+)|\[(\d+)\]")
REGEX_WINDOW: Final = 16 * 1024  # регулярное выражение — по первым 16 КБ: дорого по CPU
REGEX_TIMEOUT_SEC: Final = 0.05


class ExtractError(Exception):
    """Значение не найдено; текст — без содержимого ответа."""


def json_path(document: Any, path: str) -> Any:
    """Значение по пути вида ``data.items[0].token`` (``$.`` в начале допустим)."""
    path = path.removeprefix("$").lstrip(".")
    current = document
    for match in _PATH_TOKEN.finditer(path):
        key, index = match.groups()
        if key is not None:
            if not isinstance(current, dict) or key not in current:
                raise ExtractError(f"json path '{path}' not found")
            current = current[key]
        else:
            position = int(index)
            if not isinstance(current, list) or position >= len(current):
                raise ExtractError(f"json path '{path}' not found")
            current = current[position]
    return current


def response_cookies(headers: httpx.Headers) -> dict[str, str]:
    """Cookie из всех ``Set-Cookie`` ответа (атрибуты домена и пути не важны: origin один)."""
    cookies: dict[str, str] = {}
    for raw in headers.get_list("set-cookie"):
        parsed: SimpleCookie = SimpleCookie()
        try:
            parsed.load(raw)
        except CookieError:
            continue
        for name, morsel in parsed.items():
            cookies[name] = morsel.value
    return cookies


def extract(rule: ExtractRule, headers: httpx.Headers, body: bytes) -> str:
    """Достаёт значение по правилу или бросает :class:`ExtractError`."""
    value: str
    if rule.source == "header":
        header = headers.get(rule.expr)
        if header is None:
            raise ExtractError(f"header '{rule.expr}' not found")
        value = str(header)
    elif rule.source == "cookie":
        found = response_cookies(headers).get(rule.expr)
        if found is None:
            raise ExtractError(f"cookie '{rule.expr}' not found")
        value = found
    elif rule.source == "json":
        try:
            document = json.loads(body)
        except ValueError as exc:
            raise ExtractError("response is not valid JSON") from exc
        found_value = json_path(document, rule.expr)
        if isinstance(found_value, dict | list):
            raise ExtractError(f"json path '{rule.expr}' is not a scalar")
        value = "" if found_value is None else str(found_value)
        if isinstance(found_value, bool):
            value = value.lower()
    else:
        text = body[:REGEX_WINDOW].decode("utf-8", "replace")
        try:
            # У стандартного re нет таймаута, а выражение вида (a|aa)+$ может работать
            # экспоненциально долго и подвесить генератор; у regex таймаут есть.
            match = regex.search(rule.expr, text, timeout=REGEX_TIMEOUT_SEC)
        except TimeoutError as exc:
            raise ExtractError("regex took too long") from exc
        if match is None:
            raise ExtractError("regex did not match")
        value = str(match.group(1) if match.groups() else match.group(0))
    if len(value) > MAX_VALUE_LENGTH:
        raise ExtractError("extracted value is too long")
    return value
