"""Buffers per-recipient results and flushes them in batches (every N results or T seconds).

Incremental reporting keeps the duplicate-send window small if this worker crashes (§31).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger("worker.reporting")


class ResultBuffer:
    def __init__(self, flush: Callable[[list[dict[str, Any]]], Awaitable[Any]], size: int = 200,
                 interval: float = 1.0) -> None:
        self._flush_fn = flush
        self.size = size
        self.interval = interval
        self._items: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self.reported = 0

    def start(self) -> None:
        self._task = asyncio.create_task(self._periodic())

    async def _periodic(self) -> None:
        while True:
            await asyncio.sleep(self.interval)
            await self.flush()

    async def add(self, result: dict[str, Any]) -> None:
        self._items.append(result)
        if len(self._items) >= self.size:
            await self.flush()

    async def flush(self) -> None:
        async with self._lock:
            if not self._items:
                return
            batch, self._items = self._items, []
            await self._flush_fn(batch)
            self.reported += len(batch)

    async def close(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        await self.flush()
