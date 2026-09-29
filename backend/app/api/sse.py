"""Server-Sent Events for live dashboards (design DS-20, ADR-009).

Each tick builds the same payload as the matching GET endpoint with a short-lived DB session, sends it
only when it changed, and pings every ``PING_SECONDS`` so proxies keep the connection open. Streams end
after ``MAX_SECONDS`` (clients reconnect) or when ``done(payload)`` says the resource reached a final state.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import sessionmaker

MAX_SECONDS = 300.0
PING_SECONDS = 15.0

Producer = Callable[[AsyncSession], Awaitable[Any]]


def _frame(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(jsonable_encoder(data), separators=(',', ':'))}\n\n"


async def _events(request: Request, produce: Producer, interval: float,
                  done: Callable[[Any], bool] | None) -> AsyncIterator[str]:
    started = last_sent_at = time.monotonic()
    last: str | None = None
    yield "retry: 3000\n\n"  # EventSource-compatible reconnect hint
    while True:
        async with sessionmaker()() as db:
            payload = await produce(db)
        frame = _frame("update", payload)
        now = time.monotonic()
        if frame != last:
            last, last_sent_at = frame, now
            yield frame
        elif now - last_sent_at >= PING_SECONDS:
            last_sent_at = now
            yield ": ping\n\n"
        if done is not None and done(payload):
            yield _frame("end", {"reason": "final"})
            return
        if now - started >= MAX_SECONDS:
            yield _frame("end", {"reason": "timeout"})
            return
        if await request.is_disconnected():
            return
        await asyncio.sleep(interval)


async def stream(request: Request, request_db: AsyncSession, produce: Producer, *, interval: float,
                 done: Callable[[Any], bool] | None = None) -> StreamingResponse:
    # The request's session (auth, ownership check) is released now, not when the stream ends minutes
    # later; every tick uses its own short-lived session instead.
    await request_db.close()
    return StreamingResponse(
        _events(request, produce, interval, done),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


FINAL_CAMPAIGN_STATES = {"COMPLETED", "FAILED", "CANCELLED"}


def campaign_done(payload: Any) -> bool:
    status = payload.get("status") if isinstance(payload, dict) else getattr(payload, "status", None)
    return str(getattr(status, "value", status)) in FINAL_CAMPAIGN_STATES
