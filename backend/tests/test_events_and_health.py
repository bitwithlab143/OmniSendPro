"""Delivery webhooks, unsubscribe, provider health, settings, security middleware."""

from __future__ import annotations

import hashlib
import hmac
import json
import time

from httpx import AsyncClient
from sqlalchemy import select

from app.db.session import sessionmaker
from app.models import EventInbox
from app.services import health, processor
from tests.conftest import Session, assign, create_provider, provision_worker, ready_campaign, tick


def _sign(secret: str, body: bytes, ts: int | None = None) -> dict[str, str]:
    ts = ts or int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return {"X-OmniSend-Timestamp": str(ts), "X-OmniSend-Signature": f"sha256={sig}",
            "Content-Type": "application/json"}


async def _last_result() -> dict:
    async with sessionmaker()() as db:
        row = (await db.execute(select(EventInbox).order_by(EventInbox.id.desc()).limit(1))).scalar_one()
        assert row.processed_at is not None, row.last_error
        return row.result or {}


async def _sent_campaign(client: AsyncClient, admin: Session, user: Session, n: int = 3) -> tuple[dict, dict, list]:
    provider = await create_provider(client, admin)
    await assign(client, admin, user.user_id, provider["id"])
    campaign = await ready_campaign(client, user, provider["id"], [f"e{i}@example.org" for i in range(n)])
    await client.post(f"/api/v1/user/campaigns/{campaign['id']}/start", headers=user.headers,
                      json={"consent_confirmed": True})
    await tick()
    worker = await provision_worker(client, admin)
    job = (await client.post("/api/v1/worker/jobs/claim", headers=worker)).json()
    await client.post(f"/api/v1/worker/jobs/{job['job_id']}/results", headers=worker, json={
        "attempt_id": job["attempt_id"],
        "results": [{"recipient_id": r["id"], "outcome": "sent", "provider_message_id": f"msg-{i}"}
                    for i, r in enumerate(job["recipients"])]})
    await client.post(f"/api/v1/worker/jobs/{job['job_id']}/ack", headers=worker, json={"attempt_id": job["attempt_id"]})
    return provider, campaign, job["recipients"]


async def test_webhook_events(client: AsyncClient, admin: Session, user: Session) -> None:
    provider, campaign, recipients = await _sent_campaign(client, admin, user)
    r = await client.post(f"/api/v1/admin/providers/{provider['id']}/webhook-secret", headers=admin.headers)
    secret = r.json()["webhook_secret"]
    url = f"/api/v1/hooks/providers/{provider['id']}"

    body = json.dumps({"events": [
        {"type": "delivered", "provider_message_id": "msg-0"},
        {"type": "bounced", "provider_message_id": "msg-1", "bounce_type": "hard", "error_code": "5.1.1"},
        {"type": "complained", "provider_message_id": "msg-2"},
        {"type": "delivered", "provider_message_id": "unknown"},
    ]}).encode()
    r = await client.post(url, content=body, headers={"Content-Type": "application/json"})
    assert r.status_code == 401
    r = await client.post(url, content=body, headers=_sign(secret, body, ts=int(time.time()) - 3600))
    assert r.status_code == 401, "stale timestamp must be rejected (replay)"
    r = await client.post(url, content=body, headers=_sign(secret, body))
    assert r.status_code == 202, r.text
    assert r.json() == {"queued": 4, "rejected": 0}
    assert await processor.drain() == 1
    assert await _last_result() == {"accepted": 3, "duplicates": 0, "unknown": 1, "rejected": 0}
    r = await client.post(url, content=body, headers=_sign(secret, body))
    await processor.drain()
    assert (await _last_result())["duplicates"] == 3

    stats = (await client.get(f"/api/v1/user/campaigns/{campaign['id']}/stats", headers=user.headers)).json()
    # SMTP acceptance counted as delivered before the webhook existed; bounce moves one out of "sent".
    assert stats["bounced"] == 1 and stats["complained"] == 1 and stats["sent"] == 2
    supp = (await client.get("/api/v1/admin/suppressions", headers=admin.headers)).json()["items"]
    assert {(s["type"], s["email_normalized"]) for s in supp} == {
        ("hard_bounce", recipients[1]["email"]), ("complaint", recipients[2]["email"])}
    complaint = next(s for s in supp if s["type"] == "complaint")
    r = await client.delete(f"/api/v1/admin/suppressions/{complaint['id']}", headers=admin.headers)
    assert r.status_code == 400


async def test_unsubscribe_flow(client: AsyncClient, admin: Session, user: Session) -> None:
    _, campaign, recipients = await _sent_campaign(client, admin, user, n=1)
    url = recipients[0]["unsubscribe_url"].replace("http://test", "")
    r = await client.get(url)
    assert r.status_code == 200 and "<form" in r.text
    assert (await client.get("/api/v1/admin/suppressions", headers=admin.headers)).json()["items"] == []
    r = await client.post(url, content=b"List-Unsubscribe=One-Click",
                          headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 200 and "unsubscribed" in r.text
    supp = (await client.get("/api/v1/admin/suppressions", headers=admin.headers)).json()["items"]
    assert supp[0]["type"] == "unsubscribe" and supp[0]["scope_user_id"] == user.user_id
    stats = (await client.get(f"/api/v1/user/campaigns/{campaign['id']}/stats", headers=user.headers)).json()
    assert stats["unsubscribed"] == 1
    r = await client.post(url[:-3] + "xyz")
    assert r.status_code == 404


async def test_provider_health_state_machine(client: AsyncClient, admin: Session) -> None:
    provider = await create_provider(client, admin)
    pid = provider["id"]
    async with sessionmaker()() as db:
        # Few failures: not enough evidence, nothing changes.
        await health.bump(pid, attempts=5, auth_failures=5)
        assert await health.evaluate_all(db) == []
    # A heavily failing window degrades, repeated bad windows eventually disable (never on one failure).
    statuses = []
    for _ in range(6):
        await health.bump(pid, attempts=100, connection_failures=95, successes=5)
        async with sessionmaker()() as db:
            await health.evaluate_all(db)
        r = await client.get(f"/api/v1/admin/providers/{pid}", headers=admin.headers)
        statuses.append(r.json()["status"])
    assert "DEGRADED" in statuses and statuses[-1] == "DISABLED"
    assert statuses.index("DISABLED") >= 2
    logs = (await client.get(f"/api/v1/admin/providers/{pid}/health", headers=admin.headers)).json()
    assert len(logs) >= 3
    r = await client.post(f"/api/v1/admin/providers/{pid}/enable", headers=admin.headers)
    assert r.json()["status"] == "ACTIVE"


async def test_provider_secret_is_never_returned(client: AsyncClient, admin: Session) -> None:
    provider = await create_provider(client, admin, password="very-secret-value")
    assert provider["has_secret"] is True
    r = await client.get(f"/api/v1/admin/providers/{provider['id']}", headers=admin.headers)
    assert "very-secret-value" not in r.text and "encrypted" not in r.text
    r = await client.get("/api/v1/admin/audit-logs", headers=admin.headers)
    assert "very-secret-value" not in r.text


async def test_settings_validation(client: AsyncClient, admin: Session) -> None:
    r = await client.put("/api/v1/admin/settings", headers=admin.headers,
                         json={"values": {"max_attempts": -1, "nope": 1}})
    assert r.status_code == 400 and set(r.json()["error"]["details"]) == {"max_attempts", "nope"}
    r = await client.put("/api/v1/admin/settings", headers=admin.headers,
                         json={"values": {"retry_schedule_seconds": [10, 20], "max_attempts": 3}})
    assert r.status_code == 200 and r.json()["values"]["max_attempts"] == 3


async def test_security_headers_and_size_limit(client: AsyncClient) -> None:
    r = await client.get("/healthz")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    assert "x-request-id" in r.headers
    r = await client.post("/api/v1/auth/login", content=b"x" * (3 * 1024 * 1024),
                          headers={"Content-Type": "application/json"})
    assert r.status_code == 413


async def test_readyz_and_metrics(client: AsyncClient) -> None:
    r = await client.get("/readyz")
    assert r.status_code == 200 and r.json()["checks"] == {"database": True, "redis": True}
    r = await client.get("/metrics")
    assert "omnisend_queue_depth" in r.text and "omnisend_workers_online" in r.text
