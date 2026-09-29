from __future__ import annotations

import pyotp
from httpx import AsyncClient

from app.core.redis import get_redis
from tests.conftest import ADMIN_PASSWORD, USER_PASSWORD, Session, create_user, login_user

CSRF = {"X-Requested-With": "XMLHttpRequest"}


async def test_user_login_me_refresh_logout(client: AsyncClient, admin: Session) -> None:
    u = await create_user(client, admin, username="bob")
    session = await login_user(client, "bob@example.com")  # login by email works too
    r = await client.get("/api/v1/auth/me", headers=session.headers)
    assert r.status_code == 200
    me = r.json()
    assert me["username"] == "bob" and me["role"] == "USER"
    assert "campaigns.write" in me["permissions"] and "users.write" not in me["permissions"]

    # refresh requires the CSRF header
    r = await client.post("/api/v1/auth/refresh", json={"app": "user"})
    assert r.status_code == 403
    old_cookie = client.cookies.get("osp_user_rt")
    r = await client.post("/api/v1/auth/refresh", json={"app": "user"}, headers=CSRF)
    assert r.status_code == 200, r.text
    assert client.cookies.get("osp_user_rt") != old_cookie

    # re-using the rotated token revokes the whole family
    new_cookie = client.cookies.get("osp_user_rt")
    client.cookies.set("osp_user_rt", old_cookie, path="/api/v1/auth")
    r = await client.post("/api/v1/auth/refresh", json={"app": "user"}, headers=CSRF)
    assert r.status_code == 401
    client.cookies.set("osp_user_rt", new_cookie, path="/api/v1/auth")
    r = await client.post("/api/v1/auth/refresh", json={"app": "user"}, headers=CSRF)
    assert r.status_code == 401, "family must be revoked after reuse"

    assert u["status"] == "active"


async def test_wrong_password_and_lockout(client: AsyncClient, admin: Session) -> None:
    await create_user(client, admin, username="carol")
    for _ in range(5):
        r = await client.post("/api/v1/auth/login", json={"login": "carol", "password": "nope", "app": "user"})
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "invalid_credentials"
    r = await client.post("/api/v1/auth/login", json={"login": "carol", "password": USER_PASSWORD, "app": "user"})
    assert r.status_code == 423


async def test_unknown_user_same_error(client: AsyncClient) -> None:
    r = await client.post("/api/v1/auth/login", json={"login": "ghost", "password": "x", "app": "user"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_credentials"


async def test_wildcards_do_not_match_other_users(client: AsyncClient, admin: Session) -> None:
    await create_user(client, admin, username="dave")
    r = await client.post("/api/v1/auth/login", json={"login": "d%", "password": USER_PASSWORD, "app": "user"})
    assert r.status_code == 401


async def test_user_cannot_use_admin_app_and_vice_versa(client: AsyncClient, admin: Session) -> None:
    await create_user(client, admin, username="erin")
    r = await client.post("/api/v1/auth/login", json={"login": "erin", "password": USER_PASSWORD, "app": "admin"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "wrong_app"
    r = await client.post("/api/v1/auth/login", json={"login": "root", "password": ADMIN_PASSWORD, "app": "user"})
    assert r.status_code == 403


async def test_admin_requires_totp_and_rejects_bad_or_replayed_codes(client: AsyncClient, admin: Session) -> None:
    # `admin` fixture already enrolled TOTP; a new login now requires verification.
    r = await client.post("/api/v1/auth/login", json={"login": "root", "password": ADMIN_PASSWORD, "app": "admin"})
    body = r.json()
    assert body["status"] == "mfa_required" and "access_token" not in body
    r = await client.post("/api/v1/auth/mfa/verify", json={"mfa_token": body["mfa_token"], "code": "000000"})
    assert r.status_code == 401

    from tests.conftest import _SECRETS

    code = pyotp.TOTP(_SECRETS["root"]).now()
    await get_redis().delete(*[k async for k in get_redis().scan_iter("totp:used:*")] or ["_"])
    r = await client.post("/api/v1/auth/mfa/verify", json={"mfa_token": body["mfa_token"], "code": code})
    assert r.status_code == 200
    r = await client.post("/api/v1/auth/mfa/verify", json={"mfa_token": body["mfa_token"], "code": code})
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_code"


async def test_invalid_and_worker_tokens_rejected_on_user_api(client: AsyncClient, admin: Session) -> None:
    r = await client.get("/api/v1/auth/me", headers={"Authorization": "Bearer nonsense"})
    assert r.status_code == 401
    from tests.conftest import provision_worker

    worker_headers = await provision_worker(client, admin)
    r = await client.get("/api/v1/auth/me", headers=worker_headers)
    assert r.status_code == 401
    r = await client.post("/api/v1/worker/jobs/claim", headers=admin.headers)
    assert r.status_code == 401


async def test_password_change_invalidates_existing_access_tokens(client: AsyncClient, admin: Session) -> None:
    await create_user(client, admin, username="frank")
    session = await login_user(client, "frank")
    r = await client.post("/api/v1/user/profile/password", headers=session.headers,
                          json={"current_password": USER_PASSWORD, "new_password": "N3w-Password-123"})
    assert r.status_code == 204
    r = await client.get("/api/v1/auth/me", headers=session.headers)
    assert r.status_code == 401
    await login_user(client, "frank", "N3w-Password-123")


async def test_weak_password_rejected(client: AsyncClient, admin: Session) -> None:
    r = await client.post("/api/v1/admin/users", headers=admin.headers, json={
        "email": "weak@example.com", "username": "weak", "password": "alllowercase", "role": "USER"})
    assert r.status_code in (400, 422)
