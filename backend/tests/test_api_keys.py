"""API keys for the User API (design DS-17, ADR-012)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from app.services import api_keys
from tests.conftest import Session, create_provider, create_user, login_user


def _bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


async def test_user_key_lifecycle(client: AsyncClient, admin: Session, user: Session) -> None:
    r = await client.post("/api/v1/user/api-keys", headers=user.headers,
                          json={"name": "CRM sync", "scopes": ["campaigns.read", "reports.read"]})
    assert r.status_code == 201, r.text
    created = r.json()
    key = created["key"]
    assert key.startswith(f"osk_{created['prefix']}_") and created["active"] is True

    # Scoped access to the User API.
    assert (await client.get("/api/v1/user/campaigns", headers=_bearer(key))).status_code == 200
    assert (await client.get("/api/v1/user/dashboard", headers=_bearer(key))).status_code == 200
    r = await client.post("/api/v1/user/campaigns", headers=_bearer(key), json={"name": "x"})
    assert r.status_code == 403, "campaigns.write is not in the key's scopes"
    assert (await client.get("/api/v1/user/providers", headers=_bearer(key))).status_code == 403

    # Never on admin, auth, account management or worker APIs.
    assert (await client.get("/api/v1/admin/users", headers=_bearer(key))).status_code == 403
    assert (await client.get("/api/v1/auth/me", headers=_bearer(key))).status_code == 403
    assert (await client.get("/api/v1/user/api-keys", headers=_bearer(key))).status_code == 403
    r = await client.post("/api/v1/user/profile/password", headers=_bearer(key),
                          json={"current_password": "x", "new_password": "y"})
    assert r.status_code == 403
    assert (await client.post("/api/v1/user/profile/2fa/setup", headers=_bearer(key))).status_code == 403
    assert (await client.post("/api/v1/worker/jobs/claim", headers=_bearer(key))).status_code == 401

    listed = (await client.get("/api/v1/user/api-keys", headers=user.headers)).json()
    assert [k["id"] for k in listed] == [created["id"]]
    assert "key" not in listed[0] and listed[0]["last_used_at"] is not None

    r = await client.delete(f"/api/v1/user/api-keys/{created['id']}", headers=user.headers)
    assert r.status_code == 200 and r.json()["active"] is False
    r = await client.get("/api/v1/user/campaigns", headers=_bearer(key))
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_api_key"

    logs = (await client.get("/api/v1/admin/audit-logs", headers=admin.headers, params={"resource": "api_key"})).json()
    assert {e["action"] for e in logs["items"]} == {"API_KEY_CREATED", "API_KEY_REVOKED"}
    assert all(key not in str(e) for e in logs["items"]), "the key itself is never logged"


async def test_key_validation(client: AsyncClient, user: Session) -> None:
    r = await client.post("/api/v1/user/api-keys", headers=user.headers,
                          json={"name": "x", "scopes": ["users.write"]})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_scope"
    r = await client.post("/api/v1/user/api-keys", headers=user.headers, json={"name": "x", "scopes": []})
    assert r.status_code == 422
    r = await client.get("/api/v1/user/campaigns", headers=_bearer("osk_deadbeef_not-a-real-key"))
    assert r.status_code == 401


async def test_admin_manages_keys(client: AsyncClient, admin: Session, user: Session) -> None:
    scopes = (await client.get("/api/v1/admin/api-keys/scopes", headers=admin.headers)).json()["scopes"]
    assert "campaigns.start" in scopes and "users.read" not in scopes

    viewer = await create_user(client, admin, role="VIEWER")
    r = await client.post("/api/v1/admin/api-keys", headers=admin.headers,
                          json={"user_id": viewer["id"], "name": "x", "scopes": ["campaigns.read"]})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_owner"

    r = await client.post("/api/v1/admin/api-keys", headers=admin.headers, json={
        "user_id": user.user_id, "name": "Pipeline", "scopes": ["campaigns.read", "providers.read"],
        "expires_in_days": 30})
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["expires_at"] is not None and created["created_by"] != user.user_id
    key = created["key"]
    assert (await client.get("/api/v1/user/providers", headers=_bearer(key))).status_code == 200

    listed = (await client.get("/api/v1/admin/api-keys", headers=admin.headers,
                               params={"user_id": user.user_id})).json()
    assert [k["username"] for k in listed] and listed[0]["prefix"] == created["prefix"]

    # Suspending the owner disables the key at once; reactivating restores it.
    await client.post(f"/api/v1/admin/users/{user.user_id}/suspend", headers=admin.headers)
    assert (await client.get("/api/v1/user/campaigns", headers=_bearer(key))).status_code == 401
    await client.post(f"/api/v1/admin/users/{user.user_id}/activate", headers=admin.headers)
    assert (await client.get("/api/v1/user/campaigns", headers=_bearer(key))).status_code == 200

    r = await client.delete(f"/api/v1/admin/api-keys/{created['id']}", headers=admin.headers)
    assert r.status_code == 200 and r.json()["revoked_at"] is not None
    assert (await client.get("/api/v1/user/campaigns", headers=_bearer(key))).status_code == 401
    listed = (await client.get("/api/v1/admin/api-keys", headers=admin.headers,
                               params={"include_revoked": "true"})).json()
    assert created["id"] in {k["id"] for k in listed}


async def test_key_isolation_and_rate_limit(client: AsyncClient, admin: Session, user: Session,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    other = await create_user(client, admin)
    other_session = await login_user(client, other["username"])
    provider = await create_provider(client, admin)
    await client.post("/api/v1/admin/assignments", headers=admin.headers,
                      json={"user_id": other["id"], "provider_id": provider["id"]})
    r = await client.post("/api/v1/user/campaigns", headers=other_session.headers, json={
        "name": "Theirs", "subject": "Hi", "provider_id": provider["id"], "html_body": "<p>x</p>"})
    assert r.status_code == 201, r.text
    theirs = r.json()["id"]

    created = (await client.post("/api/v1/user/api-keys", headers=user.headers,
                                 json={"name": "mine", "scopes": ["campaigns.read"]})).json()
    key = created["key"]
    # A key only ever sees its owner's data.
    assert (await client.get(f"/api/v1/user/campaigns/{theirs}", headers=_bearer(key))).status_code == 404
    # ...and can only be revoked by its owner.
    r = await client.delete(f"/api/v1/user/api-keys/{created['id']}", headers=other_session.headers)
    assert r.status_code == 404

    r = await client.put("/api/v1/admin/settings", headers=admin.headers,
                         json={"values": {"api_key_requests_per_minute": 3}})
    assert r.status_code == 200, r.text

    class FrozenClock(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001, ANN206 - datetime.now signature
            return datetime(2030, 1, 1, 12, 0, 30, tzinfo=tz or UTC)

    monkeypatch.setattr(api_keys, "datetime", FrozenClock)  # one fixed-window bucket for the whole burst
    codes = [(await client.get("/api/v1/user/campaigns", headers=_bearer(key))).status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200] and codes[3:] == [429, 429]
