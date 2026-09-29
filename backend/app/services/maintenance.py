"""Data lifecycle maintenance — design DS-18 (P3-11 partitions, P3-12 retention).

Run by the scheduler leader every ``MAINTENANCE_INTERVAL_SECONDS``. Every step is idempotent, so a crash or
a second run is harmless.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import partitions
from app.models import EmailEventKey, EventInbox, RecipientImport, RefreshToken
from app.services import settings as settings_service

log = logging.getLogger("omnisend.maintenance")

REFRESH_TOKEN_GRACE = timedelta(days=1)  # keep just-expired tokens so reuse detection still works
INBOX_RETENTION = timedelta(days=7)  # processed provider reports (the events themselves are kept)


async def ensure_partitions(db: AsyncSession, today: date | None = None) -> list[str]:
    today = today or partitions.utc_today()
    created: list[str] = []
    for t in partitions.TABLES:
        existing = {r[0] for r in await db.execute(text(partitions.LIST_SQL), {"parent": t.name})}
        for start in partitions.wanted_periods(t, today):
            name = partitions.partition_name(t.name, start, t.granularity)
            if name in existing:
                continue
            has_rows = bool((await db.execute(text(partitions.default_rows_sql(t, start)))).scalar())
            for stmt in partitions.create_statements(t, start, has_rows):
                await db.execute(text(stmt))
            await db.commit()
            created.append(name)
            log.info("partition_created", extra={"partition": name, "moved_default_rows": has_rows})
    return created


async def drop_expired(db: AsyncSession, today: date | None = None) -> list[str]:
    today = today or partitions.utc_today()
    cfg = await settings_service.get_all(db)
    dropped: list[str] = []
    for t in partitions.TABLES:
        days = int(cfg[t.retention_setting])
        names = [r[0] for r in await db.execute(text(partitions.LIST_SQL), {"parent": t.name})]
        for name in partitions.expired(t, names, today, days):
            # Detach first so concurrent readers never see a half-dropped partition.
            await db.execute(text(f"ALTER TABLE {t.name} DETACH PARTITION {name}"))
            await db.execute(text(f"DROP TABLE {name}"))
            await db.commit()
            dropped.append(name)
            log.info("partition_dropped", extra={"partition": name, "retention_days": days})
        # Rows that landed in the default partition (clock skew, pre-partitioning history) age out too.
        cutoff = datetime.combine(today - timedelta(days=days), datetime.min.time(), UTC)
        stmt = f"DELETE FROM {t.name}_default WHERE {t.column} < :cutoff"  # noqa: S608 - names from TABLES
        await db.execute(text(stmt), {"cutoff": cutoff})
        await db.commit()
    return dropped


async def prune(db: AsyncSession, now: datetime | None = None) -> dict[str, int]:
    """Row-level retention for small unpartitioned tables."""
    now = now or datetime.now(UTC)
    cfg = await settings_service.get_all(db)
    out: dict[str, int] = {}
    res: Any = await db.execute(delete(RefreshToken).where(RefreshToken.expires_at < now - REFRESH_TOKEN_GRACE))
    out["refresh_tokens"] = res.rowcount or 0
    keys_cutoff = now - timedelta(days=int(cfg["retention_events_days"]))
    res = await db.execute(delete(EmailEventKey).where(EmailEventKey.created_at < keys_cutoff))
    out["event_keys"] = res.rowcount or 0
    res = await db.execute(delete(EventInbox).where(EventInbox.processed_at < now - INBOX_RETENTION))
    out["inbox"] = res.rowcount or 0
    res = await db.execute(delete(RecipientImport).where(RecipientImport.finished_at < now - timedelta(days=30)))
    out["imports"] = res.rowcount or 0
    await db.commit()
    return out


async def run(db: AsyncSession) -> dict[str, Any]:
    created = await ensure_partitions(db)
    dropped = await drop_expired(db)
    pruned = await prune(db)
    summary = {"partitions_created": len(created), "partitions_dropped": len(dropped), **pruned}
    if any(summary.values()):
        log.info("maintenance_done", extra=summary)
    return summary
