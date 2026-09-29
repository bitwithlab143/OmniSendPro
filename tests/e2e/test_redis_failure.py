"""Queue recovery drill (design DS-23, P4-07): Redis dies in the middle of a campaign.

Postgres holds all job state (ADR-002), so losing Redis — even losing its data — must not lose or duplicate
a single message. The drill runs the production topology (API + runner + worker) against a dedicated
redis-server, kills it with SIGKILL mid-send, keeps it down for a few seconds, restarts it *empty* and then
requires: the campaign completes, and every recipient received exactly one message.
"""

from __future__ import annotations

import asyncio
import shutil
import signal
import socket
import subprocess
import time
from collections import Counter

import httpx
import pytest
from aiosmtpd.controller import Controller

from test_pipeline import (  # sibling module (tests/e2e is on sys.path)
    ADMIN_EMAIL,
    ADMIN_PASSWORD,
    PY,
    ROOT,
    USER_PASSWORD,
    Sink,
    _admin_login,
    _env,
    free_port,
)

pytestmark = pytest.mark.skipif(shutil.which("redis-server") is None, reason="redis-server not installed")
DB_URL = "postgresql+asyncpg://omnisend:omnisend@localhost:5432/omnisend_drill"


def _start_redis(port: int) -> subprocess.Popen:
    proc = subprocess.Popen(["redis-server", "--port", str(port), "--bind", "127.0.0.1", "--save", "",  # noqa: S603, S607
                             "--appendonly", "no"], stdout=subprocess.DEVNULL)
    for _ in range(100):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return proc
        except OSError:
            time.sleep(0.05)
    raise RuntimeError("redis did not start")


def _wait_http(url: str) -> None:
    for _ in range(150):
        try:
            if httpx.get(url, timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise RuntimeError(f"{url} not ready")


async def test_redis_loss_mid_campaign_loses_nothing() -> None:
    redis_port, api_port = free_port(), free_port()
    redis = _start_redis(redis_port)
    sink = Sink()
    smtp = Controller(sink, hostname="127.0.0.1", port=free_port())
    smtp.start()
    env = _env(RUN_SCHEDULER="false", SCHEDULER_INTERVAL_SECONDS="0.5", ADMIN_REQUIRE_2FA="true",
               BOOTSTRAP_ADMIN_EMAIL=ADMIN_EMAIL, BOOTSTRAP_ADMIN_PASSWORD=ADMIN_PASSWORD,
               PUBLIC_BASE_URL=f"http://127.0.0.1:{api_port}")
    env.update(DATABASE_URL=DB_URL, REDIS_URL=f"redis://127.0.0.1:{redis_port}/0")
    backend_dir = ROOT / "backend"
    reset = (
        "import asyncio;from sqlalchemy import text;from sqlalchemy.ext.asyncio import create_async_engine\n"
        f"async def m():\n e=create_async_engine('{DB_URL.rsplit('/', 1)[0]}/postgres', isolation_level='AUTOCOMMIT')\n"
        " async with e.connect() as c:\n"
        "  await c.execute(text('DROP DATABASE IF EXISTS omnisend_drill WITH (FORCE)'))\n"
        "  await c.execute(text('CREATE DATABASE omnisend_drill'))\n"
        " await e.dispose()\nasyncio.run(m())"
    )
    subprocess.run([PY, "-c", reset], cwd=backend_dir, env=env, check=True)  # noqa: S603
    subprocess.run([PY, "-m", "alembic", "upgrade", "head"], cwd=backend_dir, env=env, check=True,  # noqa: S603
                   capture_output=True)
    procs = [subprocess.Popen([PY, "-m", "uvicorn", "main:app", "--port", str(api_port), "--log-level", "warning"],  # noqa: S603
                              cwd=backend_dir, env=env)]
    base = f"http://127.0.0.1:{api_port}"
    try:
        _wait_http(f"{base}/readyz")
        procs.append(subprocess.Popen([PY, "-m", "app.runner"], cwd=backend_dir, env=env))  # noqa: S603
        async with httpx.AsyncClient(base_url=base, timeout=30) as c:
            admin = await _admin_login(c)
            r = await c.post("/api/v1/admin/users", headers=admin, json={
                "email": "drill@example.com", "username": "drill", "password": USER_PASSWORD, "role": "USER"})
            user_id = r.json()["id"]
            r = await c.post("/api/v1/admin/providers", headers=admin, json={
                "provider_name": "sink", "host": "127.0.0.1", "port": smtp.port, "tls_mode": "none",
                "from_email": "news@brand.example", "per_second_limit": 60})
            provider_id = r.json()["id"]
            await c.post("/api/v1/admin/assignments", headers=admin, json={"user_id": user_id, "provider_id": provider_id})
            credential = (await c.post("/api/v1/admin/workers", headers=admin,
                                       json={"worker_id": "drill-worker", "name": "Drill"})).json()["credential"]
            token = (await c.post("/api/v1/auth/login", json={"login": "drill", "password": USER_PASSWORD,
                                                              "app": "user"})).json()["access_token"]
            user = {"Authorization": f"Bearer {token}"}
            cid = (await c.post("/api/v1/user/campaigns", headers=user, json={
                "name": "Drill", "subject": "Hi", "from_email": "news@brand.example", "html_body": "<p>Hi</p>",
                "provider_id": provider_id, "batch_size": 50})).json()["id"]
            emails = [f"drill{i}@example.org" for i in range(360)]  # ~6 s at 60/s
            await c.post(f"/api/v1/user/campaigns/{cid}/recipients", headers=user,
                         files={"file": ("l.csv", ("email\n" + "\n".join(emails)).encode(), "text/csv")})
            assert (await c.post(f"/api/v1/user/campaigns/{cid}/start", headers=user,
                                 json={"consent_confirmed": True})).status_code == 200
            procs.append(subprocess.Popen(  # noqa: S603
                [PY, "worker.py"], cwd=ROOT / "worker",
                env={**env, "WORKER_API_URL": base, "WORKER_ID": "drill-worker", "WORKER_CREDENTIAL": credential,
                     "WORKER_MAX_CONCURRENT_JOBS": "3"}))

            for _ in range(200):  # wait until sending is under way
                if len(sink.messages) >= 60:
                    break
                await asyncio.sleep(0.05)
            assert 0 < len(sink.messages) < len(emails), "Redis must fail mid-campaign"
            redis.send_signal(signal.SIGKILL)
            redis.wait(timeout=10)
            await asyncio.sleep(4)
            redis = _start_redis(redis_port)  # back, but empty: every Redis key is gone

            stats: dict = {}
            for _ in range(600):
                try:
                    stats = (await c.get(f"/api/v1/user/campaigns/{cid}/stats", headers=user)).json()
                except httpx.HTTPError:
                    stats = {}
                if stats.get("status") in ("COMPLETED", "FAILED"):
                    break
                await asyncio.sleep(0.25)
            assert stats.get("status") == "COMPLETED", stats
            assert stats["sent"] == len(emails) and stats["failed"] == 0
            counts = Counter(rcpt for rcpt, _ in sink.messages)
            assert set(counts) == set(emails), "no recipient lost"
            assert max(counts.values()) == 1, f"duplicates: {[e for e, n in counts.items() if n > 1][:5]}"
    finally:
        for p in reversed(procs):
            p.send_signal(signal.SIGTERM)
        for p in procs:
            try:
                p.wait(timeout=30)
            except subprocess.TimeoutExpired:
                p.kill()
        redis.terminate()
        smtp.stop()
