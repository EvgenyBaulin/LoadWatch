"""FastAPI-приложение демо-цели.

Сайт нарочно «плохой» там, где это нужно для демонстрации LoadWatch:

* ``/delay`` и базовая задержка ``DEMO_LATENCY_MS`` — управляемая медленность;
  ``POST /admin/latency`` меняет её на лету («после деплоя сайт стал медленнее»);
* ``/limited`` — потолок пропускной способности: сверх ``DEMO_CAPACITY_RPS``
  запросов в секунду отвечает 503 и отвечает тем дольше, чем сильнее перегрузка;
* ``/slow`` и ``/flaky`` — случайно медленные ответы и случайные ошибки 500;
* ``/api/login`` → ``/dashboard`` — поток из нескольких шагов с токеном;
* ``/.well-known/loadwatch-<token>.txt`` — файл подтверждения владения.

Всё состояние в памяти и ограничено по размеру: приложение живёт на том же
слабом VPS, что и сам LoadWatch.
"""

from __future__ import annotations

import asyncio
import hmac
import os
import random
import secrets
import time
from collections import OrderedDict, deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Annotated, Final

from fastapi import FastAPI, Header, HTTPException, Query, status
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

MAX_DELAY_MS: Final = 30_000
MAX_OVERLOAD_DELAY_MS: Final = 2_000
OVERLOAD_DELAY_STEP_MS: Final = 200  # +200 мс задержки на каждые 100 % перегрузки
MAX_SESSIONS: Final = 10_000  # старые токены вытесняются: память не растёт под нагрузкой
WINDOW_SEC: Final = 1.0

Clock = Callable[[], float]


def _env_int(env: Mapping[str, str], name: str, default: int) -> int:
    """Целое из окружения; пустое значение — значение по умолчанию."""
    raw = env.get(name, "").strip()
    return int(raw) if raw else default


@dataclass(slots=True)
class DemoConfig:
    """Настройки демо-цели (из переменных окружения ``DEMO_*``)."""

    latency_ms: int = 0
    capacity_rps: int = 50
    username: str = "demo"
    password: str = "demo-password"  # noqa: S105 — учебная учётка демо-сайта
    verify_token: str | None = None
    admin_token: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> DemoConfig:
        """Читает ``DEMO_*`` из окружения процесса (или переданного словаря)."""
        env = os.environ if env is None else env
        return cls(
            latency_ms=_env_int(env, "DEMO_LATENCY_MS", 0),
            capacity_rps=max(1, _env_int(env, "DEMO_CAPACITY_RPS", 50)),
            username=env.get("DEMO_USERNAME") or "demo",
            password=env.get("DEMO_PASSWORD") or "demo-password",
            verify_token=env.get("DEMO_VERIFY_TOKEN") or None,
            admin_token=env.get("DEMO_ADMIN_TOKEN") or None,
        )


@dataclass(slots=True)
class CapacityWindow:
    """Скользящее окно в одну секунду: сколько запросов пришло и сколько обслужено.

    Как настоящий перегруженный сервер, окно обслуживает не больше ``capacity``
    запросов в секунду, а остальным отказывает; перегрузка считается по всем
    пришедшим запросам.
    """

    capacity: int
    clock: Clock
    _arrivals: deque[float] = field(default_factory=deque)
    _accepted: deque[float] = field(default_factory=deque)

    def arrive(self) -> tuple[bool, int]:
        """Учитывает запрос; возвращает (обслужить ли, сколько пришло за секунду с ним)."""
        now = self.clock()
        for window in (self._arrivals, self._accepted):
            while window and now - window[0] >= WINDOW_SEC:
                window.popleft()
        # Окно ограничено: при лавине запросов память не растёт сверх 20× ёмкости.
        arrived = len(self._arrivals) + 1
        if len(self._arrivals) < self.capacity * 20:
            self._arrivals.append(now)
        accepted = len(self._accepted) < self.capacity
        if accepted:
            self._accepted.append(now)
        return accepted, arrived


@dataclass(slots=True)
class DemoState:
    """Изменяемое состояние приложения."""

    config: DemoConfig
    latency_ms: int
    window: CapacityWindow
    rng: random.Random
    sessions: OrderedDict[str, None] = field(default_factory=OrderedDict)

    def add_session(self) -> str:
        """Выдаёт новый токен сессии, вытесняя самые старые сверх лимита."""
        token = secrets.token_urlsafe(24)
        self.sessions[token] = None
        while len(self.sessions) > MAX_SESSIONS:
            self.sessions.popitem(last=False)
        return token


class LoginIn(BaseModel):
    """Учётные данные для входа."""

    username: str = Field(max_length=200)
    password: str = Field(max_length=200)


class LatencyIn(BaseModel):
    """Новая базовая задержка."""

    ms: int = Field(ge=0, le=MAX_DELAY_MS)


INDEX_HTML: Final = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>LoadWatch demo target</title></head>
<body><h1>LoadWatch demo target</h1>
<p>A tiny site for load-testing demos.</p>
<ul><li><a href="/delay?ms=200">/delay</a></li><li><a href="/limited">/limited</a></li>
<li><a href="/slow">/slow</a></li><li><a href="/flaky">/flaky</a></li>
<li><a href="/dashboard">/dashboard</a> (login required)</li></ul>
</body></html>
"""


async def _sleep_ms(ms: float) -> None:
    """Асинхронная пауза: задержки не занимают процессор."""
    if ms > 0:
        await asyncio.sleep(ms / 1000)


def create_app(
    config: DemoConfig | None = None,
    *,
    clock: Clock = time.monotonic,
    rng: random.Random | None = None,
) -> FastAPI:
    """Собирает приложение; часы и генератор случайных чисел подменяются в тестах."""
    config = config or DemoConfig.from_env()
    state = DemoState(
        config=config,
        latency_ms=config.latency_ms,
        window=CapacityWindow(config.capacity_rps, clock),
        rng=rng or random.Random(),
    )
    app = FastAPI(title="LoadWatch demo target", docs_url=None, redoc_url=None)
    app.state.demo = state

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        """Главная: быстрая HTML-страница (плюс базовая задержка)."""
        await _sleep_ms(state.latency_ms)
        return HTMLResponse(INDEX_HTML)

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Проверка живости."""
        return {"status": "ok"}

    @app.get("/delay")
    async def delay(
        ms: Annotated[int, Query(ge=0, le=MAX_DELAY_MS)] = 0,
    ) -> PlainTextResponse:
        """Ответ ровно через ``ms`` миллисекунд."""
        await _sleep_ms(ms)
        return PlainTextResponse(f"delayed {ms} ms")

    @app.get("/limited")
    async def limited() -> PlainTextResponse:
        """Потолок ``capacity_rps``: сверх него — 503 и задержка, растущая с перегрузкой."""
        accepted, arrived = state.window.arrive()
        if accepted:
            return PlainTextResponse("ok")
        capacity = state.window.capacity
        overload = max(0, arrived - capacity) / capacity
        await _sleep_ms(min(MAX_OVERLOAD_DELAY_MS, overload * OVERLOAD_DELAY_STEP_MS))
        return PlainTextResponse(
            "overloaded",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            headers={"Retry-After": "1"},
        )

    @app.get("/slow")
    async def slow() -> PlainTextResponse:
        """Случайно медленный ответ: 500–1500 мс."""
        ms = state.rng.uniform(500, 1500)
        await _sleep_ms(ms)
        return PlainTextResponse(f"slow {ms:.0f} ms")

    @app.get("/flaky")
    async def flaky(
        error_rate: Annotated[float, Query(ge=0, le=1)] = 0.1,
    ) -> PlainTextResponse:
        """Доля ``error_rate`` ответов — ошибка 500."""
        if state.rng.random() < error_rate:
            return PlainTextResponse("random failure", status_code=500)
        return PlainTextResponse("ok")

    @app.post("/api/login")
    async def login(data: LoginIn) -> JSONResponse:
        """Вход: токен в JSON и cookie ``session``."""
        user_ok = hmac.compare_digest(data.username.encode(), config.username.encode())
        password_ok = hmac.compare_digest(data.password.encode(), config.password.encode())
        if not (user_ok and password_ok):
            return JSONResponse({"error": "invalid credentials"}, status_code=401)
        token = state.add_session()
        response = JSONResponse({"data": {"token": token}})
        response.set_cookie("session", token, httponly=True, samesite="lax")
        return response

    @app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard(
        authorization: Annotated[str | None, Header()] = None,
    ) -> HTMLResponse:
        """Защищённая страница: нужен ``Authorization: Bearer <token>`` из входа."""
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or token.strip() not in state.sessions:
            raise HTTPException(
                status_code=401, detail="unauthorized", headers={"WWW-Authenticate": "Bearer"}
            )
        await _sleep_ms(state.latency_ms)
        return HTMLResponse(
            f"<!doctype html><html><body><h1>Welcome, {config.username}!</h1>"
            "<p>This page is only for signed-in users.</p></body></html>"
        )

    @app.get("/.well-known/loadwatch-{token}.txt")
    async def verification_file(token: str) -> PlainTextResponse:
        """Файл подтверждения владения: только для токена из ``DEMO_VERIFY_TOKEN``."""
        expected = config.verify_token
        if not expected or not hmac.compare_digest(token.encode(), expected.encode()):
            raise HTTPException(status_code=404, detail="not found")
        return PlainTextResponse(expected)

    @app.post("/admin/latency")
    async def set_latency(
        data: LatencyIn,
        x_demo_admin: Annotated[str | None, Header()] = None,
    ) -> dict[str, int]:
        """Меняет базовую задержку ``/`` и ``/dashboard``; без ``DEMO_ADMIN_TOKEN`` выключено."""
        expected = config.admin_token
        if not expected:
            raise HTTPException(status_code=404, detail="not found")
        if x_demo_admin is None or not hmac.compare_digest(
            x_demo_admin.encode(), expected.encode()
        ):
            raise HTTPException(status_code=403, detail="forbidden")
        state.latency_ms = data.ms
        return {"latency_ms": state.latency_ms}

    @app.get("/admin/latency")
    async def get_latency() -> dict[str, int]:
        """Текущая базовая задержка (без секретов, поэтому без авторизации)."""
        return {"latency_ms": state.latency_ms}

    return app
