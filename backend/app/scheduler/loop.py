"""In-process scheduler (ARCHITECTURE.md §60, design DS-05). MVP: runs inside the API process; with several
API instances only the holder of a Redis lock does the work, so it can later move to a dedicated service
without code changes (Phase 3, P3-02).

Duties per tick:
  * build batches/jobs for newly started campaigns
  * recover jobs whose lease expired (worker crash, §47)
  * mark workers WARNING/OFFLINE when heartbeats stop (§10)
  * finish campaigns with no open jobs
  * provider health scoring (every `HEALTH_INTERVAL_SECONDS`)
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
import time

from app.core.config import get_settings
from app.core.redis import Keys, get_redis
from app.db.session import sessionmaker
from app.services import campaigns as campaign_service
from app.services import health
from app.services import jobs as job_service
from app.services import workers as worker_service

log = logging.getLogger("omnisend.scheduler")

_RELEASE_LUA = "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) else return 0 end"


class Scheduler:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.identity = f"{socket.gethostname()}:{os.getpid()}"
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._last_health = 0.0

    async def _acquire(self, key: str, ttl: int) -> bool:
        redis = get_redis()
        if await redis.set(key, self.identity, nx=True, ex=ttl):
            return True
        # Extend if we already own it.
        if await redis.get(key) == self.identity:
            await redis.expire(key, ttl)
            return True
        return False

    async def tick(self) -> dict[str, int]:
        """One scheduler pass. Public so tests and ops tooling can drive it deterministically."""
        stats = {"built": 0, "recovered": 0, "offline": 0, "completed": 0, "health_changes": 0}
        maker = sessionmaker()
        async with maker() as db:
            for cid in await campaign_service.campaigns_needing_batches(db):
                async with maker() as build_db:
                    result = await campaign_service.build_batches(build_db, cid)
                    if result:
                        stats["built"] += 1
                        log.info("batches_built", extra={"campaign_id": str(cid), "batches": result.batches,
                                                         "queued": result.queued, "suppressed": result.suppressed})
        async with maker() as db:
            stats["recovered"] = await job_service.recover_expired_leases(db)
        async with maker() as db:
            stats["offline"] = len(await worker_service.refresh_statuses(db))
        async with maker() as db:
            for cid in await campaign_service.running_campaign_ids(db):
                async with maker() as cdb:
                    if await campaign_service.check_completion(cdb, cid):
                        stats["completed"] += 1
        if time.monotonic() - self._last_health >= self.settings.health_interval_seconds:
            self._last_health = time.monotonic()
            async with maker() as db:
                changes = await health.evaluate_all(db)
                stats["health_changes"] = len(changes)
                for name, old, new in changes:
                    log.warning("provider_state_changed", extra={"provider": name, "from": old, "to": new})
        return stats

    async def run(self) -> None:
        interval = self.settings.scheduler_interval_seconds
        ttl = max(15, int(interval * 3))
        log.info("scheduler_started", extra={"identity": self.identity})
        while not self._stop.is_set():
            try:
                if await self._acquire(Keys.SCHEDULER_LOCK, ttl):
                    stats = await self.tick()
                    if any(stats.values()):
                        log.info("scheduler_tick", extra=stats)
            except Exception:  # noqa: BLE001 - never let the loop die
                log.exception("scheduler_tick_failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=interval)

    def start(self) -> None:
        self._task = asyncio.create_task(self.run(), name="omnisend-scheduler")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(self._task, timeout=10)
        with contextlib.suppress(Exception):
            await get_redis().eval(_RELEASE_LUA, 1, Keys.SCHEDULER_LOCK, self.identity)
