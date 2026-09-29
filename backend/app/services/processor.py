"""Event processor: drains the event inbox (design DS-19, P3-06).

Rows are *leased* (not locked for the whole run): a claim pushes ``next_attempt_at`` forward and bumps
``attempts`` in one statement, so several processor replicas never take the same row, and a crashed
processor's rows come back automatically when the lease runs out. Processing is idempotent (event
de-duplication, ADR-013), so a row processed twice after a crash is harmless.
"""

from __future__ import annotations

import contextlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import Keys, get_redis
from app.db.session import sessionmaker
from app.models import EventInbox, Provider
from app.services import events, inbound

log = logging.getLogger("omnisend.processor")

BATCH = 100
LEASE = timedelta(minutes=5)
MAX_ATTEMPTS = 10


def backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(3600, 5 * 2 ** max(0, attempts - 1)))


async def enqueue(db: AsyncSession, provider_id: Any, *, events_: list[dict[str, Any]] | None = None,
                  raw: bytes | None = None) -> int:
    row = EventInbox(provider_id=provider_id, kind="raw" if raw is not None else "json", events=events_, raw=raw)
    db.add(row)
    await db.commit()
    await wake()
    return row.id


async def wake() -> None:
    with contextlib.suppress(Exception):
        redis = get_redis()
        await redis.lpush(Keys.PROCESSOR_WAKE, "1")
        await redis.ltrim(Keys.PROCESSOR_WAKE, 0, 0)


async def _claim(db: AsyncSession, limit: int) -> list[int]:
    rows = await db.execute(
        text(
            """
            UPDATE event_inbox SET attempts = attempts + 1, next_attempt_at = now() + make_interval(secs => :lease)
             WHERE id IN (SELECT id FROM event_inbox
                           WHERE processed_at IS NULL AND next_attempt_at <= now() AND attempts < :max
                           ORDER BY id LIMIT :limit FOR UPDATE SKIP LOCKED)
            RETURNING id
            """
        ),
        {"lease": LEASE.total_seconds(), "max": MAX_ATTEMPTS, "limit": limit},
    )
    ids = sorted(r[0] for r in rows)
    await db.commit()
    return ids


async def _process_one(row_id: int) -> None:
    maker = sessionmaker()
    async with maker() as db:
        row = await db.get(EventInbox, row_id)
        if row is None or row.processed_at is not None:
            return
        attempts = row.attempts  # read now: a rollback below expires the instance
        try:
            if row.kind == "raw":
                provider = await db.get(Provider, row.provider_id)
                if provider is None:
                    result: dict[str, Any] = {"skipped": "provider deleted"}
                else:
                    result = (await inbound.process_raw(db, provider, row.raw or b"")).as_dict()
            else:
                r = await events.ingest(db, row.provider_id, list(row.events or []))
                result = {"accepted": r.accepted, "duplicates": r.duplicates, "unknown": r.unknown,
                          "rejected": len(r.errors)}
        except Exception as exc:  # noqa: BLE001 - recorded on the row and retried with backoff
            await db.rollback()
            await db.execute(
                update(EventInbox).where(EventInbox.id == row_id).values(
                    last_error=f"{type(exc).__name__}: {exc}"[:512],
                    next_attempt_at=datetime.now(UTC) + backoff(attempts),
                )
            )
            await db.commit()
            log.warning("inbox_item_failed", extra={"id": row_id, "attempts": attempts, "error": str(exc)[:200]})
            return
        await db.execute(
            update(EventInbox).where(EventInbox.id == row_id)
            .values(processed_at=datetime.now(UTC), result=result, last_error=None)
        )
        await db.commit()


async def process_inbox(limit: int = BATCH) -> int:
    """Process one batch; returns the number of rows claimed."""
    async with sessionmaker()() as db:
        ids = await _claim(db, limit)
    for row_id in ids:
        await _process_one(row_id)
    return len(ids)


async def drain(max_batches: int = 100) -> int:
    """Process until the inbox is empty (tests, ops tooling)."""
    total = 0
    for _ in range(max_batches):
        n = await process_inbox()
        total += n
        if n == 0:
            break
    return total


async def inbox_counts(db: AsyncSession) -> dict[str, int]:
    row = (
        await db.execute(
            text(
                """
                SELECT count(*) FILTER (WHERE processed_at IS NULL AND attempts < :max) AS pending,
                       count(*) FILTER (WHERE processed_at IS NULL AND attempts >= :max) AS dead,
                       COALESCE(EXTRACT(EPOCH FROM now() - min(received_at) FILTER (WHERE processed_at IS NULL)), 0)
                         AS oldest_seconds
                  FROM event_inbox
                """
            ),
            {"max": MAX_ATTEMPTS},
        )
    ).one()
    return {"pending": int(row.pending), "dead": int(row.dead), "oldest_seconds": int(row.oldest_seconds)}
