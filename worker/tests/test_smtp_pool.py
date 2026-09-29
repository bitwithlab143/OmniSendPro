from __future__ import annotations

import socket

import pytest
from aiosmtpd.controller import Controller
from aiosmtpd.handlers import Sink

from app.providers.smtp import SmtpPoolRegistry, SmtpProvider
from app.sender.message import CompiledCampaign


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def smtp_server():  # noqa: ANN201
    controller = Controller(Sink(), hostname="127.0.0.1", port=free_port())
    controller.start()
    yield controller.port
    controller.stop()


async def test_consecutive_jobs_reuse_authenticated_connections(smtp_server: int, monkeypatch) -> None:  # noqa: ANN001
    opened = 0
    original = SmtpProvider._open

    async def counting_open(self: SmtpProvider):  # noqa: ANN202
        nonlocal opened
        opened += 1
        return await original(self)

    monkeypatch.setattr(SmtpProvider, "_open", counting_open)
    config = {"id": "p1", "host": "127.0.0.1", "port": smtp_server, "tls_mode": "none", "password": "x"}
    compiled = CompiledCampaign({"id": "c", "subject": "s", "from_email": "a@b.example", "html_body": "<p>x</p>"})
    registry = SmtpPoolRegistry(max_connections=4, timeout=10)

    for job in range(3):  # three consecutive jobs, one message each
        pool = registry.acquire(config)
        await pool.connect()
        raw, _ = compiled.render({"id": job, "email": f"r{job}@example.org", "variables": {},
                                  "unsubscribe_url": "https://u.example/x"})
        await pool.send(raw, "a@b.example", f"r{job}@example.org")
        registry.release(pool)

    assert opened == 1, "later jobs must reuse the open connection"
    # A rotated password is a different configuration → separate pool.
    assert registry.acquire({**config, "password": "rotated"}) is not registry.acquire(config)
    registry.idle_ttl = 0
    for p in list(registry._pools.values()):
        p.users = 0
    await registry.sweep()
    assert registry._pools == {}


def test_provider_max_connections_caps_shared_pool() -> None:
    registry = SmtpPoolRegistry(max_connections=32, timeout=10)
    capped = registry.acquire({"id": "p", "host": "h", "port": 25, "max_connections": 5})
    default = registry.acquire({"id": "q", "host": "h", "port": 25})
    assert capped.pool_size == 5 and default.pool_size == 32
