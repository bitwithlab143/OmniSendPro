"""Report queries go to the read replica when DATABASE_READ_URL is set (design DS-23, P4-01)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import event

from app.core.config import get_settings
from app.db import session as db_session
from tests.conftest import Session


async def test_reports_use_the_read_engine(client: AsyncClient, admin: Session, user: Session,
                                           monkeypatch: pytest.MonkeyPatch) -> None:
    assert db_session.read_sessionmaker() is db_session.sessionmaker(), "no replica configured → primary"
    # Point the "replica" at the same test database through a separate engine and count its statements.
    monkeypatch.setattr(get_settings(), "database_read_url", get_settings().database_url)
    maker = db_session.read_sessionmaker()
    assert maker is not db_session.sessionmaker()
    statements: list[str] = []
    event.listen(db_session._read_engine.sync_engine, "before_cursor_execute",  # type: ignore[union-attr]
                 lambda conn, cursor, stmt, *a: statements.append(stmt))
    try:
        r = await client.get("/api/v1/admin/reports/summary", headers=admin.headers, params={"days": 7})
        assert r.status_code == 200
        assert any("email_events" in s for s in statements), "summary read from the replica"
        statements.clear()
        assert (await client.get("/api/v1/user/reports", headers=user.headers)).status_code == 200
        assert statements, "user reports read from the replica"
        statements.clear()
        # Writes and interactive reads stay on the primary.
        assert (await client.get("/api/v1/admin/users", headers=admin.headers)).status_code == 200
        assert not statements
    finally:
        await db_session._read_engine.dispose()  # type: ignore[union-attr]
        db_session._read_engine = None
        db_session._read_sessionmaker = None
