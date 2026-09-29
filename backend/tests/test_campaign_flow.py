"""Full control-plane → data-plane → event-plane flow through the public APIs (§1 core pipeline)."""

from __future__ import annotations

import uuid

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CampaignRecipient, EmailEvent, Job
from tests.conftest import Session, assign, create_provider, csv_file, provision_worker, ready_campaign, tick


async def test_upload_validation_and_readiness(client: AsyncClient, admin: Session, user: Session) -> None:
    provider = await create_provider(client, admin)
    await assign(client, admin, user.user_id, provider["id"])
    r = await client.post("/api/v1/user/campaigns", headers=user.headers, json={"name": "Draft only"})
    campaign = r.json()
    assert campaign["status"] == "DRAFT"
    assert set(campaign["missing"]) >= {"subject", "from_email", "content", "provider", "recipients"}

    content = "Email,First Name\nA@Example.org,Ann\nnot-an-email,Bob\na@example.org,Dup\nc@example.org,Cy\n"
    r = await client.post(f"/api/v1/user/campaigns/{campaign['id']}/recipients", headers=user.headers,
                          files={"file": ("list.csv", content.encode(), "text/csv")})
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["imported"] == 2 and result["invalid"] == 1 and result["duplicates"] == 1
    assert result["invalid_samples"] == ["not-an-email"]

    r = await client.get(f"/api/v1/user/campaigns/{campaign['id']}/recipients", headers=user.headers)
    rows = r.json()["items"]
    assert {row["variables"].get("first_name") for row in rows} == {"Ann", "Cy"}

    r = await client.post(f"/api/v1/user/campaigns/{campaign['id']}/recipients", headers=user.headers,
                          files={"file": ("x.exe", b"a@b.co", "application/octet-stream")})
    assert r.status_code == 415

    r = await client.post(f"/api/v1/user/campaigns/{campaign['id']}/recipients", headers=user.headers,
                          files={"file": ("x.csv", b"name,phone\nbob,123\n", "text/csv")})
    assert r.status_code == 400 and r.json()["error"]["code"] == "missing_email_column"


async def test_header_injection_rejected(client: AsyncClient, user: Session) -> None:
    r = await client.post("/api/v1/user/campaigns", headers=user.headers,
                          json={"name": "x", "subject": "Hi\r\nBcc: victim@example.com"})
    assert r.status_code == 422


async def test_start_guards(client: AsyncClient, admin: Session, user: Session) -> None:
    provider = await create_provider(client, admin, from_email="news@brand.example")
    await assign(client, admin, user.user_id, provider["id"])
    r = await client.post("/api/v1/user/campaigns", headers=user.headers, json={
        "name": "c", "subject": "s", "from_email": "news@other.example", "html_body": "<p>x</p>",
        "provider_id": provider["id"]})
    cid = r.json()["id"]
    await client.post(f"/api/v1/user/campaigns/{cid}/recipients", headers=user.headers,
                      files={"file": csv_file(["x@example.org"])})
    r = await client.post(f"/api/v1/user/campaigns/{cid}/start", headers=user.headers, json={})
    assert r.status_code == 422 and r.json()["error"]["code"] == "consent_required"
    r = await client.post(f"/api/v1/user/campaigns/{cid}/start", headers=user.headers,
                          json={"consent_confirmed": True})
    assert r.status_code == 422 and r.json()["error"]["code"] == "from_domain_mismatch"

    await client.patch(f"/api/v1/user/campaigns/{cid}", headers=user.headers,
                       json={"from_email": "hello@brand.example"})
    await client.post(f"/api/v1/admin/providers/{provider['id']}/disable", headers=admin.headers)
    r = await client.post(f"/api/v1/user/campaigns/{cid}/start", headers=user.headers,
                          json={"consent_confirmed": True})
    assert r.status_code == 409 and r.json()["error"]["code"] == "provider_unavailable"

    await client.post(f"/api/v1/admin/providers/{provider['id']}/enable", headers=admin.headers)
    r = await client.post(f"/api/v1/user/campaigns/{cid}/start", headers=user.headers,
                          json={"consent_confirmed": True})
    assert r.status_code == 200 and r.json()["status"] == "QUEUED"
    # Once started the campaign is locked.
    r = await client.patch(f"/api/v1/user/campaigns/{cid}", headers=user.headers, json={"subject": "new"})
    assert r.status_code == 409


async def test_end_to_end_send(client: AsyncClient, admin: Session, user: Session, db: AsyncSession) -> None:
    provider = await create_provider(client, admin)
    await assign(client, admin, user.user_id, provider["id"])
    emails = [f"r{i}@example.org" for i in range(7)] + ["blocked@example.org"]
    await client.post("/api/v1/admin/suppressions", headers=admin.headers,
                      json={"email": "BLOCKED@example.org", "type": "admin_blocked"})
    campaign = await ready_campaign(client, user, provider["id"], emails, batch_size=3)
    cid = campaign["id"]
    r = await client.post(f"/api/v1/user/campaigns/{cid}/start", headers=user.headers,
                          json={"consent_confirmed": True})
    assert r.status_code == 200

    stats = await tick()
    assert stats["built"] == 1
    jobs = (await db.execute(select(Job).where(Job.campaign_id == uuid.UUID(cid)))).scalars().all()
    assert sorted(j.batch_size for j in jobs) == [1, 3, 3]  # 7 sendable, suppressed one skipped

    worker = await provision_worker(client, admin)
    r = await client.post("/api/v1/worker/register", headers=worker, json={"version": "1.0.0"})
    assert r.status_code == 200 and r.json()["lease_seconds"] > 0

    sent_total = 0
    while True:
        r = await client.post("/api/v1/worker/jobs/claim", headers=worker)
        if r.status_code == 204:
            break
        assert r.status_code == 200, r.text
        job = r.json()
        assert job["provider"]["password"] == "smtp-secret"
        assert job["campaign"]["subject"] == "Hello {{first_name}}"
        assert all(rc["unsubscribe_url"].startswith("http://test/api/v1/u/") for rc in job["recipients"])
        assert "blocked@example.org" not in [rc["email"] for rc in job["recipients"]]
        r = await client.post(f"/api/v1/worker/jobs/{job['job_id']}/lease", headers=worker,
                              json={"attempt_id": job["attempt_id"]})
        assert r.json()["action"] == "continue"
        results = [{"recipient_id": rc["id"], "outcome": "sent", "provider_message_id": f"<{rc['id']}@mx>"}
                   for rc in job["recipients"]]
        r = await client.post(f"/api/v1/worker/jobs/{job['job_id']}/results", headers=worker,
                              json={"attempt_id": job["attempt_id"], "results": results})
        assert r.json()["accepted"] == len(results)
        # duplicate report is ignored (idempotency)
        r = await client.post(f"/api/v1/worker/jobs/{job['job_id']}/results", headers=worker,
                              json={"attempt_id": job["attempt_id"], "results": results})
        assert r.json()["accepted"] == 0 and r.json()["ignored"] == len(results)
        r = await client.post(f"/api/v1/worker/jobs/{job['job_id']}/ack", headers=worker,
                              json={"attempt_id": job["attempt_id"]})
        assert r.json()["status"] == "completed"
        sent_total += len(results)

    assert sent_total == 7
    r = await client.get(f"/api/v1/user/campaigns/{cid}/stats", headers=user.headers)
    s = r.json()
    assert s["status"] == "COMPLETED"
    assert s["sent"] == 7 and s["delivered"] == 7 and s["skipped_suppressed"] == 1
    assert s["remaining"] == 0 and s["percent"] == 100.0

    n_sent = (await db.execute(select(func.count()).select_from(EmailEvent)
                               .where(EmailEvent.event_type == "sent"))).scalar_one()
    assert n_sent == 7

    r = await client.get(f"/api/v1/user/campaigns/{cid}/report", headers=user.headers)
    assert r.json()["by_status"] == {"sent": 7, "suppressed": 1}
    r = await client.get(f"/api/v1/user/campaigns/{cid}/report.csv", headers=user.headers)
    assert r.status_code == 200 and r.text.count("\n") == 9

    r = await client.get("/api/v1/admin/dashboard", headers=admin.headers)
    d = r.json()
    assert d["today"]["sent"] == 7 and d["active_workers"] == 1

    r = await client.get("/api/v1/user/dashboard", headers=user.headers)
    assert r.json()["quota"]["used_today"] == 7


async def test_csv_export_neutralises_formulas(client: AsyncClient, admin: Session, user: Session) -> None:
    provider = await create_provider(client, admin)
    await assign(client, admin, user.user_id, provider["id"])
    campaign = await ready_campaign(client, user, provider["id"], ["ok@example.org"])
    r = await client.get(f"/api/v1/user/campaigns/{campaign['id']}/report.csv", headers=user.headers)
    assert r.headers["content-type"].startswith("text/csv")
    from app.api.v1.campaign_common import _csv_safe

    assert _csv_safe("=HYPERLINK(1)") == "'=HYPERLINK(1)"
    assert _csv_safe("plain") == "plain"


async def test_recipients_are_not_loaded_twice_across_batches(client: AsyncClient, admin: Session,
                                                             user: Session, db: AsyncSession) -> None:
    provider = await create_provider(client, admin)
    await assign(client, admin, user.user_id, provider["id"])
    campaign = await ready_campaign(client, user, provider["id"], [f"x{i}@example.org" for i in range(25)],
                                    batch_size=10)
    await client.post(f"/api/v1/user/campaigns/{campaign['id']}/start", headers=user.headers,
                      json={"consent_confirmed": True})
    await tick()
    await tick()  # idempotent: second pass must not rebuild
    per_batch = (await db.execute(
        select(CampaignRecipient.batch_id, func.count()).where(CampaignRecipient.campaign_id == uuid.UUID(campaign["id"]))
        .group_by(CampaignRecipient.batch_id))).all()
    assert sorted(n for _, n in per_batch) == [5, 10, 10]
