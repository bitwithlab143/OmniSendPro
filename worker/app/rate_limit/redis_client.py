"""Redis connection for the shared provider token buckets: plain URL or Sentinel (design DS-23, P4-02)."""

from __future__ import annotations

from urllib.parse import unquote, urlparse

from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.asyncio.sentinel import Sentinel
from redis.backoff import ExponentialBackoff
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import ReadOnlyError
from redis.exceptions import TimeoutError as RedisTimeoutError


def connect(url: str, sentinels: str | None = None, master: str = "omnisend") -> Redis:
    if not sentinels:
        return Redis.from_url(url)
    parsed = urlparse(url)
    hosts = []
    for part in sentinels.split(","):
        host, _, port = part.strip().rpartition(":")
        hosts.append((host or part.strip(), int(port or 26379)))
    return Sentinel(hosts, socket_timeout=2.0, sentinel_kwargs={"socket_timeout": 2.0}).master_for(
        master,
        db=int((parsed.path or "/0").lstrip("/") or 0),
        username=unquote(parsed.username) if parsed.username else None,
        password=unquote(parsed.password) if parsed.password else None,
        retry=Retry(ExponentialBackoff(cap=2.0, base=0.1), retries=5),
        retry_on_error=[RedisConnectionError, RedisTimeoutError, ReadOnlyError],
    )
