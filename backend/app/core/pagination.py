"""Cursor (keyset) pagination (ARCHITECTURE.md §54, DS-09)."""

from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel
from sqlalchemy import Select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.core.errors import bad_request

T = TypeVar("T")

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


class Page(BaseModel, Generic[T]):
    items: list[T]
    next_cursor: str | None = None


def like_escape(value: str) -> str:
    """Escape LIKE wildcards in user-supplied search text (PostgreSQL default escape char is backslash)."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _encode(values: list[Any]) -> str:
    def conv(v: Any) -> Any:
        if isinstance(v, datetime):
            return {"t": v.isoformat()}
        if isinstance(v, uuid.UUID):
            return {"u": str(v)}
        return v

    return base64.urlsafe_b64encode(json.dumps([conv(v) for v in values]).encode()).decode().rstrip("=")


def _decode(cursor: str) -> list[Any]:
    try:
        raw = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    except (ValueError, json.JSONDecodeError) as exc:
        raise bad_request("Invalid cursor", "invalid_cursor") from exc

    def conv(v: Any) -> Any:
        if isinstance(v, dict) and "t" in v:
            return datetime.fromisoformat(v["t"])
        if isinstance(v, dict) and "u" in v:
            return uuid.UUID(v["u"])
        return v

    if not isinstance(raw, list):
        raise bad_request("Invalid cursor", "invalid_cursor")
    return [conv(v) for v in raw]


async def paginate(
    db: AsyncSession,
    stmt: Select[Any],
    order: list[InstrumentedAttribute[Any]],
    limit: int | None,
    cursor: str | None,
) -> tuple[list[Any], str | None]:
    """Descending keyset pagination over `order` columns (last column must be unique, e.g. id)."""
    limit = max(1, min(limit or DEFAULT_LIMIT, MAX_LIMIT))
    if cursor:
        values = _decode(cursor)
        if len(values) != len(order):
            raise bad_request("Invalid cursor", "invalid_cursor")
        stmt = stmt.where(tuple_(*order) < tuple_(*values))
    stmt = stmt.order_by(*[c.desc() for c in order]).limit(limit + 1)
    rows = list((await db.execute(stmt)).unique().scalars().all())
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1]
        next_cursor = _encode([getattr(last, c.key) for c in order])
    return rows, next_cursor
