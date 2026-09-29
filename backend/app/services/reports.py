"""Dashboards and reports (ARCHITECTURE.md §7, §42, §44; design DS-09, DS-12, DS-13).

Campaign counters are denormalised and eventually consistent; `email_events` is the source of truth for
time-ranged reports.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Campaign, CampaignRecipient, EmailEvent, Job, Provider, User, Worker
from app.models.enums import CampaignStatus, JobStatus, ProviderStatus, WorkerStatus
from app.services import jobs as job_service
from app.services import quotas

EVENT_KEYS = ["sent", "delivered", "failed", "bounced", "deferred", "complained", "unsubscribed", "suppressed"]


def start_of_day() -> datetime:
    now = datetime.now(UTC)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


async def event_counts(
    db: AsyncSession,
    since: datetime,
    until: datetime | None = None,
    user_id: uuid.UUID | None = None,
    campaign_id: uuid.UUID | None = None,
    provider_id: uuid.UUID | None = None,
) -> dict[str, int]:
    conds = [EmailEvent.created_at >= since]
    if until:
        conds.append(EmailEvent.created_at < until)
    if user_id:
        conds.append(EmailEvent.user_id == user_id)
    if campaign_id:
        conds.append(EmailEvent.campaign_id == campaign_id)
    if provider_id:
        conds.append(EmailEvent.provider_id == provider_id)
    rows = await db.execute(
        select(EmailEvent.event_type, func.count()).where(and_(*conds)).group_by(EmailEvent.event_type)
    )
    counts = {k: 0 for k in EVENT_KEYS}
    for etype, n in rows:
        counts[etype.value] = int(n)
    return counts


async def admin_dashboard(db: AsyncSession) -> dict[str, Any]:
    workers = (await db.execute(select(Worker))).scalars().all()
    providers = (await db.execute(select(Provider))).scalars().all()
    queue_rows = await db.execute(select(Job.status, func.count(), func.coalesce(func.sum(Job.batch_size), 0))
                                  .group_by(Job.status))
    queue = {s.value: {"jobs": 0, "recipients": 0} for s in JobStatus}
    for status, n, recipients in queue_rows:
        queue[status.value] = {"jobs": int(n), "recipients": int(recipients)}
    campaign_rows = await db.execute(select(Campaign.status, func.count()).group_by(Campaign.status))
    campaigns = {s.value: 0 for s in CampaignStatus}
    for status, n in campaign_rows:
        campaigns[status.value] = int(n)
    today = await event_counts(db, start_of_day())
    return {
        "active_workers": sum(1 for w in workers if w.status == WorkerStatus.ONLINE and not w.disabled),
        "total_workers": len(workers),
        "online_providers": sum(1 for p in providers if p.status != ProviderStatus.DISABLED),
        "total_providers": len(providers),
        "queue_depth": queue["pending"]["jobs"] + queue["retry"]["jobs"],
        "queue_recipients": queue["pending"]["recipients"] + queue["retry"]["recipients"],
        "emails_per_second": await job_service.sending_rate(),
        "queue": queue,
        "campaigns": campaigns,
        "today": today,
        "workers": [
            {"id": str(w.id), "worker_id": w.worker_id, "name": w.name, "status": w.status.value,
             "cpu": w.cpu, "memory": w.memory, "active_jobs": w.active_jobs, "current_rate": w.current_rate,
             "capacity": w.capacity, "disabled": w.disabled,
             "last_heartbeat_at": w.last_heartbeat_at.isoformat() if w.last_heartbeat_at else None}
            for w in workers
        ],
        "providers": [
            {"id": str(p.id), "provider_name": p.provider_name, "status": p.status.value,
             "health_score": p.health_score}
            for p in providers
        ],
    }


async def user_dashboard(db: AsyncSession, user: User) -> dict[str, Any]:
    today = await event_counts(db, start_of_day(), user_id=user.id)
    running = [CampaignStatus.QUEUED, CampaignStatus.PROCESSING, CampaignStatus.PAUSED]
    # "Today's sending" (§7): campaigns still running plus everything started today.
    todays = (
        await db.execute(
            select(Campaign).where(
                Campaign.user_id == user.id,
                or_(Campaign.status.in_(running), Campaign.started_at >= start_of_day()),
            )
        )
    ).scalars().all()
    active = [c for c in todays if c.status in running]
    assigned = sum(c.total_recipients for c in todays)
    processed = sum(c.sent + c.failed + c.bounced + c.skipped_suppressed for c in todays)
    limits = user.limits
    usage = await quotas.usage(user_id=user.id)
    rate = 0.0
    for c in active:
        rate += await job_service.sending_rate(c.id)
    return {
        "today": today,
        "active_campaigns": len(active),
        "assigned": assigned,
        "processed": processed,
        "delivered": sum(c.delivered for c in todays),
        "failed": sum(c.failed + c.bounced for c in todays),
        "remaining": max(0, assigned - processed),
        "current_speed": round(rate, 2),
        "quota": {
            "daily_limit": limits.daily_limit if limits else None,
            "hourly_limit": limits.hourly_limit if limits else None,
            "used_today": usage["day"],
            "used_this_hour": usage["hour"],
        },
    }


async def campaign_report(db: AsyncSession, campaign: Campaign) -> dict[str, Any]:
    status_rows = await db.execute(
        select(CampaignRecipient.status, func.count())
        .where(CampaignRecipient.campaign_id == campaign.id)
        .group_by(CampaignRecipient.status)
    )
    by_status = {s.value: int(n) for s, n in status_rows}
    error_rows = await db.execute(
        select(EmailEvent.error_code, func.count())
        .where(EmailEvent.campaign_id == campaign.id,
               EmailEvent.event_type.in_(["bounced", "failed"]), EmailEvent.error_code.is_not(None))
        .group_by(EmailEvent.error_code)
        .order_by(func.count().desc())
        .limit(10)
    )
    bucket = func.date_trunc("hour", EmailEvent.created_at)
    series_rows = await db.execute(
        select(bucket.label("hour"), EmailEvent.event_type, func.count())
        .where(EmailEvent.campaign_id == campaign.id, EmailEvent.created_at >= datetime.now(UTC) - timedelta(days=7))
        .group_by("hour", EmailEvent.event_type)
        .order_by("hour")
    )
    series: dict[str, dict[str, Any]] = {}
    for hour, etype, n in series_rows:
        key = hour.isoformat()
        series.setdefault(key, {"hour": key, **{k: 0 for k in EVENT_KEYS}})[etype.value] = int(n)
    return {
        "campaign_id": str(campaign.id),
        "by_status": by_status,
        "top_errors": [{"code": code, "count": int(n)} for code, n in error_rows],
        "hourly": list(series.values()),
    }


async def breakdown(db: AsyncSession, since: datetime, until: datetime, group: str) -> list[dict[str, Any]]:
    col = {"user": EmailEvent.user_id, "provider": EmailEvent.provider_id}[group]
    rows = await db.execute(
        select(col, EmailEvent.event_type, func.count())
        .where(EmailEvent.created_at >= since, EmailEvent.created_at < until)
        .group_by(col, EmailEvent.event_type)
    )
    data: dict[Any, dict[str, int]] = {}
    for key, etype, n in rows:
        data.setdefault(key, {k: 0 for k in EVENT_KEYS})[etype.value] = int(n)
    names: dict[Any, str] = {}
    ids = [k for k in data if k]
    if ids:
        if group == "user":
            for u in (await db.execute(select(User).where(User.id.in_(ids)))).scalars():
                names[u.id] = u.username
        else:
            for p in (await db.execute(select(Provider).where(Provider.id.in_(ids)))).scalars():
                names[p.id] = p.provider_name
    return sorted(
        ({"id": str(k) if k else None, "name": names.get(k, "—"), **v} for k, v in data.items()),
        key=lambda r: -r["sent"],
    )


async def daily_series(db: AsyncSession, since: datetime, until: datetime,
                       user_id: uuid.UUID | None = None) -> list[dict[str, Any]]:
    day = func.date_trunc("day", EmailEvent.created_at)
    conds = [EmailEvent.created_at >= since, EmailEvent.created_at < until]
    if user_id:
        conds.append(EmailEvent.user_id == user_id)
    rows = await db.execute(
        select(day.label("day"), EmailEvent.event_type, func.count()).where(*conds)
        .group_by("day", EmailEvent.event_type).order_by("day")
    )
    series: dict[str, dict[str, Any]] = {}
    for d, etype, n in rows:
        key = d.date().isoformat()
        series.setdefault(key, {"day": key, **{k: 0 for k in EVENT_KEYS}})[etype.value] = int(n)
    return list(series.values())
