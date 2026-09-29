"""Campaign lifecycle: readiness, state machine, batch building, completion (DS-04, DS-05, §13–§14, §50)."""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, exists, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError, conflict
from app.core.ids import uuid7
from app.models import (
    Campaign,
    CampaignBatch,
    CampaignRecipient,
    Job,
    Provider,
    ProviderAssignment,
    RecipientImport,
    User,
    UserLimits,
)
from app.models.enums import (
    OPEN_JOB_STATUSES,
    SELECTABLE_PROVIDER_STATUSES,
    AssignmentStatus,
    CampaignStatus,
    JobStatus,
    RecipientStatus,
)
from app.services import audit
from app.services import settings as settings_service
from app.services.audit import RequestContext

EDITABLE = {CampaignStatus.DRAFT, CampaignStatus.READY}
RUNNING = {CampaignStatus.QUEUED, CampaignStatus.PROCESSING}
BUILD_CHUNK_TARGET = 50_000


def now() -> datetime:
    return datetime.now(UTC)


def email_domain(email: str | None) -> str:
    return (email or "").rsplit("@", 1)[-1].lower()


def readiness(campaign: Campaign) -> list[str]:
    """Missing requirements for DRAFT → READY (empty list = ready)."""
    missing = []
    if not campaign.subject.strip():
        missing.append("subject")
    if not campaign.from_email:
        missing.append("from_email")
    if not (campaign.html_body or campaign.text_body):
        missing.append("content")
    if campaign.provider_id is None:
        missing.append("provider")
    if campaign.total_recipients <= 0:
        missing.append("recipients")
    return missing


def refresh_readiness(campaign: Campaign) -> None:
    if campaign.status in EDITABLE:
        campaign.status = CampaignStatus.READY if not readiness(campaign) else CampaignStatus.DRAFT


def ensure_editable(campaign: Campaign) -> None:
    if campaign.status not in EDITABLE:
        raise conflict(f"Campaign is {campaign.status.value} and can no longer be edited", "campaign_locked")


async def active_assignment(db: AsyncSession, user_id: uuid.UUID, provider_id: uuid.UUID) -> bool:
    return bool(
        (
            await db.execute(
                select(
                    exists().where(
                        ProviderAssignment.user_id == user_id,
                        ProviderAssignment.provider_id == provider_id,
                        ProviderAssignment.status == AssignmentStatus.ACTIVE,
                    )
                )
            )
        ).scalar()
    )


async def validate_provider_choice(db: AsyncSession, user_id: uuid.UUID, provider_id: uuid.UUID) -> Provider:
    provider = await db.get(Provider, provider_id)
    if provider is None or not await active_assignment(db, user_id, provider_id):
        raise ApiError(422, "provider_not_assigned", "Provider is not assigned to this user")
    return provider


async def user_limits(db: AsyncSession, user_id: uuid.UUID) -> UserLimits | None:
    return (await db.execute(select(UserLimits).where(UserLimits.user_id == user_id))).scalar_one_or_none()


async def effective_batch_size(db: AsyncSession, campaign: Campaign, provider: Provider) -> int:
    """min(user choice, user max batch, system max, provider/user hourly+daily caps) — §13."""
    cfg = await settings_service.get_all(db)
    limits = await user_limits(db, campaign.user_id)
    caps = [campaign.batch_size, int(cfg["max_batch_size"])]
    if limits:
        caps += [v for v in (limits.max_batch_size, limits.hourly_limit, limits.daily_limit) if v]
    caps += [v for v in (provider.hourly_limit, provider.daily_limit) if v]
    return max(1, min(caps))


async def _wake_scheduler() -> None:
    from app.scheduler.loop import wake  # local import: the scheduler imports this module

    await wake()


# --------------------------------------------------------------------------- state transitions


async def start(db: AsyncSession, campaign: Campaign, ctx: RequestContext, consent: bool) -> Campaign:
    refresh_readiness(campaign)
    if campaign.status != CampaignStatus.READY:
        missing = readiness(campaign)
        raise ApiError(409, "campaign_not_ready",
                       "Campaign is not ready to start" if missing else f"Campaign is {campaign.status.value}",
                       {"missing": missing})
    busy = (await db.execute(select(RecipientImport.id).where(
        RecipientImport.campaign_id == campaign.id, RecipientImport.status.in_(("queued", "processing"))).limit(1))
    ).scalar_one_or_none()
    if busy is not None:
        raise ApiError(409, "import_in_progress", "Wait for the recipient import to finish before starting")
    if not consent:
        raise ApiError(422, "consent_required",
                       "Confirm that all recipients opted in to receive this email")
    assert campaign.provider_id is not None
    provider = await validate_provider_choice(db, campaign.user_id, campaign.provider_id)
    if provider.status not in SELECTABLE_PROVIDER_STATUSES:
        raise ApiError(409, "provider_unavailable", f"Provider is {provider.status.value}")
    cfg = await settings_service.get_all(db)
    if cfg["require_from_domain_match"] and email_domain(campaign.from_email) != email_domain(provider.from_email):
        raise ApiError(422, "from_domain_mismatch",
                       f"From address must use the provider's domain ({email_domain(provider.from_email)})")
    owner = await db.get(User, campaign.user_id)
    if owner is None or owner.status.value != "active":
        raise ApiError(409, "user_inactive", "The campaign owner account is not active")
    limits = await user_limits(db, campaign.user_id)
    if limits and limits.max_recipients_per_campaign and campaign.total_recipients > limits.max_recipients_per_campaign:
        raise ApiError(422, "recipient_limit",
                       f"Campaign exceeds the per-campaign limit of {limits.max_recipients_per_campaign} recipients")

    campaign.batch_size = await effective_batch_size(db, campaign, provider)
    campaign.status = CampaignStatus.QUEUED
    campaign.started_at = now()
    campaign.consent_confirmed_at = now()
    campaign.consent_confirmed_by = ctx.actor_id
    campaign.last_error = None
    audit.record(db, ctx, "CAMPAIGN_STARTED", "campaign", campaign.id,
                 new={"batch_size": campaign.batch_size, "recipients": campaign.total_recipients})
    await db.commit()
    await db.refresh(campaign)
    await _wake_scheduler()
    return campaign


async def pause(db: AsyncSession, campaign: Campaign, ctx: RequestContext) -> Campaign:
    if campaign.status not in RUNNING:
        raise conflict(f"Cannot pause a {campaign.status.value} campaign", "invalid_transition")
    campaign.status = CampaignStatus.PAUSED
    campaign.paused_at = now()
    audit.record(db, ctx, "CAMPAIGN_PAUSED", "campaign", campaign.id)
    await db.commit()
    await db.refresh(campaign)
    return campaign


async def resume(db: AsyncSession, campaign: Campaign, ctx: RequestContext) -> Campaign:
    if campaign.status != CampaignStatus.PAUSED:
        raise conflict(f"Cannot resume a {campaign.status.value} campaign", "invalid_transition")
    if campaign.provider_id:
        provider = await db.get(Provider, campaign.provider_id)
        if provider is None or provider.status not in SELECTABLE_PROVIDER_STATUSES:
            raise ApiError(409, "provider_unavailable", "Provider is not available")
    campaign.status = CampaignStatus.PROCESSING if campaign.batches_built else CampaignStatus.QUEUED
    campaign.paused_at = None
    audit.record(db, ctx, "CAMPAIGN_RESUMED", "campaign", campaign.id)
    await db.commit()
    await db.refresh(campaign)
    await _wake_scheduler()
    return campaign


async def cancel(db: AsyncSession, campaign: Campaign, ctx: RequestContext) -> Campaign:
    if campaign.status not in RUNNING | {CampaignStatus.PAUSED, CampaignStatus.DRAFT, CampaignStatus.READY}:
        raise conflict(f"Cannot cancel a {campaign.status.value} campaign", "invalid_transition")
    campaign.status = CampaignStatus.CANCELLED
    campaign.cancelled_at = now()
    # Unclaimed work is cancelled now; jobs currently leased by a worker are told to stop on their next
    # lease renewal and cancel their remaining recipients when they report back (jobs.finalize).
    await db.execute(
        update(Job)
        .where(Job.campaign_id == campaign.id, Job.status.in_([JobStatus.PENDING, JobStatus.RETRY]))
        .values(status=JobStatus.CANCELLED, completed_at=now())
    )
    cancelled_batches = select(Job.batch_id).where(Job.campaign_id == campaign.id,
                                                   Job.status == JobStatus.CANCELLED)
    await db.execute(
        update(CampaignRecipient)
        .where(
            CampaignRecipient.campaign_id == campaign.id,
            (CampaignRecipient.status == RecipientStatus.PENDING)
            | CampaignRecipient.batch_id.in_(cancelled_batches),
            CampaignRecipient.status.in_([RecipientStatus.PENDING, RecipientStatus.QUEUED, RecipientStatus.DEFERRED]),
        )
        .values(status=RecipientStatus.CANCELLED)
    )
    audit.record(db, ctx, "CAMPAIGN_CANCELLED", "campaign", campaign.id)
    await db.commit()
    await db.refresh(campaign)
    return campaign


# --------------------------------------------------------------------------- batch building (scheduler)


@dataclass(slots=True)
class BuildResult:
    batches: int
    queued: int
    suppressed: int


async def build_batches(db: AsyncSession, campaign_id: uuid.UUID) -> BuildResult | None:
    """Suppression check + split into batches + create jobs, in set-based SQL (no recipients in memory).

    Idempotent and safe to run concurrently: the campaign row is locked with SKIP LOCKED.
    """
    campaign = (
        await db.execute(
            select(Campaign).where(Campaign.id == campaign_id).with_for_update(skip_locked=True, of=Campaign)
        )
    ).scalar_one_or_none()
    if campaign is None or campaign.batches_built or campaign.status not in RUNNING:
        await db.rollback()
        return None
    provider = await db.get(Provider, campaign.provider_id)
    if provider is None:
        await db.rollback()
        return None
    cfg = await settings_service.get_all(db)
    batch_size = await effective_batch_size(db, campaign, provider)

    # 1. Suppression check (set-based). Scope: global entries + the sending user's own list.
    suppressed_rows = await db.execute(
        text(
            """
            WITH hit AS (
                UPDATE campaign_recipients r
                   SET status = 'suppressed', last_event_at = now()
                 WHERE r.campaign_id = :cid AND r.status = 'pending'
                   AND EXISTS (SELECT 1 FROM suppression_list s
                                WHERE s.email_normalized = r.email_normalized
                                  AND (s.scope_user_id IS NULL OR s.scope_user_id = :uid))
             RETURNING r.id
            )
            INSERT INTO email_events (campaign_id, recipient_id, provider_id, user_id, event_type, created_at)
            SELECT :cid, id, :pid, :uid, 'suppressed', now() FROM hit
            RETURNING id
            """
        ),
        {"cid": campaign.id, "uid": campaign.user_id, "pid": provider.id},
    )
    suppressed = len(suppressed_rows.all())

    # 2. Batch the remaining pending recipients in chunks (each chunk = whole batches).
    chunk = batch_size * max(1, BUILD_CHUNK_TARGET // batch_size)
    seq = int(
        (await db.execute(select(func.coalesce(func.max(CampaignBatch.sequence_no), 0))
                          .where(CampaignBatch.campaign_id == campaign.id))).scalar_one()
    )
    total_batches = 0
    total_queued = 0
    while True:
        pending = int(
            (
                await db.execute(
                    text("SELECT count(*) FROM (SELECT 1 FROM campaign_recipients WHERE campaign_id = :cid "
                         "AND status = 'pending' LIMIT :lim) t"),
                    {"cid": campaign.id, "lim": chunk},
                )
            ).scalar_one()
        )
        if pending == 0:
            break
        n_batches = math.ceil(pending / batch_size)
        batches = []
        for i in range(n_batches):
            size = min(batch_size, pending - i * batch_size)
            batches.append(CampaignBatch(id=uuid7(), campaign_id=campaign.id, sequence_no=seq + i + 1,
                                         recipient_count=size))
        db.add_all(batches)
        await db.flush()
        await db.execute(
            text(
                """
                WITH numbered AS (
                    SELECT id, (row_number() OVER (ORDER BY id) - 1) / :bs AS grp
                      FROM campaign_recipients
                     WHERE campaign_id = :cid AND status = 'pending'
                     ORDER BY id
                     LIMIT :lim
                )
                UPDATE campaign_recipients r
                   SET batch_id = b.id, status = 'queued'
                  FROM numbered n
                  JOIN campaign_batches b ON b.campaign_id = :cid AND b.sequence_no = n.grp + :seq0
                 WHERE r.id = n.id
                """
            ),
            {"cid": campaign.id, "bs": batch_size, "lim": chunk, "seq0": seq + 1},
        )
        max_attempts = int(cfg["max_attempts"])
        start_at = campaign.scheduled_at if campaign.scheduled_at and campaign.scheduled_at > now() else now()
        db.add_all(
            [
                Job(
                    id=uuid7(),
                    campaign_id=campaign.id,
                    user_id=campaign.user_id,
                    batch_id=b.id,
                    provider_id=provider.id,
                    batch_size=b.recipient_count,
                    max_attempts=max_attempts,
                    idempotency_key=f"{campaign.id}:{b.sequence_no}:0",
                    available_at=start_at,
                )
                for b in batches
            ]
        )
        seq += n_batches
        total_batches += n_batches
        total_queued += pending
        await db.flush()

    campaign.batch_size = batch_size
    campaign.skipped_suppressed += suppressed
    campaign.batches_built = True
    await db.commit()
    return BuildResult(batches=total_batches, queued=total_queued, suppressed=suppressed)


async def campaigns_needing_batches(db: AsyncSession) -> list[uuid.UUID]:
    rows = await db.execute(
        select(Campaign.id).where(Campaign.status.in_(list(RUNNING)), Campaign.batches_built.is_(False)).limit(50)
    )
    return list(rows.scalars())


# --------------------------------------------------------------------------- completion


async def check_completion(db: AsyncSession, campaign_id: uuid.UUID) -> CampaignStatus | None:
    campaign = (
        await db.execute(
            select(Campaign).where(Campaign.id == campaign_id).with_for_update(skip_locked=True, of=Campaign)
        )
    ).scalar_one_or_none()
    if campaign is None or campaign.status not in RUNNING or not campaign.batches_built:
        await db.rollback()
        return None
    open_jobs = (
        await db.execute(
            select(exists().where(and_(Job.campaign_id == campaign.id, Job.status.in_(list(OPEN_JOB_STATUSES)))))
        )
    ).scalar()
    if open_jobs:
        await db.rollback()
        return None
    cfg = await settings_service.get_all(db)
    bad = campaign.failed + campaign.bounced
    good = campaign.sent
    ratio = bad / max(1, good + bad)
    if (good == 0 and bad > 0) or ratio > float(cfg["campaign_failure_threshold"]):
        campaign.status = CampaignStatus.FAILED
        campaign.last_error = f"{bad} of {good + bad} messages failed"
    else:
        campaign.status = CampaignStatus.COMPLETED
    campaign.completed_at = now()
    await db.commit()
    return campaign.status


async def running_campaign_ids(db: AsyncSession) -> list[uuid.UUID]:
    rows = await db.execute(
        select(Campaign.id).where(Campaign.status.in_(list(RUNNING)), Campaign.batches_built.is_(True)).limit(500)
    )
    return list(rows.scalars())


def campaign_snapshot(c: Campaign) -> dict[str, Any]:
    return {
        "name": c.name,
        "subject": c.subject,
        "from_name": c.from_name,
        "from_email": c.from_email,
        "reply_to": c.reply_to,
        "provider_id": c.provider_id,
        "batch_size": c.batch_size,
        "user_id": c.user_id,
        "scheduled_at": c.scheduled_at,
    }


def progress(c: Campaign) -> dict[str, Any]:
    done = c.sent + c.failed + c.bounced + c.skipped_suppressed
    remaining = max(0, c.total_recipients - done)
    pct = round(100.0 * done / c.total_recipients, 1) if c.total_recipients else 0.0
    return {"done": done, "remaining": remaining, "percent": min(100.0, pct)}


def retry_available_at(schedule: list[int], attempt: int) -> datetime:
    return now() + timedelta(seconds=settings_service.retry_delay(schedule, attempt))
