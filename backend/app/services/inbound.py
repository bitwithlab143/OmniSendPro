"""Asynchronous bounce (DSN, RFC 3464) and complaint (ARF, RFC 5965) processing — design DS-16.

Reports arrive by email (bounce mailbox) or on the signed raw-message endpoint. They are parsed with
the standard-library email parser only (nothing is rendered or executed), correlated to the message we
sent (Message-ID, or campaign header + recipient), and fed to the same event pipeline as webhooks.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from email import message_from_bytes, policy
from email.message import EmailMessage, Message
from email.parser import BytesHeaderParser, HeaderParser
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Provider
from app.services import events

MAX_REPORT_BYTES = 1024 * 1024
_ENHANCED = re.compile(r"\b([245])\.(\d{1,3})\.(\d{1,3})\b")
HARD_STATUS_PREFIXES = ("5.1.",)
HARD_STATUS_EXACT = {"5.2.1"}  # mailbox disabled
COMPLAINT_TYPES = {"abuse", "fraud", "virus", "other"}


@dataclass(slots=True)
class ParsedReport:
    kind: str  # dsn | arf | unknown
    events: list[dict[str, Any]] = field(default_factory=list)
    original_message_id: str | None = None
    campaign_id: str | None = None


# --------------------------------------------------------------------------- helpers


def _field_blocks(part: Message) -> list[Message]:
    """Header blocks inside message/delivery-status or message/feedback-report parts."""
    payload = part.get_payload()
    if isinstance(payload, list):
        return [p for p in payload if isinstance(p, Message)]
    raw = part.get_payload(decode=True) or (payload.encode() if isinstance(payload, str) else b"")
    text = raw.decode("utf-8", "replace").replace("\r\n", "\n")
    return [HeaderParser().parsestr(block) for block in re.split(r"\n\s*\n", text.strip()) if block.strip()]


def _original_headers(report: Message) -> Message | None:
    for part in report.walk():
        ctype = part.get_content_type()
        if ctype == "message/rfc822":
            payload = part.get_payload()
            if isinstance(payload, list) and payload:
                return payload[0]
        if ctype == "text/rfc822-headers":
            raw = part.get_payload(decode=True) or b""
            return BytesHeaderParser().parsebytes(raw)
    return None


def _address(value: str | None) -> str | None:
    """'rfc822; User@Example.org' → 'user@example.org'."""
    if not value:
        return None
    addr = value.split(";", 1)[-1].strip().strip("<>").strip()
    return addr.lower() if "@" in addr else None


def _status(value: str | None) -> str | None:
    match = _ENHANCED.search(value or "")
    return ".".join(match.groups()) if match else None


def classify_dsn(action: str, status: str | None) -> tuple[str, str | None]:
    """Return (event type, bounce_type) for one DSN recipient block."""
    action = (action or "").strip().lower()
    if action in ("delivered", "relayed", "expanded"):
        return "delivered", None
    if action == "delayed" or (status or "").startswith("4."):
        return "deferred", None
    if action == "failed":
        if status and (status.startswith(HARD_STATUS_PREFIXES) or status in HARD_STATUS_EXACT):
            return "bounced", "hard"
        return "bounced", "soft"
    return "deferred", None


# --------------------------------------------------------------------------- parsing


def parse_report(raw: bytes) -> ParsedReport:
    msg: EmailMessage = message_from_bytes(raw[:MAX_REPORT_BYTES], policy=policy.compat32)  # type: ignore[assignment]
    original = _original_headers(msg)
    mid = (original.get("Message-ID") if original else None) or None
    cid = (original.get("X-OmniSend-Campaign") if original else None) or None
    mid = mid.strip() if mid else None
    cid = cid.strip() if cid else None

    if msg.get_content_type() != "multipart/report":
        return ParsedReport("unknown", original_message_id=mid, campaign_id=cid)
    report_type = (msg.get_param("report-type") or "").lower()

    if report_type == "delivery-status":
        report = ParsedReport("dsn", original_message_id=mid, campaign_id=cid)
        for part in msg.walk():
            if part.get_content_type() != "message/delivery-status":
                continue
            blocks = _field_blocks(part)
            for block in blocks[1:] if len(blocks) > 1 else blocks:  # first block = per-message fields
                email = _address(block.get("Final-Recipient")) or _address(block.get("Original-Recipient"))
                if not email and not block.get("Action"):
                    continue
                status = _status(block.get("Status"))
                etype, bounce_type = classify_dsn(block.get("Action", ""), status)
                diagnostic = " ".join((block.get("Diagnostic-Code") or "").split())
                report.events.append({
                    "type": etype,
                    "bounce_type": bounce_type,
                    "email": email,
                    "provider_message_id": mid,
                    "campaign_id": cid,
                    "error_code": status,
                    "error_message": diagnostic[:500] or None,
                })
        return report

    if report_type == "feedback-report":
        report = ParsedReport("arf", original_message_id=mid, campaign_id=cid)
        for part in msg.walk():
            if part.get_content_type() != "message/feedback-report":
                continue
            fields = _field_blocks(part)
            info = fields[0] if fields else Message()
            feedback_type = (info.get("Feedback-Type") or "abuse").strip().lower()
            if feedback_type not in COMPLAINT_TYPES:
                continue  # e.g. "not-spam": nothing to suppress
            email = None
            if original is not None:
                email = _address(original.get("To"))
            email = email or _address(info.get("Original-Rcpt-To"))
            report.events.append({
                "type": "complained",
                "email": email,
                "provider_message_id": mid,
                "campaign_id": cid,
                "error_code": f"arf:{feedback_type}",
                "error_message": (info.get("User-Agent") or "")[:200] or None,
            })
        return report

    return ParsedReport("unknown", original_message_id=mid, campaign_id=cid)


# --------------------------------------------------------------------------- processing


@dataclass(slots=True)
class InboundResult:
    kind: str
    accepted: int = 0
    duplicates: int = 0
    unmatched: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "accepted": self.accepted, "duplicates": self.duplicates,
                "unmatched": self.unmatched}


async def process_raw(db: AsyncSession, provider: Provider, raw: bytes) -> InboundResult:
    report = parse_report(raw)
    if report.kind == "unknown" or not report.events:
        return InboundResult(kind=report.kind, unmatched=0 if report.kind == "unknown" else 1)
    for ev in report.events:
        if ev.get("campaign_id"):
            try:
                ev["campaign_id"] = uuid.UUID(str(ev["campaign_id"]))
            except ValueError:
                ev["campaign_id"] = None
    result = await events.ingest(db, provider.id, report.events)
    await db.execute(
        update(Provider).where(Provider.id == provider.id)
        .values(bounce_processed_total=Provider.bounce_processed_total + result.accepted)
    )
    await db.commit()
    return InboundResult(kind=report.kind, accepted=result.accepted, duplicates=result.duplicates,
                         unmatched=result.unknown + len(result.errors))
