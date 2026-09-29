"""Campaign operations shared by the admin and user APIs (scope is enforced by the caller)."""

from __future__ import annotations

import csv
import io
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import ApiError, not_found
from app.models import Campaign, CampaignRecipient, Job, RecipientImport
from app.schemas.domain import (
    CampaignDetail,
    CampaignOut,
    CampaignStats,
    CampaignUpdate,
    ImportResult,
    RecipientImportOut,
    UploadOptions,
    UploadUrlOut,
    UploadUrlRequest,
)
from app.services import audit, imports, recipients, storage
from app.services import campaigns as svc
from app.services import jobs as job_service
from app.services.audit import RequestContext

_EDIT_FIELDS = ("name", "subject", "from_name", "from_email", "reply_to", "html_body", "text_body",
                "batch_size", "scheduled_at", "provider_id")


def serialize(c: Campaign, detail: bool = False) -> CampaignOut:
    model = CampaignDetail if detail else CampaignOut
    out = model.model_validate(c)
    out.username = c.user.username if c.user else None
    out.provider_name = c.provider.provider_name if c.provider else None
    out.progress = svc.progress(c)
    out.missing = svc.readiness(c) if c.status in svc.EDITABLE else []
    return out


async def load(db: AsyncSession, campaign_id: uuid.UUID, owner: uuid.UUID | None = None) -> Campaign:
    stmt = select(Campaign).where(Campaign.id == campaign_id)
    if owner is not None:
        stmt = stmt.where(Campaign.user_id == owner)
    campaign = (await db.execute(stmt)).unique().scalar_one_or_none()
    if campaign is None:
        raise not_found("Campaign")
    return campaign


async def apply_update(db: AsyncSession, campaign: Campaign, body: CampaignUpdate, ctx: RequestContext) -> Campaign:
    svc.ensure_editable(campaign)
    data = body.model_dump(exclude_unset=True)
    if "provider_id" in data and data["provider_id"] is not None:
        await svc.validate_provider_choice(db, campaign.user_id, data["provider_id"])
    before = {k: getattr(campaign, k) for k in _EDIT_FIELDS if k in data and k not in ("html_body", "text_body")}
    for key, value in data.items():
        if key not in _EDIT_FIELDS or (key in ("name", "subject") and value is None):
            continue
        setattr(campaign, key, value)
    svc.refresh_readiness(campaign)
    after = {k: getattr(campaign, k) for k in before}
    old, new = audit.diff(before, after)
    if old or new or "html_body" in data or "text_body" in data:
        audit.record(db, ctx, "CAMPAIGN_UPDATED", "campaign", campaign.id, old=old,
                     new={**new, **({"content": "changed"} if {"html_body", "text_body"} & data.keys() else {})})
    await db.commit()
    await db.refresh(campaign)
    return campaign


async def upload(db: AsyncSession, campaign: Campaign, file: UploadFile, replace: bool,
                 ctx: RequestContext) -> ImportResult:
    svc.ensure_editable(campaign)
    await imports.ensure_no_active_import(db, campaign.id)
    limit = get_settings().max_upload_bytes
    if file.size is not None and file.size > limit:
        raise ApiError(413, "payload_too_large", f"File exceeds the {limit // (1024 * 1024)} MB upload limit")
    name = (file.filename or "").lower()
    if name and not name.endswith((".csv", ".txt")):
        raise ApiError(415, "unsupported_file", "Upload a .csv or .txt file")
    stats = await recipients.import_csv(db, campaign, file.file, replace=replace)
    svc.refresh_readiness(campaign)
    audit.record(db, ctx, "RECIPIENTS_UPLOADED", "campaign", campaign.id,
                 new={"imported": stats.imported, "invalid": stats.invalid, "replace": replace})
    await db.commit()
    await db.refresh(campaign)
    return ImportResult(**stats.as_dict(), total_recipients=campaign.total_recipients, status=campaign.status,
                        missing=svc.readiness(campaign))


async def clear_recipients(db: AsyncSession, campaign: Campaign, ctx: RequestContext) -> Campaign:
    svc.ensure_editable(campaign)
    await imports.ensure_no_active_import(db, campaign.id)
    await recipients.clear(db, campaign)
    svc.refresh_readiness(campaign)
    audit.record(db, ctx, "RECIPIENTS_CLEARED", "campaign", campaign.id)
    await db.commit()
    await db.refresh(campaign)
    return campaign


async def stats(db: AsyncSession, campaign: Campaign) -> CampaignStats:
    await db.refresh(campaign)
    rows = await db.execute(select(Job.status, func.count()).where(Job.campaign_id == campaign.id)
                            .group_by(Job.status))
    progress = svc.progress(campaign)
    return CampaignStats(
        id=campaign.id,
        status=campaign.status,
        total_recipients=campaign.total_recipients,
        processed=campaign.processed,
        sent=campaign.sent,
        delivered=campaign.delivered,
        failed=campaign.failed,
        bounced=campaign.bounced,
        complained=campaign.complained,
        deferred=campaign.deferred,
        unsubscribed=campaign.unsubscribed,
        skipped_suppressed=campaign.skipped_suppressed,
        remaining=progress["remaining"],
        percent=progress["percent"],
        speed=await job_service.sending_rate(campaign.id),
        jobs={s.value: int(n) for s, n in rows},
    )


def recipients_csv(db: AsyncSession, campaign: Campaign) -> StreamingResponse:
    """Stream the per-recipient report as CSV without loading everything in memory."""

    async def gen() -> AsyncIterator[str]:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["email", "status", "attempts", "sent_at", "last_error"])
        yield buf.getvalue()
        last_id = 0
        while True:
            rows = (
                await db.execute(
                    select(CampaignRecipient)
                    .where(CampaignRecipient.campaign_id == campaign.id, CampaignRecipient.id > last_id)
                    .order_by(CampaignRecipient.id)
                    .limit(5000)
                )
            ).scalars().all()
            if not rows:
                break
            buf.seek(0)
            buf.truncate()
            for r in rows:
                writer.writerow([_csv_safe(r.email), r.status.value, r.attempts,
                                 r.sent_at.isoformat() if r.sent_at else "", _csv_safe(r.last_error or "")])
            last_id = rows[-1].id
            yield buf.getvalue()

    filename = f"campaign-{campaign.id}.csv"
    return StreamingResponse(gen(), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _csv_safe(value: str) -> str:
    """Neutralise spreadsheet formula injection in exported cells."""
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def stats_dict(s: CampaignStats) -> dict[str, Any]:
    return s.model_dump(mode="json")


# --------------------------------------------------------------------------- large uploads (DS-21)


def upload_options() -> UploadOptions:
    s = get_settings()
    return UploadOptions(object_storage=storage.enabled(), max_upload_bytes=s.max_upload_bytes,
                         max_object_bytes=s.object_storage_max_bytes if storage.enabled() else None)


async def upload_url(db: AsyncSession, campaign: Campaign, body: UploadUrlRequest) -> UploadUrlOut:
    svc.ensure_editable(campaign)
    if not storage.enabled():
        raise ApiError(400, "object_storage_disabled", "Large uploads are not configured on this server")
    limit = get_settings().object_storage_max_bytes
    if body.size > limit:
        raise ApiError(413, "payload_too_large", f"File exceeds the {limit // (1024 * 1024)} MB limit")
    name = (body.filename or "").lower()
    if name and not name.endswith((".csv", ".txt")):
        raise ApiError(415, "unsupported_file", "Upload a .csv or .txt file")
    await imports.ensure_no_active_import(db, campaign.id)
    return UploadUrlOut(**storage.presign_upload(imports.new_object_key(campaign), limit))


async def list_imports(db: AsyncSession, campaign: Campaign) -> list[RecipientImportOut]:
    rows = (
        await db.execute(select(RecipientImport).where(RecipientImport.campaign_id == campaign.id)
                         .order_by(RecipientImport.created_at.desc()).limit(20))
    ).scalars().all()
    return [RecipientImportOut.model_validate(r) for r in rows]
