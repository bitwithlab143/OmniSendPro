"""Worker provisioning, authentication, heartbeat and liveness (ARCHITECTURE.md §8–§10, DS-08)."""

from __future__ import annotations

import hmac
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError, conflict
from app.core.security import create_worker_token, sha256_hex
from app.models import Job, Worker, WorkerHeartbeat
from app.models.enums import JobStatus, WorkerStatus
from app.services import audit
from app.services import settings as settings_service

WORKER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")


def now() -> datetime:
    return datetime.now(UTC)


def new_credential() -> str:
    return "osw_" + secrets.token_urlsafe(32)


async def provision(db: AsyncSession, worker_id: str, name: str, capacity: int, max_concurrent_jobs: int,
                    ctx: audit.RequestContext) -> tuple[Worker, str]:
    if not WORKER_ID_RE.match(worker_id):
        raise ApiError(422, "invalid_worker_id", "worker_id must be lowercase letters, digits and dashes")
    existing = (await db.execute(select(Worker).where(Worker.worker_id == worker_id))).scalar_one_or_none()
    if existing:
        raise conflict("A worker with this worker_id already exists", "duplicate_worker")
    credential = new_credential()
    worker = Worker(worker_id=worker_id, name=name, capacity=capacity, max_concurrent_jobs=max_concurrent_jobs,
                    credential_hash=sha256_hex(credential), status=WorkerStatus.OFFLINE)
    db.add(worker)
    await db.flush()
    audit.record(db, ctx, "WORKER_PROVISIONED", "worker", worker.id, new={"worker_id": worker_id, "name": name})
    await db.commit()
    return worker, credential


async def rotate_credential(db: AsyncSession, worker: Worker, ctx: audit.RequestContext) -> str:
    credential = new_credential()
    worker.credential_hash = sha256_hex(credential)
    audit.record(db, ctx, "WORKER_CREDENTIAL_ROTATED", "worker", worker.id)
    await db.commit()
    return credential


async def exchange_token(db: AsyncSession, worker_id: str, credential: str) -> tuple[Worker, str, int]:
    worker = (await db.execute(select(Worker).where(Worker.worker_id == worker_id))).scalar_one_or_none()
    expected = worker.credential_hash if worker else sha256_hex("invalid")
    if worker is None or not hmac.compare_digest(expected, sha256_hex(credential)):
        raise ApiError(401, "invalid_credentials", "Invalid worker credentials")
    if worker.disabled:
        raise ApiError(403, "worker_disabled", "Worker is disabled")
    token, ttl = create_worker_token(worker.id, worker.worker_id)
    return worker, token, ttl


async def register(db: AsyncSession, worker: Worker, data: dict[str, Any]) -> dict[str, Any]:
    worker.version = data.get("version") or worker.version
    worker.hostname = data.get("hostname") or worker.hostname
    if data.get("name"):
        worker.name = data["name"]
    if data.get("capacity"):
        worker.capacity = int(data["capacity"])
    if data.get("max_concurrent_jobs"):
        worker.max_concurrent_jobs = int(data["max_concurrent_jobs"])
    worker.registered_at = now()
    worker.last_heartbeat_at = now()
    was_offline = worker.status == WorkerStatus.OFFLINE
    worker.status = WorkerStatus.ONLINE
    if was_offline:
        audit.record(db, audit.RequestContext(None, None, None, "worker"), "WORKER_ONLINE", "worker", worker.id)
    await db.commit()
    cfg = await settings_service.get_all(db)
    return {
        "worker_id": worker.worker_id,
        "heartbeat_interval_seconds": cfg["worker_heartbeat_interval_seconds"],
        "lease_seconds": cfg["job_lease_seconds"],
        "max_concurrent_jobs": worker.max_concurrent_jobs,
        "capacity": worker.capacity,
    }


async def heartbeat(db: AsyncSession, worker: Worker, data: dict[str, Any]) -> dict[str, Any]:
    status = data.get("status", "online")
    worker.last_heartbeat_at = now()
    worker.cpu = data.get("cpu")
    worker.memory = data.get("memory")
    worker.active_jobs = int(data.get("active_jobs") or 0)
    worker.current_rate = float(data.get("current_rate") or 0.0)
    if status == "offline":
        worker.status = WorkerStatus.OFFLINE
        # A clean shutdown: its unfinished leases become recoverable immediately.
        await db.execute(
            update(Job)
            .where(Job.worker_id == worker.id, Job.status.in_([JobStatus.CLAIMED, JobStatus.PROCESSING]))
            .values(lease_expires_at=now())
        )
    else:
        if worker.status == WorkerStatus.OFFLINE:
            audit.record(db, audit.RequestContext(None, None, None, "worker"), "WORKER_ONLINE", "worker", worker.id)
        worker.status = WorkerStatus.ONLINE
    db.add(
        WorkerHeartbeat(
            worker_id=worker.id,
            status=worker.status.value,
            cpu=worker.cpu,
            memory=worker.memory,
            active_jobs=worker.active_jobs,
            current_rate=worker.current_rate,
        )
    )
    await db.commit()
    return {"status": worker.status.value, "disabled": worker.disabled}


async def refresh_statuses(db: AsyncSession) -> list[str]:
    """ONLINE → WARNING → OFFLINE when heartbeats go missing (§10). Returns worker_ids that went offline."""
    cfg = await settings_service.get_all(db)
    warn_at = now() - timedelta(seconds=int(cfg["worker_warning_after_seconds"]))
    off_at = now() - timedelta(seconds=int(cfg["worker_offline_after_seconds"]))
    went_offline: list[str] = []
    workers = (
        await db.execute(select(Worker).where(Worker.status != WorkerStatus.OFFLINE).with_for_update(skip_locked=True))
    ).scalars().all()
    for w in workers:
        seen = w.last_heartbeat_at
        if seen is None or seen < off_at:
            w.status = WorkerStatus.OFFLINE
            w.active_jobs = 0
            w.current_rate = 0.0
            went_offline.append(w.worker_id)
            await db.execute(
                update(Job)
                .where(Job.worker_id == w.id, Job.status.in_([JobStatus.CLAIMED, JobStatus.PROCESSING]))
                .values(lease_expires_at=now())
            )
            audit.record(db, audit.SYSTEM, "WORKER_OFFLINE", "worker", w.id,
                         new={"last_heartbeat_at": seen.isoformat() if seen else None})
        elif seen < warn_at:
            w.status = WorkerStatus.WARNING
    await db.commit()
    return went_offline
