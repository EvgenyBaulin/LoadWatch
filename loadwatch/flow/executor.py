"""Выполнение потока одним виртуальным пользователем: шаги по порядку, свои cookie."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlencode

from loadwatch.flow.cookies import CookieStore
from loadwatch.flow.extract import ExtractError, extract
from loadwatch.flow.steps import ASSERT_WINDOW_BYTES, MAX_BODY_BYTES, Flow, RequestStep
from loadwatch.flow.templating import UndefinedVariableError, render, render_value
from loadwatch.metrics import RequestRecord
from loadwatch.runner.generator import VirtualUser
from loadwatch.runner.http import PinnedRequester, ResponseSnapshot

REDACTED = "***"


class StepFailedError(Exception):
    """Шаг не прошёл до отправки запроса; ``kind`` — тип ошибки для статистики."""

    def __init__(self, kind: str, detail: str) -> None:
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


@dataclass(slots=True)
class StepResult:
    """Итог шага для dry-run; секреты в нём уже замаскированы."""

    name: str
    method: str
    path: str
    ok: bool
    status: int | None = None
    error: str | None = None
    detail: str | None = None
    duration_ms: float = 0.0
    timings: dict[str, float] = field(default_factory=dict)
    response_bytes: int = 0
    truncated: bool = False
    extracted: dict[str, str] = field(default_factory=dict)


def redact(text: str, secrets: Mapping[str, str]) -> str:
    """Заменяет значения секретов на ``***``, в том числе в URL-кодированном виде.

    Длинные значения заменяются первыми, чтобы не оставить хвостов.
    """
    forms = {form for value in secrets.values() if value for form in (value, quote(value, safe=""))}
    for form in sorted(forms, key=len, reverse=True):
        text = text.replace(form, REDACTED)
    return text


class FlowExecutor:
    """Проход потока: подстановка переменных, запрос, проверки, извлечение, пауза.

    Экземпляр без состояния и годится как ``Iteration`` для генератора: всё, что
    относится к одному виртуальному пользователю (переменные и cookie), живёт
    внутри одного вызова.
    """

    def __init__(self, flow: Flow, secrets: Mapping[str, str]) -> None:
        self._flow = flow
        self._secrets = dict(secrets)

    async def __call__(self, user: VirtualUser) -> bool:
        """Проход для генератора: каждый запрос попадает в метрики."""

        def record(request_record: RequestRecord) -> None:
            user.collector.record_request(request_record, user.now_sec())

        results = await self.run(user.requester, on_record=record)
        return all(result.ok for result in results)

    async def run(
        self,
        requester: PinnedRequester,
        *,
        on_record: Any = None,
        skip_think: bool = False,
        results: list[StepResult] | None = None,
    ) -> list[StepResult]:
        """Выполняет шаги, пока все успешны; возвращает итог по каждому выполненному.

        ``results`` можно передать снаружи, чтобы при внешнем таймауте видеть, что
        успело выполниться; ``skip_think`` убирает паузы (для dry-run).
        """
        variables: dict[str, str] = {}
        target = requester.target.url
        cookies = CookieStore(target.host, https=target.scheme == "https")
        results = [] if results is None else results
        for step in self._flow.steps:
            result = await self._run_step(step, requester, variables, cookies, on_record)
            results.append(result)
            if not result.ok:
                break
            if step.think_ms and not skip_think:
                await asyncio.sleep(step.think_ms / 1000)
        return results

    async def _run_step(
        self,
        step: RequestStep,
        requester: PinnedRequester,
        variables: dict[str, str],
        cookies: CookieStore,
        on_record: Any,
    ) -> StepResult:
        try:
            path, headers, content = self._build_request(step, variables, cookies)
        except StepFailedError as exc:
            if on_record is not None:  # ошибка шага видна в статистике ошибок
                on_record(RequestRecord(step=step.name, total_ms=0.0, error=exc.kind))
            return StepResult(
                step.name,
                step.method,
                self._safe(step.path),
                ok=False,
                error=exc.kind,
                detail=self._safe(exc.detail),
            )
        record, response = await requester.request(
            step.name,
            step.method,
            path,
            headers=headers,
            content=content,
            expected_status=step.status_matches,
        )
        result = StepResult(
            step.name,
            step.method,
            self._safe(path),
            ok=False,
            status=record.status,
            duration_ms=round(record.total_ms, 2),
            timings={
                name: round(getattr(record, f"{name}_ms"), 2)
                for name in ("dns", "connect", "tls", "ttfb", "transfer")
            },
        )
        if response is not None:
            result.response_bytes = len(response.body)
            result.truncated = response.truncated
            cookies.update(response.headers, path)
        if record.error is None and response is not None:
            record.error, result.detail = self._check_response(step, response, variables)
            result.extracted = {
                rule.var: self._safe(variables[rule.var])
                for rule in step.extract
                if rule.var in variables
            }
        result.error = record.error
        result.ok = record.error is None
        if on_record is not None:
            on_record(record)
        return result

    def _build_request(
        self, step: RequestStep, variables: dict[str, str], cookies: CookieStore
    ) -> tuple[str, dict[str, str], bytes | None]:
        try:
            path = render(step.path, variables, self._secrets, url_encode=True)
            headers = {
                name: render(value, variables, self._secrets)
                for name, value in step.headers.items()
            }
            content = self._render_body(step, variables)
        except UndefinedVariableError as exc:
            kind = "missing_secret" if exc.name.startswith("secret.") else "missing_variable"
            raise StepFailedError(kind, f"variable {{{{{exc.name}}}}} is not defined") from exc
        for value in headers.values():
            # Перевод строки — инъекция заголовка; не-ASCII httpx отправить не сможет.
            if "\r" in value or "\n" in value or not value.isascii():
                raise StepFailedError(
                    "invalid_header", "header value has a line break or non-ASCII characters"
                )
        if content is not None and len(content) > MAX_BODY_BYTES:
            raise StepFailedError("body_too_large", f"body is larger than {MAX_BODY_BYTES} bytes")
        if step.body is not None and not any(n.lower() == "content-type" for n in headers):
            headers["Content-Type"] = (
                step.body.content_type
                or {
                    "json": "application/json",
                    "form": "application/x-www-form-urlencoded",
                    "raw": "text/plain; charset=utf-8",
                }[step.body.type]
            )
        jar = cookies.header_for(path)
        if jar:
            existing = next((n for n in headers if n.lower() == "cookie"), None)
            if existing is None:
                headers["Cookie"] = jar
            else:
                headers[existing] = f"{headers[existing]}; {jar}"
        return path, headers, content

    def _render_body(self, step: RequestStep, variables: dict[str, str]) -> bytes | None:
        body = step.body
        if body is None:
            return None
        if body.type == "json":
            data = render_value(body.content, variables, self._secrets)
            return json.dumps(data, ensure_ascii=False).encode()
        if body.type == "form":
            form = render_value(body.content, variables, self._secrets)
            return urlencode(form).encode()
        return render(str(body.content), variables, self._secrets).encode()

    def _check_response(
        self, step: RequestStep, response: ResponseSnapshot, variables: dict[str, str]
    ) -> tuple[str | None, str | None]:
        """``assert_contains`` и ``extract``; возвращает (тип ошибки, пояснение)."""
        if step.assert_contains is not None:
            try:
                needle = render(step.assert_contains, variables, self._secrets)
            except UndefinedVariableError as exc:
                kind = "missing_secret" if exc.name.startswith("secret.") else "missing_variable"
                return kind, f"variable {{{{{exc.name}}}}} is not defined"
            window = response.body[:ASSERT_WINDOW_BYTES].decode("utf-8", "replace")
            if needle not in window:
                return "assert_failed", "expected text not found in the response"
        for rule in step.extract:
            try:
                variables[rule.var] = extract(rule, response.headers, response.body)
            except ExtractError as exc:
                return "extract_failed", f"{rule.var}: {exc}"
        return None, None

    def _safe(self, text: str) -> str:
        return redact(text, self._secrets)
