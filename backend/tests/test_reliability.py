"""Retries, dead-letter, lease recovery, quotas, pause/cancel (§22, §31, §47, §68)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CampaignRecipient, Job
from tests.conftest import Session, assign, create_provider, provision_worker, ready_campaign, tick


async def _started(client: AsyncClient, admin: Session, user: Session, n: int = 3, batch_size: int = 1000,
                   **provider_kw: object) -> tuple[dict, dict]:
    provider = await create_provider(client, admin, **provider_kw)
    await assign(client, admin, user.user_id, provider["id"])
    campaign = await ready_campaign(client, user, provider["id"], [f"p{i}@example.org" for i in range(n)],
                                    batch_size=batch_size)
    r = await client.post(f"/api/v1/user/campaigns/{campaign['id']}/start", headers=user.headers,
                          json={"consent_confirmed": True})
    assert r.status_code == 200, r.text
    await tick()
    return provider, campaign


async def _make_available(db: AsyncSession) -> None:
    await db.execute(update(Job).where(Job.status.in_(["retry", "pending"]))
                     .values(available_at=datetime.now(UTC) - timedelta(seconds=1)))
    await db.commit()


async def test_transient_recipient_failures_are_retried_then_exhausted(
    client: AsyncClient, admin: Session, user: Session, db: AsyncSession
) -> None:
    _, campaign = await _started(client, admin, user, n=3)
    worker = await provision_worker(client, admin)

    job = (await client.post("/api/v1/worker/jobs/claim", headers=worker)).json()
    rs = job["recipients"]
    results = [
        {"recipient_id": rs[0]["id"], "outcome": "sent", "provider_message_id": "m0"},
        {"recipient_id": rs[1]["id"], "outcome": "failed", "smtp_code": 451, "enhanced_code": "4.7.1",
         "category": "transient", "error_message": "try later"},
        {"recipient_id": rs[2]["id"], "outcome": "failed", "smtp_code": 550, "enhanced_code": "5.1.1",
         "category": "rejected", "error_message": "no such user"},
    ]
    r = await client.post(f"/api/v1/worker/jobs/{job['job_id']}/results", headers=worker,
                          json={"attempt_id": job["attempt_id"], "results": results})
    assert r.json() == {"accepted": 3, "ignored": 0, "sent": 1, "deferred": 1, "bounced": 1, "failed": 0}
    r = await client.post(f"/api/v1/worker/jobs/{job['job_id']}/ack", headers=worker,
                          json={"attempt_id": job["attempt_id"]})
    assert r.json()["requeued"] == 1

    # Hard bounce was suppressed globally.
    r = await client.get("/api/v1/admin/suppressions", headers=admin.headers, params={"type": "hard_bounce"})
    assert [s["email_normalized"] for s in r.json()["items"]] == [rs[2]["email"]]

    # Retry job is scheduled in the future (30s default), so nothing is claimable right now.
    assert (await client.post("/api/v1/worker/jobs/claim", headers=worker)).status_code == 204

    for attempt in range(2, 6):
        await _make_available(db)
        retry = (await client.post("/api/v1/worker/jobs/claim", headers=worker)).json()
        assert [x["id"] for x in retry["recipients"]] == [rs[1]["id"]], attempt
        await client.post(f"/api/v1/worker/jobs/{retry['job_id']}/results", headers=worker, json={
            "attempt_id": retry["attempt_id"],
            "results": [{"recipient_id": rs[1]["id"], "outcome": "failed", "smtp_code": 421,
                         "category": "transient"}]})
        await client.post(f"/api/v1/worker/jobs/{retry['job_id']}/ack", headers=worker,
                          json={"attempt_id": retry["attempt_id"]})

    await _make_available(db)
    assert (await client.post("/api/v1/worker/jobs/claim", headers=worker)).status_code == 204
    stats = (await client.get(f"/api/v1/user/campaigns/{campaign['id']}/stats", headers=user.headers)).json()
    assert stats["sent"] == 1 and stats["bounced"] == 1 and stats["failed"] == 1
    assert stats["status"] == "FAILED"  # 2 of 3 failed > 50% threshold
    rec = (await db.execute(select(CampaignRecipient).where(CampaignRecipient.id == rs[1]["id"]))).scalar_one()
    assert rec.attempts == 5 and rec.status.value == "failed"


async def test_job_level_failure_retries_then_dead_letters_and_requeue(
    client: AsyncClient, admin: Session, user: Session, db: AsyncSession
) -> None:
    _, campaign = await _started(client, admin, user, n=2)
    worker = await provision_worker(client, admin)
    for i in range(5):
        await _make_available(db)
        job = (await client.post("/api/v1/worker/jobs/claim", headers=worker)).json()
        r = await client.post(f"/api/v1/worker/jobs/{job['job_id']}/failure", headers=worker, json={
            "attempt_id": job["attempt_id"], "error_code": "535", "error_message": "auth failed",
            "transient": True, "category": "auth"})
        assert r.json()["status"] == ("retry" if i < 4 else "dead_letter")

    r = await client.get("/api/v1/admin/queues", headers=admin.headers)
    assert r.json()["by_status"]["dead_letter"]["jobs"] == 1
    stats = (await client.get(f"/api/v1/user/campaigns/{campaign['id']}/stats", headers=user.headers)).json()
    assert stats["failed"] == 2 and stats["status"] == "FAILED"

    job_id = job["job_id"]
    detail = (await client.get(f"/api/v1/admin/queues/jobs/{job_id}", headers=admin.headers)).json()
    assert len(detail["attempts"]) == 5 and detail["attempts"][0]["outcome"] == "failed"

    r = await client.post(f"/api/v1/admin/queues/jobs/{job_id}/requeue", headers=admin.headers)
    assert r.status_code == 200 and r.json()["status"] == "pending"
    job = (await client.post("/api/v1/worker/jobs/claim", headers=worker)).json()
    assert len(job["recipients"]) == 2
    stats = (await client.get(f"/api/v1/user/campaigns/{campaign['id']}/stats", headers=user.headers)).json()
    assert stats["status"] == "PROCESSING" and stats["failed"] == 0


async def test_stale_attempt_and_lease_recovery(client: AsyncClient, admin: Session, user: Session,
                                                db: AsyncSession) -> None:
    await _started(client, admin, user, n=2)
    w1 = await provision_worker(client, admin, "worker-a")
    w2 = await provision_worker(client, admin, "worker-b")
    job = (await client.post("/api/v1/worker/jobs/claim", headers=w1)).json()
    # Only one worker can own a job.
    assert (await client.post("/api/v1/worker/jobs/claim", headers=w2)).status_code == 204

    # Simulate a crash: lease expires, scheduler recovers.
    await db.execute(update(Job).values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=5)))
    await db.commit()
    assert (await tick())["recovered"] == 1
    await _make_available(db)
    job2 = (await client.post("/api/v1/worker/jobs/claim", headers=w2)).json()
    assert job2["job_id"] == job["job_id"] and job2["attempt_id"] != job["attempt_id"]

    # The crashed worker comes back and tries to report: rejected, nothing double counted.
    r = await client.post(f"/api/v1/worker/jobs/{job['job_id']}/results", headers=w1, json={
        "attempt_id": job["attempt_id"],
        "results": [{"recipient_id": job["recipients"][0]["id"], "outcome": "sent"}]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "stale_attempt"


async def test_offline_worker_leases_are_recovered(client: AsyncClient, admin: Session, user: Session,
                                                   db: AsyncSession) -> None:
    from app.models import Worker

    await _started(client, admin, user, n=1)
    w = await provision_worker(client, admin)
    await client.post("/api/v1/worker/heartbeat", headers=w, json={"status": "online", "cpu": 10, "memory": 20})
    await client.post("/api/v1/worker/jobs/claim", headers=w)
    await db.execute(update(Worker).values(last_heartbeat_at=datetime.now(UTC) - timedelta(minutes=5)))
    await db.commit()
    stats = await tick()
    assert stats["offline"] == 1
    stats = await tick()
    assert stats["recovered"] == 1
    workers = (await client.get("/api/v1/admin/workers", headers=admin.headers)).json()["items"]
    assert workers[0]["status"] == "offline"


async def test_hourly_quota_limits_claims(client: AsyncClient, admin: Session, user: Session,
                                          db: AsyncSession) -> None:
    r = await client.put(f"/api/v1/admin/users/{user.user_id}/limits", headers=admin.headers,
                         json={"hourly_limit": 5})
    assert r.status_code == 200
    # batch size gets clamped to the hourly limit (5), so 12 recipients → 5, 5, 2
    _, campaign = await _started(client, admin, user, n=12, batch_size=1000)
    jobs = (await db.execute(select(Job).where(Job.campaign_id == uuid.UUID(campaign["id"])))).scalars().all()
    assert sorted(j.batch_size for j in jobs) == [2, 5, 5]
    worker = await provision_worker(client, admin)
    first = (await client.post("/api/v1/worker/jobs/claim", headers=worker)).json()
    assert len(first["recipients"]) == 5
    # quota exhausted for this hour
    assert (await client.post("/api/v1/worker/jobs/claim", headers=worker)).status_code == 204
    # releasing the job refunds the reservation
    await client.post(f"/api/v1/worker/jobs/{first['job_id']}/release", headers=worker,
                      json={"attempt_id": first["attempt_id"], "reason": "shutdown"})
    again = await client.post("/api/v1/worker/jobs/claim", headers=worker)
    assert again.status_code == 200


async def test_provider_daily_limit(client: AsyncClient, admin: Session, user: Session) -> None:
    await _started(client, admin, user, n=4, batch_size=2, daily_limit=2)
    worker = await provision_worker(client, admin)
    job = (await client.post("/api/v1/worker/jobs/claim", headers=worker)).json()
    await client.post(f"/api/v1/worker/jobs/{job['job_id']}/results", headers=worker, json={
        "attempt_id": job["attempt_id"],
        "results": [{"recipient_id": x["id"], "outcome": "sent"} for x in job["recipients"]]})
    await client.post(f"/api/v1/worker/jobs/{job['job_id']}/ack", headers=worker,
                      json={"attempt_id": job["attempt_id"]})
    assert (await client.post("/api/v1/worker/jobs/claim", headers=worker)).status_code == 204


async def test_pause_resume_cancel(client: AsyncClient, admin: Session, user: Session, db: AsyncSession) -> None:
    _, campaign = await _started(client, admin, user, n=4, batch_size=2)
    cid = campaign["id"]
    worker = await provision_worker(client, admin)
    job = (await client.post("/api/v1/worker/jobs/claim", headers=worker)).json()

    r = await client.post(f"/api/v1/user/campaigns/{cid}/pause", headers=user.headers)
    assert r.json()["status"] == "PAUSED"
    lease = (await client.post(f"/api/v1/worker/jobs/{job['job_id']}/lease", headers=worker,
                               json={"attempt_id": job["attempt_id"]})).json()
    assert lease["action"] == "stop"
    assert (await client.post("/api/v1/worker/jobs/claim", headers=worker)).status_code == 204
    await client.post(f"/api/v1/worker/jobs/{job['job_id']}/release", headers=worker,
                      json={"attempt_id": job["attempt_id"], "reason": "paused"})

    r = await client.post(f"/api/v1/user/campaigns/{cid}/resume", headers=user.headers)
    assert r.json()["status"] == "PROCESSING"
    job = (await client.post("/api/v1/worker/jobs/claim", headers=worker)).json()
    await client.post(f"/api/v1/worker/jobs/{job['job_id']}/results", headers=worker, json={
        "attempt_id": job["attempt_id"],
        "results": [{"recipient_id": job["recipients"][0]["id"], "outcome": "sent"}]})

    r = await client.post(f"/api/v1/user/campaigns/{cid}/cancel", headers=user.headers)
    assert r.json()["status"] == "CANCELLED"
    # In-flight job finishes: its unreported recipient is cancelled, not retried.
    await client.post(f"/api/v1/worker/jobs/{job['job_id']}/ack", headers=worker,
                      json={"attempt_id": job["attempt_id"]})
    rows = (await db.execute(select(CampaignRecipient.status).where(
        CampaignRecipient.campaign_id == uuid.UUID(cid)))).scalars().all()
    assert sorted(s.value for s in rows) == ["cancelled", "cancelled", "cancelled", "sent"]
    assert (await client.post("/api/v1/worker/jobs/claim", headers=worker)).status_code == 204
    r = await client.post(f"/api/v1/user/campaigns/{cid}/start", headers=user.headers,
                          json={"consent_confirmed": True})
    assert r.status_code == 409


async def test_disabled_worker_cannot_work(client: AsyncClient, admin: Session) -> None:
    worker = await provision_worker(client, admin)
    workers = (await client.get("/api/v1/admin/workers", headers=admin.headers)).json()["items"]
    await client.post(f"/api/v1/admin/workers/{workers[0]['id']}/disable", headers=admin.headers)
    assert (await client.post("/api/v1/worker/jobs/claim", headers=worker)).status_code == 401
    r = await client.post("/api/v1/worker/token", json={"worker_id": "worker-001", "credential": "wrong"})
    assert r.status_code == 401


async def test_concurrent_acks_allocate_unique_retry_batches(client: AsyncClient, admin: Session,
                                                             user: Session) -> None:
    """Regression (found by the 500k load test): workers finishing jobs of one campaign at the same time
    must not both pick the same next batch number for their retry batches."""
    import asyncio

    _, campaign = await _started(client, admin, user, n=16, batch_size=2)
    workers = [await provision_worker(client, admin, worker_id=f"race-{i}") for i in range(2)]
    jobs = []
    for w in workers:
        for _ in range(4):
            job = (await client.post("/api/v1/worker/jobs/claim", headers=w)).json()
            jobs.append((w, job))
            rs = job["recipients"]
            await client.post(f"/api/v1/worker/jobs/{job['job_id']}/results", headers=w, json={
                "attempt_id": job["attempt_id"],
                "results": [{"recipient_id": rs[0]["id"], "outcome": "sent", "provider_message_id": uuid.uuid4().hex},
                            {"recipient_id": rs[1]["id"], "outcome": "failed", "smtp_code": 451,
                             "category": "transient"}]})
    responses = await asyncio.gather(*(
        client.post(f"/api/v1/worker/jobs/{job['job_id']}/ack", headers=w, json={"attempt_id": job["attempt_id"]})
        for w, job in jobs))
    assert [r.status_code for r in responses] == [200] * len(jobs), [r.text for r in responses if r.status_code != 200]
    assert all(r.json()["requeued"] == 1 for r in responses)
    from app.db.session import sessionmaker
    from app.models import CampaignBatch

    async with sessionmaker()() as db:
        seqs = (await db.execute(select(CampaignBatch.sequence_no).where(
            CampaignBatch.campaign_id == campaign["id"], CampaignBatch.retry_round == 1))).scalars().all()
    assert len(seqs) == len(jobs) and len(set(seqs)) == len(seqs)
