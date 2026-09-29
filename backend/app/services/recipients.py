"""Recipient import: streamed CSV parse → normalise → validate → dedupe → persist (§55–§56, DS-07).

Rows are processed in chunks so a large file is never held in memory. Suppression is applied later, at
batch-build time and again at claim time, so suppressions added after the upload are still honoured.
"""

from __future__ import annotations

import asyncio
import codecs
import csv
import io
import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import IO, Any

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import bad_request
from app.models import Campaign, CampaignRecipient

CHUNK_ROWS = 5_000
MAX_VARIABLES = 30
MAX_VALUE_LEN = 1_000
MAX_TRACKED_IN_MEMORY = 200_000  # beyond this, the DB unique constraint alone dedupes
_VAR_KEY = re.compile(r"[^a-z0-9_]+")
EMAIL_HEADERS = {"email", "e-mail", "email_address", "emailaddress", "mail"}


def normalize_email(email: str) -> str:
    """Lower-cased full address. Used for dedupe and suppression lookups (design DS-07)."""
    return email.strip().lower()


def check_email(email: str) -> str | None:
    """Return the canonical address if syntactically valid, else None (no DNS lookups here)."""
    email = email.strip()
    if not email or len(email) > 320:
        return None
    try:
        result = validate_email(email, check_deliverability=False)
    except EmailNotValidError:
        return None
    return result.normalized


def _var_key(header: str) -> str:
    return _VAR_KEY.sub("_", header.strip().lower()).strip("_")[:64]


@dataclass(slots=True)
class ImportStats:
    rows: int = 0
    imported: int = 0
    invalid: int = 0
    duplicates: int = 0
    invalid_samples: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "rows": self.rows,
            "imported": self.imported,
            "invalid": self.invalid,
            "duplicates": self.duplicates,
            "invalid_samples": self.invalid_samples,
        }


def _rows(fileobj: IO[bytes]) -> Iterator[tuple[str, dict[str, str]]]:
    """Yield (email, variables) from a CSV byte stream. Accepts a header row or a single email column."""
    text = codecs.getreader("utf-8-sig")(fileobj, errors="replace")
    sample = text.read(64 * 1024)
    if not sample.strip():
        raise bad_request("The recipient file is empty", "empty_file")
    try:
        dialect: Any = csv.Sniffer().sniff(sample.split("\n", 20)[0] if "\n" in sample else sample, ",;\t|")
    except csv.Error:
        dialect = csv.excel
    stream = io.StringIO(sample)

    def chained() -> Iterator[str]:
        yield from stream
        yield from text

    reader = csv.reader(chained(), dialect)
    first = next(reader, None)
    if first is None:
        raise bad_request("The recipient file is empty", "empty_file")
    headers = [h.strip().lower() for h in first]
    email_idx = next((i for i, h in enumerate(headers) if h in EMAIL_HEADERS), None)
    if email_idx is None:
        if len(first) >= 1 and "@" in first[0]:
            email_idx, var_keys = 0, [f"col{i}" for i in range(len(first))]
            yield first[0], {}
        else:
            raise bad_request("No 'email' column found in the header row", "missing_email_column")
    else:
        var_keys = [_var_key(h) for h in first]

    for row in reader:
        if not row or all(not c.strip() for c in row):
            continue
        if email_idx >= len(row):
            yield "", {}
            continue
        variables: dict[str, str] = {}
        for i, value in enumerate(row):
            if i == email_idx or i >= len(var_keys) or len(variables) >= MAX_VARIABLES:
                continue
            key = var_keys[i]
            if key and not key.startswith("col"):
                variables[key] = value.strip()[:MAX_VALUE_LEN]
        yield row[email_idx], variables


async def import_csv(
    db: AsyncSession, campaign: Campaign, fileobj: IO[bytes], replace: bool = False
) -> ImportStats:
    if replace:
        await db.execute(delete(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign.id))
        campaign.invalid_recipients = 0
        campaign.duplicate_recipients = 0

    stats = ImportStats()
    rows = _rows(fileobj)
    seen_in_file: set[str] = set()

    def parse_chunk() -> list[dict[str, Any]] | None:
        """Runs in a worker thread: CPU-bound parsing/validation must not block the event loop."""
        out: list[dict[str, Any]] = []
        for raw_email, variables in rows:
            stats.rows += 1
            canonical = check_email(raw_email)
            if canonical is None:
                stats.invalid += 1
                if len(stats.invalid_samples) < 10:
                    stats.invalid_samples.append(raw_email[:120])
                continue
            norm = normalize_email(canonical)
            if norm in seen_in_file:
                stats.duplicates += 1
                continue
            if len(seen_in_file) < MAX_TRACKED_IN_MEMORY:
                seen_in_file.add(norm)
            out.append({"campaign_id": campaign.id, "email": canonical, "email_normalized": norm,
                        "variables": variables})
            if len(out) >= CHUNK_ROWS:
                return out
        return out or None

    while (chunk := await asyncio.to_thread(parse_chunk)) is not None:
        # De-duplicate inside the statement too (seen_in_file stops growing after MAX_TRACKED_IN_MEMORY).
        unique = list({row["email_normalized"]: row for row in chunk}.values())
        stats.duplicates += len(chunk) - len(unique)
        stmt = (
            insert(CampaignRecipient)
            .values(unique)
            .on_conflict_do_nothing(constraint="uq_campaign_recipients_campaign_email")
            .returning(CampaignRecipient.id)
        )
        inserted = len((await db.execute(stmt)).all())
        stats.imported += inserted
        stats.duplicates += len(unique) - inserted

    campaign.total_recipients = await count_recipients(db, campaign.id)
    campaign.invalid_recipients += stats.invalid
    campaign.duplicate_recipients += stats.duplicates
    return stats


async def count_recipients(db: AsyncSession, campaign_id: uuid.UUID) -> int:
    return int(
        (
            await db.execute(
                select(func.count()).select_from(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign_id)
            )
        ).scalar_one()
    )


async def clear(db: AsyncSession, campaign: Campaign) -> None:
    await db.execute(delete(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign.id))
    campaign.total_recipients = 0
    campaign.invalid_recipients = 0
    campaign.duplicate_recipients = 0
