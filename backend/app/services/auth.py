"""Login, MFA, refresh-token rotation (DS-10, ADR-005)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import ApiError
from app.core.redis import Keys, get_redis
from app.core.security import (
    create_access_token,
    hash_password,
    new_opaque_token,
    password_needs_rehash,
    session_version,
    sha256_hex,
    verify_password,
)
from app.models import RefreshToken, User
from app.models.enums import ADMIN_PANEL_ROLES, RoleName, UserStatus

MAX_FAILED_LOGINS = 5
LOCKOUT_MINUTES = 15
IP_ATTEMPTS_PER_MINUTE = 20


@dataclass(slots=True)
class IssuedTokens:
    access_token: str
    expires_in: int
    refresh_token: str
    refresh_expires: datetime


def now() -> datetime:
    return datetime.now(UTC)


async def throttle_login_ip(ip: str | None) -> None:
    if not ip:
        return
    redis = get_redis()
    key = Keys.login_attempts(ip)
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, 60)
    if count > IP_ATTEMPTS_PER_MINUTE:
        raise ApiError(429, "rate_limited", "Too many login attempts. Try again in a minute.")


def app_allowed(user: User, app: str) -> bool:
    role = RoleName(user.role.name)
    return role in ADMIN_PANEL_ROLES if app == "admin" else role == RoleName.USER


async def authenticate(db: AsyncSession, login: str, password: str, app: str) -> User:
    login = login.strip().lower()
    user = (
        await db.execute(
            select(User).where(or_(func.lower(User.email) == login, func.lower(User.username) == login))
        )
    ).scalar_one_or_none()

    invalid = ApiError(401, "invalid_credentials", "Invalid login or password")
    if user is None:
        verify_password(None, password)  # constant-ish time
        raise invalid
    if user.locked_until and user.locked_until > now():
        raise ApiError(423, "account_locked", "Account temporarily locked after repeated failures")
    if not verify_password(user.password_hash, password):
        user.failed_login_count += 1
        if user.failed_login_count >= MAX_FAILED_LOGINS:
            user.locked_until = now() + timedelta(minutes=LOCKOUT_MINUTES)
            user.failed_login_count = 0
        await db.commit()
        raise invalid
    if user.status != UserStatus.ACTIVE:
        raise ApiError(403, "account_inactive", f"Account is {user.status.value}")
    if not app_allowed(user, app):
        raise ApiError(403, "wrong_app", "This account cannot sign in to this application")
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.failed_login_count = 0
    user.locked_until = None
    return user


async def issue_tokens(
    db: AsyncSession,
    user: User,
    app: str,
    ip: str | None,
    user_agent: str | None,
    family_id: uuid.UUID | None = None,
) -> IssuedTokens:
    settings = get_settings()
    access, ttl = create_access_token(user.id, user.role.name, app, session_version(user.password_changed_at))
    raw = new_opaque_token()
    expires = now() + timedelta(days=settings.refresh_token_days)
    db.add(
        RefreshToken(
            user_id=user.id,
            family_id=family_id or uuid.uuid4(),
            token_hash=sha256_hex(raw),
            app=app,
            expires_at=expires,
            ip=ip,
            user_agent=(user_agent or "")[:512] or None,
        )
    )
    user.last_login_at = now() if family_id is None else user.last_login_at
    await db.commit()
    return IssuedTokens(access_token=access, expires_in=ttl, refresh_token=raw, refresh_expires=expires)


async def rotate_refresh(
    db: AsyncSession, raw: str, app: str, ip: str | None, user_agent: str | None
) -> tuple[User, IssuedTokens]:
    invalid = ApiError(401, "invalid_refresh", "Session expired. Please sign in again.")
    token = (
        await db.execute(select(RefreshToken).where(RefreshToken.token_hash == sha256_hex(raw)).with_for_update())
    ).scalar_one_or_none()
    if token is None or token.app != app:
        raise invalid
    if token.revoked_at is not None:
        # Reuse of a rotated token → assume theft, revoke the whole family.
        await db.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == token.family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now())
        )
        await db.commit()
        raise invalid
    if token.expires_at <= now():
        raise invalid
    user = await db.get(User, token.user_id)
    if user is None or user.status != UserStatus.ACTIVE or not app_allowed(user, app):
        raise invalid
    token.revoked_at = now()
    issued = await issue_tokens(db, user, app, ip, user_agent, family_id=token.family_id)
    return user, issued


async def revoke_refresh(db: AsyncSession, raw: str | None) -> None:
    if not raw:
        return
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.token_hash == sha256_hex(raw), RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now())
    )
    await db.commit()


async def revoke_all_for_user(db: AsyncSession, user_id: uuid.UUID) -> None:
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now())
    )
