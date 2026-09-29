"""/api/v1/admin/users (DS-09): list, create, update, suspend/activate, limits, password, 2FA reset."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import DB, Ctx, Principal, require
from app.core.errors import ApiError, bad_request, conflict, forbidden, not_found
from app.core.pagination import Page, like_escape, paginate
from app.core.security import hash_password, validate_password_strength
from app.models import Role, User, UserLimits
from app.models.enums import RoleName, UserStatus
from app.schemas.users import LimitsIn, LimitsOut, PasswordReset, UserCreate, UserOut, UserUpdate
from app.services import audit
from app.services import auth as auth_service

router = APIRouter(prefix="/users", tags=["admin:users"])

Read = Annotated[Principal, Depends(require("users.read"))]
Write = Annotated[Principal, Depends(require("users.write"))]


async def _role(db: DB, name: RoleName) -> Role:
    role = (await db.execute(select(Role).where(Role.name == name.value))).scalar_one_or_none()
    if role is None:
        raise ApiError(500, "roles_missing", "Reference data not initialised")
    return role


def _guard_role_change(actor: Principal, target_role: RoleName, target: User | None = None) -> None:
    """Only SUPER_ADMIN may create or modify SUPER_ADMIN / ADMIN accounts (least privilege)."""
    privileged = {RoleName.SUPER_ADMIN, RoleName.ADMIN}
    touching_privileged = target_role in privileged or (
        target is not None and RoleName(target.role.name) in privileged
    )
    if touching_privileged and actor.role != RoleName.SUPER_ADMIN:
        raise forbidden("Only a super admin can manage admin accounts")


async def _get(db: DB, user_id: uuid.UUID) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise not_found("User")
    return user


async def _unique(db: DB, email: str | None, username: str | None, exclude: uuid.UUID | None = None) -> None:
    conds = []
    if email:
        conds.append(func.lower(User.email) == email.lower())
    if username:
        conds.append(func.lower(User.username) == username.lower())
    if not conds:
        return
    stmt = select(User.id).where(or_(*conds))
    if exclude:
        stmt = stmt.where(User.id != exclude)
    if (await db.execute(stmt)).first():
        raise conflict("A user with this email or username already exists", "duplicate_user")


@router.get("", response_model=Page[UserOut])
async def list_users(
    db: DB,
    _: Read,
    status: UserStatus | None = None,
    role: RoleName | None = None,
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
) -> Page[UserOut]:
    stmt = select(User)
    if status:
        stmt = stmt.where(User.status == status)
    if role:
        stmt = stmt.join(Role).where(Role.name == role.value)
    if q:
        like = f"%{like_escape(q.lower())}%"
        stmt = stmt.where(or_(func.lower(User.email).like(like), func.lower(User.username).like(like),
                              func.lower(User.full_name).like(like)))
    rows, nxt = await paginate(db, stmt, [User.created_at, User.id], limit, cursor)
    return Page(items=[UserOut.model_validate(u) for u in rows], next_cursor=nxt)


@router.post("", response_model=UserOut, status_code=201)
async def create_user(body: UserCreate, db: DB, actor: Write, ctx: Ctx) -> UserOut:
    _guard_role_change(actor, body.role)
    if err := validate_password_strength(body.password):
        raise bad_request(err, "weak_password")
    await _unique(db, body.email, body.username)
    user = User(
        email=body.email.lower(),
        username=body.username,
        full_name=body.full_name,
        password_hash=hash_password(body.password),
        role_id=(await _role(db, body.role)).id,
        status=UserStatus.ACTIVE,
        password_changed_at=datetime.now(UTC),
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise conflict("A user with this email or username already exists", "duplicate_user") from exc
    if body.limits:
        db.add(UserLimits(user_id=user.id, updated_by=actor.id, **body.limits.model_dump()))
    audit.record(db, ctx, "USER_CREATED", "user", user.id,
                 new={"email": user.email, "username": user.username, "role": body.role.value})
    await db.commit()
    await db.refresh(user)
    return UserOut.model_validate(user)


@router.get("/{user_id}", response_model=UserOut)
async def get_user(user_id: uuid.UUID, db: DB, _: Read) -> UserOut:
    return UserOut.model_validate(await _get(db, user_id))


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(user_id: uuid.UUID, body: UserUpdate, db: DB, actor: Write, ctx: Ctx) -> UserOut:
    user = await _get(db, user_id)
    data = body.model_dump(exclude_unset=True, exclude_none=True)
    _guard_role_change(actor, RoleName(data.get("role", user.role.name)), user)
    if user.id == actor.id and "role" in data and data["role"] != RoleName(user.role.name):
        raise forbidden("You cannot change your own role")
    await _unique(db, data.get("email"), data.get("username"), exclude=user.id)
    before: dict[str, Any] = {"email": user.email, "username": user.username, "full_name": user.full_name,
                              "role": user.role.name}
    if "email" in data:
        user.email = data["email"].lower()
    if "username" in data:
        user.username = data["username"]
    if "full_name" in data:
        user.full_name = data["full_name"]
    if "role" in data:
        user.role_id = (await _role(db, data["role"])).id
        await auth_service.revoke_all_for_user(db, user.id)
    await db.flush()
    await db.refresh(user)
    after = {"email": user.email, "username": user.username, "full_name": user.full_name, "role": user.role.name}
    old, new = audit.diff(before, after)
    if new:
        audit.record(db, ctx, "USER_UPDATED" if "role" not in new else "USER_ROLE_CHANGED", "user", user.id,
                     old=old, new=new)
    await db.commit()
    return UserOut.model_validate(user)


async def _set_status(db: DB, actor: Principal, ctx: Ctx, user_id: uuid.UUID, status: UserStatus,
                      action: str) -> UserOut:
    user = await _get(db, user_id)
    _guard_role_change(actor, RoleName(user.role.name), user)
    if user.id == actor.id:
        raise forbidden("You cannot change the status of your own account")
    old = user.status
    user.status = status
    if status != UserStatus.ACTIVE:
        await auth_service.revoke_all_for_user(db, user.id)
    else:
        user.locked_until = None
        user.failed_login_count = 0
    audit.record(db, ctx, action, "user", user.id, old={"status": old.value}, new={"status": status.value})
    await db.commit()
    await db.refresh(user)
    return UserOut.model_validate(user)


@router.post("/{user_id}/suspend", response_model=UserOut)
async def suspend_user(user_id: uuid.UUID, db: DB, actor: Write, ctx: Ctx) -> UserOut:
    return await _set_status(db, actor, ctx, user_id, UserStatus.SUSPENDED, "USER_SUSPENDED")


@router.post("/{user_id}/activate", response_model=UserOut)
async def activate_user(user_id: uuid.UUID, db: DB, actor: Write, ctx: Ctx) -> UserOut:
    return await _set_status(db, actor, ctx, user_id, UserStatus.ACTIVE, "USER_ACTIVATED")


@router.post("/{user_id}/disable", response_model=UserOut)
async def disable_user(user_id: uuid.UUID, db: DB, actor: Write, ctx: Ctx) -> UserOut:
    return await _set_status(db, actor, ctx, user_id, UserStatus.DISABLED, "USER_DISABLED")


@router.put("/{user_id}/limits", response_model=LimitsOut)
async def set_limits(user_id: uuid.UUID, body: LimitsIn, db: DB, actor: Write, ctx: Ctx) -> LimitsOut:
    user = await _get(db, user_id)
    limits = (await db.execute(select(UserLimits).where(UserLimits.user_id == user.id))).scalar_one_or_none()
    before = LimitsIn.model_validate(limits, from_attributes=True).model_dump() if limits else {}
    if limits is None:
        limits = UserLimits(user_id=user.id)
        db.add(limits)
    for key, value in body.model_dump().items():
        setattr(limits, key, value)
    limits.updated_by = actor.id
    old, new = audit.diff(before, body.model_dump())
    audit.record(db, ctx, "LIMIT_CHANGED", "user", user.id, old=old, new=new)
    await db.commit()
    await db.refresh(limits)
    return LimitsOut.model_validate(limits)


@router.post("/{user_id}/password", status_code=204)
async def reset_password(user_id: uuid.UUID, body: PasswordReset, db: DB, actor: Write, ctx: Ctx) -> None:
    user = await _get(db, user_id)
    _guard_role_change(actor, RoleName(user.role.name), user)
    if err := validate_password_strength(body.password):
        raise bad_request(err, "weak_password")
    user.password_hash = hash_password(body.password)
    user.password_changed_at = datetime.now(UTC)
    await auth_service.revoke_all_for_user(db, user.id)
    audit.record(db, ctx, "USER_PASSWORD_RESET", "user", user.id)
    await db.commit()


@router.post("/{user_id}/reset-2fa", response_model=UserOut)
async def reset_2fa(user_id: uuid.UUID, db: DB, actor: Write, ctx: Ctx) -> UserOut:
    user = await _get(db, user_id)
    _guard_role_change(actor, RoleName(user.role.name), user)
    user.totp_enabled = False
    user.totp_secret_encrypted = None
    await auth_service.revoke_all_for_user(db, user.id)
    audit.record(db, ctx, "USER_2FA_RESET", "user", user.id)
    await db.commit()
    await db.refresh(user)
    return UserOut.model_validate(user)
