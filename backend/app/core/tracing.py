"""Optional OpenTelemetry tracing (design DS-23, P4-04).

Enabled when OTEL_EXPORTER_OTLP_ENDPOINT is set and the ``otel`` extra is installed
(``pip install -e "backend[otel]"``). Traces FastAPI requests, SQLAlchemy queries and outgoing httpx calls,
exported over OTLP/HTTP; OTEL_SERVICE_NAME defaults to "omnisend-backend". Without the variable nothing is
imported, so there is no overhead.
"""

from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger("omnisend.tracing")


def setup(app: Any, engine: Any, exporter: Any = None) -> bool:
    """Instrument ``app`` and ``engine``. ``exporter`` overrides OTLP (tests). Returns True when enabled."""
    if exporter is None and not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
    except ImportError:
        log.warning("tracing_unavailable", extra={"hint": 'install the "otel" extra to enable tracing'})
        return False
    provider = TracerProvider(resource=Resource.create(
        {"service.name": os.environ.get("OTEL_SERVICE_NAME", "omnisend-backend")}))
    if exporter is None:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    else:
        provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    # /metrics and health probes would only add noise.
    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider, excluded_urls="/metrics,/healthz,/readyz")
    # The instrumentation's version gate lags SQLAlchemy 2.1; its engine event hooks are unchanged (tested).
    SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine, tracer_provider=provider, skip_dep_check=True)
    HTTPXClientInstrumentor().instrument(tracer_provider=provider)
    log.info("tracing_enabled")
    return True
