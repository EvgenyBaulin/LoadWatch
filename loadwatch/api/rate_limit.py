"""In-memory sliding-window rate limiter.

Limits are per API worker process and reset on restart; see docs/SECURITY.md.
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
    """Allow at most ``limit`` hits per ``window_sec`` for each key."""

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
        self._lock = threading.Lock()  # sync endpoints run in a thread pool

    def hit(self, key: str) -> None:
        """Record a hit for ``key`` or raise :class:`RateLimitedError`."""
        now = self._clock()
        with self._lock:
            hits = self._hits.get(key)
            if hits is None:
                if len(self._hits) >= self._max_keys:
                    self._evict_expired(now)
                if len(self._hits) >= self._max_keys:
                    # Table full of active clients (likely an attack): refuse new ones
                    # instead of evicting live counters, which would reset their limits.
                    raise RateLimitedError(retry_after=math.ceil(self.window_sec))
                hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] >= self.window_sec:
                hits.popleft()
            if len(hits) >= self.limit:
                retry_after = math.ceil(self.window_sec - (now - hits[0]))
                raise RateLimitedError(retry_after=max(retry_after, 1))
            hits.append(now)

    def reset(self) -> None:
        """Forget all hits (used by tests)."""
        with self._lock:
            self._hits.clear()

    def _evict_expired(self, now: float) -> None:
        """Drop keys whose hits have all left the window."""
        for key in [k for k, v in self._hits.items() if not v or now - v[-1] >= self.window_sec]:
            del self._hits[key]


def client_key(request: Request) -> str:
    """Identify the client by IP (uvicorn sets it from X-Forwarded-For behind Nginx)."""
    return request.client.host if request.client else "unknown"
