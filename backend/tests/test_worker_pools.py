"""Worker pools for autoscaling + the desired-workers signal (design DS-23, P4-03)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import update

from app.db.session import sessionmaker
from app.models import Worker
from app.models.enums import WorkerStatus
from app.services import workers as worker_service
from tests.conftest import Session, assign, create_provider, ready_campaign, tick


async def _token(client: AsyncClient, worker_id: str, credential: str, instance: str | None) -> tuple[int, dict]:
    r = await client.post("/api/v1/worker/token", json={"worker_id": worker_id, "credential": credential,
                                                         "instance": instance})
    return r.status_code, r.json()


async def test_pool_instances(client: AsyncClient, admin: Session, user: Session) -> None:
    r = await client.post("/api/v1/admin/workers", headers=admin.headers,
                          json={"worker_id": "fleet", "name": "Fleet", "capacity": 50, "pool": True})
    assert r.status_code == 201 and r.json()["worker"]["is_pool"] is True
    cred = r.json()["credential"]
    pool_pk = r.json()["worker"]["id"]

    status, body = await _token(client, "fleet", cred, None)
    assert status == 422 and body["error"]["code"] == "instance_required"
    status, a = await _token(client, "fleet", cred, "Container_A1")
    assert status == 200 and a["worker_id"] == "fleet--container-a1"
    status, a_again = await _token(client, "fleet", cred, "Container_A1")
    assert a_again["worker_id"] == a["worker_id"], "same instance → same record"
    _, b = await _token(client, "fleet", cred, "container-b2")
    assert (await _token(client, "fleet", "wrong", "x"))[0] == 401

    items = (await client.get("/api/v1/admin/workers", headers=admin.headers)).json()["items"]
    children = [w for w in items if w["pool_id"] == pool_pk]
    assert {w["worker_id"] for w in children} == {"fleet--container-a1", "fleet--container-b2"}
    assert all(w["capacity"] == 50 for w in children), children
    assert not any(w["disabled"] for w in children), children

    # Instances work like normal workers: they claim jobs.
    provider = await create_provider(client, admin)
    await assign(client, admin, user.user_id, provider["id"])
    campaign = await ready_campaign(client, user, provider["id"], ["a@example.org"])
    await client.post(f"/api/v1/user/campaigns/{campaign['id']}/start", headers=user.headers,
                      json={"consent_confirmed": True})
    await tick()
    r = await client.post("/api/v1/worker/jobs/claim", headers={"Authorization": f"Bearer {a['access_token']}"})
    assert r.status_code == 200 and r.json()["job_id"], r.text

    # Disabling the pool disables every instance, including already-issued tokens.
    await client.post(f"/api/v1/admin/workers/{pool_pk}/disable", headers=admin.headers)
    r = await client.post("/api/v1/worker/jobs/claim", headers={"Authorization": f"Bearer {b['access_token']}"})
    assert r.status_code == 401
    assert (await _token(client, "fleet", cred, "container-c3"))[0] == 403
    await client.post(f"/api/v1/admin/workers/{pool_pk}/enable", headers=admin.headers)
    assert (await _token(client, "fleet", cred, "container-b2"))[0] == 200


async def test_desired_workers_and_pruning(client: AsyncClient, admin: Session, user: Session) -> None:
    provider = await create_provider(client, admin)
    await assign(client, admin, user.user_id, provider["id"])
    campaign = await ready_campaign(client, user, provider["id"], [f"r{i}@example.org" for i in range(30)])
    await client.patch(f"/api/v1/user/campaigns/{campaign['id']}", headers=user.headers, json={"batch_size": 1})
    await client.put("/api/v1/admin/settings", headers=admin.headers, json={"values": {
        "autoscale_jobs_per_worker": 4, "autoscale_min_workers": 1, "autoscale_max_workers": 5}})
    await client.post(f"/api/v1/user/campaigns/{campaign['id']}/start", headers=user.headers,
                      json={"consent_confirmed": True})
    await tick()
    q = (await client.get("/api/v1/admin/queues", headers=admin.headers)).json()["autoscale"]
    assert q["open_jobs"] == 30 and q["desired"] == 5, "ceil(30/4)=8, capped at max 5"
    assert "omnisend_workers_desired 5" in (await client.get("/metrics")).text

    await client.post("/api/v1/admin/workers", headers=admin.headers,
                      json={"worker_id": "fleet2", "name": "Fleet", "pool": True})
    async with sessionmaker()() as db:
        pool = (await db.execute(Worker.__table__.select().where(Worker.worker_id == "fleet2"))).one()
        db.add(Worker(worker_id="fleet2--gone", name="gone", credential_hash="x", pool_id=pool.id,
                      status=WorkerStatus.OFFLINE, last_heartbeat_at=datetime.now(UTC) - timedelta(days=2)))
        db.add(Worker(worker_id="fleet2--recent", name="recent", credential_hash="y", pool_id=pool.id,
                      status=WorkerStatus.OFFLINE, last_heartbeat_at=datetime.now(UTC) - timedelta(hours=1)))
        db.add(Worker(worker_id="fleet2--new", name="new", credential_hash="z", pool_id=pool.id,
                      status=WorkerStatus.OFFLINE))  # never sent a heartbeat yet
        await db.commit()
        assert await worker_service.prune_pool_instances(db) == 1
        await db.execute(update(Worker).values(status=WorkerStatus.OFFLINE))
        await db.commit()
    ids = {w["worker_id"] for w in (await client.get("/api/v1/admin/workers", headers=admin.headers)).json()["items"]}
    assert {"fleet2", "fleet2--recent", "fleet2--new"} <= ids and "fleet2--gone" not in ids
