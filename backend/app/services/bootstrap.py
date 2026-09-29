"""Idempotent reference data (roles, permissions) and first super-admin bootstrap."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.rbac import PERMISSIONS, ROLE_DESCRIPTIONS, ROLE_PERMISSIONS
from app.core.security import hash_password, validate_password_strength
from app.models import Permission, Role, User, role_permissions
from app.models.enums import RoleName, UserStatus

log = logging.getLogger("omnisend.bootstrap")


async def ensure_reference_data(db: AsyncSession) -> None:
    await db.execute(
        insert(Permission)
        .values([{"code": code, "description": desc} for code, desc in PERMISSIONS.items()])
        .on_conflict_do_nothing(index_elements=["code"])
    )
    await db.execute(
        insert(Role)
        .values([{"name": r.value, "description": ROLE_DESCRIPTIONS[r]} for r in RoleName])
        .on_conflict_do_nothing(index_elements=["name"])
    )
    roles = {r.name: r.id for r in (await db.execute(select(Role))).scalars()}
    perms = {p.code: p.id for p in (await db.execute(select(Permission))).scalars()}
    desired = {(roles[role.value], perms[code]) for role, codes in ROLE_PERMISSIONS.items() for code in codes}
    existing = {(r.role_id, r.permission_id) for r in (await db.execute(select(role_permissions))).all()}
    missing = desired - existing
    stale = existing - desired
    if missing:
        await db.execute(
            insert(role_permissions)
            .values([{"role_id": r, "permission_id": p} for r, p in missing])
            .on_conflict_do_nothing()
        )
    for role_id, perm_id in stale:
        await db.execute(
            role_permissions.delete().where(
                role_permissions.c.role_id == role_id, role_permissions.c.permission_id == perm_id
            )
        )
    await db.commit()


async def create_super_admin(db: AsyncSession, email: str, password: str, username: str = "admin") -> User:
    if err := validate_password_strength(password):
        raise ValueError(err)
    role = (await db.execute(select(Role).where(Role.name == RoleName.SUPER_ADMIN.value))).scalar_one()
    user = User(email=email.lower(), username=username, password_hash=hash_password(password), role_id=role.id,
                status=UserStatus.ACTIVE, full_name="Administrator", password_changed_at=datetime.now(UTC))
    db.add(user)
    await db.commit()
    return user


async def bootstrap_admin_from_env(db: AsyncSession) -> None:
    """Create the first SUPER_ADMIN from BOOTSTRAP_ADMIN_* only when no admin exists yet."""
    settings = get_settings()
    if not settings.bootstrap_admin_email or not settings.bootstrap_admin_password:
        return
    count = (
        await db.execute(select(func.count()).select_from(User).join(Role).where(Role.name == RoleName.SUPER_ADMIN.value))
    ).scalar_one()
    if count:
        return
    await create_super_admin(db, settings.bootstrap_admin_email, settings.bootstrap_admin_password)
    log.warning("bootstrap_admin_created", extra={"email": settings.bootstrap_admin_email})
