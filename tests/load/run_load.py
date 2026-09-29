"""Load test (ARCHITECTURE.md §63, PROGRESS P3-10).

Starts the real API (+ scheduler), N real worker processes and a local SMTP sink, sends one campaign
and reports throughput. Only ever sends to the local sink.

    .venv/bin/python tests/load/run_load.py --recipients 20000 --workers 2 --jobs 4 --connections 8

Env: LOAD_DATABASE_URL (default …/omnisend_load), LOAD_REDIS_URL (default redis://localhost:6379/13).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable
DB_URL = os.environ.get("LOAD_DATABASE_URL", "postgresql+asyncpg://omnisend:omnisend@localhost:5432/omnisend_load")
REDIS_URL = os.environ.get("LOAD_REDIS_URL", "redis://localhost:6379/13")
ADMIN_EMAIL, ADMIN_PASSWORD = "load-admin@example.com", "Load-Admin-Passw0rd!"
USER_PASSWORD = "Load-User-Passw0rd!"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def env(**extra: str) -> dict[str, str]:
    e = {k: v for k, v in os.environ.items() if not k.startswith(("DATABASE_", "REDIS_"))}
    e.update(DATABASE_URL=DB_URL, REDIS_URL=REDIS_URL, APP_ENV="test", LOG_LEVEL="WARNING")
    e.update(extra)
    return e


def reset_db() -> None:
    backend = ROOT / "backend"
    code = (
        "import asyncio;from sqlalchemy import text;from sqlalchemy.ext.asyncio import create_async_engine\n"
        "async def m():\n e=create_async_engine('%s')\n async with e.begin() as c:\n"
        "  await c.execute(text('DROP SCHEMA public CASCADE'));await c.execute(text('CREATE SCHEMA public'))\n"
        " await e.dispose()\nasyncio.run(m())" % DB_URL
    )
    subprocess.run([PY, "-c", code], cwd=backend, env=env(), check=True)  # noqa: S603
    subprocess.run([PY, "-m", "alembic", "upgrade", "head"], cwd=backend, env=env(), check=True,  # noqa: S603
                   capture_output=True)
    subprocess.run(["redis-cli", "-n", REDIS_URL.rsplit("/", 1)[-1], "flushdb"], check=True,  # noqa: S603, S607
                   capture_output=True)


async def main(args: argparse.Namespace) -> None:
    reset_db()
    smtp_port, api_port = free_port(), free_port()
    procs: list[subprocess.Popen] = []
    sink_cmd = ([PY, str(ROOT / "tests/load/fast_sink.py"), "--port", str(smtp_port), "--processes",
                 str(args.sink_processes), "--latency-ms", str(args.latency_ms)] if args.sink_processes else
                [PY, "-m", "aiosmtpd", "-n", "-c", "aiosmtpd.handlers.Sink", "-l", f"127.0.0.1:{smtp_port}"])
    sink = subprocess.Popen(sink_cmd)  # noqa: S603
    procs.append(sink)
    api = subprocess.Popen(  # noqa: S603
        [PY, "-m", "uvicorn", "main:app", "--port", str(api_port), "--workers", str(args.api_workers),
         "--log-level", "warning"],
        cwd=ROOT / "backend",
        env=env(RUN_SCHEDULER="true", SCHEDULER_INTERVAL_SECONDS=str(args.scheduler_interval), ADMIN_REQUIRE_2FA="false",
                BOOTSTRAP_ADMIN_EMAIL=ADMIN_EMAIL, BOOTSTRAP_ADMIN_PASSWORD=ADMIN_PASSWORD,
                PUBLIC_BASE_URL=f"http://127.0.0.1:{api_port}"),
    )
    procs.append(api)
    base = f"http://127.0.0.1:{api_port}"
    try:
        async with httpx.AsyncClient(base_url=base, timeout=120) as c:
            for _ in range(150):
                try:
                    if (await c.get("/readyz")).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.2)
            r = await c.post("/api/v1/auth/login", json={"login": ADMIN_EMAIL, "password": ADMIN_PASSWORD, "app": "admin"})
            admin = {"Authorization": f"Bearer {r.json()['access_token']}"}
            r = await c.post("/api/v1/admin/users", headers=admin, json={
                "email": "load@example.com", "username": "load", "password": USER_PASSWORD, "role": "USER"})
            user_id = r.json()["id"]
            provider = {"provider_name": "sink", "host": "127.0.0.1", "port": smtp_port, "tls_mode": "none",
                        "from_email": "news@load.example"}
            if args.provider_rate:
                provider["per_second_limit"] = args.provider_rate
            r = await c.post("/api/v1/admin/providers", headers=admin, json=provider)
            pid = r.json()["id"]
            await c.post("/api/v1/admin/assignments", headers=admin, json={"user_id": user_id, "provider_id": pid})
            await c.put("/api/v1/admin/settings", headers=admin, json={"values": {"max_batch_size": 100000}})
            creds = []
            for i in range(args.workers):
                r = await c.post("/api/v1/admin/workers", headers=admin, json={
                    "worker_id": f"load-{i}", "name": f"load {i}", "capacity": args.worker_capacity,
                    "max_concurrent_jobs": args.jobs})
                creds.append(r.json()["credential"])

            r = await c.post("/api/v1/auth/login", json={"login": "load", "password": USER_PASSWORD, "app": "user"})
            user = {"Authorization": f"Bearer {r.json()['access_token']}"}
            r = await c.post("/api/v1/user/campaigns", headers=user, json={
                "name": "load", "subject": "Hello {{first_name}}", "from_email": "news@load.example",
                "html_body": "<h1>Hi {{first_name}}</h1><p>" + "Lorem ipsum dolor sit amet. " * 40 + "</p>",
                "provider_id": pid, "batch_size": args.batch_size})
            cid = r.json()["id"]
            csv = "email,first_name\n" + "\n".join(f"u{i}@load.example,Name{i}" for i in range(args.recipients))
            t0 = time.perf_counter()
            r = await c.post(f"/api/v1/user/campaigns/{cid}/recipients", headers=user,
                             files={"file": ("l.csv", csv.encode(), "text/csv")})
            upload_s = time.perf_counter() - t0
            assert r.json()["imported"] == args.recipients, r.text
            r = await c.post(f"/api/v1/user/campaigns/{cid}/start", headers=user, json={"consent_confirmed": True})
            assert r.status_code == 200, r.text

            start = time.perf_counter()
            for i, cred in enumerate(creds):
                cmd = [PY, "worker.py"]
                if args.profile and i == 0:  # sample worker 0 with py-spy (raw folded stacks)
                    cmd = [str(Path(PY).parent / "py-spy"), "record", "-r", "250", "-f", "raw", "-o", args.profile,
                           "--", *cmd]
                procs.append(subprocess.Popen(  # noqa: S603
                    cmd, cwd=ROOT / "worker",
                    env=env(WORKER_API_URL=base, WORKER_ID=f"load-{i}", WORKER_CREDENTIAL=cred,
                            SMTP_CONNECTIONS_PER_JOB=str(args.connections), LOG_LEVEL="WARNING",
                            WORKER_SMTP_PIPELINING="true" if args.pipelining == "on" else "false")))
            first_sent_at = None
            last = 0
            samples = []
            while True:
                s = (await c.get(f"/api/v1/user/campaigns/{cid}/stats", headers=user)).json()
                now = time.perf_counter()
                if s["sent"] and first_sent_at is None:
                    first_sent_at = now
                samples.append((now, s["sent"]))
                if s["sent"] != last:
                    last = s["sent"]
                if s["status"] in ("COMPLETED", "FAILED") or now - start > args.timeout:
                    break
                await asyncio.sleep(0.5)
            end = time.perf_counter()
            sent = s["sent"]
            steady = sent / (end - first_sent_at) if first_sent_at and end > first_sent_at else 0
            print("\n=== load test ===")
            print(f"recipients={args.recipients} workers={args.workers} jobs/worker={args.jobs} "
                  f"connections/job={args.connections} batch={args.batch_size} api_workers={args.api_workers}")
            print(f"upload+validate: {upload_s:.1f}s ({args.recipients / upload_s:,.0f} rows/s)")
            print(f"status={s['status']} sent={sent} failed={s['failed']} time_to_first_send={(first_sent_at or end) - start:.1f}s")
            print(f"total={end - start:.1f}s  throughput={steady:,.0f} msgs/s  (~{steady * 3600:,.0f}/hour)")
    finally:
        for p in reversed(procs):
            p.send_signal(signal.SIGTERM)
        for p in procs:
            try:
                p.wait(timeout=30)
            except subprocess.TimeoutExpired:
                p.kill()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--recipients", type=int, default=20000)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--jobs", type=int, default=4, help="max concurrent jobs per worker")
    ap.add_argument("--connections", type=int, default=4, help="SMTP connections per job")
    ap.add_argument("--batch-size", type=int, default=1000)
    ap.add_argument("--worker-capacity", type=int, default=100000, help="msgs/s cap per worker")
    ap.add_argument("--provider-rate", type=int, default=0, help="provider per-second limit (0 = none)")
    ap.add_argument("--api-workers", type=int, default=2)
    ap.add_argument("--timeout", type=float, default=600)
    ap.add_argument("--sink-processes", type=int, default=0,
                    help="use tests/load/fast_sink.py with N processes (0 = single-process aiosmtpd)")
    ap.add_argument("--latency-ms", type=float, default=0.0,
                    help="simulated provider round-trip per SMTP reply (fast sink only), e.g. 25")
    ap.add_argument("--pipelining", choices=("on", "off"), default="on",
                    help="worker SMTP PIPELINING/CHUNKING (the fast sink advertises both)")
    ap.add_argument("--scheduler-interval", type=float, default=5.0, help="production default is 5 s")
    ap.add_argument("--profile", default="", help="write a py-spy folded profile of worker 0 to this path")
    asyncio.run(main(ap.parse_args()))
