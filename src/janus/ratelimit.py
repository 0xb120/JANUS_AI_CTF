"""In-memory sliding-window rate limiting for a single JANUS process."""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Callable

from .errors import RateLimitedError


class RateLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}

    def _recent(self, key: str, window_seconds: float) -> deque[float]:
        hits = self._hits.setdefault(key, deque())
        horizon = self._clock() - window_seconds
        while hits and hits[0] <= horizon:
            hits.popleft()
        return hits

    def check(self, key: str, *, limit: int, window_seconds: float) -> None:
        hits = self._recent(key, window_seconds)
        if len(hits) >= limit:
            retry_after = max(1, math.ceil(hits[0] + window_seconds - self._clock()))
            raise RateLimitedError(retry_after)

    def record(self, key: str, *, window_seconds: float) -> None:
        self._recent(key, window_seconds).append(self._clock())

    def hit(self, key: str, *, limit: int, window_seconds: float) -> None:
        self.check(key, limit=limit, window_seconds=window_seconds)
        self.record(key, window_seconds=window_seconds)

    def refund(self, key: str) -> None:
        """Give back the most recent hit for key, e.g. when the charged action failed."""
        hits = self._hits.get(key)
        if hits:
            hits.pop()

    def prune(self, max_window_seconds: float) -> int:
        horizon = self._clock() - max_window_seconds
        idle = [key for key, hits in self._hits.items() if not hits or hits[-1] <= horizon]
        for key in idle:
            del self._hits[key]
        return len(idle)
