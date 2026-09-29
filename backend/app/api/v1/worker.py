"""/api/v1/worker — data-plane API used by worker nodes (§33, ADR-004, ADR-010).

Workers never receive database credentials: they authenticate with a provisioned credential, receive a
short-lived token (separate audience + signing key) and use only these endpoints.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Response

from app.api.deps import DB, CurrentWorker
from app.schemas.domain import (
    AttemptRef,
    FailureRequest,
    HeartbeatRequest,
    ReleaseRequest,
    ResultsRequest,
    WorkerRegisterRequest,
    WorkerTokenRequest,
)
from app.services import jobs as job_service
from app.services import workers as worker_service

router = APIRouter(prefix="/worker", tags=["worker"])


@router.post("/token")
async def token(body: WorkerTokenRequest, db: DB) -> dict[str, Any]:
    worker, access, ttl = await worker_service.exchange_token(db, body.worker_id, body.credential)
    return {"access_token": access, "token_type": "bearer", "expires_in": ttl, "worker_id": worker.worker_id}


@router.post("/register")
async def register(body: WorkerRegisterRequest, db: DB, worker: CurrentWorker) -> dict[str, Any]:
    return await worker_service.register(db, worker, body.model_dump(exclude_none=True))


@router.post("/heartbeat")
async def heartbeat(body: HeartbeatRequest, db: DB, worker: CurrentWorker) -> dict[str, Any]:
    return await worker_service.heartbeat(db, worker, body.model_dump())


@router.get("/health")
async def health(worker: CurrentWorker) -> dict[str, Any]:
    return {"ok": True, "worker_id": worker.worker_id, "disabled": worker.disabled}


@router.post("/jobs/claim", response_model=None)
async def claim(db: DB, worker: CurrentWorker) -> Response | dict[str, Any]:
    claimed = await job_service.claim(db, worker)
    if claimed is None:
        return Response(status_code=204)
    return job_service.claim_payload(claimed)


@router.post("/jobs/{job_id}/lease")
async def renew_lease(job_id: uuid.UUID, body: AttemptRef, db: DB, worker: CurrentWorker) -> dict[str, Any]:
    return await job_service.renew_lease(db, worker, job_id, body.attempt_id)


@router.post("/jobs/{job_id}/results")
async def results(job_id: uuid.UUID, body: ResultsRequest, db: DB, worker: CurrentWorker) -> dict[str, Any]:
    summary = await job_service.record_results(
        db, worker, job_id, body.attempt_id, [r.model_dump() for r in body.results]
    )
    return {"accepted": summary.accepted, "ignored": summary.ignored, "sent": summary.sent,
            "deferred": summary.deferred, "bounced": summary.bounced, "failed": summary.failed}


@router.post("/jobs/{job_id}/ack")
async def ack(job_id: uuid.UUID, body: AttemptRef, db: DB, worker: CurrentWorker) -> dict[str, Any]:
    return await job_service.ack(db, worker, job_id, body.attempt_id)


@router.post("/jobs/{job_id}/failure")
async def failure(job_id: uuid.UUID, body: FailureRequest, db: DB, worker: CurrentWorker) -> dict[str, Any]:
    return await job_service.fail(db, worker, job_id, body.attempt_id, body.error_code, body.error_message,
                                  body.transient, body.category)


@router.post("/jobs/{job_id}/release")
async def release(job_id: uuid.UUID, body: ReleaseRequest, db: DB, worker: CurrentWorker) -> dict[str, Any]:
    return await job_service.release(db, worker, job_id, body.attempt_id, body.reason)
