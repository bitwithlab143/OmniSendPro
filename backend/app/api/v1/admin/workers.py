"""/api/v1/admin/workers + /queues (§8–§10, §31, DS-05, DS-08)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from app.api.deps import DB, Ctx, Principal, require
from app.core.errors import not_found
from app.core.pagination import Page, paginate
from app.models import Job, JobAttempt, Worker, WorkerHeartbeat
from app.models.enums import JobStatus, WorkerStatus
from app.schemas.domain import (
    HeartbeatOut,
    JobOut,
    WorkerCreate,
    WorkerCredentialOut,
    WorkerOut,
    WorkerUpdate,
)
from app.services import audit, processor
from app.services import jobs as job_service
from app.services import workers as worker_service

router = APIRouter(tags=["admin:workers"])

WRead = Annotated[Principal, Depends(require("workers.read"))]
WWrite = Annotated[Principal, Depends(require("workers.write"))]
QRead = Annotated[Principal, Depends(require("queues.read"))]
QWrite = Annotated[Principal, Depends(require("queues.write"))]


async def _get(db: DB, worker_pk: uuid.UUID) -> Worker:
    worker = await db.get(Worker, worker_pk)
    if worker is None:
        raise not_found("Worker")
    return worker


@router.get("/workers", response_model=Page[WorkerOut])
async def list_workers(db: DB, _: WRead, status: WorkerStatus | None = None,
                       limit: int = Query(default=50, ge=1, le=200), cursor: str | None = None) -> Page[WorkerOut]:
    stmt = select(Worker)
    if status:
        stmt = stmt.where(Worker.status == status)
    rows, nxt = await paginate(db, stmt, [Worker.created_at, Worker.id], limit, cursor)
    return Page(items=[WorkerOut.model_validate(w) for w in rows], next_cursor=nxt)


@router.post("/workers", response_model=WorkerCredentialOut, status_code=201)
async def provision_worker(body: WorkerCreate, db: DB, _: WWrite, ctx: Ctx) -> WorkerCredentialOut:
    worker, credential = await worker_service.provision(
        db, body.worker_id, body.name, body.capacity, body.max_concurrent_jobs, ctx
    )
    return WorkerCredentialOut(worker=WorkerOut.model_validate(worker), credential=credential)


@router.get("/workers/{worker_pk}", response_model=WorkerOut)
async def get_worker(worker_pk: uuid.UUID, db: DB, _: WRead) -> WorkerOut:
    return WorkerOut.model_validate(await _get(db, worker_pk))


@router.patch("/workers/{worker_pk}", response_model=WorkerOut)
async def update_worker(worker_pk: uuid.UUID, body: WorkerUpdate, db: DB, _: WWrite, ctx: Ctx) -> WorkerOut:
    worker = await _get(db, worker_pk)
    data = body.model_dump(exclude_unset=True, exclude_none=True)
    before = {k: getattr(worker, k) for k in data}
    for k, v in data.items():
        setattr(worker, k, v)
    old, new = audit.diff(before, data)
    if new:
        audit.record(db, ctx, "WORKER_UPDATED", "worker", worker.id, old=old, new=new)
    await db.commit()
    await db.refresh(worker)
    return WorkerOut.model_validate(worker)


@router.post("/workers/{worker_pk}/disable", response_model=WorkerOut)
async def disable_worker(worker_pk: uuid.UUID, db: DB, _: WWrite, ctx: Ctx) -> WorkerOut:
    worker = await _get(db, worker_pk)
    worker.disabled = True
    audit.record(db, ctx, "WORKER_DISABLED", "worker", worker.id)
    await db.commit()
    await db.refresh(worker)
    return WorkerOut.model_validate(worker)


@router.post("/workers/{worker_pk}/enable", response_model=WorkerOut)
async def enable_worker(worker_pk: uuid.UUID, db: DB, _: WWrite, ctx: Ctx) -> WorkerOut:
    worker = await _get(db, worker_pk)
    worker.disabled = False
    audit.record(db, ctx, "WORKER_ENABLED", "worker", worker.id)
    await db.commit()
    await db.refresh(worker)
    return WorkerOut.model_validate(worker)


@router.post("/workers/{worker_pk}/rotate-credential", response_model=WorkerCredentialOut)
async def rotate_worker_credential(worker_pk: uuid.UUID, db: DB, _: WWrite, ctx: Ctx) -> WorkerCredentialOut:
    worker = await _get(db, worker_pk)
    credential = await worker_service.rotate_credential(db, worker, ctx)
    await db.refresh(worker)
    return WorkerCredentialOut(worker=WorkerOut.model_validate(worker), credential=credential)


@router.get("/workers/{worker_pk}/heartbeats", response_model=list[HeartbeatOut])
async def worker_heartbeats(worker_pk: uuid.UUID, db: DB, _: WRead,
                            limit: int = Query(default=60, ge=1, le=500)) -> list[HeartbeatOut]:
    await _get(db, worker_pk)
    rows = (
        await db.execute(select(WorkerHeartbeat).where(WorkerHeartbeat.worker_id == worker_pk)
                         .order_by(WorkerHeartbeat.timestamp.desc()).limit(limit))
    ).scalars().all()
    return [HeartbeatOut.model_validate(r) for r in rows]


# --------------------------------------------------------------------------- queues


@router.get("/queues")
async def queue_summary(db: DB, _: QRead) -> dict:
    rows = await db.execute(
        select(Job.status, func.count(), func.coalesce(func.sum(Job.batch_size), 0)).group_by(Job.status)
    )
    summary = {s.value: {"jobs": 0, "recipients": 0} for s in JobStatus}
    for status, n, recipients in rows:
        summary[status.value] = {"jobs": int(n), "recipients": int(recipients)}
    oldest = (
        await db.execute(select(func.min(Job.available_at)).where(Job.status.in_([JobStatus.PENDING, JobStatus.RETRY]),
                                                                    Job.available_at <= func.now()))
    ).scalar_one_or_none()
    return {"by_status": summary, "oldest_ready_at": oldest.isoformat() if oldest else None,
            "event_inbox": await processor.inbox_counts(db)}


@router.get("/queues/jobs", response_model=Page[JobOut])
async def queue_jobs(db: DB, _: QRead, status: JobStatus | None = None, campaign_id: uuid.UUID | None = None,
                     limit: int = Query(default=50, ge=1, le=200), cursor: str | None = None) -> Page[JobOut]:
    stmt = select(Job)
    if status:
        stmt = stmt.where(Job.status == status)
    if campaign_id:
        stmt = stmt.where(Job.campaign_id == campaign_id)
    rows, nxt = await paginate(db, stmt, [Job.created_at, Job.id], limit, cursor)
    return Page(items=[JobOut.model_validate(j) for j in rows], next_cursor=nxt)


@router.get("/queues/jobs/{job_id}")
async def job_detail(job_id: uuid.UUID, db: DB, _: QRead) -> dict:
    job = await db.get(Job, job_id)
    if job is None:
        raise not_found("Job")
    attempts = (
        await db.execute(select(JobAttempt).where(JobAttempt.job_id == job_id).order_by(JobAttempt.attempt_no))
    ).scalars().all()
    return {
        "job": JobOut.model_validate(job).model_dump(mode="json"),
        "attempts": [
            {"id": str(a.id), "attempt_no": a.attempt_no, "worker_id": str(a.worker_id) if a.worker_id else None,
             "started_at": a.started_at.isoformat(), "finished_at": a.finished_at.isoformat() if a.finished_at else None,
             "outcome": a.outcome.value, "error_code": a.error_code, "error_message": a.error_message}
            for a in attempts
        ],
    }


@router.post("/queues/jobs/{job_id}/requeue", response_model=JobOut)
async def requeue_job(job_id: uuid.UUID, db: DB, _: QWrite, ctx: Ctx) -> JobOut:
    job = await job_service.requeue_dead_letter(db, job_id)
    audit.record(db, ctx, "JOB_REQUEUED", "job", job.id)
    await db.commit()
    return JobOut.model_validate(job)
