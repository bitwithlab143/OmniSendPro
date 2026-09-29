# Testing

| Suite | Command | Needs | Covers |
|---|---|---|---|
| Backend | `cd backend && ../.venv/bin/pytest -q` | Postgres + Redis (+ `redis-server`, `pg_dump` for the drills) | auth/MFA/refresh rotation, RBAC & tenant isolation, API keys, audit, pagination, upload validation, start guards, full send flow via the Worker API, retries → dead-letter → requeue, lease recovery, offline workers, worker pools, quotas, pause/resume/cancel, webhooks → event inbox → processor, DSN/ARF + IMAP bounce mailbox, unsubscribe, provider health, SSE streams, object-storage imports (moto S3), partitioning & retention, template-tag payloads, read-replica routing, Redis Sentinel failover, backup/restore drill, tracing, settings, security headers, metrics, unit tests |
| Worker | `cd worker && ../.venv/bin/pytest -q` | (`redis-server` for the ACL test) | rendering & escaping, template tags, fast-path equivalence with the reference MIME builder, headers, SMTP error classification, pipelined SMTP client (CHUNKING / PIPELINING / lock-step, TLS, AUTH, rejections), token bucket, Redis ACL for workers, connection-pool reuse, job runner |
| End-to-end | `.venv/bin/pytest tests/e2e -q` | Postgres + Redis + `redis-cli`/`redis-server` | production topology (uvicorn API + `app.runner` + worker process + aiosmtpd sink): 120 real deliveries with template tags, no duplicates, hard bounce → suppression, asynchronous DSN bounce via the processor, graceful shutdown, one-click unsubscribe; **Redis-failure drill**: Redis killed and restarted empty mid-campaign, every recipient sent exactly once |
| Web | `npm run typecheck && npm test && npm run build` | Node 22 | API client (refresh single-flight, error mapping, SSE parsing), UI primitives, API-key table, template-tag table, type safety of both apps |
| Monitoring | `promtool check config` / `promtool test rules alerts_test.yml` / `amtool check-config` | Docker | Prometheus config, alert rules (unit-tested), Alertmanager routing |

Environment variables: `TEST_DATABASE_URL` (default `postgresql+asyncpg://omnisend:omnisend@localhost:5432/omnisend_test`),
`TEST_REDIS_URL` (default `redis://localhost:6379/15`), `E2E_DATABASE_URL` (…/omnisend_e2e), `E2E_REDIS_URL` (…/14).
The backend suite drops and recreates the test schema and exercises the real migration (upgrade → downgrade → upgrade).

## Load testing (P3-10)
`tests/load/run_load.py` runs the real API, scheduler and N worker processes against a local SMTP sink
(optionally with simulated provider latency) and reports throughput. Results and options:
[`PERFORMANCE.md`](./PERFORMANCE.md).
