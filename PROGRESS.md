# OmniSendPro — Project Progress

> Single source of truth for **what has been done, what is in progress, and what is next**.
> Companion documents:
> - [`ARCHITECTURE.md`](./ARCHITECTURE.md): the target architecture (the "what" and "why"). Treat it as the baseline spec.
> - [`design.md`](./design.md): concrete design decisions, data models, state machines, API contracts, UI screens (the "how").

**Last updated:** 2026-09-29
**Current phase:** Phase 2 (Reliability & Compliance) is complete; Phase 3 (Scale & Real-time) is in progress
**Overall status:** 🟢 MVP implemented and tested end to end; performance pass done (≈5× per worker, 17M/hour measured)

---

## How to use this file

1. **Before starting work**, find the task below (or add it) and set it to `🔄 In progress`.
2. **Every task links to the design it implements** through `design.md` IDs (e.g. `DS-05`, `ADR-003`). If the design does not exist yet, write or extend it in `design.md` **first**, then start coding.
3. **When a task is finished**, set it to `✅ Done`, fill in the date, and add a line to the [Changelog](#changelog).
4. **If a design changes during implementation**, update `design.md` (bump the item's status/version) and record the reason in the [Decision log](#decision-log) here.
5. **Blocked work** goes to `⛔ Blocked` with the reason in the *Notes* column and an entry under [Blockers & risks](#blockers--risks).
6. Update the header (`Last updated`, `Current phase`, `Overall status`) at the end of every working session.

### Status legend

| Symbol | Meaning |
|---|---|
| ⬜ | Not started |
| 🔄 | In progress / partially done (see Notes) |
| ✅ | Done |
| ⛔ | Blocked |
| ⏸️ | Deferred / postponed |
| ❌ | Dropped (keep the row, explain why) |

### Task ID convention

`P<phase>-<number>`, e.g. `P1-07`. IDs are never reused, even if a task is dropped.

---

## Phase overview

Phases follow `ARCHITECTURE.md §62 Development Phases`, plus a Phase 0 for setup.

| Phase | Goal | Status | Progress |
|---|---|---|---|
| **Phase 0**: Foundation & Planning | Understand architecture, resolve gaps, scaffold monorepo, dev environment | ✅ | 12 / 12 |
| **Phase 1**: MVP | Admin/User login, campaigns, provider management, basic queue, single worker, basic sending & reports | ✅ | 22 / 22 |
| **Phase 2**: Reliability & Compliance | Batch processing, retries, worker monitoring, provider health, suppression, audit logs | ✅ | 15 / 15 |
| **Phase 3**: Scale & Real-time | Multiple workers, horizontal scaling, real-time dashboard, advanced reports, object storage, event processing | 🔄 | 15 / 16 (P3-10 large multi-machine run left) |
| **Phase 4**: Production Hardening | HA, DB replication, queue HA, autoscaling, observability, disaster recovery | 🔄 | 0 / 10 (4 partial) |

---

## Phase 0: Foundation & Planning

| ID | Task | Design ref | Status | Date | Notes |
|---|---|---|---|---|---|
| P0-01 | Read and analyse `ARCHITECTURE.md` in full | — | ✅ | 2026-09-29 | Gaps and inconsistencies recorded in `design.md` §Open Questions |
| P0-02 | Create `PROGRESS.md` (this file) | — | ✅ | 2026-09-29 | |
| P0-03 | Create `design.md` with baseline designs extracted from architecture | all | ✅ | 2026-09-29 | Items marked *Baseline* (from spec) or *Proposed* (fills a gap) |
| P0-04 | Resolve open questions OQ-01 … OQ-15 in `design.md` | OQ-* | ✅ | 2026-09-29 | Proposed answers adopted as defaults (overridable). OQ-15 (§19/§59 missing) still needs the author |
| P0-05 | Accept/reject proposed ADRs (ADR-001 … ADR-010) | ADR-* | ✅ | 2026-09-29 | All accepted and implemented; see design.md §Implementation notes |
| P0-06 | Scaffold monorepo layout (`admin-web/`, `user-web/`, `backend/`, `worker/`, `infrastructure/`, `docs/`) | DS-01 | ✅ | 2026-09-29 | Plus `packages/web-shared` (npm workspaces) and `tests/e2e` |
| P0-07 | `docker-compose.yml` for local dev: postgres, redis, backend, worker, admin-web, user-web, nginx | DS-14 | ✅ | 2026-09-29 | Admin + user panels served by one nginx `web` image; `migrate` one-shot; `worker`/`dev` profiles. Verified: full send through containers |
| P0-08 | `.env.example` with all variables from §35 | DS-14 | ✅ | 2026-09-29 | |
| P0-09 | Backend skeleton: FastAPI app, settings, health endpoint, SQLAlchemy + Alembic wiring | DS-01, ADR-001 | ✅ | 2026-09-29 | `/healthz`, `/readyz`, `/metrics` |
| P0-10 | Worker skeleton: asyncio entrypoint, config loader, structured logging | DS-08 | ✅ | 2026-09-29 | |
| P0-11 | Frontend skeletons: React + TS + Vite + Tailwind + shadcn/ui for admin & user apps | DS-12, DS-13 | ✅ | 2026-09-29 | React 19, Vite 7, Tailwind 4, shadcn-style components in `packages/web-shared` |
| P0-12 | CI pipeline: lint, typecheck, unit tests for backend/worker/frontends | DS-15 | ✅ | 2026-09-29 | `.github/workflows/ci.yml`: backend (ruff, `alembic check`, pytest), worker, e2e, web, docker build |

## Phase 1: MVP

| ID | Task | Design ref | Status | Date | Notes |
|---|---|---|---|---|---|
| P1-01 | DB schema v1: users, roles, permissions, user_limits | DS-03 | ✅ | 2026-09-29 | Plus `refresh_tokens` |
| P1-02 | DB schema v1: campaigns, campaign_recipients, campaign_batches | DS-03 | ✅ | 2026-09-29 | |
| P1-03 | DB schema v1: providers, provider_credentials, provider_assignments | DS-03 | ✅ | 2026-09-29 | Plus `provider_health_logs` |
| P1-04 | DB schema v1: workers, jobs, job_attempts, email_events | DS-03 | ✅ | 2026-09-29 | Plus `worker_heartbeats`, `suppression_list`, `audit_logs`, `system_settings` |
| P1-05 | Secret encryption service (envelope encryption for provider secrets) | DS-11, ADR-006 | ✅ | 2026-09-29 | AES-256-GCM, versioned keys |
| P1-06 | Password hashing (Argon2id) + user/admin auth (JWT access + refresh) | DS-10, ADR-005 | ✅ | 2026-09-29 | Refresh rotation with reuse detection, lockout, IP throttling |
| P1-07 | Admin 2FA (TOTP) | DS-10 | ✅ | 2026-09-29 | Mandatory enrolment on first admin login; optional for users; replay-protected |
| P1-08 | RBAC: roles, permissions, route guards | DS-10 | ✅ | 2026-09-29 | Matrix in `backend/app/core/rbac.py` |
| P1-09 | Admin API: users CRUD, suspend/activate, limits | DS-09 | ✅ | 2026-09-29 | Plus password reset and 2FA reset |
| P1-10 | Admin API: providers CRUD, enable/disable, test connection | DS-09, DS-06 | ✅ | 2026-09-29 | |
| P1-11 | Admin API: provider assignment to users | DS-09 | ✅ | 2026-09-29 | |
| P1-12 | Admin API + User API: campaigns (create, assign, list, detail) | DS-09, DS-04 | ✅ | 2026-09-29 | |
| P1-13 | Recipient CSV upload (small files, streamed parse, validate, dedupe) | DS-07 | ✅ | 2026-09-29 | Chunked, parsed in a thread; up to 50 MB. Object storage deferred to P3-04 |
| P1-14 | Campaign start → job creation → enqueue (basic queue on Redis) | DS-05, ADR-002 | ✅ | 2026-09-29 | Queue is the Postgres `jobs` table (ADR-002/010); Redis holds limits, locks and live stats |
| P1-15 | Worker auth: register, credential exchange, short-lived token | DS-08, DS-10 | ✅ | 2026-09-29 | |
| P1-16 | Worker API: claim job (atomic), ack, failure | DS-09, DS-05 | ✅ | 2026-09-29 | Plus lease, results, release |
| P1-17 | Worker: SMTP provider adapter behind provider abstraction | DS-06, ADR-007 | ✅ | 2026-09-29 | Pooled aiosmtplib |
| P1-18 | Worker: basic sending loop with per-provider rate limiting | DS-08 | ✅ | 2026-09-29 | Redis token bucket shared across workers |
| P1-19 | Record email events (sent/failed) and update campaign counters | DS-03, DS-05 | ✅ | 2026-09-29 | |
| P1-20 | Admin web: login, dashboard, users, providers, campaigns pages | DS-12 | ✅ | 2026-09-29 | Also workers, queues, reports, suppressions, audit logs, settings |
| P1-21 | User web: login, dashboard, campaign screen, sending monitor (polling) | DS-13 | ✅ | 2026-09-29 | 4-step wizard, live monitor (2 s polling), reports, profile |
| P1-22 | Basic reports: per-campaign delivery summary | DS-09 | ✅ | 2026-09-29 | Plus hourly chart, top errors and CSV export |

## Phase 2: Reliability & Compliance

| ID | Task | Design ref | Status | Date | Notes |
|---|---|---|---|---|---|
| P2-01 | Batch builder honouring user/provider/campaign/worker limits | DS-05, DS-07 | ✅ | 2026-09-29 | Set-based SQL, no recipients in memory |
| P2-02 | Retry with backoff schedule + max attempts (configurable) | DS-05, ADR-008 | ✅ | 2026-09-29 | Per recipient and per job |
| P2-03 | Dead-letter queue + admin view/requeue | DS-05 | ✅ | 2026-09-29 | |
| P2-04 | Job leases & recovery of jobs from dead workers | DS-05, DS-08 | ✅ | 2026-09-29 | |
| P2-05 | Worker heartbeat + ONLINE/WARNING/OFFLINE state | DS-08 | ✅ | 2026-09-29 | |
| P2-06 | Provider health scoring over rolling windows | DS-06 | ✅ | 2026-09-29 | Never disables on a single failure (min sample + consecutive bad windows) |
| P2-07 | Provider state machine ACTIVE/WARNING/DEGRADED/DISABLED + admin alerting | DS-06 | ✅ | 2026-09-29 | Alerting = dashboard, audit log and Prometheus rules; push notifications tracked in P4-05 |
| P2-08 | Suppression list + pre-send suppression check | DS-07 | ✅ | 2026-09-29 | Checked at batch build and again at claim |
| P2-09 | Unsubscribe endpoint + `List-Unsubscribe` / one-click headers | DS-07 | ✅ | 2026-09-29 | RFC 8058 |
| P2-10 | Bounce & complaint ingestion (provider webhooks / DSN) → auto-suppression | DS-07, DS-05, DS-16 | ✅ | 2026-09-29 | Signed webhooks, SMTP-time hard bounces, and asynchronous DSN (RFC 3464) + ARF (RFC 5965) reports via an IMAP bounce mailbox polled by the scheduler or the signed raw-message endpoint (`forward-bounce.sh` for MTA pipes). Explicit per-provider *reports delivery* flag |
| P2-11 | Audit logging of all sensitive admin actions | DS-11 | ✅ | 2026-09-29 | |
| P2-12 | User quota enforcement (daily/hourly) | DS-05 | ✅ | 2026-09-29 | Atomic reserve/refund in Redis |
| P2-13 | Campaign pause / resume / cancel | DS-04 | ✅ | 2026-09-29 | |
| P2-14 | Sender authentication checks (SPF/DKIM/DMARC) surfaced in provider setup | DS-06 | ✅ | 2026-09-29 | |
| P2-15 | Admin "System → API Keys" (§6) for programmatic access | DS-17, ADR-012 | ✅ | 2026-09-29 | Scoped `osk_` keys for the User API (hash stored, shown once, optional expiry, per-key rate limit, audited). Admin → API keys page and user Profile → API keys |

## Phase 3: Scale & Real-time

| ID | Task | Design ref | Status | Date | Notes |
|---|---|---|---|---|---|
| P3-01 | Multiple workers with capacity-aware scheduling | DS-05, DS-08 | ✅ | 2026-09-29 | Pull-based: workers claim only when they have capacity; multi-worker exclusivity is tested |
| P3-02 | Dedicated scheduler service (delayed jobs, retries, balancing) | DS-05, DS-19 | ✅ | 2026-09-29 | `python -m app.runner [scheduler] [processor]`; compose service `runner`, API runs with `RUN_SCHEDULER=false`; e2e test runs this topology |
| P3-03 | Real-time dashboard via SSE/WebSocket | DS-20, ADR-009 | ✅ | 2026-09-29 | SSE streams for admin/user dashboards and campaign stats; `useLiveQuery` (fetch streaming with bearer auth) with polling fallback and backoff |
| P3-04 | Presigned uploads to object storage (S3/R2/MinIO) | DS-07, DS-21, ADR-015 | ✅ | 2026-09-29 | Presigned POST (size-limited) straight from the browser; boto3 signs only; CSP origin via `STORAGE_UPLOAD_ORIGIN`; MinIO compose profile. Verified in Chromium with 200k rows |
| P3-05 | Validation worker for large recipient files (streaming, chunked) | DS-07, DS-21 | ✅ | 2026-09-29 | `recipient_imports` (migration 0007) processed by the runner: streamed via presigned GET, live progress, atomic commit, stale imports requeued, start/upload blocked while importing |
| P3-06 | Advanced event processing pipeline (receiver → validator → processor) | DS-05, DS-19 | ✅ | 2026-09-29 | Webhooks/raw reports are validated and queued in `event_inbox` (202); leased, retried with backoff, dead after 10 attempts; inbox depth in `/admin/queues` and `/metrics` |
| P3-07 | Advanced reports: bounce, complaint, per-user, per-provider | DS-09 | ✅ | 2026-09-29 | Rates plus per-user and per-provider breakdowns |
| P3-08 | Cursor-based pagination across all list endpoints | DS-09 | ✅ | 2026-09-29 | |
| P3-09 | Metrics endpoint (Prometheus) for backend & workers | DS-15 | ✅ | 2026-09-29 | Backend `/metrics`; worker metrics arrive through heartbeats |
| P3-10 | Load test: 10k jobs, 100k / 500k / 1M recipients | DS-15 | 🔄 | 2026-09-29 | `tests/load/run_load.py` + results in `docs/PERFORMANCE.md` (up to 60k recipients, 1–3 workers, simulated latency). **Remaining:** 500k–1M run on separate machines |
| P3-11 | Table partitioning for email_events / heartbeats / health logs / audit logs | DS-03, DS-18, ADR-013 | ✅ | 2026-09-29 | Migration 0005: monthly (events, audit, health) and daily (heartbeats) range partitions + DEFAULT partition; event dedupe moved to `email_event_keys` |
| P3-12 | Retention/cleanup jobs for heartbeats, health logs, expired refresh tokens | DS-18 | ✅ | 2026-09-29 | Hourly maintenance: creates partitions ahead (moving any default-partition rows), drops expired partitions per `retention_*_days` settings, prunes refresh tokens and dedupe keys |
| P3-13 | Worker throughput: compiled messages, SMTP connection reuse across jobs, uvloop | DS-08 | ✅ | 2026-09-29 | 396 → 2,307 msgs/s per worker (docs/PERFORMANCE.md) |
| P3-14 | Provider `max_connections` + immediate scheduler wake-up on start/resume | DS-06, DS-05 | ✅ | 2026-09-29 | Migration 0002; start latency ≈1 s |
| P3-15 | SMTP PIPELINING (RFC 2920) client | DS-08, DS-22, ADR-016 | ✅ | 2026-09-29 | Own asyncio SMTP client: PIPELINING+CHUNKING = 1 round trip/message, PIPELINING = 2, else lock-step; `WORKER_SMTP_PIPELINING`. 25 ms RTT, 1 worker × 16 connections: 153 → 590 msgs/s |
| P3-16 | Email template tags (#USERID#, #RANDOM#, #EMAIL#, #SUBSID#, #INVOICE#, #REF#, #HASH#, #DATE#, #TIME#, #OTP#, #$$#, #MASSAGE#) | DS-24 | ✅ | 2026-09-29 | User request. Worker renders per recipient (HMAC of a secret per-campaign seed: stable per message, unpredictable across recipients; HTML/header-safe); `message_list` + `template_timezone`; "Available tags" table with copy/insert beside subject and HTML editors |

## Phase 4: Production Hardening

| ID | Task | Design ref | Status | Date | Notes |
|---|---|---|---|---|---|
| P4-01 | PostgreSQL HA + replication | DS-14 | ⬜ | | |
| P4-02 | Redis HA / evaluate RabbitMQ or Kafka for durability | DS-05, DS-23, ADR-002, ADR-014 | ✅ | 2026-09-29 | `REDIS_SENTINELS` in backend and workers; failover test kills the master and the client continues on the promoted replica. Broker not adopted (ADR-014) |
| P4-03 | Worker autoscaling from queue depth | DS-08 | ⬜ | | `omnisend_queue_depth` metric is available to drive it |
| P4-04 | Observability stack: Prometheus, Grafana, Loki, OpenTelemetry | DS-15 | 🔄 | | JSON logs, `/metrics`, and an example scrape config and alert rules exist. **Remaining:** deploy the stack, dashboards, tracing |
| P4-05 | Alerting rules (worker offline, provider degraded, queue backlog, DLQ growth) | DS-15 | 🔄 | | Rules in `infrastructure/monitoring/alerts.yml`. **Remaining:** Alertmanager routing (email/Slack) |
| P4-06 | Backups: daily full + PITR; tested restore runbook | DS-14 | ⬜ | | Untested backup ≠ backup |
| P4-07 | Queue recovery drill (Redis failure, no silent job loss) | DS-05 | ⬜ | | Design guarantees it; drill not yet run |
| P4-08 | Production deployment (Nginx, Cloudflare, TLS, secret manager) | DS-14 | 🔄 | | Images, nginx and a production-mode compose run are verified. **Remaining:** real environment |
| P4-09 | Verify all §68 Architecture Success Criteria | — | 🔄 | | 14 / 17 verified (see below) |
| P4-10 | Restricted Redis ACL user for workers (rate-limit keys only) | ADR-004, DS-23 | ✅ | 2026-09-29 | `infrastructure/redis/render-acl.sh`: `worker` user limited to `rl:provider:*` and the bucket's commands; default user off; tested against a real redis-server |

---

## Architecture success criteria (§68)

Tick when verified by a test, drill, or measurement (link the evidence).

- [x] Admin and User applications are separated: separate apps, and tokens are bound to their app (`backend/tests/test_rbac.py`)
- [x] Sending is completely decoupled from the Admin API: only workers send, via the Worker API (`tests/e2e/test_pipeline.py`)
- [x] Workers can be added/removed without changing campaigns: pull model; two-worker exclusivity (`backend/tests/test_reliability.py::test_stale_attempt_and_lease_recovery`)
- [x] Jobs are idempotent: duplicate result reports ignored, stale attempts rejected (`backend/tests/test_campaign_flow.py`, `backend/tests/test_reliability.py`)
- [x] Jobs survive worker failures: lease expiry and offline recovery (`backend/tests/test_reliability.py`)
- [x] Provider failures do not stop the entire system: health state machine, and only that provider's jobs are skipped (`backend/tests/test_events_and_health.py`)
- [x] User quotas are enforced (`test_hourly_quota_limits_claims`)
- [x] Provider limits are enforced: daily/hourly (`test_provider_daily_limit`) and per-second shared bucket (`worker/tests`)
- [x] Suppressed recipients are never intentionally sent to (`tests/e2e`, `test_end_to_end_send`)
- [x] Delivery events are recorded (`email_events`; webhook tests)
- [x] Reports are eventually consistent and auditable: counters plus the event log; audit log tests
- [x] Secrets are encrypted: AES-256-GCM, never returned (`test_provider_secret_is_never_returned`)
- [x] Worker credentials are isolated: separate key and audience (`test_invalid_and_worker_tokens_rejected_on_user_api`)
- [ ] Database backups are tested (P4-06)
- [ ] Queue recovery is tested: lease recovery is tested; Redis-failure drill pending (P4-07)
- [x] Load testing validates the required throughput (target 1.5M/hour ≈ 416.67/sec): measured 2,307 msgs/s with one worker, 4,803 msgs/s with three (`docs/PERFORMANCE.md`); large-scale multi-machine run still pending (P3-10)
- [ ] Monitoring and alerting are operational (P4-04, P4-05)

---

## Test status (2026-09-29)

| Suite | Result |
|---|---|
| Backend (`backend/tests`, real Postgres + Redis) | ✅ 75 passed |
| Worker (`worker/tests`) | ✅ 29 passed |
| Load test (`tests/load`) | ✅ 60k recipients: 2,307 / 3,734 / 4,803 msgs/s with 1 / 2 / 3 workers |
| End-to-end (`tests/e2e`: uvicorn + scheduler + worker process + SMTP sink) | ✅ 1 passed (120 deliveries) |
| Web (`packages/web-shared` vitest) | ✅ 11 passed; both apps typecheck and build |
| Browser verification (Chromium, dev and production-mode Docker stack) | ✅ admin and user flows; 300 real deliveries through the containers; bounce mailbox and API-key screens (desktop + 390 px) |

---

## Blockers & risks

| ID | Type | Description | Impact | Mitigation / owner | Status |
|---|---|---|---|---|---|
| R-01 | Spec gap | `ARCHITECTURE.md` has no §19 or §59 (numbering skips) | Possibly missing requirements | Confirm with author whether content was lost (OQ-15) | Open |
| R-02 | Spec gap | Campaign table has no content fields (HTML/text body, template) | Cannot send without them | Added `html_body`/`text_body` + merge variables (OQ-03) | Resolved |
| R-03 | Spec gap | Tenancy model unspecified | Affects every table and query | User = tenant (OQ-01); add `organizations` later if needed | Resolved (default) |
| R-04 | Throughput | 416.67/sec target depends on provider limits outside our control | Target may not be met | Platform measured at 4.8k msgs/s; real-world rate = provider limits × `max_connections` (docs/PERFORMANCE.md) | Mitigated |
| R-05 | Repo hygiene | A local Redis snapshot (`dump.rdb`, test data only) was committed in `e0d9add` and removed in `79d4439` | Low: no secrets, but it stays in history | Now in `.gitignore`; rewrite history only if the owner wants it | Accepted |
| R-06 | Operations | Scheduler runs inside API replicas (leader lock) | A slow tick shares CPU with API requests | Split into its own process (P3-02) | Open |
| R-07 | Compliance | Plain-SMTP providers without webhooks only report bounces seen during SMTP; asynchronous bounces and complaints are missed | Bounce/complaint suppression incomplete for those providers | DSN/FBL ingestion (P2-10): bounce mailbox (IMAP) or signed raw-message endpoint per provider | Resolved |

---

## Decision log

Short record of decisions that changed scope or design. Full rationale lives in `design.md` ADRs.

| Date | Decision | Ref |
|---|---|---|
| 2026-09-29 | Adopt `ARCHITECTURE.md` as baseline spec; track gaps as open questions rather than silently filling them | design.md §Open Questions |
| 2026-09-29 | Use `PROGRESS.md` + `design.md` as the two living tracking documents | — |
| 2026-09-29 | Adopt the proposed answers to OQ-01…OQ-14 as working defaults to unblock implementation | design.md §Open Questions |
| 2026-09-29 | Postgres `jobs` table is the queue (claim via `SKIP LOCKED`); Redis is used for limits, locks and live stats, not job storage | ADR-002, ADR-010 |
| 2026-09-29 | Worker API returns the provider credential inside the claim payload (workers never touch the DB) | ADR-004, design §Implementation notes |
| 2026-09-29 | Providers without a delivery webhook count SMTP acceptance as "delivered" | design §Implementation notes |
| 2026-09-29 | Superseded: an explicit provider flag `reports_delivery` decides whether SMTP acceptance counts as delivered (a webhook secret can now exist just for raw bounce reports) | DS-16 |
| 2026-09-29 | Asynchronous bounces: IMAP polling + signed raw endpoint; no inbound SMTP server | ADR-011 |
| 2026-09-29 | Shared frontend package `packages/web-shared` introduced up front (duplication between the two apps would be large) | DS-01 |
| 2026-09-29 | Unsubscribe links point at the User panel origin (`PUBLIC_BASE_URL`); https one-click only, no mailto | DS-07 |

---

## Changelog

Newest first. One line per completed task or significant change.

- **2026-09-29**: Email template tags (DS-24, user request) and the "Available tags" reference table with copy-to-clipboard; SMTP pipelining/chunking (P3-15: 153 → 590 msgs/s at 25 ms RTT); large uploads via object storage + import worker (P3-04/05); SSE live dashboards (P3-03); runner process + event inbox (P3-02/06); partitioning + retention (P3-11/12).
- **2026-09-29**: API keys (DS-17): `api_keys` table (migration 0004), `osk_` bearer auth on the User API with scopes ∩ role, per-key rate limit, session-only account endpoints, admin *API keys* page and user *Profile → API keys*; shared key table/dialogs in `web-shared`. Failed connection tests / polls now show error toasts. `npm test` no longer fails on the apps without test files. Phase 2 complete. (P2-15)

- **2026-09-29**: Asynchronous bounces and complaints (DS-16): DSN/ARF parser, IMAP bounce mailbox per provider (scheduler-polled, test/poll/remove in the admin UI), signed raw-message endpoint and `forward-bounce.sh`, correlation by Message-ID or campaign + address restricted to the sending provider, SSRF guard and mandatory TLS for IMAP. 17 new tests. Resolves R-07. (P2-10)

- **2026-09-29**: Performance pass. Load-test harness; compiled message rendering (~110× faster per message), SMTP connection reuse across jobs, uvloop, provider *Max connections*, scheduler wake-up on start. 396 → 2,307 msgs/s per worker; 4,803 msgs/s with 3 workers. Fixed two bugs found by load testing: CSV row split at the 64 KB sample boundary, and bootstrap race with several API processes. (P3-10 partial, P3-13, P3-14)
- **2026-09-29**: Docker images, nginx (strict CSP, login rate limits, separate worker-API listener), compose stack, CI workflow, docs (`docs/*.md`, README, CONTRIBUTING). Production-mode compose verified with 300 deliveries. (P0-07, P0-08, P0-12, P4-04/05/08 partial)
- **2026-09-29**: Admin and User web apps with shared UI package; verified in Chromium against the real stack. Fixed from that verification: user dashboard "today" semantics, report refresh after completion, mobile nav accessible names. (P0-11, P1-20, P1-21)
- **2026-09-29**: Delivery worker and the black-box end-to-end pipeline test. (P0-10, P1-15 … P1-18, P3-01)
- **2026-09-29**: Backend control plane: schema, auth/2FA/RBAC, admin/user/worker/public APIs, queue/leases/retries/DLQ, quotas, suppression, webhooks, health scoring, reports, metrics, scheduler; 53 tests. (P0-09, P1-01 … P1-14, P1-19, P1-22, P2-*, P3-07/08/09)
- **2026-09-29**: Adopted default answers for OQ-01 … OQ-14. (P0-04, P0-05)
- **2026-09-29**: Reviewed `ARCHITECTURE.md` in full; created `PROGRESS.md` and `design.md`. (P0-01, P0-02, P0-03)

---

## Next up (recommended order)

1. **P3-15**: SMTP PIPELINING, then the large multi-machine load test (P3-10).
2. **P3-12 / P3-11**: retention jobs, then partitioning for high-volume tables.
3. **P3-03**: SSE live updates (replace polling on the monitor and dashboards).
4. **P3-04 / P3-05**: object-storage uploads and an out-of-API validation worker for very large lists.
5. **P4-10**: Redis ACL for workers; **P3-02**: dedicated scheduler process.
6. Phase 4: backups with a restore drill (P4-06), Redis failure drill (P4-07), monitoring deployment (P4-04/05).
