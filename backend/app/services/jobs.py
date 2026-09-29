"""Job claim / lease / results / ack / failure / release + lease recovery.

Design refs: DS-05 (job state machine), ADR-002 (Postgres is the job system of record),
ADR-008 (retry classification), ADR-010 (lease-based atomic claim), §31 (idempotency), §47 (worker crash).

Idempotency model
-----------------
* A job can only be owned by one attempt at a time (`jobs.current_attempt_id`); every write from a worker
  must present the current attempt id, stale attempts get 409.
* A recipient result is applied only while the recipient is still `queued` in that job's batch, so a
  retried/duplicated report can never double-count, and a recipient reported once is never re-sent.
* Workers report results incrementally, so after a crash only the few in-flight messages can be
  re-attempted by the next claimer (at-least-once with a very small window).
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import ApiError
from app.core.ids import uuid7
from app.core.redis import Keys, get_redis
from app.core.security import decrypt_secret, sign_value
from app.models import (
    Campaign,
    CampaignBatch,
    CampaignRecipient,
    Job,
    JobAttempt,
    Provider,
    UserLimits,
    Worker,
)
from app.models.enums import (
    SENDABLE_PROVIDER_STATUSES,
    AttemptOutcome,
    CampaignStatus,
    JobStatus,
    ProviderStatus,
    RecipientStatus,
    SuppressionType,
)
from app.services import campaigns as campaign_service
from app.services import health, quotas, suppression
from app.services import settings as settings_service

CLAIM_CANDIDATES = 25
UNSUBSCRIBE_PURPOSE = "unsubscribe"


def now() -> datetime:
    return datetime.now(UTC)


def unsubscribe_token(campaign_id: uuid.UUID, recipient_id: int) -> str:
    return sign_value(f"{campaign_id}:{recipient_id}", UNSUBSCRIBE_PURPOSE)


def unsubscribe_url(campaign_id: uuid.UUID, recipient_id: int) -> str:
    base = get_settings().public_base_url.rstrip("/")
    return f"{base}/api/v1/u/{unsubscribe_token(campaign_id, recipient_id)}"


# --------------------------------------------------------------------------- claim


@dataclass(slots=True)
class ClaimedJob:
    job: Job
    attempt: JobAttempt
    campaign: Campaign
    provider: Provider
    recipients: list[dict[str, Any]]
    per_second: int | None


async def _limits_for(db: AsyncSession, job: Job, provider: Provider) -> quotas.QuotaLimits:
    limits = (await db.execute(select(UserLimits).where(UserLimits.user_id == job.user_id))).scalar_one_or_none()
    return quotas.QuotaLimits(
        user_hourly=limits.hourly_limit if limits else None,
        user_daily=limits.daily_limit if limits else None,
        provider_hourly=provider.hourly_limit,
        provider_daily=provider.daily_limit,
    )


async def claim(db: AsyncSession, worker: Worker) -> ClaimedJob | None:
    cfg = await settings_service.get_all(db)
    lease_seconds = int(cfg["job_lease_seconds"])
    candidates = (
        await db.execute(
            select(Job)
            .join(Campaign, Campaign.id == Job.campaign_id)
            .join(Provider, Provider.id == Job.provider_id)
            .where(
                Job.status.in_([JobStatus.PENDING, JobStatus.RETRY]),
                Job.available_at <= func.now(),
                Campaign.status.in_([CampaignStatus.QUEUED, CampaignStatus.PROCESSING]),
                Provider.status.in_(list(SENDABLE_PROVIDER_STATUSES)),
            )
            .order_by(Job.available_at, Job.id)
            .limit(CLAIM_CANDIDATES)
            .with_for_update(of=Job, skip_locked=True)
        )
    ).scalars().all()

    for job in candidates:
        provider = await db.get(Provider, job.provider_id)
        campaign = await db.get(Campaign, job.campaign_id)
        assert provider is not None and campaign is not None
        limits = await _limits_for(db, job, provider)

        # Just-before-send suppression check (catches suppressions added after batch build).
        rows = (
            await db.execute(
                select(CampaignRecipient.id, CampaignRecipient.email, CampaignRecipient.email_normalized,
                       CampaignRecipient.variables)
                .where(CampaignRecipient.batch_id == job.batch_id,
                       CampaignRecipient.status == RecipientStatus.QUEUED)
                .order_by(CampaignRecipient.id)
            )
        ).all()
        blocked = await suppression.suppressed_set(db, job.user_id, [r.email_normalized for r in rows])
        if blocked:
            await _mark_suppressed(db, job, [r.id for r in rows if r.email_normalized in blocked])
        sendable = [r for r in rows if r.email_normalized not in blocked]

        if not sendable:
            job.status = JobStatus.COMPLETED
            job.completed_at = now()
            await _set_batch_status(db, job.batch_id, JobStatus.COMPLETED)
            continue

        attempt_id = uuid7()
        if not await quotas.reserve(attempt_id, job.user_id, job.provider_id, len(sendable), limits):
            continue  # over quota right now — leave for later, try the next candidate

        attempt_no = job.attempts + 1
        attempt = JobAttempt(id=attempt_id, job_id=job.id, attempt_no=attempt_no, worker_id=worker.id)
        db.add(attempt)
        job.status = JobStatus.CLAIMED
        job.worker_id = worker.id
        job.attempts = attempt_no
        job.current_attempt_id = attempt_id
        job.lease_expires_at = now() + timedelta(seconds=lease_seconds)
        job.started_at = job.started_at or now()
        await _set_batch_status(db, job.batch_id, JobStatus.CLAIMED)
        if campaign.status == CampaignStatus.QUEUED:
            campaign.status = CampaignStatus.PROCESSING
        await db.commit()

        per_second = _per_second(provider, limits_obj=await _user_limits(db, job.user_id), cfg=cfg)
        recipients = [
            {
                "id": r.id,
                "email": r.email,
                "variables": r.variables or {},
                "unsubscribe_url": unsubscribe_url(job.campaign_id, r.id),
            }
            for r in sendable
        ]
        return ClaimedJob(job, attempt, campaign, provider, recipients, per_second)

    await db.commit()
    return None


async def _user_limits(db: AsyncSession, user_id: uuid.UUID) -> UserLimits | None:
    return (await db.execute(select(UserLimits).where(UserLimits.user_id == user_id))).scalar_one_or_none()


def _per_second(provider: Provider, limits_obj: UserLimits | None, cfg: dict[str, Any]) -> int | None:
    caps = [v for v in (provider.per_second_limit, limits_obj.per_second_limit if limits_obj else None) if v]
    rate = min(caps) if caps else None
    if rate and provider.status == ProviderStatus.DEGRADED:
        rate = max(1, int(rate * float(cfg["provider_degraded_throttle"])))
    return rate


async def _set_batch_status(db: AsyncSession, batch_id: uuid.UUID, status: JobStatus) -> None:
    await db.execute(update(CampaignBatch).where(CampaignBatch.id == batch_id).values(status=status))


async def _mark_suppressed(db: AsyncSession, job: Job, recipient_ids: list[int]) -> None:
    result = await db.execute(
        text(
            """
            WITH hit AS (
              UPDATE campaign_recipients SET status = 'suppressed', last_event_at = now()
               WHERE id = ANY(:ids) AND status = 'queued' RETURNING id
            )
            INSERT INTO email_events (campaign_id, job_id, recipient_id, provider_id, user_id, event_type, created_at)
            SELECT :cid, :jid, id, :pid, :uid, 'suppressed', now() FROM hit RETURNING id
            """
        ),
        {"ids": recipient_ids, "cid": job.campaign_id, "jid": job.id, "pid": job.provider_id, "uid": job.user_id},
    )
    n = len(result.all())
    if n:
        await db.execute(
            update(Campaign).where(Campaign.id == job.campaign_id)
            .values(skipped_suppressed=Campaign.skipped_suppressed + n)
        )


def claim_payload(claimed: ClaimedJob) -> dict[str, Any]:
    c, p, j = claimed.campaign, claimed.provider, claimed.job
    secret = decrypt_secret(p.credential.encrypted_secret) if p.credential else None
    return {
        "job_id": str(j.id),
        "attempt_id": str(claimed.attempt.id),
        "attempt_no": claimed.attempt.attempt_no,
        "lease_expires_at": j.lease_expires_at.isoformat() if j.lease_expires_at else None,
        "campaign": {
            "id": str(c.id),
            "subject": c.subject,
            "from_name": c.from_name or p.from_name,
            "from_email": c.from_email or p.from_email,
            "reply_to": c.reply_to,
            "html_body": c.html_body,
            "text_body": c.text_body,
        },
        "provider": {
            "id": str(p.id),
            "type": p.type.value,
            "host": p.host,
            "port": p.port,
            "username": p.username,
            "password": secret,
            "tls_mode": p.tls_mode.value,
            "max_connections": p.max_connections,
        },
        "rate_limit_per_second": claimed.per_second,
        "recipients": claimed.recipients,
    }


# --------------------------------------------------------------------------- ownership checks


async def _owned_job(db: AsyncSession, worker: Worker, job_id: uuid.UUID, attempt_id: uuid.UUID) -> Job:
    job = (await db.execute(select(Job).where(Job.id == job_id).with_for_update())).scalar_one_or_none()
    if job is None:
        raise ApiError(404, "not_found", "Job not found")
    if (
        job.worker_id != worker.id
        or job.current_attempt_id != attempt_id
        or job.status not in (JobStatus.CLAIMED, JobStatus.PROCESSING)
    ):
        raise ApiError(409, "stale_attempt", "This attempt no longer owns the job")
    return job


async def renew_lease(db: AsyncSession, worker: Worker, job_id: uuid.UUID, attempt_id: uuid.UUID) -> dict[str, Any]:
    job = await _owned_job(db, worker, job_id, attempt_id)
    cfg = await settings_service.get_all(db)
    job.lease_expires_at = now() + timedelta(seconds=int(cfg["job_lease_seconds"]))
    if job.status == JobStatus.CLAIMED:
        job.status = JobStatus.PROCESSING
        await _set_batch_status(db, job.batch_id, JobStatus.PROCESSING)
    campaign = await db.get(Campaign, job.campaign_id)
    provider = await db.get(Provider, job.provider_id)
    stop = (
        campaign is None
        or campaign.status not in (CampaignStatus.QUEUED, CampaignStatus.PROCESSING)
        or provider is None
        or provider.status == ProviderStatus.DISABLED
    )
    await db.commit()
    return {"action": "stop" if stop else "continue", "lease_expires_at": job.lease_expires_at.isoformat()}


# --------------------------------------------------------------------------- results


HARD_BOUNCE_CODES = {550, 551, 553}


def classify(result: dict[str, Any]) -> str:
    """Map a worker-reported result to sent | deferred | bounced | failed | invalid (ADR-008)."""
    if result["outcome"] == "sent":
        return "sent"
    category = result.get("category") or ""
    code = result.get("smtp_code") or 0
    enhanced = str(result.get("enhanced_code") or "")
    if category == "invalid":
        return "invalid"
    if category in ("transient", "timeout", "connection") or 400 <= code < 500 or enhanced.startswith("4."):
        return "deferred"
    if enhanced.startswith("5.1.") or code in HARD_BOUNCE_CODES and not enhanced.startswith("5.7."):
        return "bounced"
    return "failed"


@dataclass(slots=True)
class ResultSummary:
    accepted: int = 0
    ignored: int = 0
    sent: int = 0
    deferred: int = 0
    bounced: int = 0
    failed: int = 0


async def record_results(
    db: AsyncSession, worker: Worker, job_id: uuid.UUID, attempt_id: uuid.UUID, results: list[dict[str, Any]]
) -> ResultSummary:
    job = await _owned_job(db, worker, job_id, attempt_id)
    provider = await db.get(Provider, job.provider_id)
    assert provider is not None
    # Providers without a delivery webhook cannot confirm delivery: SMTP acceptance counts as delivered.
    accept_is_delivery = provider.webhook_secret_encrypted is None

    groups: dict[str, list[dict[str, Any]]] = {"sent": [], "deferred": [], "bounced": [], "failed": [], "invalid": []}
    for r in results:
        groups[classify(r)].append(r)

    summary = ResultSummary()
    applied: dict[str, list[tuple[int, str]]] = {}
    for kind, items in groups.items():
        if not items:
            continue
        status = {"sent": "sent", "deferred": "deferred", "bounced": "bounced", "failed": "failed",
                  "invalid": "failed"}[kind]
        rows = (
            await db.execute(
                text(
                    """
                    UPDATE campaign_recipients r
                       SET status = CAST(:status AS varchar),
                           attempts = r.attempts + 1,
                           provider_message_id = COALESCE(v.mid, r.provider_message_id),
                           last_error = v.err,
                           sent_at = CASE WHEN CAST(:status AS varchar) = 'sent' THEN now() ELSE r.sent_at END,
                           last_event_at = now()
                      FROM unnest(CAST(:ids AS bigint[]), CAST(:mids AS text[]), CAST(:errs AS text[]))
                           AS v(id, mid, err)
                     WHERE r.id = v.id AND r.batch_id = :batch AND r.status = 'queued'
                 RETURNING r.id, r.email_normalized
                    """
                ),
                {
                    "status": status,
                    "ids": [int(i["recipient_id"]) for i in items],
                    "mids": [(i.get("provider_message_id") or None) for i in items],
                    "errs": [_error_text(i) if kind != "sent" else None for i in items],
                    "batch": job.batch_id,
                },
            )
        ).all()
        applied[kind] = [(row.id, row.email_normalized) for row in rows]
        summary.ignored += len(items) - len(rows)

    by_id = {int(r["recipient_id"]): r for r in results}
    event_rows: list[dict[str, Any]] = []
    for kind, rows in applied.items():
        event_type = {"sent": "sent", "deferred": "deferred", "bounced": "bounced", "failed": "failed",
                      "invalid": "failed"}[kind]
        for rid, _email in rows:
            src = by_id[rid]
            event_rows.append(
                {
                    "campaign_id": job.campaign_id,
                    "job_id": job.id,
                    "recipient_id": rid,
                    "provider_id": job.provider_id,
                    "user_id": job.user_id,
                    "event_type": event_type,
                    "provider_message_id": src.get("provider_message_id"),
                    "error_code": _error_code(src),
                    "error_message": (src.get("error_message") or None) and str(src.get("error_message"))[:512],
                }
            )
            if accept_is_delivery and kind == "sent":
                event_rows.append({**event_rows[-1], "event_type": "delivered", "provider_message_id": None})
    if event_rows:
        await db.execute(
            text(
                """
                INSERT INTO email_events (campaign_id, job_id, recipient_id, provider_id, user_id, event_type,
                                          provider_message_id, error_code, error_message, created_at)
                VALUES (:campaign_id, :job_id, :recipient_id, :provider_id, :user_id, :event_type,
                        :provider_message_id, :error_code, :error_message, now())
                """
            ),
            event_rows,
        )

    for kind in ("bounced", "invalid"):
        stype = SuppressionType.HARD_BOUNCE if kind == "bounced" else SuppressionType.INVALID
        for _rid, email in applied.get(kind, []):
            await suppression.add(db, email, stype, job.user_id, reason=f"Automatic: {kind} during send")

    summary.sent = len(applied.get("sent", []))
    summary.deferred = len(applied.get("deferred", []))
    summary.bounced = len(applied.get("bounced", []))
    summary.failed = len(applied.get("failed", [])) + len(applied.get("invalid", []))
    summary.accepted = summary.sent + summary.deferred + summary.bounced + summary.failed

    final = summary.sent + summary.bounced + summary.failed
    if summary.accepted:
        await db.execute(
            update(Campaign)
            .where(Campaign.id == job.campaign_id)
            .values(
                processed=Campaign.processed + final,
                sent=Campaign.sent + summary.sent,
                delivered=Campaign.delivered + (summary.sent if accept_is_delivery else 0),
                failed=Campaign.failed + summary.failed,
                bounced=Campaign.bounced + summary.bounced,
                deferred=Campaign.deferred + summary.deferred,
            )
        )
    await db.commit()

    if summary.accepted:
        await quotas.record_usage(attempt_id, summary.accepted)
        await health.bump(
            job.provider_id,
            attempts=summary.accepted,
            successes=summary.sent,
            deferrals=summary.deferred,
            bounces=summary.bounced,
            connection_failures=sum(1 for r in groups["deferred"] if r.get("category") == "connection"),
            timeouts=sum(1 for r in groups["deferred"] if r.get("category") == "timeout"),
        )
        await _live_stats(job.campaign_id, summary.sent)
    return summary


def _error_code(r: dict[str, Any]) -> str | None:
    parts = [str(p) for p in (r.get("smtp_code"), r.get("enhanced_code")) if p]
    return " ".join(parts)[:64] or (r.get("category") or None)


def _error_text(r: dict[str, Any]) -> str | None:
    msg = r.get("error_message")
    code = _error_code(r)
    text_ = f"{code}: {msg}" if code and msg else (msg or code)
    return text_[:512] if text_ else None


async def _live_stats(campaign_id: uuid.UUID, sent: int) -> None:
    if sent <= 0:
        return
    second = int(time.time())
    redis = get_redis()
    pipe = redis.pipeline()
    pipe.incrby(Keys.sent_per_second(second), sent)
    pipe.expire(Keys.sent_per_second(second), 120)
    pipe.incrby(Keys.campaign_rate(campaign_id, second), sent)
    pipe.expire(Keys.campaign_rate(campaign_id, second), 120)
    await pipe.execute()


async def sending_rate(campaign_id: uuid.UUID | None = None, window: int = 10) -> float:
    second = int(time.time())
    keys = [
        Keys.campaign_rate(campaign_id, s) if campaign_id else Keys.sent_per_second(s)
        for s in range(second - window, second)
    ]
    values = await get_redis().mget(keys)
    return round(sum(int(v or 0) for v in values) / window, 2)


# --------------------------------------------------------------------------- finishing an attempt


async def _finish_attempt(db: AsyncSession, job: Job, outcome: AttemptOutcome, code: str | None = None,
                          message: str | None = None) -> None:
    if job.current_attempt_id:
        await db.execute(
            update(JobAttempt)
            .where(JobAttempt.id == job.current_attempt_id)
            .values(outcome=outcome, finished_at=now(), error_code=code, error_message=(message or None)
                    and message[:512])
        )


async def _requeue_leftovers(db: AsyncSession, job: Job, cfg: dict[str, Any]) -> tuple[int, int]:
    """Move deferred/unreported recipients of a finished job into a new retry batch + job.

    Returns (requeued, exhausted). Recipients that used up max_attempts become failed.
    """
    max_attempts = int(cfg["max_attempts"])
    exhausted_rows = (
        await db.execute(
            text(
                """
                WITH x AS (
                  UPDATE campaign_recipients SET status = 'failed', last_event_at = now(),
                         last_error = COALESCE(last_error, 'Retry limit reached')
                   WHERE batch_id = :batch AND status = 'deferred' AND attempts >= :max RETURNING id
                )
                INSERT INTO email_events (campaign_id, job_id, recipient_id, provider_id, user_id, event_type,
                                          error_message, created_at)
                SELECT :cid, :jid, id, :pid, :uid, 'failed', 'Retry limit reached', now() FROM x RETURNING id
                """
            ),
            {"batch": job.batch_id, "max": max_attempts, "cid": job.campaign_id, "jid": job.id,
             "pid": job.provider_id, "uid": job.user_id},
        )
    ).all()
    exhausted = len(exhausted_rows)
    if exhausted:
        await db.execute(
            update(Campaign).where(Campaign.id == job.campaign_id)
            .values(failed=Campaign.failed + exhausted, processed=Campaign.processed + exhausted)
        )

    leftover = int(
        (
            await db.execute(
                select(func.count()).select_from(CampaignRecipient).where(
                    CampaignRecipient.batch_id == job.batch_id,
                    CampaignRecipient.status.in_([RecipientStatus.DEFERRED, RecipientStatus.QUEUED]),
                )
            )
        ).scalar_one()
    )
    if leftover == 0:
        return 0, exhausted

    batch = await db.get(CampaignBatch, job.batch_id)
    assert batch is not None
    seq = int(
        (await db.execute(select(func.max(CampaignBatch.sequence_no))
                          .where(CampaignBatch.campaign_id == job.campaign_id))).scalar_one()
    ) + 1
    retry_round = batch.retry_round + 1
    new_batch = CampaignBatch(id=uuid7(), campaign_id=job.campaign_id, sequence_no=seq,
                              recipient_count=leftover, retry_round=retry_round)
    db.add(new_batch)
    await db.flush()
    await db.execute(
        update(CampaignRecipient)
        .where(CampaignRecipient.batch_id == job.batch_id,
               CampaignRecipient.status.in_([RecipientStatus.DEFERRED, RecipientStatus.QUEUED]))
        .values(batch_id=new_batch.id, status=RecipientStatus.QUEUED)
    )
    db.add(
        Job(
            id=uuid7(),
            campaign_id=job.campaign_id,
            user_id=job.user_id,
            batch_id=new_batch.id,
            provider_id=job.provider_id,
            batch_size=leftover,
            max_attempts=job.max_attempts,
            idempotency_key=f"{job.campaign_id}:{seq}:{retry_round}",
            available_at=campaign_service.retry_available_at(list(cfg["retry_schedule_seconds"]), retry_round),
        )
    )
    return leftover, exhausted


async def _cancel_leftovers(db: AsyncSession, job: Job) -> None:
    await db.execute(
        update(CampaignRecipient)
        .where(CampaignRecipient.batch_id == job.batch_id,
               CampaignRecipient.status.in_([RecipientStatus.DEFERRED, RecipientStatus.QUEUED]))
        .values(status=RecipientStatus.CANCELLED)
    )


async def _fail_leftovers(db: AsyncSession, job: Job, reason: str) -> int:
    rows = (
        await db.execute(
            text(
                """
                WITH x AS (
                  UPDATE campaign_recipients SET status = 'failed', last_error = :reason, last_event_at = now()
                   WHERE batch_id = :batch AND status IN ('queued', 'deferred') RETURNING id
                )
                INSERT INTO email_events (campaign_id, job_id, recipient_id, provider_id, user_id, event_type,
                                          error_message, created_at)
                SELECT :cid, :jid, id, :pid, :uid, 'failed', :reason, now() FROM x RETURNING id
                """
            ),
            {"batch": job.batch_id, "reason": reason[:512], "cid": job.campaign_id, "jid": job.id,
             "pid": job.provider_id, "uid": job.user_id},
        )
    ).all()
    n = len(rows)
    if n:
        await db.execute(
            update(Campaign).where(Campaign.id == job.campaign_id)
            .values(failed=Campaign.failed + n, processed=Campaign.processed + n)
        )
    return n


async def ack(db: AsyncSession, worker: Worker, job_id: uuid.UUID, attempt_id: uuid.UUID) -> dict[str, Any]:
    job = await _owned_job(db, worker, job_id, attempt_id)
    cfg = await settings_service.get_all(db)
    campaign = await db.get(Campaign, job.campaign_id)
    requeued = exhausted = 0
    if campaign is not None and campaign.status == CampaignStatus.CANCELLED:
        await _cancel_leftovers(db, job)
    else:
        requeued, exhausted = await _requeue_leftovers(db, job, cfg)
    await _finish_attempt(db, job, AttemptOutcome.COMPLETED)
    job.status = JobStatus.COMPLETED
    job.completed_at = now()
    job.lease_expires_at = None
    await _set_batch_status(db, job.batch_id, JobStatus.COMPLETED)
    await db.commit()
    await quotas.settle(attempt_id)
    await campaign_service.check_completion(db, job.campaign_id)
    return {"status": "completed", "requeued": requeued, "exhausted": exhausted}


async def fail(
    db: AsyncSession,
    worker: Worker,
    job_id: uuid.UUID,
    attempt_id: uuid.UUID,
    error_code: str | None,
    error_message: str | None,
    transient: bool,
    category: str | None,
) -> dict[str, Any]:
    """Job-level (systemic) failure: auth, connection, provider outage (ADR-008)."""
    job = await _owned_job(db, worker, job_id, attempt_id)
    cfg = await settings_service.get_all(db)
    await _finish_attempt(db, job, AttemptOutcome.FAILED, error_code, error_message)
    job.last_error = (f"{error_code}: {error_message}" if error_code else error_message or "failed")[:512]
    job.worker_id = None
    job.lease_expires_at = None
    job.current_attempt_id = None
    campaign = await db.get(Campaign, job.campaign_id)

    if campaign is not None and campaign.status == CampaignStatus.CANCELLED:
        await _cancel_leftovers(db, job)
        job.status = JobStatus.CANCELLED
        outcome = "cancelled"
    elif transient and job.attempts < job.max_attempts:
        job.status = JobStatus.RETRY
        job.available_at = campaign_service.retry_available_at(list(cfg["retry_schedule_seconds"]), job.attempts)
        outcome = "retry"
    else:
        await _fail_leftovers(db, job, job.last_error)
        job.status = JobStatus.DEAD_LETTER
        job.completed_at = now()
        outcome = "dead_letter"
    await _set_batch_status(db, job.batch_id, job.status)
    await db.commit()
    await quotas.settle(attempt_id)
    await health.bump(
        job.provider_id,
        attempts=1,
        auth_failures=int(category == "auth"),
        connection_failures=int(category == "connection"),
        timeouts=int(category == "timeout"),
    )
    if outcome != "retry":
        await campaign_service.check_completion(db, job.campaign_id)
    return {"status": outcome, "available_at": job.available_at.isoformat() if outcome == "retry" else None}


async def release(db: AsyncSession, worker: Worker, job_id: uuid.UUID, attempt_id: uuid.UUID,
                  reason: str | None) -> dict[str, Any]:
    """Give a job back without counting an attempt (pause, shutdown, stop signal)."""
    job = await _owned_job(db, worker, job_id, attempt_id)
    await _finish_attempt(db, job, AttemptOutcome.RELEASED, None, reason)
    campaign = await db.get(Campaign, job.campaign_id)
    job.worker_id = None
    job.lease_expires_at = None
    job.current_attempt_id = None
    job.attempts = max(0, job.attempts - 1)
    if campaign is not None and campaign.status == CampaignStatus.CANCELLED:
        await _cancel_leftovers(db, job)
        job.status = JobStatus.CANCELLED
        job.completed_at = now()
    else:
        # Recipients reported as deferred during this attempt go back to the queue with the rest.
        await db.execute(
            update(CampaignRecipient)
            .where(CampaignRecipient.batch_id == job.batch_id, CampaignRecipient.status == RecipientStatus.DEFERRED)
            .values(status=RecipientStatus.QUEUED)
        )
        job.status = JobStatus.PENDING
        job.available_at = now()
    await _set_batch_status(db, job.batch_id, job.status)
    await db.commit()
    await quotas.settle(attempt_id)
    if job.status == JobStatus.CANCELLED:
        await campaign_service.check_completion(db, job.campaign_id)
    return {"status": job.status.value}


# --------------------------------------------------------------------------- lease recovery (scheduler)


async def recover_expired_leases(db: AsyncSession) -> int:
    """Jobs whose worker stopped renewing (crash, network loss) become claimable again (§47)."""
    cfg = await settings_service.get_all(db)
    jobs = (
        await db.execute(
            select(Job)
            .where(Job.status.in_([JobStatus.CLAIMED, JobStatus.PROCESSING]), Job.lease_expires_at < func.now())
            .limit(200)
            .with_for_update(skip_locked=True)
        )
    ).scalars().all()
    settled: list[uuid.UUID] = []
    touched_campaigns: set[uuid.UUID] = set()
    for job in jobs:
        await _finish_attempt(db, job, AttemptOutcome.LEASE_EXPIRED, "lease_expired", "Worker lease expired")
        if job.current_attempt_id:
            settled.append(job.current_attempt_id)
        await db.execute(
            update(CampaignRecipient)
            .where(CampaignRecipient.batch_id == job.batch_id, CampaignRecipient.status == RecipientStatus.DEFERRED)
            .values(status=RecipientStatus.QUEUED)
        )
        job.worker_id = None
        job.lease_expires_at = None
        job.current_attempt_id = None
        job.last_error = "Worker lease expired"
        campaign = await db.get(Campaign, job.campaign_id)
        if campaign is not None and campaign.status == CampaignStatus.CANCELLED:
            await _cancel_leftovers(db, job)
            job.status = JobStatus.CANCELLED
            touched_campaigns.add(job.campaign_id)
        elif job.attempts < job.max_attempts:
            job.status = JobStatus.RETRY
            job.available_at = campaign_service.retry_available_at(list(cfg["retry_schedule_seconds"]), 1)
        else:
            await _fail_leftovers(db, job, "Worker lease expired too many times")
            job.status = JobStatus.DEAD_LETTER
            job.completed_at = now()
            touched_campaigns.add(job.campaign_id)
        await _set_batch_status(db, job.batch_id, job.status)
    await db.commit()
    for attempt_id in settled:
        await quotas.settle(attempt_id)
    for cid in touched_campaigns:
        await campaign_service.check_completion(db, cid)
    return len(jobs)


async def requeue_dead_letter(db: AsyncSession, job_id: uuid.UUID) -> Job:
    job = (await db.execute(select(Job).where(Job.id == job_id).with_for_update())).scalar_one_or_none()
    if job is None:
        raise ApiError(404, "not_found", "Job not found")
    if job.status != JobStatus.DEAD_LETTER:
        raise ApiError(409, "invalid_transition", "Only dead-letter jobs can be requeued")
    campaign = await db.get(Campaign, job.campaign_id)
    if campaign is None or campaign.status in (CampaignStatus.CANCELLED,):
        raise ApiError(409, "campaign_cancelled", "Campaign was cancelled")
    # Failed recipients of this batch get another chance.
    n = (
        await db.execute(
            update(CampaignRecipient)
            .where(CampaignRecipient.batch_id == job.batch_id, CampaignRecipient.status == RecipientStatus.FAILED)
            .values(status=RecipientStatus.QUEUED, attempts=0)
            .returning(CampaignRecipient.id)
        )
    ).all()
    job.status = JobStatus.PENDING
    job.attempts = 0
    job.available_at = now()
    job.completed_at = None
    job.last_error = None
    if len(n):
        await db.execute(
            update(Campaign).where(Campaign.id == job.campaign_id)
            .values(failed=func.greatest(Campaign.failed - len(n), 0),
                    processed=func.greatest(Campaign.processed - len(n), 0))
        )
    if campaign.status in (CampaignStatus.COMPLETED, CampaignStatus.FAILED):
        campaign.status = CampaignStatus.PROCESSING
        campaign.completed_at = None
    await _set_batch_status(db, job.batch_id, JobStatus.PENDING)
    await db.commit()
    return job
