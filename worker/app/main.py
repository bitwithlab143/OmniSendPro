"""Worker process: authenticate → register → heartbeat → claim/run jobs until SIGTERM (§8)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from typing import Any

import httpx
from redis.asyncio import Redis

from app.config import WorkerConfig
from app.health.metrics import RateMeter, system_usage
from app.queue.client import ApiClient, WorkerDisabledError
from app.rate_limit.bucket import LocalBucket, Unlimited
from app.sender.runner import JobRunner

log = logging.getLogger("worker")


class Worker:
    def __init__(self, config: WorkerConfig, transport: httpx.AsyncBaseTransport | None = None,
                 provider_factory: Any = None) -> None:
        self.config = config
        self.api = ApiClient(config, transport=transport)
        self.meter = RateMeter()
        self.running: dict[str, tuple[asyncio.Task[str], JobRunner]] = {}
        self.stopping = asyncio.Event()
        self.max_jobs = config.max_concurrent_jobs or 4
        self.heartbeat_interval = 10.0
        self.lease_seconds = 120
        self.redis: Redis | None = Redis.from_url(config.redis_url) if config.redis_url else None
        self.worker_bucket: LocalBucket | Unlimited = Unlimited()
        self.provider_factory = provider_factory
        self.jobs_completed = 0

    async def _heartbeat(self, status: str = "online") -> None:
        cpu, mem = system_usage()
        with contextlib.suppress(Exception):
            await self.api.heartbeat({"status": status, "cpu": cpu, "memory": mem,
                                      "active_jobs": len(self.running), "current_rate": self.meter.rate()})

    async def _heartbeat_loop(self) -> None:
        while not self.stopping.is_set():
            await self._heartbeat("draining" if self.stopping.is_set() else "online")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.stopping.wait(), timeout=self.heartbeat_interval)

    async def _run_job(self, runner: JobRunner) -> str:
        try:
            return await runner.run()
        finally:
            self.running.pop(runner.job_id, None)
            self.jobs_completed += 1

    async def run(self, max_idle_polls: int | None = None) -> None:
        info = await self.api.register()
        self.heartbeat_interval = float(info.get("heartbeat_interval_seconds", 10))
        self.lease_seconds = int(info.get("lease_seconds", 120))
        self.max_jobs = int(self.config.max_concurrent_jobs or info.get("max_concurrent_jobs", 4))
        capacity = self.config.capacity or info.get("capacity")
        if capacity:
            self.worker_bucket = LocalBucket(float(capacity))
        log.info("worker_registered", extra={"max_jobs": self.max_jobs, "capacity": capacity})
        hb = asyncio.create_task(self._heartbeat_loop())
        idle_delay = 0.5
        idle_polls = 0
        try:
            while not self.stopping.is_set():
                if len(self.running) >= self.max_jobs:
                    await asyncio.sleep(0.2)
                    continue
                try:
                    job = await self.api.claim()
                except WorkerDisabledError:
                    log.error("worker_disabled")
                    break
                except Exception as exc:  # noqa: BLE001
                    log.warning("claim_failed", extra={"error": str(exc)})
                    job = None
                if job is None:
                    idle_polls += 1
                    if max_idle_polls is not None and idle_polls >= max_idle_polls and not self.running:
                        break
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(self.stopping.wait(), timeout=idle_delay)
                    idle_delay = min(idle_delay * 2, self.config.idle_poll_max)
                    continue
                idle_polls = 0
                idle_delay = 0.5
                runner = JobRunner(self.api, self.config, job, self.meter, self.worker_bucket, self.redis,
                                   self.provider_factory)
                runner.lease_seconds = self.lease_seconds
                task = asyncio.create_task(self._run_job(runner))
                self.running[runner.job_id] = (task, runner)
                log.info("job_claimed", extra={"job_id": runner.job_id, "recipients": len(job["recipients"])})
        finally:
            await self.shutdown()
            hb.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await hb

    def request_stop(self) -> None:
        if not self.stopping.is_set():
            log.info("worker_stopping", extra={"active_jobs": len(self.running)})
            self.stopping.set()

    async def shutdown(self) -> None:
        """Graceful: stop claiming, ask running jobs to stop (they report + release), then go offline."""
        self.stopping.set()
        for _, runner in list(self.running.values()):
            runner.request_stop()
        tasks = [t for t, _ in list(self.running.values())]
        if tasks:
            await asyncio.wait(tasks, timeout=60)
        await self._heartbeat("offline")
        await self.api.close()
        if self.redis is not None:
            await self.redis.aclose()


async def main() -> None:
    from app.logging import configure

    config = WorkerConfig.from_env()
    configure(config.worker_id, config.log_level)
    worker = Worker(config)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, worker.request_stop)
    await worker.run()
