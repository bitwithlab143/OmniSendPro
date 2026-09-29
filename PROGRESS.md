# OmniSendPro — Project Progress

> Single source of truth for **what has been done, what is in progress, and what is next**.
> Companion documents:
> - [`ARCHITECTURE.md`](./ARCHITECTURE.md): the target architecture (the "what" and "why"). Treat it as the baseline spec.
> - [`design.md`](./design.md): concrete design decisions, data models, state machines, API contracts, UI screens (the "how").

**Last updated:** 2026-09-29
**Current phase:** Phase 0: Foundation & Planning
**Overall status:** 🟡 Planning (no application code yet)

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
| 🔄 | In progress |
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
| **Phase 0**: Foundation & Planning | Understand architecture, resolve gaps, scaffold monorepo, dev environment | 🔄 | 3 / 12 |
| **Phase 1**: MVP | Admin/User login, campaigns, provider management, basic queue, single worker, basic sending & reports | ⬜ | 0 / 22 |
| **Phase 2**: Reliability & Compliance | Batch processing, retries, worker monitoring, provider health, suppression, audit logs | ⬜ | 0 / 14 |
| **Phase 3**: Scale & Real-time | Multiple workers, horizontal scaling, real-time dashboard, advanced reports, object storage, event processing | ⬜ | 0 / 11 |
| **Phase 4**: Production Hardening | HA, DB replication, queue HA, autoscaling, observability, disaster recovery | ⬜ | 0 / 9 |

---

## Phase 0: Foundation & Planning

| ID | Task | Design ref | Status | Date | Notes |
|---|---|---|---|---|---|
| P0-01 | Read and analyse `ARCHITECTURE.md` in full | — | ✅ | 2026-09-29 | Gaps and inconsistencies recorded in `design.md` §Open Questions |
| P0-02 | Create `PROGRESS.md` (this file) | — | ✅ | 2026-09-29 | |
| P0-03 | Create `design.md` with baseline designs extracted from architecture | all | ✅ | 2026-09-29 | Items marked *Baseline* (from spec) or *Proposed* (fills a gap) |
| P0-04 | Resolve open questions OQ-01 … OQ-15 in `design.md` | OQ-* | ⬜ | | Needs product-owner input for OQ-01, OQ-02, OQ-05 |
| P0-05 | Accept/reject proposed ADRs (ADR-001 … ADR-010) | ADR-* | ⬜ | | |
| P0-06 | Scaffold monorepo layout (`admin-web/`, `user-web/`, `backend/`, `worker/`, `infrastructure/`, `docs/`) | DS-01 | ⬜ | | Repo root = `OmniSendPro/` (spec says `email-platform/`) |
| P0-07 | `docker-compose.yml` for local dev: postgres, redis, backend, worker, admin-web, user-web, nginx | DS-14 | ⬜ | | |
| P0-08 | `.env.example` with all variables from §35 | DS-14 | ⬜ | | No real secrets committed |
| P0-09 | Backend skeleton: FastAPI app, settings, health endpoint, SQLAlchemy + Alembic wiring | DS-01, ADR-001 | ⬜ | | |
| P0-10 | Worker skeleton: asyncio entrypoint, config loader, structured logging | DS-08 | ⬜ | | |
| P0-11 | Frontend skeletons: React + TS + Vite + Tailwind + shadcn/ui for admin & user apps | DS-12, DS-13 | ⬜ | | |
| P0-12 | CI pipeline: lint, typecheck, unit tests for backend/worker/frontends | DS-15 | ⬜ | | |

## Phase 1: MVP

| ID | Task | Design ref | Status | Date | Notes |
|---|---|---|---|---|---|
| P1-01 | DB schema v1: users, roles, permissions, user_limits | DS-03 | ⬜ | | |
| P1-02 | DB schema v1: campaigns, campaign_recipients, campaign_batches | DS-03 | ⬜ | | |
| P1-03 | DB schema v1: providers, provider_credentials, provider_assignments | DS-03 | ⬜ | | |
| P1-04 | DB schema v1: workers, jobs, job_attempts, email_events | DS-03 | ⬜ | | |
| P1-05 | Secret encryption service (envelope encryption for provider secrets) | DS-11, ADR-006 | ⬜ | | |
| P1-06 | Password hashing (Argon2id) + user/admin auth (JWT access + refresh) | DS-10, ADR-005 | ⬜ | | |
| P1-07 | Admin 2FA (TOTP) | DS-10 | ⬜ | | Required for admin per §32 |
| P1-08 | RBAC: roles, permissions, route guards | DS-10 | ⬜ | | |
| P1-09 | Admin API: users CRUD, suspend/activate, limits | DS-09 | ⬜ | | |
| P1-10 | Admin API: providers CRUD, enable/disable, test connection | DS-09, DS-06 | ⬜ | | |
| P1-11 | Admin API: provider assignment to users | DS-09 | ⬜ | | |
| P1-12 | Admin API + User API: campaigns (create, assign, list, detail) | DS-09, DS-04 | ⬜ | | |
| P1-13 | Recipient CSV upload (small files, streamed parse, validate, dedupe) | DS-07 | ⬜ | | Object storage deferred to P3 |
| P1-14 | Campaign start → job creation → enqueue (basic queue on Redis) | DS-05, ADR-002 | ⬜ | | |
| P1-15 | Worker auth: register, credential exchange, short-lived token | DS-08, DS-10 | ⬜ | | |
| P1-16 | Worker API: claim job (atomic), ack, failure | DS-09, DS-05 | ⬜ | | |
| P1-17 | Worker: SMTP provider adapter behind provider abstraction | DS-06, ADR-007 | ⬜ | | |
| P1-18 | Worker: basic sending loop with per-provider rate limiting | DS-08 | ⬜ | | |
| P1-19 | Record email events (sent/failed) and update campaign counters | DS-03, DS-05 | ⬜ | | |
| P1-20 | Admin web: login, dashboard, users, providers, campaigns pages | DS-12 | ⬜ | | |
| P1-21 | User web: login, dashboard, campaign screen, sending monitor (polling) | DS-13 | ⬜ | | Real-time push deferred to P3 |
| P1-22 | Basic reports: per-campaign delivery summary | DS-09 | ⬜ | | |

## Phase 2: Reliability & Compliance

| ID | Task | Design ref | Status | Date | Notes |
|---|---|---|---|---|---|
| P2-01 | Batch builder honouring user/provider/campaign/worker limits | DS-05, DS-07 | ⬜ | | |
| P2-02 | Retry with backoff schedule + max attempts (configurable) | DS-05, ADR-008 | ⬜ | | |
| P2-03 | Dead-letter queue + admin view/requeue | DS-05 | ⬜ | | |
| P2-04 | Job leases & recovery of jobs from dead workers | DS-05, DS-08 | ⬜ | | |
| P2-05 | Worker heartbeat + ONLINE/WARNING/OFFLINE state | DS-08 | ⬜ | | |
| P2-06 | Provider health scoring over rolling windows | DS-06 | ⬜ | | Never disable on a single failure |
| P2-07 | Provider state machine ACTIVE/WARNING/DEGRADED/DISABLED + admin alerting | DS-06 | ⬜ | | |
| P2-08 | Suppression list + pre-send suppression check | DS-07 | ⬜ | | |
| P2-09 | Unsubscribe endpoint + `List-Unsubscribe` / one-click headers | DS-07 | ⬜ | | |
| P2-10 | Bounce & complaint ingestion (provider webhooks / DSN) → auto-suppression | DS-07, DS-05 | ⬜ | | |
| P2-11 | Audit logging of all sensitive admin actions | DS-11 | ⬜ | | |
| P2-12 | User quota enforcement (daily/hourly) | DS-05 | ⬜ | | |
| P2-13 | Campaign pause / resume / cancel | DS-04 | ⬜ | | |
| P2-14 | Sender authentication checks (SPF/DKIM/DMARC) surfaced in provider setup | DS-06 | ⬜ | | |

## Phase 3: Scale & Real-time

| ID | Task | Design ref | Status | Date | Notes |
|---|---|---|---|---|---|
| P3-01 | Multiple workers with capacity-aware scheduling | DS-05, DS-08 | ⬜ | | |
| P3-02 | Dedicated scheduler service (delayed jobs, retries, balancing) | DS-05 | ⬜ | | |
| P3-03 | Real-time dashboard via SSE/WebSocket | DS-12, ADR-009 | ⬜ | | |
| P3-04 | Presigned uploads to object storage (S3/R2/MinIO) | DS-07 | ⬜ | | |
| P3-05 | Validation worker for large recipient files (streaming, chunked) | DS-07 | ⬜ | | |
| P3-06 | Advanced event processing pipeline (receiver → validator → processor) | DS-05 | ⬜ | | |
| P3-07 | Advanced reports: bounce, complaint, per-user, per-provider | DS-09 | ⬜ | | |
| P3-08 | Cursor-based pagination across all list endpoints | DS-09 | ⬜ | | Can start in P1 |
| P3-09 | Metrics endpoint (Prometheus) for backend & workers | DS-15 | ⬜ | | |
| P3-10 | Load test: 10k jobs, 100k / 500k / 1M recipients | DS-15 | ⬜ | | |
| P3-11 | Table partitioning for email_events / heartbeats / health logs / audit logs | DS-03 | ⬜ | | |

## Phase 4: Production Hardening

| ID | Task | Design ref | Status | Date | Notes |
|---|---|---|---|---|---|
| P4-01 | PostgreSQL HA + replication | DS-14 | ⬜ | | |
| P4-02 | Redis HA / evaluate RabbitMQ or Kafka for durability | DS-05, ADR-002 | ⬜ | | |
| P4-03 | Worker autoscaling from queue depth | DS-08 | ⬜ | | |
| P4-04 | Observability stack: Prometheus, Grafana, Loki, OpenTelemetry | DS-15 | ⬜ | | |
| P4-05 | Alerting rules (worker offline, provider degraded, queue backlog, DLQ growth) | DS-15 | ⬜ | | |
| P4-06 | Backups: daily full + PITR; tested restore runbook | DS-14 | ⬜ | | Untested backup ≠ backup |
| P4-07 | Queue recovery drill (Redis failure, no silent job loss) | DS-05 | ⬜ | | |
| P4-08 | Production deployment (Nginx, Cloudflare, TLS, secret manager) | DS-14 | ⬜ | | |
| P4-09 | Verify all §68 Architecture Success Criteria | — | ⬜ | | See checklist below |

---

## Architecture success criteria (§68)

Tick when verified by a test, drill, or measurement (link the evidence).

- [ ] Admin and User applications are separated
- [ ] Sending is completely decoupled from the Admin API
- [ ] Workers can be added/removed without changing campaigns
- [ ] Jobs are idempotent
- [ ] Jobs survive worker failures
- [ ] Provider failures do not stop the entire system
- [ ] User quotas are enforced
- [ ] Provider limits are enforced
- [ ] Suppressed recipients are never intentionally sent to
- [ ] Delivery events are recorded
- [ ] Reports are eventually consistent and auditable
- [ ] Secrets are encrypted
- [ ] Worker credentials are isolated
- [ ] Database backups are tested
- [ ] Queue recovery is tested
- [ ] Load testing validates the required throughput (target 1.5M/hour ≈ 416.67/sec)
- [ ] Monitoring and alerting are operational

---

## Blockers & risks

| ID | Type | Description | Impact | Mitigation / owner | Status |
|---|---|---|---|---|---|
| R-01 | Spec gap | `ARCHITECTURE.md` has no §19 or §59 (numbering skips) | Possibly missing requirements | Confirm with author whether content was lost (OQ-15) | Open |
| R-02 | Spec gap | Campaign table has no content fields (HTML/text body, template) | Cannot send without them | Proposed in `design.md` DS-03 (OQ-03) | Open |
| R-03 | Spec gap | Tenancy model unspecified (user = tenant, or organisation above users?) | Affects every table and query | OQ-01 | Open |
| R-04 | Throughput | 416.67/sec target depends on provider limits outside our control | Target may not be met | Treat as a configurable capacity target (§64); validate with load tests | Open |

---

## Decision log

Short record of decisions that changed scope or design. Full rationale lives in `design.md` ADRs.

| Date | Decision | Ref |
|---|---|---|
| 2026-09-29 | Adopt `ARCHITECTURE.md` as baseline spec; track gaps as open questions rather than silently filling them | design.md §Open Questions |
| 2026-09-29 | Use `PROGRESS.md` + `design.md` as the two living tracking documents | — |

---

## Changelog

Newest first. One line per completed task or significant change.

- **2026-09-29**: Reviewed `ARCHITECTURE.md` in full; created `PROGRESS.md` and `design.md`. (P0-01, P0-02, P0-03)
