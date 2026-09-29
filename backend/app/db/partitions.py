"""Time-range partitions for append-only tables (design DS-18).

Pure SQL generation plus two executors (sync for Alembic, async for the maintenance job). Partitions are
named ``<table>_pYYYY_MM`` (monthly) or ``<table>_pYYYY_MM_DD`` (daily); every table also has a
``<table>_default`` partition so an insert never fails for lack of a partition.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

Granularity = Literal["day", "month"]


@dataclass(frozen=True, slots=True)
class PartitionedTable:
    name: str
    column: str
    granularity: Granularity
    retention_setting: str
    ahead: int  # periods to create in advance


TABLES: tuple[PartitionedTable, ...] = (
    PartitionedTable("email_events", "created_at", "month", "retention_events_days", 2),
    PartitionedTable("audit_logs", "timestamp", "month", "retention_audit_days", 2),
    PartitionedTable("provider_health_logs", "created_at", "month", "retention_health_days", 2),
    PartitionedTable("worker_heartbeats", "timestamp", "day", "retention_heartbeat_days", 7),
)
BY_NAME = {t.name: t for t in TABLES}


def period_start(d: date, granularity: Granularity) -> date:
    return d if granularity == "day" else d.replace(day=1)


def next_period(d: date, granularity: Granularity) -> date:
    if granularity == "day":
        return d + timedelta(days=1)
    return date(d.year + (d.month == 12), d.month % 12 + 1, 1)


def partition_name(table: str, start: date, granularity: Granularity) -> str:
    return f"{table}_p{start:%Y_%m_%d}" if granularity == "day" else f"{table}_p{start:%Y_%m}"


_NAME = re.compile(r"_p(\d{4})_(\d{2})(?:_(\d{2}))?$")


def parse_partition(name: str, granularity: Granularity) -> tuple[date, date] | None:
    """(start, end) of a partition from its name, or None for the default / foreign partitions."""
    m = _NAME.search(name)
    if not m or (granularity == "day") != bool(m.group(3)):
        return None
    start = date(int(m.group(1)), int(m.group(2)), int(m.group(3) or 1))
    return start, next_period(start, granularity)


def _ts(d: date) -> str:
    return f"'{d.isoformat()} 00:00:00+00'"


def wanted_periods(t: PartitionedTable, today: date, since: date | None = None) -> list[date]:
    """Period starts from `since` (default: current period) up to `ahead` periods in the future."""
    start = period_start(since or today, t.granularity)
    end = period_start(today, t.granularity)
    for _ in range(t.ahead):
        end = next_period(end, t.granularity)
    out = []
    cur = start
    while cur <= end:
        out.append(cur)
        cur = next_period(cur, t.granularity)
    return out


def create_statements(t: PartitionedTable, start: date, default_has_rows: bool) -> list[str]:
    """SQL to add one partition. Rows already in the default partition for that range are moved first,
    otherwise attaching would fail the default partition's constraint check."""
    end = next_period(start, t.granularity)
    name = partition_name(t.name, start, t.granularity)
    rng = f"FOR VALUES FROM ({_ts(start)}) TO ({_ts(end)})"
    if not default_has_rows:
        return [f"CREATE TABLE IF NOT EXISTS {name} PARTITION OF {t.name} {rng}"]
    cond = f"{t.column} >= {_ts(start)} AND {t.column} < {_ts(end)}"
    return [
        f"CREATE TABLE {name} (LIKE {t.name} INCLUDING DEFAULTS INCLUDING CONSTRAINTS)",
        f"INSERT INTO {name} SELECT * FROM {t.name}_default WHERE {cond}",
        f"DELETE FROM {t.name}_default WHERE {cond}",
        f"ALTER TABLE {t.name} ATTACH PARTITION {name} {rng}",
    ]


LIST_SQL = (
    "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
    "WHERE i.inhparent = CAST(:parent AS regclass)"
)


def default_rows_sql(t: PartitionedTable, start: date) -> str:
    end = next_period(start, t.granularity)
    return (f"SELECT EXISTS (SELECT 1 FROM {t.name}_default WHERE {t.column} >= {_ts(start)} "
            f"AND {t.column} < {_ts(end)})")


def expired(t: PartitionedTable, names: list[str], today: date, retention_days: int) -> list[str]:
    """Partitions entirely older than the retention window."""
    cutoff = today - timedelta(days=retention_days)
    out = []
    for name in names:
        bounds = parse_partition(name, t.granularity)
        if bounds and bounds[1] <= cutoff:
            out.append(name)
    return sorted(out)


def utc_today() -> date:
    return datetime.now(UTC).date()


# --------------------------------------------------------------------------- sync executor (Alembic)


def ensure_sync(conn: Any, t: PartitionedTable, today: date, since: date | None = None) -> list[str]:
    from sqlalchemy import text

    existing = {r[0] for r in conn.execute(text(LIST_SQL), {"parent": t.name})}
    created = []
    for start in wanted_periods(t, today, since):
        name = partition_name(t.name, start, t.granularity)
        if name in existing:
            continue
        has_rows = bool(conn.execute(text(default_rows_sql(t, start))).scalar())
        for stmt in create_statements(t, start, has_rows):
            conn.execute(text(stmt))
        created.append(name)
    return created
