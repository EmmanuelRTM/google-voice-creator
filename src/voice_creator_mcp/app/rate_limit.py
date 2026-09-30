"""Per-principal sliding-window limiter for tools that spend Gemini quota."""

from __future__ import annotations

import math
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable

from voice_creator_mcp.errors import RateLimitError


class RateLimiter:
    def __init__(
        self,
        max_calls: int,
        window_seconds: float = 60.0,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_calls <= 0 or window_seconds <= 0:
            raise ValueError("max_calls and window_seconds must be positive")
        self._max_calls = max_calls
        self._window = window_seconds
        self._clock = clock
        self._calls: defaultdict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def acquire(self, key: str) -> None:
        with self._lock:
            now = self._clock()
            calls = self._calls[key]
            while calls and calls[0] <= now - self._window:
                calls.popleft()
            if len(calls) >= self._max_calls:
                retry_after = math.ceil(calls[0] + self._window - now)
                raise RateLimitError(
                    f"Rate limit of {self._max_calls} Gemini calls per {self._window:g}s exceeded; "
                    f"retry in {retry_after}s"
                )
            calls.append(now)
