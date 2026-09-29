"""Shared Redis client (queue hints, rate limits, locks, live stats — ARCHITECTURE.md §30).

With REDIS_SENTINELS set, the master is discovered through Sentinel and re-discovered after a failover
(design DS-23, P4-02). Redis holds no job state (ADR-002), so a failover only costs a few seconds of
rate-limit and live-stats accuracy.
"""

from __future__ import annotations

from urllib.parse import unquote, urlparse

from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.asyncio.sentinel import Sentinel
from redis.backoff import ExponentialBackoff
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import ReadOnlyError
from redis.exceptions import TimeoutError as RedisTimeoutError

from app.core.config import get_settings

_client: Redis | None = None


def sentinel_client(sentinels: str, master: str, url: str, **kwargs: object) -> Redis:
    """Master client discovered through Sentinel; credentials and DB come from ``url``."""
    parsed = urlparse(url)
    hosts = []
    for part in sentinels.split(","):
        host, _, port = part.strip().rpartition(":")
        hosts.append((host or part.strip(), int(port or 26379)))
    db = int((parsed.path or "/0").lstrip("/") or 0)
    auth = {"username": unquote(parsed.username) if parsed.username else None,
            "password": unquote(parsed.password) if parsed.password else None}
    retry = Retry(ExponentialBackoff(cap=2.0, base=0.1), retries=8)
    sentinel = Sentinel(hosts, socket_timeout=2.0, sentinel_kwargs={"socket_timeout": 2.0})
    return sentinel.master_for(master, db=db, retry=retry,
                               retry_on_error=[RedisConnectionError, RedisTimeoutError, ReadOnlyError],
                               **auth, **kwargs)  # type: ignore[arg-type]


def get_redis() -> Redis:
    global _client
    if _client is None:
        s = get_settings()
        if s.redis_sentinels:
            _client = sentinel_client(s.redis_sentinels, s.redis_sentinel_master, s.redis_url,
                                      decode_responses=True, health_check_interval=30)
        else:
            _client = Redis.from_url(s.redis_url, decode_responses=True, health_check_interval=30)
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
    _client = None


class Keys:
    """Redis key names. Keep in sync with design DS-05 'Redis keys'."""

    @staticmethod
    def user_quota(user_id: object, window: str) -> str:
        return f"quota:user:{user_id}:{window}"

    @staticmethod
    def provider_quota(provider_id: object, window: str) -> str:
        return f"quota:provider:{provider_id}:{window}"

    @staticmethod
    def provider_health(provider_id: object, minute: int) -> str:
        return f"health:provider:{provider_id}:{minute}"

    @staticmethod
    def campaign_stats(campaign_id: object) -> str:
        return f"stats:campaign:{campaign_id}"

    @staticmethod
    def sent_per_second(second: int) -> str:
        return f"stats:sent:{second}"

    @staticmethod
    def campaign_rate(campaign_id: object, second: int) -> str:
        return f"stats:campaign:{campaign_id}:rate:{second}"

    @staticmethod
    def login_attempts(ip: str) -> str:
        return f"ratelimit:login:{ip}"

    SCHEDULER_LOCK = "lock:scheduler"
    SCHEDULER_WAKE = "wake:scheduler"
    PROCESSOR_WAKE = "wake:processor"
    HEALTH_LOCK = "lock:provider-health"
