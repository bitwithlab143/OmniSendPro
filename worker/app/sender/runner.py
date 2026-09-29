"""Executes one claimed job: connect → send each recipient (rate limited) → report → ack/fail/release.

Lifecycle (ARCHITECTURE.md §8): claim → process batch → send → report result.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any

from app.config import WorkerConfig
from app.health.metrics import RateMeter
from app.providers.smtp import SmtpPoolRegistry, SmtpProvider
from app.queue.client import ApiClient, StaleAttemptError
from app.rate_limit.bucket import LocalBucket, RedisBucket, Unlimited
from app.reporting.buffer import ResultBuffer
from app.retry.classify import Classified, classify_exception
from app.sender.message import CompiledCampaign

log = logging.getLogger("worker.job")

# Consecutive per-recipient connection/timeout errors after which the job is treated as a systemic failure.
SYSTEMIC_STREAK = 5


def provider_is_smtp(config: dict[str, Any]) -> bool:
    return config.get("type", "smtp") == "smtp"


class JobAborted(Exception):
    def __init__(self, classified: Classified) -> None:
        super().__init__(classified.message)
        self.classified = classified


class JobRunner:
    def __init__(self, api: ApiClient, config: WorkerConfig, job: dict[str, Any], meter: RateMeter,
                 worker_bucket: LocalBucket | Unlimited, redis: Any = None, provider_factory: Any = None,
                 pools: SmtpPoolRegistry | None = None) -> None:
        self.api = api
        self.config = config
        self.job = job
        self.job_id: str = job["job_id"]
        self.attempt_id: str = job["attempt_id"]
        self.meter = meter
        self.worker_bucket = worker_bucket
        self.redis = redis
        self.provider_factory = provider_factory or (
            lambda cfg: SmtpProvider(cfg, pool_size=config.smtp_connections_per_job, timeout=config.send_timeout,
                                     pipelining=config.smtp_pipelining)
        )
        self.pools = pools
        self.stop_requested = asyncio.Event()
        self.lease_seconds = 120
        self._error_streak = 0
        self._stale = False

    def request_stop(self) -> None:
        self.stop_requested.set()

    def _provider_bucket(self) -> LocalBucket | RedisBucket | Unlimited:
        rate = self.job.get("rate_limit_per_second")
        if not rate:
            return Unlimited()
        local = LocalBucket(rate)
        if self.redis is not None:
            return RedisBucket(self.redis, f"ratelimit:provider:{self.job['provider']['id']}", rate, local)
        return local

    async def _lease_loop(self) -> None:
        interval = max(5.0, self.lease_seconds / 3)
        while True:
            await asyncio.sleep(interval)
            try:
                resp = await self.api.renew(self.job_id, self.attempt_id)
            except StaleAttemptError:
                self._stale = True
                self.request_stop()
                return
            except Exception as exc:  # noqa: BLE001 - keep trying until the lease really expires
                log.warning("lease_renew_failed", extra={"job_id": self.job_id, "error": str(exc)})
                continue
            if resp.get("action") == "stop":
                log.info("job_stop_requested", extra={"job_id": self.job_id})
                self.request_stop()

    async def run(self) -> str:
        started = time.monotonic()
        campaign = self.job["campaign"]
        compiled = CompiledCampaign(campaign)
        recipients = self.job["recipients"]
        shared = self.pools is not None and provider_is_smtp(self.job["provider"])
        provider = self.pools.acquire(self.job["provider"]) if shared else self.provider_factory(self.job["provider"])
        buffer = ResultBuffer(lambda batch: self.api.results(self.job_id, self.attempt_id, batch),
                              size=self.config.result_flush_size, interval=self.config.result_flush_interval)
        lease_task = asyncio.create_task(self._lease_loop())
        outcome = "completed"
        try:
            # Mark the job as processing immediately (first lease renewal).
            first = await self.api.renew(self.job_id, self.attempt_id)
            if first.get("action") == "stop":
                self.request_stop()
            await provider.connect()
            buffer.start()
            bucket = self._provider_bucket()
            queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
            for r in recipients:
                queue.put_nowait(r)

            async def sender_loop() -> None:
                while not self.stop_requested.is_set():
                    try:
                        recipient = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        return
                    await bucket.acquire()
                    await self.worker_bucket.acquire()
                    if self.stop_requested.is_set():
                        return
                    await buffer.add(await self._send_one(provider, compiled, recipient))

            # Parallel SMTP sessions for this job: the provider's policy wins over the worker default.
            per_job = int(self.job["provider"].get("max_connections") or self.config.smtp_connections_per_job)
            concurrency = max(1, min(per_job, len(recipients)))
            senders = [asyncio.create_task(sender_loop()) for _ in range(concurrency)]
            try:
                await asyncio.gather(*senders)
            except JobAborted:
                for t in senders:
                    t.cancel()
                raise
            await buffer.close()

            if self._stale:
                outcome = "stale"
            elif self.stop_requested.is_set() and not queue.empty():
                await self.api.release(self.job_id, self.attempt_id, "stop requested")
                outcome = "released"
            else:
                await self.api.ack(self.job_id, self.attempt_id)
        except JobAborted as exc:
            c = exc.classified
            with contextlib.suppress(Exception):
                await buffer.close()
            await self._fail(c)
            outcome = "failed"
        except StaleAttemptError:
            outcome = "stale"
        except Exception as exc:  # noqa: BLE001 - connect() failures and unexpected errors
            c = classify_exception(exc)
            with contextlib.suppress(Exception):
                await buffer.close()
            if c.category in ("connection", "timeout", "other"):
                c.systemic = True
            if c.category == "other":
                c.transient = True  # unknown errors retry with backoff before dead-lettering
                log.exception("job_unexpected_error", extra={"job_id": self.job_id})
            await self._fail(c)
            outcome = "failed"
        finally:
            lease_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await lease_task
            if shared:
                self.pools.release(provider)  # keep authenticated connections for the next job
            else:
                with contextlib.suppress(Exception):
                    await provider.close()
        log.info("job_finished", extra={"job_id": self.job_id, "outcome": outcome, "recipients": len(recipients),
                                        "reported": buffer.reported,
                                        "duration_ms": int((time.monotonic() - started) * 1000)})
        return outcome

    async def _fail(self, c: Classified) -> None:
        category = c.category if c.category in ("auth", "connection", "timeout", "provider") else "other"
        code = " ".join(str(p) for p in (c.smtp_code, c.enhanced_code) if p) or None
        with contextlib.suppress(StaleAttemptError):
            await self.api.failure(self.job_id, self.attempt_id, error_code=code, error_message=c.message or category,
                                   transient=c.transient, category=category)

    async def _send_one(self, provider: Any, compiled: CompiledCampaign, recipient: dict[str, Any]) -> dict[str, Any]:
        base = {"recipient_id": recipient["id"]}
        try:
            message, message_id = compiled.render(recipient)
        except (ValueError, UnicodeError, KeyError) as exc:
            return {**base, "outcome": "failed", "category": "invalid", "error_message": str(exc)[:500]}
        try:
            await provider.send(message, compiled.from_email, recipient["email"])
        except Exception as exc:  # noqa: BLE001 - classified below
            c = classify_exception(exc)
            if c.systemic:
                raise JobAborted(c) from exc
            if c.category in ("connection", "timeout"):
                self._error_streak += 1
                if self._error_streak >= SYSTEMIC_STREAK:
                    c.systemic = True
                    raise JobAborted(c) from exc
            else:
                self._error_streak = 0
            return {**base, "outcome": "failed", "category": c.category, "smtp_code": c.smtp_code,
                    "enhanced_code": c.enhanced_code, "error_message": (c.message or "")[:500]}
        self._error_streak = 0
        self.meter.mark()
        return {**base, "outcome": "sent", "provider_message_id": message_id}
