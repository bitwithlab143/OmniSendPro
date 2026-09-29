"""/api/v1/user — the intentionally simple User Panel API (§7, §51, DS-13).

Every query is scoped to the calling user's own campaigns / assigned providers.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.api import sse
from app.api.deps import DB, Ctx, Principal, require_user
from app.api.v1 import campaign_common as common
from app.api.v1.auth import me_response
from app.core.errors import bad_request, not_found
from app.core.pagination import Page, like_escape, paginate
from app.core.security import (
    decrypt_secret,
    encrypt_secret,
    hash_password,
    new_totp_secret,
    totp_uri,
    validate_password_strength,
    verify_password,
    verify_totp,
)
from app.models import ApiKey, Campaign, CampaignRecipient, Job, Provider, ProviderAssignment, User
from app.models.enums import CAMPAIGN_VIEWS, AssignmentStatus, JobStatus, RecipientStatus
from app.schemas.auth import ChangePasswordRequest, MeResponse, TotpCodeRequest, TotpSetupResponse
from app.schemas.domain import (
    ApiKeyCreate,
    ApiKeyCreated,
    ApiKeyOut,
    CampaignCreate,
    CampaignDetail,
    CampaignOut,
    CampaignStats,
    CampaignUpdate,
    ImportResult,
    JobOut,
    RecipientOut,
    StartRequest,
    UserProviderOut,
)
from app.schemas.users import ProfileUpdate
from app.services import api_keys, audit, reports
from app.services import auth as auth_service
from app.services import campaigns as svc
from app.services import settings as settings_service

router = APIRouter(prefix="/user", tags=["user"])

Read = Annotated[Principal, Depends(require_user("campaigns.read"))]
Write = Annotated[Principal, Depends(require_user("campaigns.write"))]
Start = Annotated[Principal, Depends(require_user("campaigns.start"))]
Stop = Annotated[Principal, Depends(require_user("campaigns.stop"))]
ReportsRead = Annotated[Principal, Depends(require_user("reports.read"))]
ProvidersRead = Annotated[Principal, Depends(require_user("providers.read"))]
# Profile, password, 2FA and key management: interactive sessions only, never with an API key (DS-17).
Account = Annotated[Principal, Depends(require_user("campaigns.read", session_only=True))]


# --------------------------------------------------------------------------- profile


@router.get("/profile", response_model=MeResponse)
async def profile(me: Read) -> MeResponse:
    return me_response(me.user)


@router.patch("/profile", response_model=MeResponse)
async def update_profile(body: ProfileUpdate, db: DB, me: Account) -> MeResponse:
    me.user.full_name = body.full_name
    await db.commit()
    return me_response(me.user)


@router.post("/profile/password", status_code=204)
async def change_password(body: ChangePasswordRequest, db: DB, me: Account, ctx: Ctx) -> None:
    if not verify_password(me.user.password_hash, body.current_password):
        raise bad_request("Current password is incorrect", "invalid_password")
    if err := validate_password_strength(body.new_password):
        raise bad_request(err, "weak_password")
    me.user.password_hash = hash_password(body.new_password)
    me.user.password_changed_at = datetime.now(UTC)
    await auth_service.revoke_all_for_user(db, me.id)
    audit.record(db, ctx, "PASSWORD_CHANGED", "user", me.id)
    await db.commit()


@router.post("/profile/2fa/setup", response_model=TotpSetupResponse)
async def totp_setup(db: DB, me: Account) -> TotpSetupResponse:
    if me.user.totp_enabled:
        raise bad_request("Two-factor authentication is already enabled", "2fa_enabled")
    secret = new_totp_secret()
    me.user.totp_secret_encrypted = encrypt_secret(secret)
    await db.commit()
    return TotpSetupResponse(otpauth_uri=totp_uri(secret, me.user.email), totp_secret=secret)


@router.post("/profile/2fa/enable", response_model=MeResponse)
async def totp_enable(body: TotpCodeRequest, db: DB, me: Account, ctx: Ctx) -> MeResponse:
    if not me.user.totp_secret_encrypted or not verify_totp(decrypt_secret(me.user.totp_secret_encrypted), body.code):
        raise bad_request("Invalid verification code", "invalid_code")
    me.user.totp_enabled = True
    audit.record(db, ctx, "TWO_FACTOR_ENABLED", "user", me.id)
    await db.commit()
    return me_response(me.user)


@router.post("/profile/2fa/disable", response_model=MeResponse)
async def totp_disable(body: TotpCodeRequest, db: DB, me: Account, ctx: Ctx) -> MeResponse:
    if not me.user.totp_enabled or not me.user.totp_secret_encrypted:
        raise bad_request("Two-factor authentication is not enabled", "2fa_disabled")
    if not verify_totp(decrypt_secret(me.user.totp_secret_encrypted), body.code):
        raise bad_request("Invalid verification code", "invalid_code")
    me.user.totp_enabled = False
    me.user.totp_secret_encrypted = None
    audit.record(db, ctx, "TWO_FACTOR_DISABLED", "user", me.id)
    await db.commit()
    return me_response(me.user)


# --------------------------------------------------------------------------- API keys (DS-17)


@router.get("/api-keys", response_model=list[ApiKeyOut])
async def my_api_keys(db: DB, me: Account) -> list[dict[str, Any]]:
    return [api_keys.serialize(k) for k in await api_keys.list_keys(db, me.id, include_revoked=False)]


@router.post("/api-keys", response_model=ApiKeyCreated, status_code=201)
async def create_api_key(body: ApiKeyCreate, db: DB, me: Account, ctx: Ctx) -> dict[str, Any]:
    key, token = await api_keys.create(db, me.user, body.name, body.scopes, body.expires_in_days, me.id)
    audit.record(db, ctx, "API_KEY_CREATED", "api_key", key.id,
                 new={"user_id": str(me.id), "name": key.name, "prefix": key.prefix, "scopes": key.scopes})
    await db.commit()
    return api_keys.serialize(key, token)


@router.delete("/api-keys/{key_id}", response_model=ApiKeyOut)
async def revoke_api_key(key_id: uuid.UUID, db: DB, me: Account, ctx: Ctx) -> dict[str, Any]:
    key = await db.get(ApiKey, key_id)
    if key is None or key.user_id != me.id:
        raise not_found("API key")
    if await api_keys.revoke(db, key):
        audit.record(db, ctx, "API_KEY_REVOKED", "api_key", key.id,
                     new={"user_id": str(me.id), "prefix": key.prefix})
    await db.commit()
    return api_keys.serialize(key)


# --------------------------------------------------------------------------- dashboard, providers


@router.get("/dashboard")
async def dashboard(db: DB, me: ReportsRead) -> dict[str, Any]:
    return await reports.user_dashboard(db, me.user)


@router.get("/dashboard/stream", response_class=StreamingResponse)
async def dashboard_stream(request: Request, db: DB, me: ReportsRead) -> StreamingResponse:
    """Live user dashboard (SSE, design DS-20)."""
    user_id = me.id

    async def produce(sdb):  # noqa: ANN001, ANN202
        return await reports.user_dashboard(sdb, await sdb.get(User, user_id))

    return await sse.stream(request, db, produce, interval=2.0)


@router.get("/providers", response_model=list[UserProviderOut])
async def my_providers(db: DB, me: ProvidersRead) -> list[UserProviderOut]:
    rows = (
        await db.execute(
            select(Provider)
            .join(ProviderAssignment, ProviderAssignment.provider_id == Provider.id)
            .where(ProviderAssignment.user_id == me.id, ProviderAssignment.status == AssignmentStatus.ACTIVE)
            .order_by(Provider.provider_name)
        )
    ).scalars().all()
    return [
        UserProviderOut(id=p.id, provider_name=p.provider_name, from_email=p.from_email, from_name=p.from_name,
                        status=p.status, per_second_limit=p.per_second_limit, hourly_limit=p.hourly_limit,
                        daily_limit=p.daily_limit)
        for p in rows
    ]


@router.get("/options")
async def options(db: DB, me: Read) -> dict[str, Any]:
    cfg = await settings_service.get_all(db)
    limits = me.user.limits
    presets = list(cfg["allowed_batch_sizes"])
    cap = min([v for v in (limits.max_batch_size if limits else None, int(cfg["max_batch_size"])) if v])
    return {"batch_sizes": [s for s in presets if s <= cap] or [cap], "max_batch_size": cap,
            "default_batch_size": min(int(cfg["default_batch_size"]), cap)}


# --------------------------------------------------------------------------- campaigns


@router.get("/campaigns", response_model=Page[CampaignOut])
async def my_campaigns(db: DB, me: Read, view: str = Query(default="all", pattern="^(all|pending|processing|completed|failed)$"),
                       q: str | None = Query(default=None, max_length=100),
                       limit: int = Query(default=50, ge=1, le=200), cursor: str | None = None) -> Page[CampaignOut]:
    stmt = select(Campaign).where(Campaign.user_id == me.id)
    if view != "all":
        stmt = stmt.where(Campaign.status.in_(list(CAMPAIGN_VIEWS[view])))
    if q:
        stmt = stmt.where(Campaign.name.ilike(f"%{like_escape(q)}%"))
    rows, nxt = await paginate(db, stmt, [Campaign.created_at, Campaign.id], limit, cursor)
    return Page(items=[common.serialize(c) for c in rows], next_cursor=nxt)


@router.post("/campaigns", response_model=CampaignDetail, status_code=201)
async def create_campaign(body: CampaignCreate, db: DB, me: Write, ctx: Ctx) -> CampaignDetail:
    if body.provider_id:
        await svc.validate_provider_choice(db, me.id, body.provider_id)
    cfg = await settings_service.get_all(db)
    data = body.model_dump(exclude_none=True)
    data.setdefault("batch_size", int(cfg["default_batch_size"]))
    campaign = Campaign(user_id=me.id, created_by=me.id, subject=data.pop("subject", ""), **data)
    svc.refresh_readiness(campaign)
    db.add(campaign)
    await db.flush()
    audit.record(db, ctx, "CAMPAIGN_CREATED", "campaign", campaign.id, new=svc.campaign_snapshot(campaign))
    await db.commit()
    return common.serialize(await common.load(db, campaign.id, owner=me.id), detail=True)  # type: ignore[return-value]


@router.get("/campaigns/{campaign_id}", response_model=CampaignDetail)
async def my_campaign(campaign_id: uuid.UUID, db: DB, me: Read) -> CampaignDetail:
    return common.serialize(await common.load(db, campaign_id, owner=me.id), detail=True)  # type: ignore[return-value]


@router.patch("/campaigns/{campaign_id}", response_model=CampaignDetail)
async def update_campaign(campaign_id: uuid.UUID, body: CampaignUpdate, db: DB, me: Write, ctx: Ctx) -> CampaignDetail:
    campaign = await common.apply_update(db, await common.load(db, campaign_id, owner=me.id), body, ctx)
    return common.serialize(campaign, detail=True)  # type: ignore[return-value]


@router.post("/campaigns/{campaign_id}/recipients", response_model=ImportResult)
async def upload_recipients(campaign_id: uuid.UUID, db: DB, me: Write, ctx: Ctx,
                            file: UploadFile = File(...), replace: bool = False) -> ImportResult:
    return await common.upload(db, await common.load(db, campaign_id, owner=me.id), file, replace, ctx)


@router.delete("/campaigns/{campaign_id}/recipients", response_model=CampaignOut)
async def clear_recipients(campaign_id: uuid.UUID, db: DB, me: Write, ctx: Ctx) -> CampaignOut:
    return common.serialize(await common.clear_recipients(db, await common.load(db, campaign_id, owner=me.id), ctx))


@router.get("/campaigns/{campaign_id}/recipients", response_model=Page[RecipientOut])
async def campaign_recipients(campaign_id: uuid.UUID, db: DB, me: Read, status: RecipientStatus | None = None,
                              limit: int = Query(default=50, ge=1, le=200), cursor: str | None = None,
                              ) -> Page[RecipientOut]:
    await common.load(db, campaign_id, owner=me.id)
    stmt = select(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign_id)
    if status:
        stmt = stmt.where(CampaignRecipient.status == status)
    rows, nxt = await paginate(db, stmt, [CampaignRecipient.id], limit, cursor)
    return Page(items=[RecipientOut.model_validate(r) for r in rows], next_cursor=nxt)


@router.post("/campaigns/{campaign_id}/start", response_model=CampaignOut)
async def start_campaign(campaign_id: uuid.UUID, body: StartRequest, db: DB, me: Start, ctx: Ctx) -> CampaignOut:
    campaign = await common.load(db, campaign_id, owner=me.id)
    return common.serialize(await svc.start(db, campaign, ctx, body.consent_confirmed))


@router.post("/campaigns/{campaign_id}/pause", response_model=CampaignOut)
async def pause_campaign(campaign_id: uuid.UUID, db: DB, me: Stop, ctx: Ctx) -> CampaignOut:
    return common.serialize(await svc.pause(db, await common.load(db, campaign_id, owner=me.id), ctx))


@router.post("/campaigns/{campaign_id}/resume", response_model=CampaignOut)
async def resume_campaign(campaign_id: uuid.UUID, db: DB, me: Start, ctx: Ctx) -> CampaignOut:
    return common.serialize(await svc.resume(db, await common.load(db, campaign_id, owner=me.id), ctx))


@router.post("/campaigns/{campaign_id}/cancel", response_model=CampaignOut)
async def cancel_campaign(campaign_id: uuid.UUID, db: DB, me: Stop, ctx: Ctx) -> CampaignOut:
    return common.serialize(await svc.cancel(db, await common.load(db, campaign_id, owner=me.id), ctx))


@router.get("/campaigns/{campaign_id}/stats", response_model=CampaignStats)
async def campaign_stats(campaign_id: uuid.UUID, db: DB, me: Read) -> CampaignStats:
    return await common.stats(db, await common.load(db, campaign_id, owner=me.id))


@router.get("/campaigns/{campaign_id}/stream", response_class=StreamingResponse)
async def campaign_stream(campaign_id: uuid.UUID, request: Request, db: DB, me: Read) -> StreamingResponse:
    """Live campaign stats (SSE, design DS-20); ends once the campaign is final."""
    owner = me.id
    await common.load(db, campaign_id, owner=owner)

    async def produce(sdb):  # noqa: ANN001, ANN202
        return await common.stats(sdb, await common.load(sdb, campaign_id, owner=owner))

    return await sse.stream(request, db, produce, interval=1.0, done=sse.campaign_done)


@router.get("/campaigns/{campaign_id}/report")
async def campaign_report(campaign_id: uuid.UUID, db: DB, me: ReportsRead) -> dict[str, Any]:
    return await reports.campaign_report(db, await common.load(db, campaign_id, owner=me.id))


@router.get("/campaigns/{campaign_id}/report.csv")
async def campaign_report_csv(campaign_id: uuid.UUID, db: DB, me: ReportsRead) -> StreamingResponse:
    return common.recipients_csv(db, await common.load(db, campaign_id, owner=me.id))


# --------------------------------------------------------------------------- jobs & reports


@router.get("/jobs", response_model=Page[JobOut])
async def my_jobs(db: DB, me: Read, campaign_id: uuid.UUID | None = None, status: JobStatus | None = None,
                  limit: int = Query(default=50, ge=1, le=200), cursor: str | None = None) -> Page[JobOut]:
    stmt = select(Job).where(Job.user_id == me.id)
    if campaign_id:
        stmt = stmt.where(Job.campaign_id == campaign_id)
    if status:
        stmt = stmt.where(Job.status == status)
    rows, nxt = await paginate(db, stmt, [Job.created_at, Job.id], limit, cursor)
    return Page(items=[JobOut.model_validate(j) for j in rows], next_cursor=nxt)


@router.get("/reports")
async def my_reports(db: DB, me: ReportsRead, days: int = Query(default=7, ge=1, le=90)) -> dict[str, Any]:
    until = datetime.now(UTC)
    since = until - timedelta(days=days)
    totals = await reports.event_counts(db, since, until, user_id=me.id)
    return {"since": since.isoformat(), "until": until.isoformat(), "totals": totals,
            "daily": await reports.daily_series(db, since, until, user_id=me.id)}
