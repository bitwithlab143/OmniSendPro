"""Partitioning and retention (design DS-18: P3-11, P3-12)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import text

from app.db import partitions
from app.db.session import sessionmaker
from app.models import RefreshToken
from app.services import maintenance
from tests.conftest import Session


async def _partitions(db, table: str) -> set[str]:  # noqa: ANN001
    return {r[0] for r in await db.execute(text(partitions.LIST_SQL), {"parent": table})}


def test_partition_math() -> None:
    t = partitions.BY_NAME["email_events"]
    assert partitions.wanted_periods(t, date(2026, 11, 20)) == [date(2026, 11, 1), date(2026, 12, 1), date(2027, 1, 1)]
    assert partitions.parse_partition("email_events_p2026_12", "month") == (date(2026, 12, 1), date(2027, 1, 1))
    assert partitions.parse_partition("email_events_default", "month") is None
    assert partitions.parse_partition("worker_heartbeats_p2026_02_28", "day") == (date(2026, 2, 28), date(2026, 3, 1))
    names = ["email_events_p2025_01", "email_events_p2025_02", "email_events_p2026_09", "email_events_default"]
    assert partitions.expired(t, names, date(2026, 3, 5), 400) == []  # cutoff 2025-01-29: January not fully expired
    assert partitions.expired(t, names, date(2026, 4, 5), 400) == ["email_events_p2025_01", "email_events_p2025_02"]


async def test_current_partitions_exist_and_rows_route() -> None:
    async with sessionmaker()() as db:
        for t in partitions.TABLES:
            names = await _partitions(db, t.name)
            today = partitions.utc_today()
            assert partitions.partition_name(t.name, partitions.period_start(today, t.granularity), t.granularity) in names
            assert f"{t.name}_default" in names
        await db.execute(text("INSERT INTO audit_logs (actor_type, action, resource) VALUES ('system', 'X', 'test')"))
        await db.commit()
        month = partitions.partition_name("audit_logs", partitions.period_start(partitions.utc_today(), "month"), "month")
        assert (await db.execute(text(f"SELECT count(*) FROM {month}"))).scalar() == 1  # noqa: S608


async def test_future_partition_moves_default_rows() -> None:
    future = date(2031, 5, 1)
    created: list[str] = []
    try:
        async with sessionmaker()() as db:
            await db.execute(text("INSERT INTO audit_logs (actor_type, action, resource, timestamp) "
                                  "VALUES ('system', 'FUTURE', 'test', '2031-05-15T10:00:00Z')"))
            await db.commit()
            assert (await db.execute(text("SELECT count(*) FROM audit_logs_default"))).scalar() == 1
            created = await maintenance.ensure_partitions(db, today=future)
            assert "audit_logs_p2031_05" in created and "worker_heartbeats_p2031_05_08" in created
            assert (await db.execute(text("SELECT count(*) FROM audit_logs_default"))).scalar() == 0
            assert (await db.execute(text("SELECT action FROM audit_logs_p2031_05"))).scalar() == "FUTURE"
            assert await maintenance.ensure_partitions(db, today=future) == [], "idempotent"
    finally:
        async with sessionmaker()() as db:
            for name in created:
                await db.execute(text(f"DROP TABLE IF EXISTS {name}"))
            await db.commit()


async def test_retention_drops_old_partitions(client: AsyncClient, admin: Session) -> None:
    async with sessionmaker()() as db:
        t = partitions.BY_NAME["audit_logs"]
        for stmt in partitions.create_statements(t, date(2025, 1, 1), default_has_rows=False):
            await db.execute(text(stmt))
        await db.execute(text("INSERT INTO audit_logs (actor_type, action, resource, timestamp) "
                              "VALUES ('system', 'OLD', 'test', '2025-01-10T00:00:00Z'), "
                              "('system', 'OLD-DEFAULT', 'test', '2019-01-10T00:00:00Z')"))
        await db.commit()
        dropped = await maintenance.drop_expired(db)
        assert "audit_logs_p2025_01" in dropped
        assert "audit_logs_p2025_01" not in await _partitions(db, "audit_logs")
        remaining = (await db.execute(text("SELECT count(*) FROM audit_logs WHERE resource = 'test'"))).scalar()
        assert remaining == 0, "old rows in the default partition are pruned as well"

    # Retention is configurable, with a floor for audit logs.
    r = await client.put("/api/v1/admin/settings", headers=admin.headers, json={"values": {"retention_audit_days": 30}})
    assert r.status_code == 400
    r = await client.put("/api/v1/admin/settings", headers=admin.headers, json={"values": {"retention_audit_days": 120}})
    assert r.status_code == 200


async def test_prune_refresh_tokens(admin: Session) -> None:
    now = datetime.now(UTC)
    async with sessionmaker()() as db:
        uid = uuid.UUID(admin.user_id)
        for days in (-10, -2, 3):
            db.add(RefreshToken(user_id=uid, family_id=uuid.uuid4(), token_hash=uuid.uuid4().hex * 2, app="admin",
                                expires_at=now + timedelta(days=days)))
        await db.commit()
        before = (await db.execute(text("SELECT count(*) FROM refresh_tokens"))).scalar()
        out = await maintenance.prune(db)
        assert out["refresh_tokens"] == 2
        after = (await db.execute(text("SELECT count(*) FROM refresh_tokens"))).scalar()
        assert before - after == 2
