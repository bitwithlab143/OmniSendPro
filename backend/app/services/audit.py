"""Append-only audit logging of sensitive actions (ARCHITECTURE.md §45, DS-11)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog

_REDACT = {"password", "password_hash", "secret", "encrypted_secret", "totp_secret_encrypted",
           "webhook_secret", "webhook_secret_encrypted", "credential", "credential_hash", "token"}


@dataclass(slots=True)
class RequestContext:
    actor_id: uuid.UUID | None
    ip: str | None
    user_agent: str | None
    actor_type: str = "user"


SYSTEM = RequestContext(actor_id=None, ip=None, user_agent=None, actor_type="system")


def _clean(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    out: dict[str, Any] = {}
    for k, v in value.items():
        if k in _REDACT:
            out[k] = "***"
        elif isinstance(v, uuid.UUID):
            out[k] = str(v)
        elif hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        elif isinstance(v, dict):
            out[k] = _clean(v)
        else:
            out[k] = v
    return out


def record(
    db: AsyncSession,
    ctx: RequestContext,
    action: str,
    resource: str,
    resource_id: object | None = None,
    old: dict[str, Any] | None = None,
    new: dict[str, Any] | None = None,
) -> None:
    """Add an audit row to the current transaction (committed with the change it describes)."""
    db.add(
        AuditLog(
            admin_id=ctx.actor_id,
            actor_type=ctx.actor_type,
            action=action,
            resource=resource,
            resource_id=str(resource_id) if resource_id is not None else None,
            old_value=_clean(old),
            new_value=_clean(new),
            ip=ctx.ip,
            user_agent=(ctx.user_agent or "")[:512] or None,
        )
    )


def diff(before: dict[str, Any], after: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    changed = {k for k in after if before.get(k) != after.get(k)}
    return {k: before.get(k) for k in changed}, {k: after.get(k) for k in changed}
