"""API keys for the User API — design DS-17, ADR-012.

Format ``osk_<prefix>_<secret>``: the prefix identifies the key in lists, only the SHA-256 of the whole
key is stored. Effective permissions are the key's scopes intersected with the owner's role, evaluated on
every request, so suspending the owner or changing their role immediately limits the key.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError, bad_request
from app.core.rbac import permissions_for
from app.core.redis import get_redis
from app.core.security import sha256_hex
from app.models import ApiKey, User
from app.models.enums import RoleName, UserStatus
from app.services import settings as settings_service

KEY_PREFIX = "osk_"
SCOPES: tuple[str, ...] = tuple(sorted(permissions_for(RoleName.USER.value)))
MAX_ACTIVE_KEYS_PER_USER = 20
LAST_USED_RESOLUTION = timedelta(minutes=1)


def looks_like_key(token: str) -> bool:
    return token.startswith(KEY_PREFIX)


def _generate() -> tuple[str, str]:
    prefix = secrets.token_hex(4)
    return prefix, f"{KEY_PREFIX}{prefix}_{secrets.token_urlsafe(32)}"


def is_active(key: ApiKey, now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC)
    return key.revoked_at is None and (key.expires_at is None or key.expires_at > now)


async def create(db: AsyncSession, owner: User, name: str, scopes: list[str], expires_in_days: int | None,
                 created_by: uuid.UUID | None) -> tuple[ApiKey, str]:
    if owner.role.name != RoleName.USER.value:
        raise bad_request("API keys can only belong to USER accounts", "invalid_owner")
    if owner.status != UserStatus.ACTIVE:
        raise bad_request("The account is not active", "inactive_owner")
    unknown = sorted(set(scopes) - set(SCOPES))
    if unknown:
        raise bad_request(f"Unknown scope(s): {', '.join(unknown)}", "invalid_scope")
    if not scopes:
        raise bad_request("Select at least one scope", "invalid_scope")
    now = datetime.now(UTC)
    active = (
        await db.execute(
            select(func.count()).select_from(ApiKey).where(
                ApiKey.user_id == owner.id, ApiKey.revoked_at.is_(None),
                (ApiKey.expires_at.is_(None)) | (ApiKey.expires_at > now),
            )
        )
    ).scalar_one()
    if active >= MAX_ACTIVE_KEYS_PER_USER:
        raise bad_request(f"At most {MAX_ACTIVE_KEYS_PER_USER} active API keys per user", "too_many_keys")
    prefix, token = _generate()
    key = ApiKey(
        user_id=owner.id, name=name.strip(), prefix=prefix, key_hash=sha256_hex(token),
        scopes=sorted(set(scopes)), created_by=created_by,
        expires_at=now + timedelta(days=expires_in_days) if expires_in_days else None,
    )
    db.add(key)
    await db.flush()
    return key, token


async def authenticate(db: AsyncSession, token: str, ip: str | None) -> tuple[ApiKey, set[str]]:
    """Resolve a presented key to (key, effective permissions) or raise 401/429."""
    key = (await db.execute(select(ApiKey).where(ApiKey.key_hash == sha256_hex(token)))).scalar_one_or_none()
    now = datetime.now(UTC)
    if key is None or not is_active(key, now):
        raise ApiError(401, "invalid_api_key", "Invalid, expired or revoked API key")
    owner = key.user
    if owner.status != UserStatus.ACTIVE or owner.role.name != RoleName.USER.value:
        raise ApiError(401, "invalid_api_key", "The key's account is not active")

    limit = int(await settings_service.get(db, "api_key_requests_per_minute"))
    redis = get_redis()
    bucket = f"ratelimit:apikey:{key.id}:{int(now.timestamp()) // 60}"
    count = await redis.incr(bucket)
    if count == 1:
        await redis.expire(bucket, 120)
    if count > limit:
        raise ApiError(429, "rate_limited", f"API key rate limit of {limit} requests per minute exceeded")

    if key.last_used_at is None or now - key.last_used_at >= LAST_USED_RESOLUTION:
        key.last_used_at = now
        key.last_used_ip = ip
        await db.commit()
    return key, set(key.scopes) & permissions_for(owner.role.name)


def serialize(key: ApiKey, token: str | None = None) -> dict[str, object]:
    out: dict[str, object] = {
        "id": key.id, "user_id": key.user_id, "username": key.user.username if key.user else None,
        "name": key.name, "prefix": key.prefix, "scopes": key.scopes, "created_by": key.created_by,
        "created_at": key.created_at, "expires_at": key.expires_at, "revoked_at": key.revoked_at,
        "last_used_at": key.last_used_at, "last_used_ip": key.last_used_ip, "active": is_active(key),
    }
    if token is not None:
        out["key"] = token
    return out


async def list_keys(db: AsyncSession, user_id: uuid.UUID | None, include_revoked: bool) -> list[ApiKey]:
    stmt = select(ApiKey).order_by(ApiKey.created_at.desc(), ApiKey.id.desc()).limit(500)
    if user_id is not None:
        stmt = stmt.where(ApiKey.user_id == user_id)
    if not include_revoked:
        stmt = stmt.where(ApiKey.revoked_at.is_(None))
    return list((await db.execute(stmt)).scalars().all())


async def revoke(db: AsyncSession, key: ApiKey) -> bool:
    """Revoke immediately. Returns False if it already was."""
    if key.revoked_at is not None:
        return False
    key.revoked_at = datetime.now(UTC)
    return True
