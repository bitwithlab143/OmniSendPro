"""/api/v1/admin/campaigns (§6, §50, §52; DS-04, DS-09)."""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.api import sse
from app.api.deps import DB, Ctx, Principal, require
from app.api.v1 import campaign_common as common
from app.core.errors import ApiError, not_found
from app.core.pagination import Page, like_escape, paginate
from app.models import Campaign, CampaignRecipient, Job, User
from app.models.enums import CAMPAIGN_VIEWS, CampaignStatus, JobStatus, RecipientStatus, RoleName
from app.schemas.domain import (
    AdminCampaignCreate,
    CampaignDetail,
    CampaignOut,
    CampaignStats,
    CampaignUpdate,
    ImportCreate,
    ImportResult,
    JobOut,
    RecipientImportOut,
    RecipientOut,
    StartRequest,
    UploadOptions,
    UploadUrlOut,
    UploadUrlRequest,
)
from app.services import audit, imports, reports
from app.services import campaigns as svc

router = APIRouter(prefix="/campaigns", tags=["admin:campaigns"])

Read = Annotated[Principal, Depends(require("campaigns.read"))]
Write = Annotated[Principal, Depends(require("campaigns.write"))]
Start = Annotated[Principal, Depends(require("campaigns.start"))]
Stop = Annotated[Principal, Depends(require("campaigns.stop"))]


@router.get("", response_model=Page[CampaignOut])
async def list_campaigns(
    db: DB, _: Read,
    view: Literal["all", "pending", "processing", "completed", "failed"] = "all",
    status: CampaignStatus | None = None,
    user_id: uuid.UUID | None = None,
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=50, ge=1, le=200), cursor: str | None = None,
) -> Page[CampaignOut]:
    stmt = select(Campaign)
    if status:
        stmt = stmt.where(Campaign.status == status)
    elif view != "all":
        stmt = stmt.where(Campaign.status.in_(list(CAMPAIGN_VIEWS[view])))
    if user_id:
        stmt = stmt.where(Campaign.user_id == user_id)
    if q:
        stmt = stmt.where(Campaign.name.ilike(f"%{like_escape(q)}%"))
    rows, nxt = await paginate(db, stmt, [Campaign.created_at, Campaign.id], limit, cursor)
    return Page(items=[common.serialize(c) for c in rows], next_cursor=nxt)


@router.post("", response_model=CampaignDetail, status_code=201)
async def create_campaign(body: AdminCampaignCreate, db: DB, actor: Write, ctx: Ctx) -> CampaignDetail:
    owner = await db.get(User, body.user_id)
    if owner is None or owner.role.name != RoleName.USER.value:
        raise ApiError(422, "invalid_owner", "Campaigns must be assigned to a USER account")
    if body.provider_id:
        await svc.validate_provider_choice(db, owner.id, body.provider_id)
    data = body.model_dump(exclude={"user_id"}, exclude_none=True)
    campaign = Campaign(user_id=owner.id, created_by=actor.id, subject=data.pop("subject", ""), **data)
    svc.refresh_readiness(campaign)
    db.add(campaign)
    await db.flush()
    audit.record(db, ctx, "CAMPAIGN_CREATED", "campaign", campaign.id, new=svc.campaign_snapshot(campaign))
    await db.commit()
    return common.serialize(await common.load(db, campaign.id), detail=True)  # type: ignore[return-value]


@router.get("/upload-options", response_model=UploadOptions)
async def upload_options(_: Read) -> UploadOptions:
    """Whether large uploads through object storage are available (design DS-21)."""
    return common.upload_options()


@router.get("/{campaign_id}", response_model=CampaignDetail)
async def get_campaign(campaign_id: uuid.UUID, db: DB, _: Read) -> CampaignDetail:
    return common.serialize(await common.load(db, campaign_id), detail=True)  # type: ignore[return-value]


@router.patch("/{campaign_id}", response_model=CampaignDetail)
async def update_campaign(campaign_id: uuid.UUID, body: CampaignUpdate, db: DB, _: Write, ctx: Ctx) -> CampaignDetail:
    campaign = await common.apply_update(db, await common.load(db, campaign_id), body, ctx)
    return common.serialize(campaign, detail=True)  # type: ignore[return-value]


@router.post("/{campaign_id}/recipients", response_model=ImportResult)
async def upload_recipients(campaign_id: uuid.UUID, db: DB, _: Write, ctx: Ctx,
                            file: UploadFile = File(...), replace: bool = False) -> ImportResult:
    return await common.upload(db, await common.load(db, campaign_id), file, replace, ctx)


@router.delete("/{campaign_id}/recipients", response_model=CampaignOut)
async def clear_recipients(campaign_id: uuid.UUID, db: DB, _: Write, ctx: Ctx) -> CampaignOut:
    return common.serialize(await common.clear_recipients(db, await common.load(db, campaign_id), ctx))


@router.get("/{campaign_id}/recipients", response_model=Page[RecipientOut])
async def list_recipients(campaign_id: uuid.UUID, db: DB, _: Read, status: RecipientStatus | None = None,
                          limit: int = Query(default=50, ge=1, le=200), cursor: str | None = None,
                          ) -> Page[RecipientOut]:
    await common.load(db, campaign_id)
    stmt = select(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign_id)
    if status:
        stmt = stmt.where(CampaignRecipient.status == status)
    rows, nxt = await paginate(db, stmt, [CampaignRecipient.id], limit, cursor)
    return Page(items=[RecipientOut.model_validate(r) for r in rows], next_cursor=nxt)


@router.post("/{campaign_id}/start", response_model=CampaignOut)
async def start_campaign(campaign_id: uuid.UUID, body: StartRequest, db: DB, _: Start, ctx: Ctx) -> CampaignOut:
    campaign = await svc.start(db, await common.load(db, campaign_id), ctx, body.consent_confirmed)
    return common.serialize(campaign)


@router.post("/{campaign_id}/pause", response_model=CampaignOut)
async def pause_campaign(campaign_id: uuid.UUID, db: DB, _: Stop, ctx: Ctx) -> CampaignOut:
    return common.serialize(await svc.pause(db, await common.load(db, campaign_id), ctx))


@router.post("/{campaign_id}/resume", response_model=CampaignOut)
async def resume_campaign(campaign_id: uuid.UUID, db: DB, _: Start, ctx: Ctx) -> CampaignOut:
    return common.serialize(await svc.resume(db, await common.load(db, campaign_id), ctx))


@router.post("/{campaign_id}/cancel", response_model=CampaignOut)
async def cancel_campaign(campaign_id: uuid.UUID, db: DB, _: Stop, ctx: Ctx) -> CampaignOut:
    return common.serialize(await svc.cancel(db, await common.load(db, campaign_id), ctx))


@router.get("/{campaign_id}/stats", response_model=CampaignStats)
async def campaign_stats(campaign_id: uuid.UUID, db: DB, _: Read) -> CampaignStats:
    return await common.stats(db, await common.load(db, campaign_id))


@router.get("/{campaign_id}/stream", response_class=StreamingResponse)
async def campaign_stream(campaign_id: uuid.UUID, request: Request, db: DB, _: Read) -> StreamingResponse:
    """Live campaign stats (SSE, design DS-20); ends once the campaign is final."""
    await common.load(db, campaign_id)

    async def produce(sdb):  # noqa: ANN001, ANN202
        return await common.stats(sdb, await common.load(sdb, campaign_id))

    return await sse.stream(request, db, produce, interval=1.0, done=sse.campaign_done)


@router.get("/{campaign_id}/report")
async def campaign_report(campaign_id: uuid.UUID, db: DB, _: Read) -> dict:
    return await reports.campaign_report(db, await common.load(db, campaign_id))


@router.get("/{campaign_id}/report.csv")
async def campaign_report_csv(campaign_id: uuid.UUID, db: DB, _: Read) -> StreamingResponse:
    return common.recipients_csv(db, await common.load(db, campaign_id))


@router.get("/{campaign_id}/jobs", response_model=Page[JobOut])
async def campaign_jobs(campaign_id: uuid.UUID, db: DB, _: Read, status: JobStatus | None = None,
                        limit: int = Query(default=50, ge=1, le=200), cursor: str | None = None) -> Page[JobOut]:
    campaign = await db.get(Campaign, campaign_id)
    if campaign is None:
        raise not_found("Campaign")
    stmt = select(Job).where(Job.campaign_id == campaign_id)
    if status:
        stmt = stmt.where(Job.status == status)
    rows, nxt = await paginate(db, stmt, [Job.created_at, Job.id], limit, cursor)
    return Page(items=[JobOut.model_validate(j) for j in rows], next_cursor=nxt)


# --------------------------------------------------------------------------- large uploads (DS-21)


@router.post("/{campaign_id}/recipients/upload-url", response_model=UploadUrlOut)
async def recipients_upload_url(campaign_id: uuid.UUID, body: UploadUrlRequest, db: DB, me: Write) -> UploadUrlOut:
    """Presigned POST for uploading a large recipient file straight to object storage."""
    return await common.upload_url(db, await common.load(db, campaign_id), body)


@router.post("/{campaign_id}/recipients/imports", response_model=RecipientImportOut, status_code=202)
async def recipients_import(campaign_id: uuid.UUID, body: ImportCreate, db: DB, me: Write,
                            ctx: Ctx) -> RecipientImportOut:
    campaign = await common.load(db, campaign_id)
    job = await imports.create(db, campaign, body.object_key, body.replace, me.id, ctx)
    return RecipientImportOut.model_validate(job)


@router.get("/{campaign_id}/recipients/imports", response_model=list[RecipientImportOut])
async def recipients_imports(campaign_id: uuid.UUID, db: DB, _: Read) -> list[RecipientImportOut]:
    return await common.list_imports(db, await common.load(db, campaign_id))
