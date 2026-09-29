"""Test fixtures: real PostgreSQL + Redis (integration level, ARCHITECTURE.md §63).

Environment: TEST_DATABASE_URL (default postgresql+asyncpg://omnisend:omnisend@localhost:5432/omnisend_test)
and TEST_REDIS_URL (default redis://localhost:6379/15).
"""

from __future__ import annotations

import os

os.environ.setdefault("APP_ENV", "test")
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://omnisend:omnisend@localhost:5432/omnisend_test"
)
os.environ["REDIS_URL"] = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")
os.environ["RUN_SCHEDULER"] = "false"
os.environ["ADMIN_REQUIRE_2FA"] = "true"
os.environ["PUBLIC_BASE_URL"] = "http://test"

import asyncio  # noqa: E402
import io  # noqa: E402
import uuid  # noqa: E402
from collections.abc import AsyncIterator  # noqa: E402
from dataclasses import dataclass  # noqa: E402
from typing import Any  # noqa: E402

import pyotp  # noqa: E402
import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402

from app.core.redis import close_redis, get_redis  # noqa: E402
from app.db.session import dispose_engine, sessionmaker  # noqa: E402
from app.main import create_app  # noqa: E402
from app.services import bootstrap  # noqa: E402
from app.services import settings as settings_service

ADMIN_PASSWORD = "Adm1n-Password!"
USER_PASSWORD = "Us3r-Password!!"
KEEP_TABLES = {"alembic_version", "roles", "permissions", "role_permissions"}


def _run_migrations(url: str) -> None:
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "migrations"))
    cfg.attributes["database_url"] = url
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session", autouse=True)
async def _database() -> AsyncIterator[None]:
    url = os.environ["DATABASE_URL"]
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await engine.dispose()
    await asyncio.to_thread(_run_migrations, url)  # exercises the real migration (upgrade/downgrade/upgrade)
    async with sessionmaker()() as db:
        await bootstrap.ensure_reference_data(db)
    yield
    await dispose_engine()
    await close_redis()


@pytest.fixture(autouse=True)
async def _clean() -> AsyncIterator[None]:
    async with sessionmaker()() as db:
        rows = await db.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))
        tables = [r[0] for r in rows if r[0] not in KEEP_TABLES]
        await db.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
        await db.commit()
    await get_redis().flushdb()
    settings_service.invalidate_cache()
    yield


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    async with sessionmaker()() as session:
        yield session


@pytest.fixture(scope="session")
def app():  # noqa: ANN201
    return create_app()


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:  # noqa: ANN001
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


# --------------------------------------------------------------------------- helpers


@dataclass
class Session:
    token: str
    user_id: str
    headers: dict[str, str]


async def create_user(client: AsyncClient, admin: Session, role: str = "USER", username: str | None = None,
                      password: str = USER_PASSWORD, limits: dict[str, Any] | None = None) -> dict[str, Any]:
    username = username or f"u{uuid.uuid4().hex[:8]}"
    r = await client.post("/api/v1/admin/users", headers=admin.headers, json={
        "email": f"{username}@example.com", "username": username, "password": password, "role": role,
        "limits": limits,
    })
    assert r.status_code == 201, r.text
    return r.json()


async def login_user(client: AsyncClient, login: str, password: str = USER_PASSWORD) -> Session:
    r = await client.post("/api/v1/auth/login", json={"login": login, "password": password, "app": "user"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    return Session(body["access_token"], body["user"]["id"], {"Authorization": f"Bearer {body['access_token']}"})


async def login_admin(client: AsyncClient, login: str, password: str = ADMIN_PASSWORD) -> Session:
    """Performs the full mandatory-2FA flow (setup on first login, verify afterwards)."""
    r = await client.post("/api/v1/auth/login", json={"login": login, "password": password, "app": "admin"})
    assert r.status_code == 200, r.text
    body = r.json()
    if body["status"] == "mfa_setup_required":
        secret = body["totp_secret"]
        _SECRETS[login] = secret
    else:
        assert body["status"] == "mfa_required"
        secret = _SECRETS[login]
    code = pyotp.TOTP(secret).now()
    await get_redis().delete(*[k async for k in get_redis().scan_iter("totp:used:*")] or ["_none"])
    r = await client.post("/api/v1/auth/mfa/verify", json={"mfa_token": body["mfa_token"], "code": code})
    assert r.status_code == 200, r.text
    tok = r.json()
    return Session(tok["access_token"], tok["user"]["id"], {"Authorization": f"Bearer {tok['access_token']}"})


_SECRETS: dict[str, str] = {}


@pytest.fixture
async def admin(client: AsyncClient) -> Session:
    async with sessionmaker()() as db:
        await bootstrap.create_super_admin(db, "root@example.com", ADMIN_PASSWORD, "root")
    return await login_admin(client, "root")


@pytest.fixture
async def user(client: AsyncClient, admin: Session) -> Session:
    u = await create_user(client, admin, username="alice")
    return await login_user(client, u["username"])


async def create_provider(client: AsyncClient, admin: Session, **overrides: Any) -> dict[str, Any]:
    body = {"provider_name": f"smtp-{uuid.uuid4().hex[:6]}", "host": "localhost", "port": 2525,
            "tls_mode": "none", "from_email": "news@example.com", "from_name": "Example",
            "per_second_limit": 100, "password": "smtp-secret"}
    body.update(overrides)
    r = await client.post("/api/v1/admin/providers", headers=admin.headers, json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def assign(client: AsyncClient, admin: Session, user_id: str, provider_id: str) -> dict[str, Any]:
    r = await client.post("/api/v1/admin/assignments", headers=admin.headers,
                          json={"user_id": user_id, "provider_id": provider_id})
    assert r.status_code == 201, r.text
    return r.json()


def csv_file(emails: list[str], extra_header: str = "first_name") -> tuple[str, io.BytesIO, str]:
    lines = [f"email,{extra_header}"] + [f"{e},Name{i}" for i, e in enumerate(emails)]
    return ("recipients.csv", io.BytesIO("\n".join(lines).encode()), "text/csv")


async def ready_campaign(client: AsyncClient, user: Session, provider_id: str, emails: list[str],
                         batch_size: int = 1000) -> dict[str, Any]:
    r = await client.post("/api/v1/user/campaigns", headers=user.headers, json={
        "name": "Launch", "subject": "Hello {{first_name}}", "from_email": "news@example.com",
        "from_name": "Example", "html_body": "<p>Hi {{first_name}}</p>", "text_body": "Hi {{first_name}}",
        "provider_id": provider_id, "batch_size": batch_size,
    })
    assert r.status_code == 201, r.text
    campaign = r.json()
    r = await client.post(f"/api/v1/user/campaigns/{campaign['id']}/recipients", headers=user.headers,
                          files={"file": csv_file(emails)})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "READY", r.json()
    return campaign


async def provision_worker(client: AsyncClient, admin: Session, worker_id: str = "worker-001") -> dict[str, str]:
    r = await client.post("/api/v1/admin/workers", headers=admin.headers,
                          json={"worker_id": worker_id, "name": "Test worker", "capacity": 80})
    assert r.status_code == 201, r.text
    cred = r.json()["credential"]
    r = await client.post("/api/v1/worker/token", json={"worker_id": worker_id, "credential": cred})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def tick() -> dict[str, int]:
    from app.scheduler.loop import Scheduler

    return await Scheduler().tick()
