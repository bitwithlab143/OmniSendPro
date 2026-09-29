"""FastAPI application factory (control-plane API)."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import func, select, text

from app.api.v1 import admin, auth, public, user, worker
from app.core import tracing
from app.core.config import get_settings
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging
from app.core.redis import close_redis, get_redis
from app.db.session import dispose_engine, get_engine, sessionmaker
from app.models import Job, Provider, Worker
from app.models.enums import JobStatus, ProviderStatus, WorkerStatus
from app.runner import Processor
from app.scheduler.loop import Scheduler
from app.services import bootstrap, processor
from app.services import jobs as job_service
from app.services import workers as worker_service

log = logging.getLogger("omnisend.api")
VERSION = "0.1.0"
BOOTSTRAP_LOCK = 815_001  # pg advisory lock id for startup bootstrap


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    # Several API processes/replicas start at once: serialise first-boot work with an advisory lock held
    # on a dedicated connection (released even if bootstrap fails).
    async with get_engine().connect() as lock_conn:
        await lock_conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": BOOTSTRAP_LOCK})
        try:
            async with sessionmaker()() as db:
                await bootstrap.ensure_reference_data(db)
                await bootstrap.bootstrap_admin_from_env(db)
        finally:
            await lock_conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": BOOTSTRAP_LOCK})
            await lock_conn.commit()
    scheduler: Scheduler | None = None
    processor: Processor | None = None
    if settings.run_scheduler:  # single-process mode; production runs `python -m app.runner` (DS-19)
        scheduler = Scheduler()
        scheduler.start()
        processor = Processor()
        processor.start()
    app.state.scheduler = scheduler
    log.info("api_started", extra={"env": settings.app_env, "version": VERSION})
    try:
        yield
    finally:
        if processor:
            await processor.stop()
        if scheduler:
            await scheduler.stop()
        await close_redis()
        await dispose_engine()


def _security_headers(response: Response, production: bool) -> None:
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
    if production:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging("backend", settings.log_level)
    app = FastAPI(
        title="OmniSendPro API",
        version=VERSION,
        lifespan=lifespan,
        docs_url=None if settings.is_production else "/api/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/api/openapi.json",
    )
    install_error_handlers(app)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
        max_age=600,
    )

    @app.middleware("http")
    async def request_guard(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        is_upload = request.url.path.endswith("/recipients") and request.method == "POST"
        limit = settings.max_upload_bytes + 64 * 1024 if is_upload else settings.max_request_bytes
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > limit:
            return JSONResponse({"error": {"code": "payload_too_large", "message": "Request body too large",
                                           "details": {"limit_bytes": limit}}}, status_code=413)
        started = time.perf_counter()
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        response.headers["X-Request-ID"] = request_id
        if not request.url.path.startswith("/api/v1/u/"):
            _security_headers(response, settings.is_production)
        if request.url.path not in ("/healthz", "/readyz", "/metrics"):
            log.info("http_request", extra={"method": request.method, "path": request.url.path,
                                            "status": response.status_code, "duration_ms": duration_ms,
                                            "request_id": request_id})
        return response

    api = APIRouter(prefix="/api/v1")
    api.include_router(auth.router)
    api.include_router(admin.router)
    api.include_router(user.router)
    api.include_router(worker.router)
    api.include_router(public.router)
    app.include_router(api)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    async def readyz() -> JSONResponse:
        checks = {"database": False, "redis": False}
        try:
            async with sessionmaker()() as db:
                await db.execute(text("SELECT 1"))
            checks["database"] = True
        except Exception:  # noqa: BLE001
            log.exception("readiness_db_failed")
        try:
            checks["redis"] = bool(await get_redis().ping())
        except Exception:  # noqa: BLE001
            log.exception("readiness_redis_failed")
        ok = all(checks.values())
        return JSONResponse({"status": "ok" if ok else "degraded", "checks": checks}, status_code=200 if ok else 503)

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> PlainTextResponse:
        """Prometheus exposition (§44). Restrict to the monitoring network at the proxy."""
        lines: list[str] = []

        def gauge(name: str, value: float, labels: str = "", help_: str = "") -> None:
            if help_:
                lines.append(f"# HELP {name} {help_}")
                lines.append(f"# TYPE {name} gauge")
            lines.append(f"{name}{{{labels}}} {value}" if labels else f"{name} {value}")

        async with sessionmaker()() as db:
            job_rows = (await db.execute(select(Job.status, func.count()).group_by(Job.status))).all()
            counts = {s.value: 0 for s in JobStatus} | {s.value: int(n) for s, n in job_rows}
            lines.append("# HELP omnisend_jobs Jobs by status")
            lines.append("# TYPE omnisend_jobs gauge")
            for status, n in counts.items():
                lines.append(f'omnisend_jobs{{status="{status}"}} {n}')
            gauge("omnisend_queue_depth", counts["pending"] + counts["retry"], help_="Claimable jobs")
            workers_online = (await db.execute(select(func.count()).select_from(Worker)
                                               .where(Worker.status == WorkerStatus.ONLINE))).scalar_one()
            gauge("omnisend_workers_online", workers_online, help_="Online workers")
            providers = (await db.execute(select(Provider.provider_name, Provider.status, Provider.health_score))).all()
            lines.append("# HELP omnisend_provider_health Provider health score (0-100)")
            lines.append("# TYPE omnisend_provider_health gauge")
            for name, status, score in providers:
                safe = name.replace('"', "'")
                lines.append(f'omnisend_provider_health{{provider="{safe}",status="{status.value}"}} {score}')
            gauge("omnisend_providers_disabled", sum(1 for p in providers if p[1] == ProviderStatus.DISABLED))
            scale = await worker_service.desired_workers(db)
            gauge("omnisend_workers_desired", scale["desired"],
                  help_="Worker instances needed for the open jobs (autoscaling signal, bounded by settings)")
            gauge("omnisend_open_jobs", scale["open_jobs"], help_="Jobs ready or in progress")
            inbox = await processor.inbox_counts(db)
            gauge("omnisend_event_inbox_pending", inbox["pending"], help_="Provider reports waiting for the processor")
            gauge("omnisend_event_inbox_dead", inbox["dead"], help_="Provider reports that failed every attempt")
            gauge("omnisend_event_inbox_oldest_seconds", inbox["oldest_seconds"],
                  help_="Age of the oldest unprocessed provider report")
        gauge("omnisend_sending_rate", await job_service.sending_rate(), help_="Messages sent per second (10s avg)")
        return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")

    tracing.setup(app, get_engine())  # no-op unless OTEL_EXPORTER_OTLP_ENDPOINT is set (DS-23)
    return app


app = create_app()
