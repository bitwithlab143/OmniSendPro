# Testing

| Suite | Command | Needs | Covers |
|---|---|---|---|
| Backend | `cd backend && ../.venv/bin/pytest -q` | Postgres + Redis | auth/MFA/refresh rotation, RBAC & tenant isolation, audit, pagination, upload validation, start guards, full send flow via the Worker API, retries → dead-letter → requeue, lease recovery, offline workers, quotas, pause/resume/cancel, webhooks, unsubscribe, provider health, settings, security headers, metrics, unit tests |
| Worker | `cd worker && ../.venv/bin/pytest -q` | – | rendering & escaping, fast-path equivalence with the reference MIME builder, headers, SMTP error classification, token bucket, connection-pool reuse, job runner (success, auth failure, connection streak, stop/release) |
| End-to-end | `.venv/bin/pytest tests/e2e -q` | Postgres + Redis + `redis-cli` | uvicorn + scheduler, worker process, aiosmtpd sink: 120 real deliveries, no duplicates, hard bounce → suppression, suppression skip, graceful shutdown, one-click unsubscribe |
| Web | `npm run typecheck && npm test && npm run build` | Node 22 | API client (refresh single-flight, error mapping), UI primitives, type safety of both apps |

Environment variables: `TEST_DATABASE_URL` (default `postgresql+asyncpg://omnisend:omnisend@localhost:5432/omnisend_test`),
`TEST_REDIS_URL` (default `redis://localhost:6379/15`), `E2E_DATABASE_URL` (…/omnisend_e2e), `E2E_REDIS_URL` (…/14).
The backend suite drops and recreates the test schema and exercises the real migration (upgrade → downgrade → upgrade).

## Load testing (P3-10)
`tests/load/run_load.py` runs the real API, scheduler and N worker processes against a local SMTP sink
(optionally with simulated provider latency) and reports throughput. Results and options:
[`PERFORMANCE.md`](./PERFORMANCE.md).
