"""User / provider hourly + daily quota enforcement (ARCHITECTURE.md §13, §64, §68; design DS-05).

Quota is *reserved* atomically when a worker claims a job (so concurrent workers cannot overshoot) and
the unused part of the reservation is refunded when the attempt finishes.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from app.core.redis import Keys, get_redis

# KEYS: counters; ARGV[1] = amount, ARGV[2..] = limits (-1 = unlimited), then TTLs.
_RESERVE_LUA = """
local n = tonumber(ARGV[1])
local count = #KEYS
for i = 1, count do
  local limit = tonumber(ARGV[1 + i])
  if limit >= 0 then
    local current = tonumber(redis.call('GET', KEYS[i]) or '0')
    if current + n > limit then
      return i
    end
  end
end
for i = 1, count do
  redis.call('INCRBY', KEYS[i], n)
  redis.call('EXPIRE', KEYS[i], tonumber(ARGV[1 + count + i]))
end
return 0
"""

_HOUR_TTL = 2 * 3600
_DAY_TTL = 2 * 86400


@dataclass(slots=True)
class QuotaLimits:
    user_hourly: int | None
    user_daily: int | None
    provider_hourly: int | None
    provider_daily: int | None


def _windows(now: datetime | None = None) -> tuple[str, str]:
    now = now or datetime.now(UTC)
    return f"h:{now:%Y%m%d%H}", f"d:{now:%Y%m%d}"


def _keys(user_id: uuid.UUID, provider_id: uuid.UUID, now: datetime | None = None) -> list[str]:
    hour, day = _windows(now)
    return [
        Keys.user_quota(user_id, hour),
        Keys.user_quota(user_id, day),
        Keys.provider_quota(provider_id, hour),
        Keys.provider_quota(provider_id, day),
    ]


def _lim(v: int | None) -> int:
    return -1 if v is None else int(v)


async def remaining(user_id: uuid.UUID, provider_id: uuid.UUID, limits: QuotaLimits) -> int | None:
    """Largest amount that could be reserved right now (None = unlimited)."""
    keys = _keys(user_id, provider_id)
    values = await get_redis().mget(keys)
    caps = [limits.user_hourly, limits.user_daily, limits.provider_hourly, limits.provider_daily]
    rem: int | None = None
    for cap, used in zip(caps, values, strict=True):
        if cap is None:
            continue
        left = max(0, cap - int(used or 0))
        rem = left if rem is None else min(rem, left)
    return rem


async def reserve(
    attempt_id: uuid.UUID, user_id: uuid.UUID, provider_id: uuid.UUID, amount: int, limits: QuotaLimits
) -> bool:
    if amount <= 0:
        return True
    keys = _keys(user_id, provider_id)
    redis = get_redis()
    args = [amount, _lim(limits.user_hourly), _lim(limits.user_daily), _lim(limits.provider_hourly),
            _lim(limits.provider_daily), _HOUR_TTL, _DAY_TTL, _HOUR_TTL, _DAY_TTL]
    result = await redis.eval(_RESERVE_LUA, len(keys), *keys, *args)
    if int(result) != 0:
        return False
    await redis.set(
        f"reservation:{attempt_id}",
        json.dumps({"keys": keys, "amount": amount}),
        ex=_DAY_TTL,
    )
    return True


async def record_usage(attempt_id: uuid.UUID, used: int) -> None:
    if used > 0:
        await get_redis().incrby(f"reservation:{attempt_id}:used", used)


async def settle(attempt_id: uuid.UUID) -> int:
    """Refund the unused part of an attempt's reservation. Idempotent. Returns refunded amount."""
    redis = get_redis()
    raw = await redis.getdel(f"reservation:{attempt_id}")
    used = int(await redis.getdel(f"reservation:{attempt_id}:used") or 0)
    if not raw:
        return 0
    data = json.loads(raw)
    refund = max(0, int(data["amount"]) - used)
    if refund:
        pipe = redis.pipeline()
        for key in data["keys"]:
            pipe.decrby(key, refund)
        await pipe.execute()
    return refund


async def usage(user_id: uuid.UUID | None = None, provider_id: uuid.UUID | None = None) -> dict[str, int]:
    hour, day = _windows()
    redis = get_redis()
    if user_id is not None:
        h, d = await redis.mget(Keys.user_quota(user_id, hour), Keys.user_quota(user_id, day))
    else:
        h, d = await redis.mget(Keys.provider_quota(provider_id, hour), Keys.provider_quota(provider_id, day))
    return {"hour": max(0, int(h or 0)), "day": max(0, int(d or 0))}
