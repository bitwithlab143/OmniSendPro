"""Background runtime outside the API (design DS-19, P3-02).

    python -m app.runner                      # scheduler + processor
    python -m app.runner scheduler            # only the scheduler (leader-elected, run 1+ for failover)
    python -m app.runner processor            # only the event/import processor (scale out freely)

The API runs the same roles in-process when RUN_SCHEDULER=true (the development default), so production
sets RUN_SCHEDULER=false on API replicas and runs this instead.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.redis import Keys, close_redis, get_redis
from app.db.session import dispose_engine
from app.scheduler.loop import Scheduler

log = logging.getLogger("omnisend.runner")

ROLES = ("scheduler", "processor")


class Processor:
    """Drains the event inbox and recipient imports; any number of replicas may run."""

    def __init__(self, poll_seconds: float = 2.0) -> None:
        self.poll_seconds = poll_seconds
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def run_once(self) -> int:
        from app.services import imports, processor

        return await processor.process_inbox() + await imports.process_next()

    async def run(self) -> None:
        log.info("processor_started")
        while not self._stop.is_set():
            try:
                busy = await self.run_once()
            except Exception:  # noqa: BLE001 - never let the loop die
                log.exception("processor_iteration_failed")
                busy = 0
            if busy:
                continue
            try:
                await get_redis().blpop([Keys.PROCESSOR_WAKE], timeout=self.poll_seconds)
            except Exception:  # noqa: BLE001 - Redis down: plain sleep
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), timeout=self.poll_seconds)

    def start(self) -> None:
        self._task = asyncio.create_task(self.run(), name="omnisend-processor")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()  # blocked in BLPOP; an in-flight item is safe (lease + idempotency)
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(self._task, timeout=10)


async def main(roles: list[str]) -> None:
    settings = get_settings()
    configure_logging("runner", settings.log_level)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    scheduler = Scheduler() if "scheduler" in roles else None
    processor = Processor() if "processor" in roles else None
    if scheduler:
        scheduler.start()
    if processor:
        processor.start()
    log.info("runner_started", extra={"roles": roles})
    await stop.wait()
    log.info("runner_stopping")
    if processor:
        await processor.stop()
    if scheduler:
        await scheduler.stop()
    await close_redis()
    await dispose_engine()


def parse_roles(argv: list[str]) -> list[str]:
    roles = argv or list(ROLES)
    unknown = [r for r in roles if r not in ROLES]
    if unknown:
        raise SystemExit(f"Unknown role(s): {', '.join(unknown)}. Available: {', '.join(ROLES)}")
    return roles


if __name__ == "__main__":
    asyncio.run(main(parse_roles(sys.argv[1:])))
