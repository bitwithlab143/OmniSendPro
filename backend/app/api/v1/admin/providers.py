"""/api/v1/admin/providers + /assignments (ARCHITECTURE.md §15–§18, DS-06)."""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import DB, Ctx, Principal, require
from app.core.errors import conflict, not_found
from app.core.pagination import Page, paginate
from app.core.security import encrypt_secret, encryption_key_version
from app.models import Provider, ProviderAssignment, ProviderCredential, ProviderHealthLog, User
from app.models.enums import AssignmentStatus, ProviderStatus, RoleName
from app.schemas.domain import (
    AssignmentCreate,
    AssignmentOut,
    HealthLogOut,
    ProviderCreate,
    ProviderDetail,
    ProviderOut,
    ProviderSecretIn,
    ProviderUpdate,
    ProviderUsage,
)
from app.services import audit, quotas
from app.services import providers as provider_service

router = APIRouter(tags=["admin:providers"])

Read = Annotated[Principal, Depends(require("providers.read"))]
Write = Annotated[Principal, Depends(require("providers.write"))]

_TRACKED = ("provider_name", "host", "port", "username", "tls_mode", "from_email", "from_name",
            "hourly_limit", "daily_limit", "per_second_limit", "max_connections")


def serialize(p: Provider) -> ProviderOut:
    out = ProviderOut.model_validate(p)
    out.has_secret = p.credential is not None
    out.webhook_enabled = p.webhook_secret_encrypted is not None
    return out


async def _get(db: DB, provider_id: uuid.UUID) -> Provider:
    provider = await db.get(Provider, provider_id)
    if provider is None:
        raise not_found("Provider")
    return provider


def _set_secret(provider: Provider, password: str) -> None:
    token = encrypt_secret(password)
    if provider.credential is None:
        provider.credential = ProviderCredential(encrypted_secret=token, key_version=encryption_key_version(token))
    else:
        provider.credential.encrypted_secret = token
        provider.credential.key_version = encryption_key_version(token)
        provider.credential.rotated_at = datetime.now(UTC)


@router.get("/providers", response_model=Page[ProviderOut])
async def list_providers(
    db: DB, _: Read, status: ProviderStatus | None = None,
    limit: int = Query(default=50, ge=1, le=200), cursor: str | None = None,
) -> Page[ProviderOut]:
    stmt = select(Provider)
    if status:
        stmt = stmt.where(Provider.status == status)
    rows, nxt = await paginate(db, stmt, [Provider.created_at, Provider.id], limit, cursor)
    return Page(items=[serialize(p) for p in rows], next_cursor=nxt)


@router.post("/providers", response_model=ProviderOut, status_code=201)
async def create_provider(body: ProviderCreate, db: DB, _: Write, ctx: Ctx) -> ProviderOut:
    data = body.model_dump(exclude={"password"})
    provider = Provider(**data, status=ProviderStatus.ACTIVE)
    if body.password:
        _set_secret(provider, body.password)
    db.add(provider)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise conflict("A provider with this name already exists", "duplicate_provider") from exc
    audit.record(db, ctx, "PROVIDER_CREATED", "provider", provider.id,
                 new={k: data[k] for k in _TRACKED if k in data})
    await db.commit()
    await db.refresh(provider)
    return serialize(provider)


@router.get("/providers/{provider_id}", response_model=ProviderDetail)
async def get_provider(provider_id: uuid.UUID, db: DB, _: Read) -> ProviderDetail:
    provider = await _get(db, provider_id)
    out = ProviderDetail(**serialize(provider).model_dump())
    out.usage = ProviderUsage(**await quotas.usage(provider_id=provider.id))
    out.assigned_users = int(
        (await db.execute(select(func.count()).select_from(ProviderAssignment).where(
            ProviderAssignment.provider_id == provider.id,
            ProviderAssignment.status == AssignmentStatus.ACTIVE))).scalar_one()
    )
    return out


@router.patch("/providers/{provider_id}", response_model=ProviderOut)
async def update_provider(provider_id: uuid.UUID, body: ProviderUpdate, db: DB, _: Write, ctx: Ctx) -> ProviderOut:
    provider = await _get(db, provider_id)
    data = body.model_dump(exclude_unset=True)
    before = {k: getattr(provider, k) for k in data}
    for key, value in data.items():
        if key in ("provider_name", "host", "port", "tls_mode", "from_email") and value is None:
            continue
        setattr(provider, key, value)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise conflict("A provider with this name already exists", "duplicate_provider") from exc
    old, new = audit.diff({k: str(v) if v is not None else None for k, v in before.items()},
                          {k: str(getattr(provider, k)) if getattr(provider, k) is not None else None for k in data})
    if new:
        audit.record(db, ctx, "PROVIDER_UPDATED", "provider", provider.id, old=old, new=new)
    await db.commit()
    await db.refresh(provider)
    return serialize(provider)


@router.put("/providers/{provider_id}/secret", response_model=ProviderOut)
async def set_secret(provider_id: uuid.UUID, body: ProviderSecretIn, db: DB, _: Write, ctx: Ctx) -> ProviderOut:
    provider = await _get(db, provider_id)
    _set_secret(provider, body.password)
    audit.record(db, ctx, "PROVIDER_SECRET_ROTATED", "provider", provider.id)
    await db.commit()
    await db.refresh(provider)
    return serialize(provider)


@router.post("/providers/{provider_id}/webhook-secret")
async def rotate_webhook_secret(provider_id: uuid.UUID, db: DB, _: Write, ctx: Ctx) -> dict[str, str]:
    provider = await _get(db, provider_id)
    secret = "whsec_" + secrets.token_urlsafe(32)
    provider.webhook_secret_encrypted = encrypt_secret(secret)
    audit.record(db, ctx, "PROVIDER_WEBHOOK_SECRET_ROTATED", "provider", provider.id)
    await db.commit()
    return {
        "webhook_secret": secret,
        "endpoint": f"/api/v1/hooks/providers/{provider.id}",
        "note": "Shown once. Sign requests with HMAC-SHA256 over '<timestamp>.<body>' "
                "(headers X-OmniSend-Timestamp and X-OmniSend-Signature).",
    }


@router.delete("/providers/{provider_id}/webhook-secret", response_model=ProviderOut)
async def disable_webhook(provider_id: uuid.UUID, db: DB, _: Write, ctx: Ctx) -> ProviderOut:
    provider = await _get(db, provider_id)
    provider.webhook_secret_encrypted = None
    audit.record(db, ctx, "PROVIDER_WEBHOOK_DISABLED", "provider", provider.id)
    await db.commit()
    await db.refresh(provider)
    return serialize(provider)


async def _set_status(db: DB, ctx: Ctx, provider_id: uuid.UUID, status: ProviderStatus, action: str,
                      reason: str) -> ProviderOut:
    provider = await _get(db, provider_id)
    old = provider.status
    provider.status = status
    provider.status_reason = reason
    if status == ProviderStatus.ACTIVE:
        provider.consecutive_bad_windows = 0
        provider.health_score = max(provider.health_score, 80.0)
    audit.record(db, ctx, action, "provider", provider.id, old={"status": old.value}, new={"status": status.value})
    await db.commit()
    await db.refresh(provider)
    return serialize(provider)


@router.post("/providers/{provider_id}/enable", response_model=ProviderOut)
async def enable_provider(provider_id: uuid.UUID, db: DB, _: Write, ctx: Ctx) -> ProviderOut:
    return await _set_status(db, ctx, provider_id, ProviderStatus.ACTIVE, "SMTP_ENABLED", "Enabled by admin")


@router.post("/providers/{provider_id}/disable", response_model=ProviderOut)
async def disable_provider(provider_id: uuid.UUID, db: DB, _: Write, ctx: Ctx) -> ProviderOut:
    return await _set_status(db, ctx, provider_id, ProviderStatus.DISABLED, "SMTP_DISABLED", "Disabled by admin")


@router.post("/providers/{provider_id}/test")
async def test_provider(provider_id: uuid.UUID, db: DB, _: Write, ctx: Ctx) -> dict[str, Any]:
    provider = await _get(db, provider_id)
    result = await provider_service.test_connection(provider)
    provider.last_tested_at = datetime.now(UTC)
    provider.last_test_ok = result.ok
    provider.last_test_message = result.message[:512]
    audit.record(db, ctx, "PROVIDER_TESTED", "provider", provider.id, new={"ok": result.ok})
    await db.commit()
    return {"ok": result.ok, "message": result.message, "latency_ms": result.latency_ms}


@router.get("/providers/{provider_id}/dns")
async def provider_dns(provider_id: uuid.UUID, db: DB, _: Read,
                       selector: str | None = Query(default=None, max_length=63, pattern=r"^[A-Za-z0-9._-]+$"),
                       ) -> dict[str, Any]:
    provider = await _get(db, provider_id)
    domain = provider.from_email.rsplit("@", 1)[-1]
    return await provider_service.check_sender_domain(domain, selector)


@router.get("/providers/{provider_id}/health", response_model=list[HealthLogOut])
async def provider_health(provider_id: uuid.UUID, db: DB, _: Read,
                          limit: int = Query(default=60, ge=1, le=500)) -> list[HealthLogOut]:
    await _get(db, provider_id)
    rows = (
        await db.execute(
            select(ProviderHealthLog).where(ProviderHealthLog.provider_id == provider_id)
            .order_by(ProviderHealthLog.created_at.desc()).limit(limit)
        )
    ).scalars().all()
    return [HealthLogOut.model_validate(r) for r in rows]


# --------------------------------------------------------------------------- assignments


def _assignment_out(a: ProviderAssignment) -> AssignmentOut:
    out = AssignmentOut.model_validate(a)
    out.username = a.user.username if a.user else None
    out.provider_name = a.provider.provider_name if a.provider else None
    return out


@router.get("/assignments", response_model=Page[AssignmentOut])
async def list_assignments(
    db: DB, _: Read,
    user_id: uuid.UUID | None = None, provider_id: uuid.UUID | None = None,
    status: AssignmentStatus | None = AssignmentStatus.ACTIVE,
    limit: int = Query(default=50, ge=1, le=200), cursor: str | None = None,
) -> Page[AssignmentOut]:
    stmt = select(ProviderAssignment)
    if user_id:
        stmt = stmt.where(ProviderAssignment.user_id == user_id)
    if provider_id:
        stmt = stmt.where(ProviderAssignment.provider_id == provider_id)
    if status:
        stmt = stmt.where(ProviderAssignment.status == status)
    rows, nxt = await paginate(db, stmt, [ProviderAssignment.assigned_at, ProviderAssignment.id], limit, cursor)
    return Page(items=[_assignment_out(a) for a in rows], next_cursor=nxt)


@router.post("/assignments", response_model=AssignmentOut, status_code=201)
async def create_assignment(body: AssignmentCreate, db: DB, actor: Write, ctx: Ctx) -> AssignmentOut:
    user = await db.get(User, body.user_id)
    if user is None:
        raise not_found("User")
    if user.role.name != RoleName.USER.value:
        raise conflict("Providers can only be assigned to USER accounts", "invalid_assignee")
    await _get(db, body.provider_id)
    existing = (
        await db.execute(select(ProviderAssignment).where(
            ProviderAssignment.user_id == body.user_id, ProviderAssignment.provider_id == body.provider_id,
            ProviderAssignment.status == AssignmentStatus.ACTIVE))
    ).scalar_one_or_none()
    if existing:
        raise conflict("Provider is already assigned to this user", "duplicate_assignment")
    assignment = ProviderAssignment(user_id=body.user_id, provider_id=body.provider_id, assigned_by=actor.id)
    db.add(assignment)
    await db.flush()
    audit.record(db, ctx, "PROVIDER_ASSIGNED", "provider", body.provider_id, new={"user_id": body.user_id})
    await db.commit()
    await db.refresh(assignment)
    return _assignment_out(assignment)


@router.delete("/assignments/{assignment_id}", response_model=AssignmentOut)
async def revoke_assignment(assignment_id: uuid.UUID, db: DB, _: Write, ctx: Ctx) -> AssignmentOut:
    assignment = await db.get(ProviderAssignment, assignment_id)
    if assignment is None:
        raise not_found("Assignment")
    if assignment.status == AssignmentStatus.ACTIVE:
        assignment.status = AssignmentStatus.REVOKED
        assignment.revoked_at = datetime.now(UTC)
        audit.record(db, ctx, "PROVIDER_UNASSIGNED", "provider", assignment.provider_id,
                     old={"user_id": assignment.user_id})
        await db.commit()
        await db.refresh(assignment)
    return _assignment_out(assignment)
