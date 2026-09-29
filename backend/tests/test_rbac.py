from __future__ import annotations

from httpx import AsyncClient

from tests.conftest import ADMIN_PASSWORD, Session, create_provider, create_user, login_admin


async def _admin_session(client: AsyncClient, admin: Session, role: str, username: str) -> Session:
    await create_user(client, admin, role=role, username=username, password=ADMIN_PASSWORD)
    return await login_admin(client, username)


async def test_viewer_is_read_only(client: AsyncClient, admin: Session) -> None:
    viewer = await _admin_session(client, admin, "VIEWER", "viewer1")
    assert (await client.get("/api/v1/admin/users", headers=viewer.headers)).status_code == 200
    assert (await client.get("/api/v1/admin/dashboard", headers=viewer.headers)).status_code == 200
    r = await client.post("/api/v1/admin/providers", headers=viewer.headers, json={
        "provider_name": "x", "host": "h", "port": 25, "from_email": "a@b.co"})
    assert r.status_code == 403
    assert (await client.get("/api/v1/admin/settings", headers=viewer.headers)).status_code == 403


async def test_operator_cannot_manage_providers_or_users(client: AsyncClient, admin: Session) -> None:
    op = await _admin_session(client, admin, "OPERATOR", "operator1")
    provider = await create_provider(client, admin)
    assert (await client.post(f"/api/v1/admin/providers/{provider['id']}/disable",
                              headers=op.headers)).status_code == 403
    assert (await client.post("/api/v1/admin/users", headers=op.headers, json={
        "email": "z@example.com", "username": "zz1", "password": "Str0ng-Password!", "role": "USER"})).status_code == 403
    assert (await client.get("/api/v1/admin/queues", headers=op.headers)).status_code == 200


async def test_admin_cannot_create_super_admin_but_super_admin_can(client: AsyncClient, admin: Session) -> None:
    plain_admin = await _admin_session(client, admin, "ADMIN", "admin2")
    r = await client.post("/api/v1/admin/users", headers=plain_admin.headers, json={
        "email": "boss@example.com", "username": "boss", "password": "Str0ng-Password!", "role": "SUPER_ADMIN"})
    assert r.status_code == 403
    r = await client.post("/api/v1/admin/users", headers=admin.headers, json={
        "email": "boss@example.com", "username": "boss", "password": "Str0ng-Password!", "role": "ADMIN"})
    assert r.status_code == 201


async def test_user_token_cannot_reach_admin_api(client: AsyncClient, user: Session) -> None:
    assert (await client.get("/api/v1/admin/users", headers=user.headers)).status_code == 403
    assert (await client.get("/api/v1/user/dashboard", headers=user.headers)).status_code == 200


async def test_admin_token_cannot_reach_user_api(client: AsyncClient, admin: Session) -> None:
    assert (await client.get("/api/v1/user/campaigns", headers=admin.headers)).status_code == 403


async def test_users_are_isolated(client: AsyncClient, admin: Session, user: Session) -> None:
    from tests.conftest import assign, login_user, ready_campaign

    provider = await create_provider(client, admin)
    await assign(client, admin, user.user_id, provider["id"])
    campaign = await ready_campaign(client, user, provider["id"], ["a@example.org"])

    await create_user(client, admin, username="mallory")
    mallory = await login_user(client, "mallory")
    assert (await client.get(f"/api/v1/user/campaigns/{campaign['id']}", headers=mallory.headers)).status_code == 404
    assert (await client.post(f"/api/v1/user/campaigns/{campaign['id']}/cancel",
                              headers=mallory.headers)).status_code == 404
    # Mallory cannot pick a provider that is not assigned to her.
    r = await client.post("/api/v1/user/campaigns", headers=mallory.headers,
                          json={"name": "x", "provider_id": provider["id"]})
    assert r.status_code == 422
    assert (await client.get("/api/v1/user/providers", headers=mallory.headers)).json() == []


async def test_audit_log_records_sensitive_actions(client: AsyncClient, admin: Session) -> None:
    u = await create_user(client, admin, username="target")
    await client.post(f"/api/v1/admin/users/{u['id']}/suspend", headers=admin.headers)
    await client.put(f"/api/v1/admin/users/{u['id']}/limits", headers=admin.headers, json={"daily_limit": 10})
    r = await client.get("/api/v1/admin/audit-logs", headers=admin.headers)
    actions = [i["action"] for i in r.json()["items"]]
    assert {"USER_CREATED", "USER_SUSPENDED", "LIMIT_CHANGED"} <= set(actions)
    created = next(i for i in r.json()["items"] if i["action"] == "USER_CREATED")
    assert created["actor"] == "root"


async def test_pagination_cursor(client: AsyncClient, admin: Session) -> None:
    for i in range(5):
        await create_user(client, admin, username=f"page{i}")
    seen: list[str] = []
    cursor = None
    while True:
        params = {"limit": 2, **({"cursor": cursor} if cursor else {})}
        r = await client.get("/api/v1/admin/users", headers=admin.headers, params=params)
        body = r.json()
        seen += [u["id"] for u in body["items"]]
        cursor = body["next_cursor"]
        if not cursor:
            break
    assert len(seen) == len(set(seen)) == 6  # 5 + root
    r = await client.get("/api/v1/admin/users", headers=admin.headers, params={"cursor": "garbage!!"})
    assert r.status_code == 400
