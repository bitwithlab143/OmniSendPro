"""Suppression list (ARCHITECTURE.md §24, design DS-07, OQ-05)."""

from __future__ import annotations

import uuid

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Suppression
from app.models.enums import GLOBAL_SUPPRESSION_TYPES, SuppressionType
from app.services.recipients import normalize_email


def scope_for(stype: SuppressionType, user_id: uuid.UUID | None) -> uuid.UUID | None:
    return None if stype in GLOBAL_SUPPRESSION_TYPES else user_id


async def add(
    db: AsyncSession,
    email: str,
    stype: SuppressionType,
    user_id: uuid.UUID | None,
    reason: str | None = None,
    source_event_id: int | None = None,
    created_by: uuid.UUID | None = None,
) -> None:
    """Idempotent insert (existing entries for the same scope/email are kept)."""
    stmt = (
        insert(Suppression)
        .values(
            scope_user_id=scope_for(stype, user_id),
            email_normalized=normalize_email(email),
            type=stype,
            reason=(reason or "")[:512] or None,
            source_event_id=source_event_id,
            created_by=created_by,
        )
        .on_conflict_do_nothing(index_elements=["scope_user_id", "email_normalized"])
    )
    await db.execute(stmt)


def applies_to(user_id: uuid.UUID):  # noqa: ANN201 - SQL expression
    """SQL condition: suppression entry applies to mail sent by `user_id`."""
    return or_(Suppression.scope_user_id.is_(None), Suppression.scope_user_id == user_id)


async def suppressed_set(db: AsyncSession, user_id: uuid.UUID, emails: list[str]) -> set[str]:
    if not emails:
        return set()
    rows = await db.execute(
        select(Suppression.email_normalized).where(
            and_(Suppression.email_normalized.in_(emails), applies_to(user_id))
        )
    )
    return {r[0] for r in rows}
