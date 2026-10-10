"""Подстановка переменных ``{{name}}`` в путь, заголовки и тело шага."""

from __future__ import annotations

import random
import re
import time
import uuid
from collections.abc import Callable, Mapping
from typing import Any, Final
from urllib.parse import quote

VAR_NAME: Final = r"[A-Za-z_][A-Za-z0-9_]*"
_PLACEHOLDER: Final = re.compile(r"\{\{\s*(" + VAR_NAME + r"(?:\." + VAR_NAME + r")?)\s*\}\}")

BUILTIN_VARIABLES: Final[dict[str, Callable[[], str]]] = {
    "uuid": lambda: str(uuid.uuid4()),
    # Не криптография: случайные данные для запроса, а не секрет.
    "rand_int": lambda: str(random.randint(0, 1_000_000)),  # noqa: S311
    "timestamp": lambda: str(int(time.time())),
}


class UndefinedVariableError(Exception):
    """В шаблоне переменная, которой нет (например, не задан секрет)."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


def referenced_variables(text: str) -> list[str]:
    """Имена переменных в строке, например ``["token", "secret.PASSWORD"]``."""
    return [match.group(1) for match in _PLACEHOLDER.finditer(text)]


def render(
    text: str,
    variables: Mapping[str, str],
    secrets: Mapping[str, str],
    *,
    url_encode: bool = False,
) -> str:
    """Подставляет значения; в пути (``url_encode``) значения кодируются целиком."""

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name.startswith("secret."):
            value = secrets.get(name.removeprefix("secret."))
        elif name in variables:
            value = variables[name]
        elif name in BUILTIN_VARIABLES:
            value = BUILTIN_VARIABLES[name]()
        else:
            value = None
        if value is None:
            raise UndefinedVariableError(name)
        # В пути значение не может добавить сегменты, запрос или фрагмент.
        return quote(value, safe="") if url_encode else value

    return _PLACEHOLDER.sub(replace, text)


def render_value(value: Any, variables: Mapping[str, str], secrets: Mapping[str, str]) -> Any:
    """Подстановка в JSON-значение: во все строки, ключи не меняются."""
    if isinstance(value, str):
        return render(value, variables, secrets)
    if isinstance(value, list):
        return [render_value(item, variables, secrets) for item in value]
    if isinstance(value, dict):
        return {key: render_value(item, variables, secrets) for key, item in value.items()}
    return value
