# Database

PostgreSQL 16, schema managed by Alembic (`backend/migrations`). Every table from ARCHITECTURE.md §25
exists; design DS-03 documents the columns added to fill gaps (content fields, leases, idempotency…).

```bash
cd backend
../.venv/bin/alembic upgrade head          # apply
../.venv/bin/alembic revision --autogenerate -m "describe change"   # after editing app/models
../.venv/bin/alembic check                 # CI fails if models and migrations drift
```

## Key design points
- **Primary keys:** UUIDv7 (time ordered) for entities; `BIGINT IDENTITY` for high-volume rows
  (`campaign_recipients`, `email_events`, `worker_heartbeats`, `provider_health_logs`, `audit_logs`, `suppression_list`).
- **Status columns** are `VARCHAR + CHECK` (portable and easy to migrate) — see `app/models/enums.py`.
- **Jobs are the system of record** (ADR-002). The claim query uses `FOR UPDATE SKIP LOCKED` on the
  partial index `idx_jobs_claimable (status, available_at) WHERE status IN ('pending','retry')`;
  expired leases are found through `idx_jobs_lease`.
- **Idempotency:** `jobs.idempotency_key` is unique; recipient results are applied only while the
  recipient is still `queued` in the job's batch; `email_events` has a partial unique index for
  provider webhook de-duplication.
- **Suppression** is unique per `(scope_user_id, email_normalized)` with `NULLS NOT DISTINCT`
  (global entries have a NULL scope).
- **Counters** on `campaigns` are denormalised and eventually consistent; `email_events` is the source of truth.

## Growth (Phase 3, P3-11)
Range-partition `email_events`, `worker_heartbeats`, `provider_health_logs` and `audit_logs` by month
once volume requires it; add a retention job for heartbeats and health logs.

## Backups (§46)
Daily full backup + WAL archiving for point-in-time recovery (e.g. pgBackRest or managed PITR).
A backup that has not been restored is not verified — schedule restore drills (P4-06).
