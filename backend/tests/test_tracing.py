"""Optional OpenTelemetry tracing (design DS-23, P4-04)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.core import tracing
from app.db.session import get_engine

pytest.importorskip("opentelemetry.sdk")


def test_disabled_without_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    assert tracing.setup(object(), object()) is False


async def test_requests_and_queries_are_traced() -> None:
    from fastapi import FastAPI
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from sqlalchemy import text

    from app.db.session import sessionmaker

    app = FastAPI()

    @app.get("/probe")
    async def probe() -> dict[str, int]:
        async with sessionmaker()() as db:
            return {"one": (await db.execute(text("SELECT 1"))).scalar_one()}

    exporter = InMemorySpanExporter()
    assert tracing.setup(app, get_engine(), exporter=exporter) is True
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            assert (await c.get("/probe")).json() == {"one": 1}
        names = [s.name for s in exporter.get_finished_spans()]
        assert any("GET /probe" in n for n in names), names
        assert any(n.startswith("SELECT") or "connect" in n for n in names), names
    finally:
        SQLAlchemyInstrumentor().uninstrument()
