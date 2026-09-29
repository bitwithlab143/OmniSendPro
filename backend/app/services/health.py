"""Provider health scoring over rolling windows (ARCHITECTURE.md §17–§18, design DS-06, OQ-07).

Workers' results increment per-minute counters in Redis. Every health tick the scheduler sums the last
`provider_health_window_minutes` buckets, computes a window score, smooths it with the previous score and
applies the state machine. A provider is never disabled because of a single failure: it needs
`provider_disable_after_windows` consecutive bad windows, and re-enabling a DISABLED provider is manual.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import Keys, get_redis
from app.models import Provider, ProviderHealthLog
from app.models.enums import ProviderStatus
from app.services import audit
from app.services import settings as settings_service

FIELDS = ("attempts", "successes", "auth_failures", "connection_failures", "timeouts", "deferrals",
          "bounces", "complaints")
_BUCKET_TTL = 3 * 3600


def current_minute() -> int:
    return int(time.time() // 60)


async def bump(provider_id: object, **counts: int) -> None:
    counts = {k: v for k, v in counts.items() if v}
    if not counts:
        return
    key = Keys.provider_health(provider_id, current_minute())
    pipe = get_redis().pipeline()
    for field, value in counts.items():
        pipe.hincrby(key, field, value)
    pipe.expire(key, _BUCKET_TTL)
    await pipe.execute()


@dataclass(slots=True)
class Window:
    attempts: int = 0
    successes: int = 0
    auth_failures: int = 0
    connection_failures: int = 0
    timeouts: int = 0
    deferrals: int = 0
    bounces: int = 0
    complaints: int = 0

    def score(self) -> float:
        """0–100. Weights: systemic errors 1×, bounces 2×, deferrals 0.5×, complaints 50× (per attempt)."""
        if self.attempts <= 0:
            return 100.0
        n = float(self.attempts)
        penalty = (
            (self.auth_failures + self.connection_failures + self.timeouts) / n * 1.0
            + self.bounces / n * 2.0
            + self.deferrals / n * 0.5
            + self.complaints / n * 50.0
        )
        return max(0.0, min(100.0, 100.0 * (1.0 - penalty)))


async def read_window(provider_id: object, minutes: int) -> Window:
    minute = current_minute()
    pipe = get_redis().pipeline()
    for m in range(minute - minutes + 1, minute + 1):
        pipe.hgetall(Keys.provider_health(provider_id, m))
    window = Window()
    for bucket in await pipe.execute():
        for field in FIELDS:
            setattr(window, field, getattr(window, field) + int(bucket.get(field, 0)))
    return window


def next_status(current: ProviderStatus, score: float, bad_windows: int, cfg: dict) -> ProviderStatus:
    if current == ProviderStatus.DISABLED:
        return current  # manual re-enable only
    if score < cfg["provider_disable_below"] and bad_windows >= int(cfg["provider_disable_after_windows"]):
        return ProviderStatus.DISABLED
    if score < cfg["provider_degraded_below"]:
        return ProviderStatus.DEGRADED
    if score < cfg["provider_warning_below"]:
        return ProviderStatus.WARNING
    return ProviderStatus.ACTIVE


async def evaluate_all(db: AsyncSession) -> list[tuple[str, str, str]]:
    """Run one health tick. Returns [(provider_name, old_status, new_status)] for changed providers."""
    cfg = await settings_service.get_all(db)
    minutes = int(cfg["provider_health_window_minutes"])
    min_sample = int(cfg["provider_health_min_sample"])
    changes: list[tuple[str, str, str]] = []
    providers = (
        await db.execute(select(Provider).where(Provider.status != ProviderStatus.DISABLED))
    ).scalars().all()
    for provider in providers:
        window = await read_window(provider.id, minutes)
        if window.attempts < min_sample:
            continue  # not enough evidence — never act on a handful of failures
        window_score = window.score()
        smoothed = round(0.5 * provider.health_score + 0.5 * window_score, 2)
        bad = provider.consecutive_bad_windows + 1 if window_score < cfg["provider_disable_below"] else 0
        old = provider.status
        new = next_status(old, smoothed, bad, cfg)
        provider.health_score = smoothed
        provider.consecutive_bad_windows = bad
        db.add(
            ProviderHealthLog(
                provider_id=provider.id,
                window_start=datetime.fromtimestamp((current_minute() - minutes + 1) * 60, UTC),
                attempts=window.attempts,
                successes=window.successes,
                auth_failures=window.auth_failures,
                connection_failures=window.connection_failures,
                timeouts=window.timeouts,
                deferrals=window.deferrals,
                bounces=window.bounces,
                complaints=window.complaints,
                health_score=smoothed,
                status=new.value,
            )
        )
        if new != old:
            provider.status = new
            provider.status_reason = f"Automatic: health score {smoothed:.0f}"
            audit.record(db, audit.SYSTEM, "PROVIDER_STATE_CHANGED", "provider", provider.id,
                         old={"status": old.value}, new={"status": new.value, "health_score": smoothed})
            changes.append((provider.provider_name, old.value, new.value))
    await db.commit()
    return changes
