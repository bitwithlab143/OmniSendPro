"""Restricted Redis user for workers (design DS-23, P4-10) against a real redis-server with the rendered ACL."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from redis.asyncio import Redis
from redis.exceptions import NoPermissionError, ResponseError

from app.rate_limit.bucket import LocalBucket, RedisBucket

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(shutil.which("redis-server") is None, reason="redis-server not installed")


@pytest.fixture(scope="module")
def acl_redis(tmp_path_factory: pytest.TempPathFactory) -> Iterator[int]:
    tmp = tmp_path_factory.mktemp("redis")
    script = str(ROOT / "infrastructure/redis/render-acl.sh")
    env = {**os.environ, "REDIS_APP_PASSWORD": "app-pw", "REDIS_WORKER_PASSWORD": "worker-pw"}
    acl = subprocess.run(["sh", script], check=True, capture_output=True, env=env).stdout  # noqa: S603, S607
    (tmp / "users.acl").write_bytes(acl)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    args = ["redis-server", "--port", str(port), "--bind", "127.0.0.1", "--save", "", "--appendonly", "no",
            "--aclfile", str(tmp / "users.acl"), "--dir", str(tmp)]
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL)  # noqa: S603 - fixed local command
    for _ in range(50):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                break
        time.sleep(0.1)
    yield port
    proc.terminate()
    proc.wait(timeout=10)


async def test_worker_user_can_only_use_provider_buckets(acl_redis: int) -> None:
    worker = Redis.from_url(f"redis://worker:worker-pw@127.0.0.1:{acl_redis}/0")
    app = Redis.from_url(f"redis://app:app-pw@127.0.0.1:{acl_redis}/0")
    try:
        await app.set("quota:user:u1:day", 5)
        await app.set("ratelimit:login:1.2.3.4", 3)
        bucket = RedisBucket(worker, "rl:provider:p1", rate=1000, fallback=LocalBucket(1))
        for _ in range(5):
            await bucket.acquire()  # EVALSHA/EVAL + HMGET/HSET/PEXPIRE inside the script
        assert await app.exists("rl:provider:p1") == 1
        assert await worker.ping()
        for forbidden in (worker.get("quota:user:u1:day"), worker.delete("ratelimit:login:1.2.3.4"),
                          worker.set("rl:provider:p1", "x"), worker.keys("*"), worker.flushdb()):
            with pytest.raises((NoPermissionError, ResponseError)):
                await forbidden
        # A bucket script aimed at another key is refused as well.
        with pytest.raises((NoPermissionError, ResponseError)):
            await RedisBucket(worker, "quota:user:u1:day", 1000, LocalBucket(1))._script(
                keys=["quota:user:u1:day"], args=[1, 1, 0])
        assert await app.get("quota:user:u1:day") == b"5", "untouched"
        # The default (password-less) user is disabled.
        with pytest.raises((ResponseError, Exception)):
            await Redis.from_url(f"redis://127.0.0.1:{acl_redis}/0").ping()
        # The app user cannot run dangerous admin commands.
        with pytest.raises((NoPermissionError, ResponseError)):
            await app.flushall()
    finally:
        await worker.aclose()
        await app.aclose()
