"""Event inbox + processor and the background runner (design DS-19: P3-02, P3-06)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from app.db.session import sessionmaker
from app.models import EventInbox
from app.runner import Processor, parse_roles
from app.services import events, processor
from tests.conftest import Session
from tests.test_events_and_health import _sent_campaign, _sign


async def _rows() -> list[EventInbox]:
    async with sessionmaker()() as db:
        return list((await db.execute(select(EventInbox).order_by(EventInbox.id))).scalars().all())


async def test_webhook_is_queued_then_processed(client: AsyncClient, admin: Session, user: Session) -> None:
    provider, campaign, _ = await _sent_campaign(client, admin, user, n=2)
    secret = (await client.post(f"/api/v1/admin/providers/{provider['id']}/webhook-secret",
                                headers=admin.headers)).json()["webhook_secret"]
    body = json.dumps({"events": [{"type": "delivered", "provider_message_id": "msg-0"},
                                  {"type": "opened", "provider_message_id": "msg-1"},
                                  {"type": "delivered"}]}).encode()
    r = await client.post(f"/api/v1/hooks/providers/{provider['id']}", content=body, headers=_sign(secret, body))
    assert r.status_code == 202 and r.json() == {"queued": 1, "rejected": 2}

    # Nothing is applied until the processor runs.
    q = (await client.get("/api/v1/admin/queues", headers=admin.headers)).json()
    assert q["event_inbox"]["pending"] == 1
    assert "omnisend_event_inbox_pending 1" in (await client.get("/metrics")).text

    await processor.drain()
    [row] = await _rows()
    assert row.processed_at is not None and row.attempts == 1 and row.result["accepted"] == 1
    q = (await client.get("/api/v1/admin/queues", headers=admin.headers)).json()
    assert q["event_inbox"] == {"pending": 0, "dead": 0, "oldest_seconds": 0}


async def test_failures_retry_with_backoff_then_dead_letter(
    client: AsyncClient, admin: Session, user: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider, _, _ = await _sent_campaign(client, admin, user, n=1)
    async with sessionmaker()() as db:
        await processor.enqueue(db, provider["id"], events_=[{"type": "delivered", "provider_message_id": "msg-0"}])

    calls = 0
    real_ingest = events.ingest

    async def flaky(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("database hiccup")
        return await real_ingest(*args, **kwargs)

    monkeypatch.setattr(events, "ingest", flaky)
    assert await processor.process_inbox() == 1
    [row] = await _rows()
    assert row.processed_at is None and row.last_error == "RuntimeError: database hiccup"
    assert row.next_attempt_at > datetime.now(UTC), "backed off"
    assert await processor.process_inbox() == 0, "not due yet"

    async with sessionmaker()() as db:  # time passes
        await db.execute(update(EventInbox).values(next_attempt_at=datetime.now(UTC) - timedelta(seconds=1)))
        await db.commit()
    assert await processor.process_inbox() == 1
    [row] = await _rows()
    assert row.processed_at is not None and row.attempts == 2 and row.last_error is None

    # A report that always fails stops after MAX_ATTEMPTS and is reported as dead.
    async def broken(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise ValueError("bad")

    monkeypatch.setattr(events, "ingest", broken)
    async with sessionmaker()() as db:
        await processor.enqueue(db, provider["id"], events_=[{"type": "delivered", "provider_message_id": "x"}])
    for _ in range(processor.MAX_ATTEMPTS + 2):
        async with sessionmaker()() as db:
            await db.execute(update(EventInbox).where(EventInbox.processed_at.is_(None))
                             .values(next_attempt_at=datetime.now(UTC) - timedelta(seconds=1)))
            await db.commit()
        await processor.process_inbox()
    async with sessionmaker()() as db:
        assert await processor.inbox_counts(db) == {"pending": 0, "dead": 1, "oldest_seconds": pytest.approx(0, abs=5)}


async def test_concurrent_processors_never_share_rows(client: AsyncClient, admin: Session, user: Session) -> None:
    provider, _, _ = await _sent_campaign(client, admin, user, n=1)
    async with sessionmaker()() as db:
        for i in range(30):
            db.add(EventInbox(provider_id=provider["id"], kind="json",
                              events=[{"type": "delivered", "provider_message_id": f"none-{i}"}]))
        await db.commit()
    counts = await asyncio.gather(*(processor.process_inbox(limit=7) for _ in range(6)))
    assert sum(counts) == 30
    assert all(r.attempts == 1 and r.processed_at for r in await _rows())


async def test_processor_loop_wakes_and_stops(client: AsyncClient, admin: Session, user: Session) -> None:
    provider, _, _ = await _sent_campaign(client, admin, user, n=1)
    loop = Processor(poll_seconds=30)
    loop.start()
    try:
        async with sessionmaker()() as db:
            await processor.enqueue(db, provider["id"], events_=[{"type": "delivered", "provider_message_id": "msg-0"}])
        for _ in range(50):  # woken through Redis, long before the 30 s poll
            if (await _rows())[0].processed_at:
                break
            await asyncio.sleep(0.05)
        assert (await _rows())[0].processed_at is not None
    finally:
        await loop.stop()


def test_runner_roles() -> None:
    assert parse_roles([]) == ["scheduler", "processor"]
    assert parse_roles(["processor"]) == ["processor"]
    with pytest.raises(SystemExit):
        parse_roles(["mailer"])
