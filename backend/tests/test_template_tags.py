"""Template tags on the control plane (design DS-24): #MASSAGE# texts, secret per-campaign seed, timezone."""

from __future__ import annotations

from httpx import AsyncClient

from tests.conftest import Session, assign, create_provider, provision_worker, ready_campaign, tick


async def test_message_list_seed_and_payload(client: AsyncClient, admin: Session, user: Session) -> None:
    provider = await create_provider(client, admin)
    await assign(client, admin, user.user_id, provider["id"])
    campaign = await ready_campaign(client, user, provider["id"], ["mahdi@gmail.com"])
    base = f"/api/v1/user/campaigns/{campaign['id']}"

    r = await client.patch(base, headers=user.headers, json={
        "subject": "Invoice #INVOICE# for #USERID#", "message_list": [" Hello ", "", "Hi", "Welcome"]})
    assert r.status_code == 200, r.text
    detail = r.json()
    assert detail["message_list"] == ["Hello", "Hi", "Welcome"], "trimmed, empties dropped"
    assert "tag_seed" not in detail, "the seed never leaves the control plane"
    r = await client.patch(base, headers=user.headers, json={"message_list": ["x" * 501]})
    assert r.status_code == 422
    r = await client.patch(base, headers=user.headers, json={"message_list": ["m"] * 101})
    assert r.status_code == 422
    for path in (base, f"/api/v1/admin/campaigns/{campaign['id']}"):
        body = (await client.get(path, headers=user.headers if "user" in path else admin.headers)).text
        assert "tag_seed" not in body

    r = await client.put("/api/v1/admin/settings", headers=admin.headers,
                         json={"values": {"template_timezone": "Mars/Olympus"}})
    assert r.status_code == 400
    r = await client.put("/api/v1/admin/settings", headers=admin.headers,
                         json={"values": {"template_timezone": "Asia/Dhaka"}})
    assert r.status_code == 200

    await client.post(f"{base}/start", headers=user.headers, json={"consent_confirmed": True})
    await tick()
    worker = await provision_worker(client, admin)
    job = (await client.post("/api/v1/worker/jobs/claim", headers=worker)).json()
    c = job["campaign"]
    assert c["subject"] == "Invoice #INVOICE# for #USERID#", "tags are rendered by the worker, per recipient"
    assert c["message_list"] == ["Hello", "Hi", "Welcome"]
    assert len(c["tag_seed"]) == 64 and c["tag_timezone"] == "Asia/Dhaka"

    # Each campaign gets its own seed.
    other = await ready_campaign(client, user, provider["id"], ["x@example.org"])
    await client.post(f"/api/v1/user/campaigns/{other['id']}/start", headers=user.headers,
                      json={"consent_confirmed": True})
    await tick()
    job2 = (await client.post("/api/v1/worker/jobs/claim", headers=worker)).json()
    assert job2["campaign"]["tag_seed"] != c["tag_seed"]
