"""Async token-bucket rate limiter for GRID's hard per-minute ceilings."""

from __future__ import annotations

import asyncio
import time


class RateLimiter:
    """Allow at most ``rpm`` acquisitions per rolling 60s window."""

    def __init__(self, rpm: int, *, clock=time.monotonic) -> None:
        self.rpm = rpm
        self._clock = clock
        self._stamps: list[float] = []
        self._lock = asyncio.Lock()

    def _wait_needed(self) -> float:
        now = self._clock()
        self._stamps = [t for t in self._stamps if now - t < 60.0]
        if len(self._stamps) < self.rpm:
            return 0.0
        return 60.0 - (now - self._stamps[0])

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                wait = self._wait_needed()
                if wait <= 0:
                    self._stamps.append(self._clock())
                    return
            await asyncio.sleep(min(wait, 5.0))
