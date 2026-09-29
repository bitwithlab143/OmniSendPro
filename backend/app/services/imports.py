"""Background recipient imports from object storage (design DS-21, P3-04/P3-05).

The API only records *what* to import; a processor replica claims the import (SKIP LOCKED), streams the
object through the same parser as direct uploads, reports progress from a separate session every chunk
(so the UI can follow along), and commits the recipients in one transaction at the end — a failed import
leaves the campaign exactly as it was.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError, bad_request
from app.db.session import sessionmaker
from app.models import Campaign, RecipientImport
from app.services import audit, recipients, storage
from app.services import campaigns as campaign_service
from app.services.audit import RequestContext

log = logging.getLogger("omnisend.imports")

ACTIVE = ("queued", "processing")
STALE_AFTER = timedelta(minutes=10)  # processing without a progress heartbeat → worker died, requeue
MAX_ATTEMPTS = 3


def object_prefix(campaign: Campaign) -> str:
    return f"imports/{campaign.user_id}/{campaign.id}/"


def new_object_key(campaign: Campaign) -> str:
    return f"{object_prefix(campaign)}{uuid.uuid4().hex}.csv"


async def active_import(db: AsyncSession, campaign_id: uuid.UUID) -> RecipientImport | None:
    return (
        await db.execute(select(RecipientImport).where(RecipientImport.campaign_id == campaign_id,
                                                       RecipientImport.status.in_(ACTIVE)).limit(1))
    ).scalar_one_or_none()


async def ensure_no_active_import(db: AsyncSession, campaign_id: uuid.UUID) -> None:
    if await active_import(db, campaign_id) is not None:
        raise ApiError(409, "import_in_progress", "A recipient import is still running for this campaign")


async def create(db: AsyncSession, campaign: Campaign, object_key: str, replace: bool, actor: uuid.UUID | None,
                 ctx: RequestContext) -> RecipientImport:
    campaign_service.ensure_editable(campaign)
    if not storage.enabled():
        raise bad_request("Object storage is not configured", "object_storage_disabled")
    if not object_key.startswith(object_prefix(campaign)) or ".." in object_key:
        raise bad_request("This upload does not belong to the campaign", "invalid_object_key")
    await ensure_no_active_import(db, campaign.id)
    job = RecipientImport(campaign_id=campaign.id, user_id=actor, object_key=object_key, replace=replace)
    db.add(job)
    audit.record(db, ctx, "RECIPIENT_IMPORT_QUEUED", "campaign", campaign.id,
                 new={"object_key": object_key, "replace": replace})
    await db.commit()
    await db.refresh(job)
    from app.services import processor

    await processor.wake()
    return job


async def _claim() -> uuid.UUID | None:
    async with sessionmaker()() as db:
        # Requeue imports whose processor died (no progress heartbeat for a while).
        await db.execute(
            update(RecipientImport)
            .where(RecipientImport.status == "processing",
                   RecipientImport.heartbeat_at < datetime.now(UTC) - STALE_AFTER)
            .values(status="queued")
        )
        row = (
            await db.execute(
                text(
                    """
                    UPDATE recipient_imports SET status = 'processing', attempts = attempts + 1,
                           started_at = now(), heartbeat_at = now(), error = NULL
                     WHERE id = (SELECT id FROM recipient_imports WHERE status = 'queued'
                                  ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED)
                    RETURNING id, attempts
                    """
                )
            )
        ).one_or_none()
        if row is not None and row.attempts > MAX_ATTEMPTS:
            await db.execute(update(RecipientImport).where(RecipientImport.id == row.id).values(
                status="failed", error="Gave up after repeated interruptions", finished_at=datetime.now(UTC)))
            await db.commit()
            return None
        await db.commit()
        return row.id if row else None


async def _progress(import_id: uuid.UUID, values: dict[str, Any]) -> None:
    async with sessionmaker()() as db:
        await db.execute(update(RecipientImport).where(RecipientImport.id == import_id)
                         .values(heartbeat_at=func.now(), **values))
        await db.commit()


async def run(import_id: uuid.UUID) -> None:
    async with sessionmaker()() as db:
        job = await db.get(RecipientImport, import_id)
        assert job is not None
        campaign = await db.get(Campaign, job.campaign_id)
        reader: storage.HttpObjectReader | None = None
        try:
            if campaign is None:
                raise ValueError("Campaign was deleted")
            campaign_service.ensure_editable(campaign)
            reader = await asyncio.to_thread(storage.open_object, job.object_key)
            opened = reader
            buffered = io.BufferedReader(reader, 256 * 1024)

            async def on_chunk(stats: recipients.ImportStats) -> None:
                await _progress(import_id, {"rows": stats.rows, "imported": stats.imported, "invalid": stats.invalid,
                                            "duplicates": stats.duplicates, "bytes_read": opened.bytes_read})

            stats = await recipients.import_csv(db, campaign, buffered, replace=job.replace, on_chunk=on_chunk)
            campaign_service.refresh_readiness(campaign)
            job.status, job.finished_at, job.error = "completed", datetime.now(UTC), None
            job.rows, job.imported, job.invalid, job.duplicates = stats.rows, stats.imported, stats.invalid, stats.duplicates
            job.invalid_samples = stats.invalid_samples
            job.bytes_read = reader.bytes_read
            audit.record(db, RequestContext(actor_id=job.user_id, ip=None, user_agent="import-worker"), "RECIPIENTS_IMPORTED", "campaign", campaign.id,
                         new={"imported": stats.imported, "invalid": stats.invalid, "import_id": str(job.id)})
            await db.commit()
            log.info("recipient_import_completed", extra={"import_id": str(import_id), "imported": stats.imported})
        except Exception as exc:  # noqa: BLE001 - reported on the import row
            await db.rollback()
            message = exc.message if isinstance(exc, ApiError) else f"{exc}"
            await _progress(import_id, {"status": "failed", "error": message[:512], "finished_at": datetime.now(UTC)})
            log.warning("recipient_import_failed", extra={"import_id": str(import_id), "error": message[:200]})
            return
        finally:
            if reader is not None:
                reader.close()
    with contextlib.suppress(Exception):  # the bucket lifecycle rule is the safety net
        await asyncio.to_thread(storage.delete, job.object_key)


async def process_next() -> int:
    """Claim and run one queued import; returns 1 if one ran."""
    import_id = await _claim()
    if import_id is None:
        return 0
    await run(import_id)
    return 1
