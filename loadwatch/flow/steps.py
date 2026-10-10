"""Описание шагов потока запросов и их проверка при сохранении сценария."""

from __future__ import annotations

import json
import re
from typing import Annotated, Any, Final, Literal

import regex
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from loadwatch.flow.templating import BUILTIN_VARIABLES, VAR_NAME, referenced_variables

MAX_STEPS: Final = 10
MAX_BODY_BYTES: Final = 64 * 1024
MAX_HEADERS: Final = 20
MAX_HEADER_NAME: Final = 100
MAX_HEADER_VALUE: Final = 4096
MAX_EXTRACT_RULES: Final = 10
MAX_THINK_MS: Final = 60_000
ASSERT_WINDOW_BYTES: Final = 64 * 1024  # assert_contains ищет в первых 64 КБ тела

# Заголовки, которые задаёт сам клиент или которые ломают соединение и закрепление IP.
FORBIDDEN_HEADERS: Final = frozenset(
    {
        "host",
        "content-length",
        "transfer-encoding",
        "connection",
        "keep-alive",
        "proxy-connection",
        "proxy-authorization",
        "proxy-authenticate",
        "te",
        "trailer",
        "upgrade",
        "expect",
    }
)
_HEADER_NAME_RE: Final = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")  # token по RFC 9110
_STATUS_RE: Final = re.compile(r"^[1-5](\d\d|xx)$")
_SECRET_REF_RE: Final = re.compile(r"^secret\.(" + VAR_NAME + ")$")

Method = Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"]
VarName = Annotated[str, Field(pattern=f"^{VAR_NAME}$", max_length=50)]


class Body(BaseModel):
    """Тело запроса: JSON, форма или произвольный текст."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["json", "form", "raw"]
    content: Any
    content_type: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _check(self) -> Body:
        if self.type == "form":
            if not isinstance(self.content, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in self.content.items()
            ):
                raise ValueError("form body must be an object of strings")
        elif self.type == "raw" and not isinstance(self.content, str):
            raise ValueError("raw body must be a string")
        if len(json.dumps(self.content).encode()) > MAX_BODY_BYTES:
            raise ValueError(f"body is larger than {MAX_BODY_BYTES} bytes")
        return self


class ExtractRule(BaseModel):
    """Что достать из ответа в переменную."""

    model_config = ConfigDict(extra="forbid")

    var: VarName
    source: Literal["json", "header", "cookie", "regex"]
    expr: str = Field(min_length=1, max_length=500)

    @field_validator("var")
    @classmethod
    def _not_builtin(cls, value: str) -> str:
        if value in BUILTIN_VARIABLES or value == "secret":
            raise ValueError(f"'{value}' is a reserved variable name")
        return value

    @model_validator(mode="after")
    def _check_regex(self) -> ExtractRule:
        if self.source == "regex":
            try:
                regex.compile(self.expr)
            except regex.error as exc:
                raise ValueError(f"invalid regex: {exc}") from exc
        return self


class RequestStep(BaseModel):
    """Один шаг потока: HTTP-запрос к относительному пути сайта."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    method: Method = "GET"
    path: str = Field(min_length=1, max_length=2000)
    headers: dict[str, str] = Field(default_factory=dict)
    body: Body | None = None
    expected_status: list[str] = Field(default_factory=lambda: ["2xx", "3xx"], max_length=20)
    assert_contains: str | None = Field(default=None, min_length=1, max_length=1000)
    extract: list[ExtractRule] = Field(default_factory=list, max_length=MAX_EXTRACT_RULES)
    think_ms: int = Field(default=0, ge=0, le=MAX_THINK_MS)

    @field_validator("path")
    @classmethod
    def _relative_path(cls, value: str) -> str:
        """Только путь от origin сайта: никаких схем, хостов и ``//host``."""
        if not value.startswith("/") or value.startswith("//"):
            raise ValueError("path must be relative to the site, e.g. /login")
        if "\\" in value or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
            raise ValueError("path contains forbidden characters")
        if "://" in value.split("?", 1)[0]:
            raise ValueError("absolute URLs are not allowed")
        return value

    @field_validator("headers")
    @classmethod
    def _check_headers(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > MAX_HEADERS:
            raise ValueError(f"no more than {MAX_HEADERS} headers")
        seen: set[str] = set()
        for name, header_value in value.items():
            lower = name.lower()
            if not _HEADER_NAME_RE.match(name) or len(name) > MAX_HEADER_NAME:
                raise ValueError(f"invalid header name: {name!r}")
            if lower in FORBIDDEN_HEADERS or lower.startswith("proxy-"):
                raise ValueError(f"header {name} is not allowed")
            if lower in seen:
                raise ValueError(f"duplicate header: {name}")
            seen.add(lower)
            if len(header_value.encode()) > MAX_HEADER_VALUE:
                raise ValueError(f"header {name} is longer than {MAX_HEADER_VALUE} bytes")
            if "\r" in header_value or "\n" in header_value:
                raise ValueError(f"header {name} contains a line break")
            if not header_value.isascii():
                raise ValueError(f"header {name} must contain only ASCII characters")
        return value

    @field_validator("expected_status")
    @classmethod
    def _check_statuses(cls, value: list[str]) -> list[str]:
        normalized = [item.strip().lower() for item in value]
        if not normalized or not all(_STATUS_RE.match(item) for item in normalized):
            raise ValueError("expected_status items look like 200, 201 or 2xx")
        return normalized

    @model_validator(mode="after")
    def _body_allowed(self) -> RequestStep:
        if self.body is not None and self.method in ("GET", "HEAD"):
            raise ValueError(f"{self.method} requests cannot have a body")
        return self

    def status_matches(self, status: int) -> bool:
        """Ожидаемый ли это статус."""
        code = str(status)
        return any(
            item == code or (item.endswith("xx") and item[0] == code[0])
            for item in self.expected_status
        )

    def templates(self) -> list[str]:
        """Все строки шага, где могут быть переменные."""
        texts = [self.path, *self.headers.values()]
        if self.assert_contains is not None:
            texts.append(self.assert_contains)
        if self.body is not None:
            texts.append(json.dumps(self.body.content))
        return texts


class Flow(BaseModel):
    """Поток запросов: шаги по порядку, от 1 до ``MAX_STEPS``."""

    steps: list[RequestStep] = Field(min_length=1, max_length=MAX_STEPS)

    @model_validator(mode="after")
    def _check_names_and_variables(self) -> Flow:
        names = [step.name for step in self.steps]
        if len(set(names)) != len(names):
            raise ValueError("step names must be unique")
        # Переменная должна быть встроенной, секретом или извлечена на одном из прошлых шагов.
        defined: set[str] = set(BUILTIN_VARIABLES)
        for step in self.steps:
            for text in step.templates():
                for ref in referenced_variables(text):
                    if ref not in defined and not _SECRET_REF_RE.match(ref):
                        raise ValueError(
                            f"step '{step.name}' uses {{{{{ref}}}}} before it is defined"
                        )
            defined.update(rule.var for rule in step.extract)
        return self

    def secret_names(self) -> set[str]:
        """Имена секретов, на которые ссылается поток."""
        result: set[str] = set()
        for step in self.steps:
            for text in step.templates():
                for ref in referenced_variables(text):
                    match = _SECRET_REF_RE.match(ref)
                    if match:
                        result.add(match.group(1))
        return result


def parse_flow(raw: list[dict[str, Any]]) -> Flow:
    """Проверяет сохранённый в БД поток."""
    return Flow(steps=[RequestStep.model_validate(step) for step in raw])
