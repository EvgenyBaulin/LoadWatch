"""Ограничение частоты запросов скользящим окном в памяти процесса.

Счётчики свои у каждого воркера API и обнуляются при перезапуске (см. docs/SECURITY.md).
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from collections.abc import Callable

from fastapi import Request

from loadwatch.errors import RateLimitedError


class SlidingWindowRateLimiter:
    """Не больше ``limit`` обращений за ``window_sec`` секунд на каждый ключ."""

    def __init__(
        self,
        limit: int,
        window_sec: float = 60.0,
        *,
        max_keys: int = 10_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limit = limit
        self.window_sec = window_sec
        self._max_keys = max_keys
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()  # sync-эндпоинты выполняются в пуле потоков

    def hit(self, key: str) -> None:
        """Учитывает обращение по ``key`` или бросает :class:`RateLimitedError`."""
        now = self._clock()
        with self._lock:
            hits = self._hits.get(key)
            if hits is None:
                if len(self._hits) >= self._max_keys:
                    self._evict_expired(now)
                if len(self._hits) >= self._max_keys:
                    # Таблица забита активными клиентами (похоже на атаку): отказываем новым,
                    # а не вытесняем живые счётчики — иначе им бы сбросился лимит.
                    raise RateLimitedError(retry_after=math.ceil(self.window_sec))
                hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] >= self.window_sec:
                hits.popleft()
            if len(hits) >= self.limit:
                retry_after = math.ceil(self.window_sec - (now - hits[0]))
                raise RateLimitedError(retry_after=max(retry_after, 1))
            hits.append(now)

    def reset(self) -> None:
        """Забывает все обращения (для тестов)."""
        with self._lock:
            self._hits.clear()

    def _evict_expired(self, now: float) -> None:
        """Удаляет ключи, у которых все обращения вышли из окна."""
        for key in [k for k, v in self._hits.items() if not v or now - v[-1] >= self.window_sec]:
            del self._hits[key]


def client_key(request: Request) -> str:
    """Клиент определяется по IP (за Nginx uvicorn берёт его из X-Forwarded-For)."""
    return request.client.host if request.client else "unknown"
