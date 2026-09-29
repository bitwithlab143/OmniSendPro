"""Live updates over Server-Sent Events (design DS-20, P3-03)."""

from __future__ import annotations

import json

import pytest
from httpx import AsyncClient

from app.api import sse
from tests.conftest import Session, assign, create_provider, create_user, login_user, ready_campaign
from tests.test_events_and_health import _sent_campaign


def _events(body: str) -> list[tuple[str, dict]]:
    out = []
    for block in body.split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line and not line.startswith(":"))
        if "event" in lines:
            out.append((lines["event"], json.loads(lines["data"])))
    return out


async def test_campaign_stream_ends_when_final(client: AsyncClient, admin: Session, user: Session) -> None:
    _, campaign, _ = await _sent_campaign(client, admin, user, n=2)
    await client.post(f"/api/v1/user/campaigns/{campaign['id']}/cancel", headers=user.headers)
    r = await client.get(f"/api/v1/user/campaigns/{campaign['id']}/stream", headers=user.headers)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["x-accel-buffering"] == "no" and r.headers["cache-control"] == "no-store"
    events = _events(r.text)
    assert events[0][0] == "update" and events[0][1]["status"] in sse.FINAL_CAMPAIGN_STATES
    assert events[-1] == ("end", {"reason": "final"})

    # Admins can watch any campaign.
    r = await client.get(f"/api/v1/admin/campaigns/{campaign['id']}/stream", headers=admin.headers)
    assert _events(r.text)[-1] == ("end", {"reason": "final"})


async def test_streams_are_scoped_and_authenticated(client: AsyncClient, admin: Session, user: Session) -> None:
    provider = await create_provider(client, admin)
    await assign(client, admin, user.user_id, provider["id"])
    campaign = await ready_campaign(client, user, provider["id"], ["a@example.org"])
    other = await create_user(client, admin)
    other_session = await login_user(client, other["username"])
    r = await client.get(f"/api/v1/user/campaigns/{campaign['id']}/stream", headers=other_session.headers)
    assert r.status_code == 404
    r = await client.get(f"/api/v1/user/campaigns/{campaign['id']}/stream")
    assert r.status_code == 401
    r = await client.get("/api/v1/admin/dashboard/stream", headers=user.headers)
    assert r.status_code == 403


async def test_dashboard_streams_time_out_and_dedupe(client: AsyncClient, admin: Session, user: Session,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sse, "MAX_SECONDS", 0.25)
    r = await client.get("/api/v1/user/dashboard/stream", headers=user.headers)
    events = _events(r.text)
    assert r.text.startswith("retry: 3000")
    # Unchanged payloads are not re-sent: one update, then the timeout marker.
    assert [e for e, _ in events] == ["update", "end"] and events[-1][1] == {"reason": "timeout"}
    assert "today" in events[0][1]
    r = await client.get("/api/v1/admin/dashboard/stream", headers=admin.headers)
    assert [e for e, _ in _events(r.text)] == ["update", "end"]
