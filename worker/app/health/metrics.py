"""Heartbeat metrics (§10): cpu, memory, active jobs, current send rate."""

from __future__ import annotations

import collections
import time

import psutil


class RateMeter:
    """Messages per second over a sliding window."""

    def __init__(self, window: float = 10.0) -> None:
        self.window = window
        self._events: collections.deque[float] = collections.deque()

    def mark(self, n: int = 1) -> None:
        now = time.monotonic()
        for _ in range(n):
            self._events.append(now)

    def rate(self) -> float:
        cutoff = time.monotonic() - self.window
        while self._events and self._events[0] < cutoff:
            self._events.popleft()
        return round(len(self._events) / self.window, 2)


def system_usage() -> tuple[float, float]:
    return psutil.cpu_percent(interval=None), psutil.virtual_memory().percent
