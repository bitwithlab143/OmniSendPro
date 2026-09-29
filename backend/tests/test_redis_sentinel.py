"""Redis HA through Sentinel: discovery and surviving a master failure (design DS-23, P4-02)."""

from __future__ import annotations

import asyncio
import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from redis.asyncio import Redis

from app.core.redis import sentinel_client

pytestmark = pytest.mark.skipif(shutil.which("redis-server") is None, reason="redis-server not installed")


def _port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait(port: int) -> None:
    for _ in range(100):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.05)
    raise RuntimeError(f"port {port} did not open")


@pytest.fixture
def sentinel_cluster(tmp_path: Path) -> Iterator[dict]:
    master, replica, sentinel = _port(), _port(), _port()
    common = ["--bind", "127.0.0.1", "--save", "", "--appendonly", "no"]
    procs = {
        "master": subprocess.Popen(["redis-server", "--port", str(master), "--dir", str(tmp_path), *common],  # noqa: S603, S607
                                   stdout=subprocess.DEVNULL),
    }
    _wait(master)
    procs["replica"] = subprocess.Popen(  # noqa: S603
        ["redis-server", "--port", str(replica), "--replicaof", "127.0.0.1", str(master), *common],  # noqa: S607
        stdout=subprocess.DEVNULL)
    conf = tmp_path / "sentinel.conf"
    conf.write_text(f"port {sentinel}\nbind 127.0.0.1\nsentinel monitor omnisend 127.0.0.1 {master} 1\n"
                    "sentinel down-after-milliseconds omnisend 1000\nsentinel failover-timeout omnisend 5000\n")
    procs["sentinel"] = subprocess.Popen(["redis-server", str(conf), "--sentinel"],  # noqa: S603, S607
                                         stdout=subprocess.DEVNULL)
    _wait(replica)
    _wait(sentinel)
    yield {"procs": procs, "sentinels": f"127.0.0.1:{sentinel}", "master": master, "replica": replica}
    for p in procs.values():
        p.terminate()
        p.wait(timeout=10)


async def test_discovery_and_failover(sentinel_cluster: dict) -> None:
    client = sentinel_client(sentinel_cluster["sentinels"], "omnisend", "redis://localhost:6379/3",
                             decode_responses=True)
    try:
        await client.set("probe", "before")
        assert await client.get("probe") == "before"
        # Sentinel learns about replicas from the master's INFO, polled every 10 s: wait until it lists
        # the replica (otherwise there is nothing to promote), then lose the master.
        host, port = sentinel_cluster["sentinels"].split(":")
        sentinel = Redis(host=host, port=int(port), decode_responses=True)
        for _ in range(300):
            replicas = await sentinel.execute_command("SENTINEL", "REPLICAS", "omnisend")
            if replicas:
                break
            await asyncio.sleep(0.1)
        await sentinel.aclose()
        assert replicas, "sentinel never discovered the replica"
        sentinel_cluster["procs"]["master"].kill()
        started = time.monotonic()
        recovered = False
        while time.monotonic() - started < 30:
            try:
                await client.set("probe", "after")
                recovered = True
                break
            except Exception:  # noqa: BLE001 - expected until Sentinel promotes the replica
                await asyncio.sleep(0.5)
        assert recovered, "client did not reach the promoted master"
        assert await client.get("probe") == "after"
        port = (await client.connection_pool.get_master_address())[1]
        assert port == sentinel_cluster["replica"], "writes now go to the former replica"
    finally:
        await client.aclose()
