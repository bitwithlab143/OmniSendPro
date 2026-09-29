"""Runtime-tunable system settings stored in `system_settings` (design DS-05, ADR-008).

Values are cached in-process for a short TTL so hot paths (claim, results) avoid a DB read.
"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SystemSetting

DEFAULTS: dict[str, Any] = {
    # Retry (ADR-008)
    "retry_schedule_seconds": [30, 60, 120, 300, 600],
    "max_attempts": 5,
    # Jobs / leases (ADR-010)
    "job_lease_seconds": 120,
    # Worker liveness (OQ-06)
    "worker_heartbeat_interval_seconds": 10,
    "worker_warning_after_seconds": 30,
    "worker_offline_after_seconds": 90,
    # Provider health (DS-06, OQ-07)
    "provider_health_window_minutes": 5,
    "provider_health_min_sample": 20,
    "provider_warning_below": 80,
    "provider_degraded_below": 50,
    "provider_disable_below": 20,
    "provider_disable_after_windows": 3,
    "provider_degraded_throttle": 0.5,
    # Campaigns
    "default_batch_size": 1000,
    "allowed_batch_sizes": [500, 1000, 5000],
    "max_batch_size": 10000,
    "campaign_failure_threshold": 0.5,
    "require_from_domain_match": True,
    # Bounce / feedback-loop mailboxes (DS-16)
    "bounce_poll_interval_seconds": 60,
    # API keys (DS-17)
    "api_key_requests_per_minute": 600,
    "retention_events_days": 400,
    "retention_audit_days": 400,
    "retention_health_days": 90,
    "retention_heartbeat_days": 14,
}

SETTING_DESCRIPTIONS: dict[str, str] = {
    "retry_schedule_seconds": "Wait before each retry of a transient failure",
    "max_attempts": "Maximum delivery attempts per recipient / job",
    "job_lease_seconds": "How long a worker owns a claimed job without renewing",
    "worker_heartbeat_interval_seconds": "Interval workers send heartbeats",
    "worker_warning_after_seconds": "Missing heartbeat time before a worker is WARNING",
    "worker_offline_after_seconds": "Missing heartbeat time before a worker is OFFLINE",
    "provider_health_window_minutes": "Rolling window used for provider health scoring",
    "provider_health_min_sample": "Minimum attempts in a window before the score is updated",
    "provider_warning_below": "Health score below which a provider is WARNING",
    "provider_degraded_below": "Health score below which a provider is DEGRADED",
    "provider_disable_below": "Health score below which a window counts toward auto-disable",
    "provider_disable_after_windows": "Consecutive bad windows before auto-disable",
    "provider_degraded_throttle": "Fraction of provider limits used while DEGRADED",
    "default_batch_size": "Default campaign batch size",
    "allowed_batch_sizes": "Batch size presets offered to users",
    "max_batch_size": "Hard upper bound for any batch",
    "campaign_failure_threshold": "Failure ratio above which a finished campaign is FAILED",
    "require_from_domain_match": "Campaign From domain must match the provider's From domain",
    "bounce_poll_interval_seconds": "How often bounce/complaint mailboxes are read",
    "api_key_requests_per_minute": "Request limit per API key",
    "retention_events_days": "Keep delivery events for this many days",
    "retention_audit_days": "Keep audit logs for this many days (minimum 90)",
    "retention_health_days": "Keep provider health history for this many days",
    "retention_heartbeat_days": "Keep worker heartbeats for this many days",
}

_TTL = 15.0
_cache: dict[str, Any] = {}
_loaded_at = 0.0


def invalidate_cache() -> None:
    global _loaded_at
    _loaded_at = 0.0


async def get_all(db: AsyncSession) -> dict[str, Any]:
    global _cache, _loaded_at
    if time.monotonic() - _loaded_at < _TTL and _cache:
        return _cache
    rows = (await db.execute(select(SystemSetting))).scalars().all()
    merged = dict(DEFAULTS)
    merged.update({r.key: r.value for r in rows if r.key in DEFAULTS})
    _cache, _loaded_at = merged, time.monotonic()
    return merged


async def get(db: AsyncSession, key: str) -> Any:
    return (await get_all(db))[key]


def retry_delay(schedule: list[int], attempt: int) -> int:
    """Delay before retry number `attempt` (1-based); the last entry repeats."""
    if not schedule:
        return 60
    return int(schedule[min(max(attempt, 1), len(schedule)) - 1])


# Lower bounds that protect compliance data and keep the system usable.
MINIMUMS: dict[str, int] = {
    "retention_audit_days": 90,
    "retention_events_days": 30,
    "retention_health_days": 7,
    "retention_heartbeat_days": 1,
}


def validate_setting(key: str, value: Any) -> str | None:
    if key not in DEFAULTS:
        return f"Unknown setting {key!r}"
    default = DEFAULTS[key]
    if isinstance(default, bool):
        return None if isinstance(value, bool) else "Expected a boolean"
    if isinstance(default, list):
        if not isinstance(value, list) or not value or not all(isinstance(v, int) and v > 0 for v in value):
            return "Expected a non-empty list of positive integers"
        return None
    if isinstance(default, int | float):
        if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
            return "Expected a non-negative number"
        if key.endswith("_throttle") and not 0 < value <= 1:
            return "Throttle must be in (0, 1]"
        if key in MINIMUMS and value < MINIMUMS[key]:
            return f"Must be at least {MINIMUMS[key]}"
        return None
    return None
