"""/api/v1/admin — dashboard, reports, suppressions, settings, audit logs (§6, §42, §45, DS-11, DS-12)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, select

from app.api import sse
from app.api.deps import DB, Ctx, Principal, require
from app.core.errors import bad_request, not_found
from app.core.pagination import Page, like_escape, paginate
from app.models import AuditLog, Suppression, SystemSetting, User
from app.models.enums import SuppressionType
from app.schemas.domain import AuditOut, SettingsUpdate, SuppressionCreate, SuppressionOut
from app.services import audit, reports, suppression
from app.services import settings as settings_service

router = APIRouter(tags=["admin:system"])

Reports = Annotated[Principal, Depends(require("reports.read"))]
SRead = Annotated[Principal, Depends(require("suppressions.read"))]
SWrite = Annotated[Principal, Depends(require("suppressions.write"))]
SettingsRead = Annotated[Principal, Depends(require("settings.read"))]
SettingsWrite = Annotated[Principal, Depends(require("settings.write"))]
AuditRead = Annotated[Principal, Depends(require("audit.read"))]


def _range(days: int, since: datetime | None, until: datetime | None) -> tuple[datetime, datetime]:
    until = until or datetime.now(UTC)
    since = since or (until - timedelta(days=days))
    if since >= until:
        raise bad_request("'since' must be before 'until'")
    if until - since > timedelta(days=366):
        raise bad_request("Date range cannot exceed one year")
    return since, until


@router.get("/dashboard")
async def dashboard(db: DB, _: Reports) -> dict[str, Any]:
    return await reports.admin_dashboard(db)


@router.get("/dashboard/stream", response_class=StreamingResponse)
async def dashboard_stream(request: Request, db: DB, _: Reports) -> StreamingResponse:
    """Live admin dashboard (SSE, design DS-20)."""
    return await sse.stream(request, db, reports.admin_dashboard, interval=2.0)


@router.get("/reports/summary")
async def report_summary(db: DB, _: Reports, days: int = Query(default=7, ge=1, le=366),
                         since: datetime | None = None, until: datetime | None = None) -> dict[str, Any]:
    since, until = _range(days, since, until)
    totals = await reports.event_counts(db, since, until)
    sent = max(1, totals["sent"])
    return {
        "since": since.isoformat(),
        "until": until.isoformat(),
        "totals": totals,
        "rates": {
            "delivery_rate": round(totals["delivered"] / sent, 4),
            "bounce_rate": round(totals["bounced"] / sent, 4),
            "complaint_rate": round(totals["complained"] / sent, 4),
        },
        "daily": await reports.daily_series(db, since, until),
    }


@router.get("/reports/breakdown")
async def report_breakdown(db: DB, _: Reports, group: Literal["user", "provider"] = "user",
                           days: int = Query(default=7, ge=1, le=366),
                           since: datetime | None = None, until: datetime | None = None) -> dict[str, Any]:
    since, until = _range(days, since, until)
    return {"group": group, "rows": await reports.breakdown(db, since, until, group)}


# --------------------------------------------------------------------------- suppressions


@router.get("/suppressions", response_model=Page[SuppressionOut])
async def list_suppressions(db: DB, _: SRead, type: SuppressionType | None = None,
                            user_id: uuid.UUID | None = None, q: str | None = Query(default=None, max_length=320),
                            limit: int = Query(default=50, ge=1, le=200), cursor: str | None = None,
                            ) -> Page[SuppressionOut]:
    stmt = select(Suppression)
    if type:
        stmt = stmt.where(Suppression.type == type)
    if user_id:
        stmt = stmt.where(Suppression.scope_user_id == user_id)
    if q:
        stmt = stmt.where(Suppression.email_normalized.like(f"%{like_escape(q.lower())}%"))
    rows, nxt = await paginate(db, stmt, [Suppression.id], limit, cursor)
    return Page(items=[SuppressionOut.model_validate(s) for s in rows], next_cursor=nxt)


@router.post("/suppressions", status_code=201)
async def add_suppression(body: SuppressionCreate, db: DB, actor: SWrite, ctx: Ctx) -> dict[str, str]:
    await suppression.add(db, body.email, body.type, body.user_id, reason=body.reason or "Added by admin",
                          created_by=actor.id)
    audit.record(db, ctx, "SUPPRESSION_ADDED", "suppression", None,
                 new={"email": body.email.lower(), "type": body.type.value, "user_id": body.user_id})
    await db.commit()
    return {"status": "suppressed"}


@router.delete("/suppressions/{suppression_id}", status_code=204)
async def remove_suppression(suppression_id: int, db: DB, _: SWrite, ctx: Ctx) -> None:
    row = await db.get(Suppression, suppression_id)
    if row is None:
        raise not_found("Suppression")
    if row.type == SuppressionType.COMPLAINT:
        # Complaints are sticky: removing them requires explicit re-consent handled outside the platform.
        raise bad_request("Complaint suppressions cannot be removed", "complaint_sticky")
    audit.record(db, ctx, "SUPPRESSION_REMOVED", "suppression", row.id,
                 old={"email": row.email_normalized, "type": row.type.value})
    await db.execute(delete(Suppression).where(Suppression.id == suppression_id))
    await db.commit()


# --------------------------------------------------------------------------- settings


@router.get("/settings")
async def get_settings(db: DB, _: SettingsRead) -> dict[str, Any]:
    settings_service.invalidate_cache()
    values = await settings_service.get_all(db)
    return {
        "values": values,
        "defaults": settings_service.DEFAULTS,
        "descriptions": settings_service.SETTING_DESCRIPTIONS,
    }


@router.put("/settings")
async def update_settings(body: SettingsUpdate, db: DB, actor: SettingsWrite, ctx: Ctx) -> dict[str, Any]:
    errors = {k: err for k, v in body.values.items() if (err := settings_service.validate_setting(k, v))}
    if errors:
        raise bad_request("Invalid settings", "invalid_settings", errors)
    current = await settings_service.get_all(db)
    old, new = audit.diff({k: current.get(k) for k in body.values}, body.values)
    for key, value in body.values.items():
        row = await db.get(SystemSetting, key)
        if row is None:
            db.add(SystemSetting(key=key, value=value, updated_by=actor.id))
        else:
            row.value = value
            row.updated_by = actor.id
    if new:
        audit.record(db, ctx, "SETTINGS_CHANGED", "settings", None, old=old, new=new)
    await db.commit()
    settings_service.invalidate_cache()
    return {"values": await settings_service.get_all(db)}


# --------------------------------------------------------------------------- audit logs


@router.get("/audit-logs", response_model=Page[AuditOut])
async def audit_logs(db: DB, _: AuditRead, action: str | None = Query(default=None, max_length=64),
                     resource: str | None = Query(default=None, max_length=64),
                     resource_id: str | None = Query(default=None, max_length=64),
                     admin_id: uuid.UUID | None = None,
                     limit: int = Query(default=50, ge=1, le=200), cursor: str | None = None) -> Page[AuditOut]:
    stmt = select(AuditLog)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if resource:
        stmt = stmt.where(AuditLog.resource == resource)
    if resource_id:
        stmt = stmt.where(AuditLog.resource_id == resource_id)
    if admin_id:
        stmt = stmt.where(AuditLog.admin_id == admin_id)
    rows, nxt = await paginate(db, stmt, [AuditLog.id], limit, cursor)
    actor_ids = {r.admin_id for r in rows if r.admin_id}
    names = {}
    if actor_ids:
        names = {u.id: u.username for u in (await db.execute(select(User).where(User.id.in_(actor_ids)))).scalars()}
    items = []
    for r in rows:
        out = AuditOut.model_validate(r)
        out.actor = names.get(r.admin_id) if r.admin_id else r.actor_type
        items.append(out)
    return Page(items=items, next_cursor=nxt)
