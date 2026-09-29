"""Black-box end-to-end test of the whole pipeline (ARCHITECTURE.md §1, §63 integration tests):

    uvicorn API (RUN_SCHEDULER=false) ─┐
    runner (scheduler + processor) ────┼── PostgreSQL + Redis      (production topology, DS-19)
    worker process ────────────────────┤
    aiosmtpd sink ◄── SMTP ────────────┘

Run:  .venv/bin/pytest tests/e2e -q
Env:  E2E_DATABASE_URL (default …/omnisend_e2e), E2E_REDIS_URL (default redis://localhost:6379/14)
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import re
import signal
import socket
import subprocess
import sys
import time
from email import message_from_bytes, policy
from pathlib import Path

import httpx
import pyotp
import pytest
from aiosmtpd.controller import Controller
from aiosmtpd.smtp import Envelope, Session

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable
DB_URL = os.environ.get("E2E_DATABASE_URL", "postgresql+asyncpg://omnisend:omnisend@localhost:5432/omnisend_e2e")
REDIS_URL = os.environ.get("E2E_REDIS_URL", "redis://localhost:6379/14")
ADMIN_EMAIL, ADMIN_PASSWORD = "e2e-admin@example.com", "E2e-Admin-Passw0rd!"
USER_PASSWORD = "E2e-User-Passw0rd!"
REJECTED = "reject@example.org"


def _dsn(message_id: str, email: str) -> bytes:
    return (
        'Content-Type: multipart/report; report-type=delivery-status; boundary="B"\r\nMIME-Version: 1.0\r\n\r\n'
        "--B\r\nContent-Type: message/delivery-status\r\n\r\nReporting-MTA: dns; mx.example\r\n\r\n"
        f"Final-Recipient: rfc822; {email}\r\nAction: failed\r\nStatus: 5.1.1\r\n\r\n"
        f"--B\r\nContent-Type: text/rfc822-headers\r\n\r\nMessage-ID: {message_id}\r\n\r\n--B--\r\n"
    ).encode()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Sink:
    def __init__(self) -> None:
        self.messages: list[tuple[str, bytes]] = []

    async def handle_RCPT(self, server, session: Session, envelope: Envelope, address: str, rcpt_options):  # noqa: ANN001, N802
        if address == REJECTED:
            return "550 5.1.1 <reject@example.org>: Recipient address rejected: User unknown"
        envelope.rcpt_tos.append(address)
        return "250 OK"

    async def handle_DATA(self, server, session: Session, envelope: Envelope) -> str:  # noqa: ANN001, N802
        for rcpt in envelope.rcpt_tos:
            self.messages.append((rcpt, envelope.content))
        return "250 2.0.0 Ok: queued"


@pytest.fixture
def smtp_sink():  # noqa: ANN201
    sink = Sink()
    controller = Controller(sink, hostname="127.0.0.1", port=free_port())
    controller.start()
    yield sink, controller.port
    controller.stop()


def _env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DATABASE_", "REDIS_"))}
    env.update(DATABASE_URL=DB_URL, REDIS_URL=REDIS_URL, APP_ENV="test", LOG_LEVEL="WARNING")
    env.update(extra)
    return env


@pytest.fixture
def backend():  # noqa: ANN201
    env = _env(RUN_SCHEDULER="false", SCHEDULER_INTERVAL_SECONDS="0.5", ADMIN_REQUIRE_2FA="true",
               BOOTSTRAP_ADMIN_EMAIL=ADMIN_EMAIL, BOOTSTRAP_ADMIN_PASSWORD=ADMIN_PASSWORD)
    port = free_port()
    env["PUBLIC_BASE_URL"] = f"http://127.0.0.1:{port}"
    backend_dir = ROOT / "backend"
    reset = (
        "import asyncio;from sqlalchemy import text;from sqlalchemy.ext.asyncio import create_async_engine\n"
        "async def m():\n e=create_async_engine('%s')\n async with e.begin() as c:\n"
        "  await c.execute(text('DROP SCHEMA public CASCADE'));await c.execute(text('CREATE SCHEMA public'))\n"
        " await e.dispose()\nasyncio.run(m())" % DB_URL
    )
    subprocess.run([PY, "-c", reset], cwd=backend_dir, env=env, check=True)  # noqa: S603
    subprocess.run([PY, "-m", "alembic", "upgrade", "head"], cwd=backend_dir, env=env, check=True,  # noqa: S603
                   capture_output=True)
    subprocess.run(["redis-cli", "-n", REDIS_URL.rsplit("/", 1)[-1], "flushdb"], check=True,  # noqa: S603, S607
                   capture_output=True)
    proc = subprocess.Popen(  # noqa: S603
        [PY, "-m", "uvicorn", "main:app", "--port", str(port), "--log-level", "warning"],
        cwd=backend_dir, env=env,
    )
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if httpx.get(f"{base}/readyz", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    else:
        proc.kill()
        pytest.fail("backend did not start")
    runner = subprocess.Popen([PY, "-m", "app.runner"], cwd=backend_dir, env=env)  # noqa: S603
    yield base
    runner.send_signal(signal.SIGTERM)
    assert runner.wait(timeout=20) == 0, "runner must stop cleanly on SIGTERM"
    proc.send_signal(signal.SIGTERM)
    proc.wait(timeout=20)


async def _admin_login(c: httpx.AsyncClient) -> dict[str, str]:
    r = await c.post("/api/v1/auth/login", json={"login": ADMIN_EMAIL, "password": ADMIN_PASSWORD, "app": "admin"})
    body = r.json()
    assert body["status"] == "mfa_setup_required", body
    code = pyotp.TOTP(body["totp_secret"]).now()
    r = await c.post("/api/v1/auth/mfa/verify", json={"mfa_token": body["mfa_token"], "code": code})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_full_pipeline(backend: str, smtp_sink) -> None:  # noqa: ANN001
    sink, smtp_port = smtp_sink
    async with httpx.AsyncClient(base_url=backend, timeout=30) as c:
        admin = await _admin_login(c)
        r = await c.post("/api/v1/admin/users", headers=admin, json={
            "email": "sender@example.com", "username": "sender", "password": USER_PASSWORD, "role": "USER",
            "limits": {"daily_limit": 10000, "per_second_limit": 200}})
        user_id = r.json()["id"]
        r = await c.post("/api/v1/admin/providers", headers=admin, json={
            "provider_name": "local-sink", "host": "127.0.0.1", "port": smtp_port, "tls_mode": "none",
            "from_email": "news@brand.example", "from_name": "Brand", "per_second_limit": 500})
        provider_id = r.json()["id"]
        r = await c.post(f"/api/v1/admin/providers/{provider_id}/test", headers=admin)
        assert r.json()["ok"] is True, r.json()
        await c.post("/api/v1/admin/assignments", headers=admin, json={"user_id": user_id, "provider_id": provider_id})
        await c.post("/api/v1/admin/suppressions", headers=admin,
                     json={"email": "suppressed@example.org", "type": "admin_blocked"})
        r = await c.post("/api/v1/admin/workers", headers=admin, json={"worker_id": "e2e-worker", "name": "E2E"})
        credential = r.json()["credential"]

        r = await c.post("/api/v1/auth/login", json={"login": "sender", "password": USER_PASSWORD, "app": "user"})
        user = {"Authorization": f"Bearer {r.json()['access_token']}"}
        r = await c.post("/api/v1/user/campaigns", headers=user, json={
            "name": "E2E launch", "subject": "Hello {{first_name}} #USERID#", "from_email": "news@brand.example",
            "from_name": "Brand", "html_body": "<h1>Hi {{first_name}}</h1><p>Welcome aboard. Invoice #INVOICE#, "
            "ref #REF#, #MASSAGE#</p>", "message_list": ["Enjoy!"],
            "provider_id": provider_id, "batch_size": 500})
        cid = r.json()["id"]
        emails = [f"person{i}@example.org" for i in range(120)] + [REJECTED, "suppressed@example.org"]
        csv = "email,first_name\n" + "\n".join(f"{e},Name{i}" for i, e in enumerate(emails))
        r = await c.post(f"/api/v1/user/campaigns/{cid}/recipients", headers=user,
                         files={"file": ("list.csv", csv.encode(), "text/csv")})
        assert r.json()["imported"] == 122
        # 122 recipients with batch size 500 is one job; use a smaller batch to exercise several jobs.
        await c.patch(f"/api/v1/user/campaigns/{cid}", headers=user, json={"batch_size": 50})
        r = await c.post(f"/api/v1/user/campaigns/{cid}/start", headers=user, json={"consent_confirmed": True})
        assert r.status_code == 200, r.text

        worker = subprocess.Popen(  # noqa: S603
            [PY, "worker.py"], cwd=ROOT / "worker",
            env=_env(WORKER_API_URL=backend, WORKER_ID="e2e-worker", WORKER_CREDENTIAL=credential,
                     WORKER_MAX_CONCURRENT_JOBS="3", SMTP_CONNECTIONS_PER_JOB="4"),
        )
        try:
            stats: dict = {}
            for _ in range(240):
                stats = (await c.get(f"/api/v1/user/campaigns/{cid}/stats", headers=user)).json()
                if stats["status"] in ("COMPLETED", "FAILED"):
                    break
                await asyncio.sleep(0.25)
            assert stats["status"] == "COMPLETED", stats
            assert stats["sent"] == 120
            assert stats["bounced"] == 1
            assert stats["skipped_suppressed"] == 1
            assert stats["remaining"] == 0

            dash = (await c.get("/api/v1/admin/dashboard", headers=admin)).json()
            assert dash["active_workers"] == 1
            assert dash["today"]["sent"] == 120
        finally:
            worker.send_signal(signal.SIGTERM)
            assert worker.wait(timeout=30) == 0

        workers = (await c.get("/api/v1/admin/workers", headers=admin)).json()["items"]
        assert workers[0]["status"] == "offline", "graceful shutdown must report offline"

        assert len(sink.messages) == 120
        assert len({rcpt for rcpt, _ in sink.messages}) == 120, "no duplicates"
        rcpt, raw = next(m for m in sink.messages if m[0] == "person7@example.org")
        msg = message_from_bytes(raw, policy=policy.default)
        assert msg["Subject"] == "Hello Name7 person7", "template tags rendered per recipient"
        body7 = msg.get_body(("html",)).get_content()
        invoice7 = re.search(r"Invoice ([0-9A-F]{7}), ref ([0-9A-F]{8}), Enjoy!", body7)
        assert invoice7, body7
        _, raw8 = next(m for m in sink.messages if m[0] == "person8@example.org")
        body8 = message_from_bytes(raw8, policy=policy.default).get_body(("html",)).get_content()
        assert invoice7.group(1) not in body8, "each recipient gets their own values"
        assert msg["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
        unsub = str(msg["List-Unsubscribe"]).strip("<>")
        assert unsub.startswith(backend)
        assert "Hi Name7" in msg.get_body(("html",)).get_content()

        # The rejected address was hard-bounced and globally suppressed.
        supp = (await c.get("/api/v1/admin/suppressions", headers=admin, params={"type": "hard_bounce"})).json()
        assert [s["email_normalized"] for s in supp["items"]] == [REJECTED]

        # An asynchronous bounce arrives later as a DSN on the signed inbound endpoint; the runner's
        # processor applies it (receiver → inbox → processor, DS-19).
        secret = (await c.post(f"/api/v1/admin/providers/{provider_id}/webhook-secret",
                               headers=admin)).json()["webhook_secret"]
        _, raw9 = next(m for m in sink.messages if m[0] == "person9@example.org")
        dsn = _dsn(message_from_bytes(raw9, policy=policy.default)["Message-ID"], "person9@example.org")
        ts = str(int(time.time()))
        sig = hmac.new(secret.encode(), f"{ts}.".encode() + dsn, hashlib.sha256).hexdigest()
        r = await c.post(f"/api/v1/hooks/providers/{provider_id}/inbound", content=dsn, headers={
            "X-OmniSend-Timestamp": ts, "X-OmniSend-Signature": f"sha256={sig}", "Content-Type": "message/rfc822"})
        assert r.status_code == 202
        for _ in range(40):
            stats = (await c.get(f"/api/v1/user/campaigns/{cid}/stats", headers=user)).json()
            if stats["bounced"] == 2:
                break
            await asyncio.sleep(0.25)
        assert stats["bounced"] == 2, stats

        # One-click unsubscribe from the delivered message works.
        r = await c.post(unsub.replace(backend, ""), content=b"List-Unsubscribe=One-Click",
                         headers={"Content-Type": "application/x-www-form-urlencoded"})
        assert r.status_code == 200
        stats = (await c.get(f"/api/v1/user/campaigns/{cid}/stats", headers=user)).json()
        assert stats["unsubscribed"] == 1
