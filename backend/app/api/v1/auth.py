"""/api/v1/auth — login, MFA, refresh, logout, me (DS-10, ADR-005)."""

from __future__ import annotations

import uuid

import jwt
from fastapi import APIRouter, Request, Response

from app.api.deps import DB, CurrentPrincipal, client_ip
from app.core.config import get_settings
from app.core.errors import ApiError
from app.core.rbac import permissions_for
from app.core.redis import get_redis
from app.core.security import (
    MFA_AUDIENCE,
    create_mfa_token,
    decode_token,
    decrypt_secret,
    encrypt_secret,
    new_totp_secret,
    totp_uri,
    verify_totp,
)
from app.models import User
from app.models.enums import UserStatus
from app.schemas.auth import (
    LoginRequest,
    LoginResponse,
    MeResponse,
    MfaVerifyRequest,
    RefreshRequest,
    TokenResponse,
)
from app.services import audit
from app.services import auth as auth_service
from app.services.audit import RequestContext

router = APIRouter(prefix="/auth", tags=["auth"])

COOKIE_PATH = "/api/v1/auth"


def cookie_name(app: str) -> str:
    return f"osp_{app}_rt"


def me_response(user: User) -> MeResponse:
    return MeResponse(
        id=user.id,
        email=user.email,
        username=user.username,
        full_name=user.full_name,
        role=user.role.name,
        permissions=sorted(permissions_for(user.role.name)),
        totp_enabled=user.totp_enabled,
        last_login_at=user.last_login_at,
    )


def _set_refresh_cookie(response: Response, app: str, issued: auth_service.IssuedTokens) -> None:
    settings = get_settings()
    response.set_cookie(
        cookie_name(app),
        issued.refresh_token,
        max_age=settings.refresh_token_days * 86400,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="strict",
        path=COOKIE_PATH,
    )


def _require_csrf_header(request: Request) -> None:
    # Defence in depth on top of SameSite=Strict: a cross-site form cannot set custom headers.
    if request.headers.get("x-requested-with", "").lower() != "xmlhttprequest":
        raise ApiError(403, "csrf", "Missing X-Requested-With header")


async def _check_totp_replay(user_id: uuid.UUID, code: str) -> None:
    key = f"totp:used:{user_id}:{code}"
    if not await get_redis().set(key, "1", ex=90, nx=True):
        raise ApiError(401, "invalid_code", "This code was already used. Wait for the next one.")


@router.post("/login", response_model=LoginResponse, response_model_exclude_none=True)
async def login(body: LoginRequest, request: Request, response: Response, db: DB) -> LoginResponse:
    ip = client_ip(request)
    await auth_service.throttle_login_ip(ip)
    user = await auth_service.authenticate(db, body.login, body.password, body.app)
    settings = get_settings()

    if user.totp_enabled and user.totp_secret_encrypted:
        await db.commit()
        return LoginResponse(status="mfa_required", mfa_token=create_mfa_token(user.id, body.app, "verify"))

    if body.app == "admin" and settings.admin_require_2fa:
        secret = new_totp_secret()
        user.totp_secret_encrypted = encrypt_secret(secret)
        await db.commit()
        return LoginResponse(
            status="mfa_setup_required",
            mfa_token=create_mfa_token(user.id, body.app, "setup"),
            otpauth_uri=totp_uri(secret, user.email),
            totp_secret=secret,
        )

    issued = await auth_service.issue_tokens(db, user, body.app, ip, request.headers.get("user-agent"))
    _set_refresh_cookie(response, body.app, issued)
    return LoginResponse(status="ok", access_token=issued.access_token, expires_in=issued.expires_in,
                         user=me_response(user))


@router.post("/mfa/verify", response_model=TokenResponse)
async def mfa_verify(body: MfaVerifyRequest, request: Request, response: Response, db: DB) -> TokenResponse:
    ip = client_ip(request)
    await auth_service.throttle_login_ip(ip)
    try:
        claims = decode_token(body.mfa_token, MFA_AUDIENCE)
        user_id = uuid.UUID(claims["sub"])
        app, purpose = claims["app"], claims["purpose"]
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise ApiError(401, "invalid_mfa_token", "Verification expired. Please sign in again.") from exc

    user = await db.get(User, user_id)
    if user is None or user.status != UserStatus.ACTIVE or not user.totp_secret_encrypted:
        raise ApiError(401, "invalid_mfa_token", "Verification expired. Please sign in again.")
    if not verify_totp(decrypt_secret(user.totp_secret_encrypted), body.code):
        raise ApiError(401, "invalid_code", "Invalid verification code")
    await _check_totp_replay(user.id, body.code)

    if purpose == "setup" and not user.totp_enabled:
        user.totp_enabled = True
        audit.record(db, RequestContext(user.id, ip, request.headers.get("user-agent")),
                     "TWO_FACTOR_ENABLED", "user", user.id)
    issued = await auth_service.issue_tokens(db, user, app, ip, request.headers.get("user-agent"))
    _set_refresh_cookie(response, app, issued)
    return TokenResponse(access_token=issued.access_token, expires_in=issued.expires_in, user=me_response(user))


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest, request: Request, response: Response, db: DB) -> TokenResponse:
    _require_csrf_header(request)
    raw = request.cookies.get(cookie_name(body.app))
    if not raw:
        raise ApiError(401, "invalid_refresh", "Session expired. Please sign in again.")
    user, issued = await auth_service.rotate_refresh(
        db, raw, body.app, client_ip(request), request.headers.get("user-agent")
    )
    _set_refresh_cookie(response, body.app, issued)
    return TokenResponse(access_token=issued.access_token, expires_in=issued.expires_in, user=me_response(user))


@router.post("/logout", status_code=204)
async def logout(body: RefreshRequest, request: Request, response: Response, db: DB) -> Response:
    _require_csrf_header(request)
    await auth_service.revoke_refresh(db, request.cookies.get(cookie_name(body.app)))
    response.delete_cookie(cookie_name(body.app), path=COOKIE_PATH)
    response.status_code = 204
    return response


@router.get("/me", response_model=MeResponse)
async def me(principal: CurrentPrincipal) -> MeResponse:
    return me_response(principal.user)
