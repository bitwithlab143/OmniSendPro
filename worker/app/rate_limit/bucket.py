"""Token buckets. The provider bucket lives in Redis so that the per-second limit is shared by every
worker sending through that provider (§21, §68 "provider limits are enforced"). Without Redis the
limit is enforced per worker only (documented degraded mode)."""

from __future__ import annotations

import asyncio
import logging
import time

from redis.asyncio import Redis
from redis.exceptions import RedisError

log = logging.getLogger("worker.ratelimit")

# KEYS[1] bucket; ARGV: rate, capacity, now_ms. Returns ms to wait (0 = token granted).
_LUA = """
local rate = tonumber(ARGV[1])
local capacity = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local data = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(data[1]) or capacity
local ts = tonumber(data[2]) or now
tokens = math.min(capacity, tokens + (now - ts) * rate / 1000.0)
local wait = 0
if tokens >= 1 then
  tokens = tokens - 1
else
  wait = math.ceil((1 - tokens) * 1000.0 / rate)
end
redis.call('HSET', KEYS[1], 'tokens', tokens, 'ts', now)
redis.call('PEXPIRE', KEYS[1], math.max(2000, math.ceil(capacity * 1000.0 / rate) * 2))
return wait
"""


class LocalBucket:
    def __init__(self, rate: float, capacity: float | None = None) -> None:
        self.rate = max(0.001, float(rate))
        self.capacity = capacity or max(1.0, self.rate)
        self.tokens = self.capacity
        self.ts = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.ts) * self.rate)
                self.ts = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                await asyncio.sleep((1 - self.tokens) / self.rate)


class RedisBucket:
    def __init__(self, redis: Redis, key: str, rate: float, fallback: LocalBucket) -> None:
        self.redis = redis
        self.key = key
        self.rate = max(0.001, float(rate))
        self.capacity = max(1.0, self.rate)
        self.fallback = fallback
        self._script = redis.register_script(_LUA)

    async def acquire(self) -> None:
        while True:
            try:
                wait_ms = int(await self._script(keys=[self.key], args=[self.rate, self.capacity, int(time.time() * 1000)]))
            except RedisError as exc:
                log.warning("redis_rate_limit_unavailable", extra={"error": str(exc)})
                await self.fallback.acquire()
                return
            if wait_ms <= 0:
                return
            await asyncio.sleep(wait_ms / 1000.0)


class Unlimited:
    async def acquire(self) -> None:
        return None
