"""Shared Redis client (queue hints, rate limits, locks, live stats — ARCHITECTURE.md §30)."""

from __future__ import annotations

from redis.asyncio import Redis

from app.core.config import get_settings

_client: Redis | None = None


def get_redis() -> Redis:
    global _client
    if _client is None:
        _client = Redis.from_url(get_settings().redis_url, decode_responses=True, health_check_interval=30)
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
    HEALTH_LOCK = "lock:provider-health"
