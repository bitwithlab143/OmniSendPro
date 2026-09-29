"""/api/v1/admin/api-keys — manage users' API keys (§6 "System → API Keys", design DS-17)."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from app.api.deps import DB, Ctx, Principal, require
from app.core.errors import not_found
from app.models import ApiKey, User
from app.schemas.domain import AdminApiKeyCreate, ApiKeyCreated, ApiKeyOut
from app.services import api_keys, audit

router = APIRouter(prefix="/api-keys", tags=["admin:api-keys"])

Read = Annotated[Principal, Depends(require("users.read"))]
Write = Annotated[Principal, Depends(require("users.write"))]


@router.get("/scopes")
async def scopes(_: Read) -> dict[str, Any]:
    return {"scopes": list(api_keys.SCOPES)}


@router.get("", response_model=list[ApiKeyOut])
async def list_keys(db: DB, _: Read, user_id: uuid.UUID | None = None,
                    include_revoked: Annotated[bool, Query()] = False) -> list[dict[str, Any]]:
    return [api_keys.serialize(k) for k in await api_keys.list_keys(db, user_id, include_revoked)]


@router.post("", response_model=ApiKeyCreated, status_code=201)
async def create_key(body: AdminApiKeyCreate, db: DB, me: Write, ctx: Ctx) -> dict[str, Any]:
    owner = (await db.execute(select(User).where(User.id == body.user_id))).scalar_one_or_none()
    if owner is None:
        raise not_found("User")
    key, token = await api_keys.create(db, owner, body.name, body.scopes, body.expires_in_days, me.id)
    audit.record(db, ctx, "API_KEY_CREATED", "api_key", key.id,
                 new={"user_id": str(owner.id), "name": key.name, "prefix": key.prefix, "scopes": key.scopes})
    await db.commit()
    return api_keys.serialize(key, token)


@router.delete("/{key_id}", response_model=ApiKeyOut)
async def revoke_key(key_id: uuid.UUID, db: DB, _: Write, ctx: Ctx) -> dict[str, Any]:
    key = await db.get(ApiKey, key_id)
    if key is None:
        raise not_found("API key")
    if await api_keys.revoke(db, key):
        audit.record(db, ctx, "API_KEY_REVOKED", "api_key", key.id,
                     new={"user_id": str(key.user_id), "prefix": key.prefix})
    await db.commit()
    return api_keys.serialize(key)
