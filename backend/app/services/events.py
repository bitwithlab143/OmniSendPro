"""Delivery-event ingestion from provider webhooks (§23, DS-05) and unsubscribes (DS-07)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import unsign_value
from app.models import Campaign, CampaignRecipient, EmailEvent, EmailEventKey
from app.models.enums import EventType, RecipientStatus, SuppressionType
from app.services import health, suppression
from app.services.jobs import UNSUBSCRIBE_PURPOSE
from app.services.recipients import normalize_email

SUPPORTED = {"delivered", "bounced", "complained", "deferred", "unsubscribed"}
# Event types de-duplicated per (provider, message, type) through email_event_keys (ADR-013).
DEDUPED = {EventType.DELIVERED, EventType.BOUNCED, EventType.COMPLAINED, EventType.UNSUBSCRIBED}
# Final statuses a later event may not downgrade.
_RANK = {
    RecipientStatus.QUEUED: 0, RecipientStatus.DEFERRED: 1, RecipientStatus.SENT: 2, RecipientStatus.FAILED: 2,
    RecipientStatus.DELIVERED: 3, RecipientStatus.BOUNCED: 4, RecipientStatus.UNSUBSCRIBED: 5,
    RecipientStatus.COMPLAINED: 6,
}


@dataclass(slots=True)
class IngestResult:
    accepted: int = 0
    duplicates: int = 0
    unknown: int = 0
    errors: list[str] = field(default_factory=list)


async def _find_recipient(db: AsyncSession, ev: dict[str, Any]) -> CampaignRecipient | None:
    """Correlate an event to a sent message: provider message id first, else campaign + address."""
    mid = ev.get("provider_message_id")
    if mid:
        found = (
            await db.execute(select(CampaignRecipient).where(CampaignRecipient.provider_message_id == str(mid)))
        ).scalar_one_or_none()
        if found is not None:
            return found
    campaign_id, email = ev.get("campaign_id"), ev.get("email")
    if campaign_id and email:
        return (
            await db.execute(
                select(CampaignRecipient).where(
                    CampaignRecipient.campaign_id == campaign_id,
                    CampaignRecipient.email_normalized == normalize_email(str(email)),
                    CampaignRecipient.provider_message_id.is_not(None),  # only messages we actually sent
                )
            )
        ).scalar_one_or_none()
    return None


async def ingest(db: AsyncSession, provider_id: uuid.UUID, events: list[dict[str, Any]]) -> IngestResult:
    result = IngestResult()
    for ev in events:
        etype = str(ev.get("type", "")).lower()
        if etype not in SUPPORTED or not (ev.get("provider_message_id") or (ev.get("campaign_id") and ev.get("email"))):
            result.errors.append(f"unsupported event {etype!r} or no message reference")
            continue
        recipient = await _find_recipient(db, ev)
        if recipient is None:
            result.unknown += 1
            continue
        campaign = await db.get(Campaign, recipient.campaign_id)
        assert campaign is not None
        if campaign.provider_id != provider_id:
            # A provider may only report on messages that were sent through it.
            result.unknown += 1
            continue
        bounce_type = str(ev.get("bounce_type") or "hard").lower()
        stored_type = EventType(etype)
        if etype == "bounced" and bounce_type != "hard":
            stored_type = EventType.DEFERRED  # soft bounce: informational only

        mid = recipient.provider_message_id
        if mid and stored_type in DEDUPED:
            fresh = (
                await db.execute(
                    insert(EmailEventKey)
                    .values(provider_id=provider_id, provider_message_id=mid, event_type=stored_type.value)
                    .on_conflict_do_nothing()
                    .returning(EmailEventKey.event_type)
                )
            ).scalar_one_or_none()
            if fresh is None:
                result.duplicates += 1
                continue
        inserted = (
            await db.execute(
                insert(EmailEvent)
                .values(
                    campaign_id=recipient.campaign_id,
                    recipient_id=recipient.id,
                    provider_id=provider_id,
                    user_id=campaign.user_id,
                    event_type=stored_type,
                    provider_message_id=mid,
                    error_code=(str(ev.get("error_code"))[:64] if ev.get("error_code") else None),
                    error_message=(str(ev.get("error_message"))[:512] if ev.get("error_message") else None),
                )
                .returning(EmailEvent.id)
            )
        ).scalar_one()
        result.accepted += 1
        await _apply(db, campaign, recipient, stored_type, inserted)
    await db.commit()
    return result


async def _apply(db: AsyncSession, campaign: Campaign, recipient: CampaignRecipient, etype: EventType,
                 event_id: int) -> None:
    new_status = {
        EventType.DELIVERED: RecipientStatus.DELIVERED,
        EventType.BOUNCED: RecipientStatus.BOUNCED,
        EventType.COMPLAINED: RecipientStatus.COMPLAINED,
        EventType.UNSUBSCRIBED: RecipientStatus.UNSUBSCRIBED,
    }.get(etype)
    counters: dict[str, Any] = {}
    if new_status is not None and recipient.status == new_status:
        # Already recorded (e.g. SMTP-time rejection followed by a DSN): keep the event, don't re-count.
        recipient.last_event_at = datetime.now(UTC)
        return
    if etype == EventType.DELIVERED:
        counters["delivered"] = Campaign.delivered + 1
    elif etype == EventType.BOUNCED:
        counters["bounced"] = Campaign.bounced + 1
        if recipient.status in (RecipientStatus.SENT, RecipientStatus.DELIVERED):
            # A message counted as sent later bounced: move it from sent to bounced.
            counters["sent"] = Campaign.sent - 1
            if recipient.status == RecipientStatus.DELIVERED:
                counters["delivered"] = Campaign.delivered - 1
        await suppression.add(db, recipient.email_normalized, SuppressionType.HARD_BOUNCE, campaign.user_id,
                              reason="Provider reported hard bounce", source_event_id=event_id)
        await health.bump(campaign.provider_id, bounces=1)
    elif etype == EventType.COMPLAINED:
        counters["complained"] = Campaign.complained + 1
        await suppression.add(db, recipient.email_normalized, SuppressionType.COMPLAINT, campaign.user_id,
                              reason="Recipient complaint (feedback loop)", source_event_id=event_id)
        await health.bump(campaign.provider_id, complaints=1)
    elif etype == EventType.UNSUBSCRIBED:
        counters["unsubscribed"] = Campaign.unsubscribed + 1
        await suppression.add(db, recipient.email_normalized, SuppressionType.UNSUBSCRIBE, campaign.user_id,
                              reason="Provider-reported unsubscribe", source_event_id=event_id)
    if counters:
        await db.execute(update(Campaign).where(Campaign.id == campaign.id).values(**counters))
    if new_status and _RANK.get(new_status, 0) >= _RANK.get(recipient.status, 0):
        recipient.status = new_status
    recipient.last_event_at = datetime.now(UTC)


def parse_unsubscribe_token(token: str) -> tuple[uuid.UUID, int] | None:
    value = unsign_value(token, UNSUBSCRIBE_PURPOSE)
    if not value:
        return None
    try:
        cid, rid = value.split(":", 1)
        return uuid.UUID(cid), int(rid)
    except ValueError:
        return None


async def unsubscribe(db: AsyncSession, token: str) -> CampaignRecipient | None:
    parsed = parse_unsubscribe_token(token)
    if parsed is None:
        return None
    campaign_id, recipient_id = parsed
    recipient = await db.get(CampaignRecipient, recipient_id)
    if recipient is None or recipient.campaign_id != campaign_id:
        return None
    campaign = await db.get(Campaign, campaign_id)
    assert campaign is not None
    already = recipient.status == RecipientStatus.UNSUBSCRIBED
    await suppression.add(db, recipient.email_normalized, SuppressionType.UNSUBSCRIBE, campaign.user_id,
                          reason="Recipient used the unsubscribe link")
    if not already:
        db.add(EmailEvent(campaign_id=campaign_id, recipient_id=recipient.id, provider_id=campaign.provider_id,
                          user_id=campaign.user_id, event_type=EventType.UNSUBSCRIBED))
        await db.execute(update(Campaign).where(Campaign.id == campaign_id)
                         .values(unsubscribed=Campaign.unsubscribed + 1))
        # Recipients not yet sent stay in their batch: the claim-time suppression check skips them.
        if recipient.status in (RecipientStatus.SENT, RecipientStatus.DELIVERED):
            recipient.status = RecipientStatus.UNSUBSCRIBED
    await db.commit()
    return recipient
