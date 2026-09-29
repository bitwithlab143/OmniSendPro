"""Shared FastAPI dependencies: DB session, authentication, RBAC, request context."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError, forbidden, unauthorized
from app.core.rbac import permissions_for
from app.core.security import USER_AUDIENCE, WORKER_AUDIENCE, decode_token, session_version
from app.db.session import get_db
from app.models import ApiKey, User, Worker
from app.models.enums import ADMIN_PANEL_ROLES, RoleName, UserStatus
from app.services import api_keys
from app.services.audit import RequestContext

DB = Annotated[AsyncSession, Depends(get_db)]

_bearer = HTTPBearer(auto_error=False)


def client_ip(request: Request) -> str | None:
    # Nginx sets X-Real-IP; uvicorn --proxy-headers handles X-Forwarded-For for request.client.
    return request.headers.get("x-real-ip") or (request.client.host if request.client else None)


@dataclass(slots=True)
class Principal:
    user: User
    permissions: set[str]
    app: str  # "admin" | "user" (panel sessions) | "api" (API key, design DS-17)
    api_key: ApiKey | None = None

    @property
    def id(self) -> uuid.UUID:
        return self.user.id

    @property
    def role(self) -> RoleName:
        return RoleName(self.user.role.name)

    def can(self, permission: str) -> bool:
        return permission in self.permissions


async def current_principal(
    request: Request,
    db: DB,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    if creds is None or creds.scheme.lower() != "bearer":
        raise unauthorized()
    if api_keys.looks_like_key(creds.credentials):
        key, permissions = await api_keys.authenticate(db, creds.credentials, client_ip(request))
        principal = Principal(user=key.user, permissions=permissions, app="api", api_key=key)
        request.state.principal = principal
        return principal
    try:
        claims = decode_token(creds.credentials, USER_AUDIENCE)
        user_id = uuid.UUID(claims["sub"])
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise ApiError(401, "invalid_token", "Invalid or expired access token") from exc
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None or user.status != UserStatus.ACTIVE:
        raise ApiError(401, "invalid_token", "Account is not active")
    if claims.get("sv", 0) != session_version(user.password_changed_at):
        raise ApiError(401, "invalid_token", "Session expired after a password change")
    principal = Principal(user=user, permissions=permissions_for(user.role.name), app=claims.get("app", "user"))
    request.state.principal = principal
    return principal


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]


async def session_principal(principal: CurrentPrincipal) -> Principal:
    """Interactive panel session only: API keys are refused (e.g. /auth/me, profile and key management)."""
    if principal.app == "api":
        raise forbidden("Not available with an API key")
    return principal


SessionPrincipal = Annotated[Principal, Depends(session_principal)]


async def admin_principal(principal: CurrentPrincipal) -> Principal:
    if principal.role not in ADMIN_PANEL_ROLES or principal.app != "admin":
        raise forbidden("Admin panel access required")
    return principal


async def user_principal(principal: CurrentPrincipal) -> Principal:
    if principal.role != RoleName.USER or principal.app not in ("user", "api"):
        raise forbidden("User panel access required")
    return principal


AdminPrincipal = Annotated[Principal, Depends(admin_principal)]
UserPrincipal = Annotated[Principal, Depends(user_principal)]


def require(permission: str) -> Callable[..., object]:
    """Dependency factory: admin principal holding `permission`."""

    async def _dep(principal: AdminPrincipal) -> Principal:
        if not principal.can(permission):
            raise forbidden(f"Missing permission: {permission}")
        return principal

    return _dep


def require_user(permission: str, *, session_only: bool = False) -> Callable[..., object]:
    """User-API principal holding `permission`; `session_only` refuses API keys (profile, 2FA, keys)."""

    async def _dep(principal: UserPrincipal) -> Principal:
        if session_only and principal.app == "api":
            raise forbidden("Not available with an API key")
        if not principal.can(permission):
            raise forbidden(f"Missing permission: {permission}")
        return principal

    return _dep


def request_context(request: Request) -> RequestContext:
    principal: Principal | None = getattr(request.state, "principal", None)
    return RequestContext(
        actor_id=principal.id if principal else None,
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )


Ctx = Annotated[RequestContext, Depends(request_context)]


async def current_worker(
    db: DB, creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]
) -> Worker:
    if creds is None:
        raise unauthorized()
    try:
        claims = decode_token(creds.credentials, WORKER_AUDIENCE)
        worker_pk = uuid.UUID(claims["sub"])
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise ApiError(401, "invalid_token", "Invalid or expired worker token") from exc
    worker = await db.get(Worker, worker_pk)
    if worker is None or worker.disabled:
        raise ApiError(401, "worker_disabled", "Worker is disabled or unknown")
    return worker


CurrentWorker = Annotated[Worker, Depends(current_worker)]
