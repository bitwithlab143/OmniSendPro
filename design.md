# OmniSendPro — Design Document

> Living record of **how** the system is designed: components, data model, state machines, API contracts, UI screens, and the decisions behind them.
> - Baseline spec: [`ARCHITECTURE.md`](./ARCHITECTURE.md) (§ numbers below refer to it)
> - Work tracking: [`PROGRESS.md`](./PROGRESS.md). Each task there references the `DS-xx` / `ADR-xxx` item it implements.

**Last updated:** 2026-09-29
**Document version:** 0.4.2

---

## How to use this file

- Each design area has a stable ID (`DS-01` … `DS-15`). Tasks in `PROGRESS.md` point to these IDs.
- Every design item carries a **status**:

| Status | Meaning |
|---|---|
| **Baseline** | Taken directly from `ARCHITECTURE.md`; do not change without an ADR |
| **Proposed** | Fills a gap or refines the baseline; needs review before implementation |
| **Accepted** | Reviewed and approved; implement as written |
| **Implemented** | Built; the section describes the code as it is (link the code) |
| **Superseded** | Replaced; keep for history and link the replacement |

- Significant decisions become **ADRs** (Architecture Decision Records) in [§ADRs](#architecture-decision-records-adrs).
- Unresolved questions go to [§Open Questions](#open-questions). **Do not implement against an open question.** Resolve it first and record the answer.
- When implementation diverges from a design, update the design **in the same change** and log it in the [Design changelog](#design-changelog) and in `PROGRESS.md` → Decision log.

---

## Design index

| ID | Area | Status | Arch § |
|---|---|---|---|
| [DS-01](#ds-01-system-context--repository-layout) | System context & repository layout | Implemented | §1, §4, §36, §66 |
| [DS-02](#ds-02-planes--core-pipeline) | Planes & core pipeline | Implemented | §1, §5, §67 |
| [DS-03](#ds-03-data-model) | Data model | Implemented | §25–§29, §57 |
| [DS-04](#ds-04-campaign-state-machine) | Campaign state machine | Implemented | §50 |
| [DS-05](#ds-05-queue-jobs-scheduling-retry--events) | Queue, jobs, scheduling, retry & events | Implemented | §11–§14, §21–§23, §30–§31, §60–§61 |
| [DS-06](#ds-06-providers) | Providers: records, assignment, health | Implemented | §15–§18 |
| [DS-07](#ds-07-recipients--suppression) | Recipients, uploads & suppression | Implemented | §24, §55–§56 |
| [DS-08](#ds-08-worker) | Worker runtime | Implemented | §8–§10, §41 |
| [DS-09](#ds-09-api) | API surface & conventions | Implemented | §33, §53–§54 |
| [DS-10](#ds-10-authentication--authorization) | Authentication & authorization | Implemented | §32, §49 |
| [DS-11](#ds-11-security-secrets--audit) | Security, secrets & audit | Implemented | §34, §45, §48 |
| [DS-12](#ds-12-admin-panel-ui) | Admin Panel UI | Implemented | §6, §42, §52 |
| [DS-13](#ds-13-user-panel-ui) | User Panel UI | Implemented | §7, §51 |
| [DS-14](#ds-14-infrastructure--deployment) | Infrastructure, deployment & DR | Implemented | §35, §37–§40, §46, §65 |
| [DS-15](#ds-15-observability-metrics--testing) | Observability, metrics & testing | Implemented | §43–§44, §63–§64 |
| [DS-16](#ds-16-asynchronous-bounces--complaints-dsn--arf) | Asynchronous bounces & complaints (DSN / ARF) | Implemented | §23, §24, P2-10 |
| [DS-17](#ds-17-api-keys) | API keys | Implemented | §6 (System → API Keys), §32, P2-15 |

---

## DS-01 System context & repository layout

**Status:** Implemented — Baseline

Three primary layers (§1): the **Admin Control Plane**, the **User Application**, and **Distributed Email Workers**.

```text
admin-web ─┐                    ┌─> PostgreSQL (source of truth)
           ├─> backend (API) ───┤
user-web ──┘                    └─> Redis (queue, locks, rate limits, live stats)
                                        │
                              worker-01 … worker-N ──> approved providers/SMTP
                                        │
                              delivery events ──> event processor ──> PostgreSQL ──> reports
```

**Repository layout.** The repo root is `OmniSendPro/`; the spec calls it `email-platform/`.

```text
OmniSendPro/
├── admin-web/          React + TS + Vite + Tailwind + shadcn/ui
├── user-web/           React + TS + Vite + Tailwind + shadcn/ui
├── backend/
│   ├── app/{api,auth,models,schemas,services,repositories,queue,providers,reports}/
│   ├── migrations/     Alembic
│   ├── tests/
│   └── main.py
├── worker/
│   ├── app/{queue,sender,providers,retry,rate_limit,health,reporting}/
│   ├── tests/
│   └── worker.py
├── infrastructure/{docker,nginx,monitoring,deployment}/
├── docs/               API.md, DATABASE.md, WORKER.md, DEPLOYMENT.md, SECURITY.md, TESTING.md (§66)
├── docker-compose.yml
├── .env.example
├── ARCHITECTURE.md · PROGRESS.md · design.md · README.md
```

**Proposed:** add `packages/shared-ui/` later only if the admin and user apps end up duplicating significant UI code. Until then the two apps stay fully separate (§68: "Admin and User applications are separated").

---

## DS-02 Planes & core pipeline

**Status:** Implemented — Baseline

| Plane | Responsibility | Components |
|---|---|---|
| **Control** | Users, campaigns, provider config, worker registration, assignments, quotas, policies, reporting, admin | Admin/User web, Backend API, PostgreSQL |
| **Data** | Fetch/claim jobs, process batches, send, retry, report results | Scheduler, Redis queue, Workers |
| **Event** | Ingest delivery events, update state, feed reports | Event receiver, validator, processor |

Core pipeline (§1):
`Campaign → Recipient Validation → Queue → Batch Creation → Worker Assignment → Provider/SMTP → Delivery → Event Processing → Reports`

**Invariants (non-negotiable, from §3, §11, §67, §68):**
1. No email is ever sent from an HTTP request handler. Sending happens only in workers.
2. Every recipient passes a suppression check before sending.
3. Provider limits are respected. The system never uses IP rotation or any other technique to evade provider limits or spam filtering.
4. Each job is processed at most once successfully (idempotency).
5. No job is silently lost. Every job ends in `completed` or `dead_letter`, or is recoverable.

---

## DS-03 Data model

**Status:** Implemented — Baseline tables from §25; columns marked `(+)` are **Proposed** additions that fill gaps.

Conventions (Proposed): UUID primary keys (`uuid` v7 for time ordering) · `created_at`/`updated_at` `timestamptz` on every table · soft status columns use Postgres enums or `text` + CHECK · all FKs indexed.

### Identity & access

```sql
users:        id, email (unique), username (unique), password_hash, status, role_id,
              totp_secret_encrypted (+), created_at, updated_at, last_login_at
roles:        id, name  -- SUPER_ADMIN | ADMIN | OPERATOR | USER | VIEWER
permissions:  id, code  -- e.g. campaigns.start
role_permissions (+): role_id, permission_id
user_limits:  id, user_id, daily_limit (+), hourly_limit (+), per_second_limit (+),
              max_batch_size (+), max_recipients_per_campaign (+), updated_by (+), updated_at
subscriptions: id, user_id, plan, status, period_start, period_end   -- (+) columns; purpose TBD (OQ-13)
```

`users.status`: `active | suspended | disabled` (Proposed).

### Campaigns

```sql
campaigns:    id, user_id, name, subject, from_name, from_email, status,
              total_recipients, processed, delivered, failed, bounced, batch_size,
              created_at, started_at, completed_at,
              created_by (+), provider_id (+), html_body (+), text_body (+),
              reply_to (+), scheduled_at (+), paused_at (+), cancelled_at (+),
              complained (+), deferred (+), skipped_suppressed (+)
campaign_recipients: id, campaign_id, email, email_normalized (+), variables jsonb (+),
              status (+), last_event_at (+)
              UNIQUE(campaign_id, email_normalized)
campaign_batches: id, campaign_id, sequence_no (+), recipient_count (+),
              first_recipient_id (+), last_recipient_id (+), status (+)
```

Content fields (`html_body`, `text_body`) are missing from the spec (OQ-03). Counters on `campaigns` are denormalised and **eventually consistent**; `email_events` is the source of truth (§68).

### Providers

```sql
providers:    id, provider_name, type (+ smtp|api), host, port, username, from_email, from_name,
              hourly_limit, daily_limit, per_second_limit, status, health_score,
              tls_mode (+), created_at, updated_at
provider_credentials: id, provider_id, encrypted_secret, key_version (+), rotated_at (+)
provider_assignments: id, user_id, provider_id, status, assigned_at, assigned_by
              -- spec §16 calls this smtp_assignments; use provider_assignments (§25)
provider_health_logs: id, provider_id, window_start, attempts, successes, auth_failures,
              timeouts, deferrals, bounces, complaints, health_score, created_at
```

### Workers & jobs

```sql
workers:      id, worker_id (unique slug e.g. worker-001), name, version, capacity,
              status, credential_hash (+), last_heartbeat_at (+), disabled (+), created_at
worker_heartbeats: id, worker_id, status, cpu, memory, active_jobs, current_rate, timestamp
jobs:         id, campaign_id, worker_id, provider_id, status, batch_size, attempts,
              available_at, started_at, completed_at, created_at,
              user_id (+), batch_id (+), idempotency_key (+ unique),
              lease_expires_at (+), max_attempts (+), last_error (+)
job_attempts: id, job_id, attempt_no, worker_id, started_at, finished_at, outcome, error_code, error_message
email_events: id, campaign_id, job_id, recipient_id, provider_id, event_type,
              provider_message_id, error_code, error_message, created_at
```

`user_id` on `jobs` appears in the §12 example but not in the §28 table. `batch_id`, `idempotency_key`, and `lease_expires_at` are required by §31 and §47 (see OQ-04, OQ-09).

### Compliance & system

```sql
suppression_list: id, scope_user_id (+ nullable = global), email_normalized, type, reason (+),
              source_event_id (+), created_by (+), created_at
              UNIQUE(scope_user_id, email_normalized)
              type ∈ unsubscribe | hard_bounce | complaint | invalid | admin_blocked
audit_logs:   id, admin_id, action, resource, resource_id, old_value jsonb, new_value jsonb,
              ip, user_agent, timestamp
system_settings: key, value jsonb, updated_by, updated_at
```

### Indexes (baseline §28, plus proposed)

```sql
CREATE INDEX idx_jobs_status          ON jobs(status);
CREATE INDEX idx_jobs_available_at    ON jobs(available_at);
CREATE INDEX idx_campaign_user        ON campaigns(user_id);
-- Proposed
CREATE INDEX idx_jobs_claimable       ON jobs(status, available_at) WHERE status IN ('pending','retry');
CREATE INDEX idx_jobs_lease           ON jobs(lease_expires_at) WHERE status IN ('claimed','processing');
CREATE INDEX idx_events_campaign_time ON email_events(campaign_id, created_at);
CREATE INDEX idx_suppression_lookup   ON suppression_list(email_normalized);
```

**Partitioning (Phase 3, §57):** range-partition `email_events`, `provider_health_logs`, `worker_heartbeats`, and `audit_logs` by month on their timestamp column.

---

## DS-04 Campaign state machine

**Status:** Implemented — Baseline (§50). Transition guards are Proposed.

```text
DRAFT ──> READY ──> QUEUED ──> PROCESSING ──> COMPLETED
                                  │  ▲
                                  ▼  │
                                PAUSED
PROCESSING ──> FAILED
PROCESSING | PAUSED | QUEUED ──> CANCELLED   (admin; user may cancel own, TBD)
```

| From → To | Trigger | Guard (Proposed) |
|---|---|---|
| DRAFT → READY | content + recipients validated | ≥1 non-suppressed recipient; from_email allowed for provider; provider assigned & ACTIVE |
| READY → QUEUED | user presses **Start** | quota available; batch_size ≤ limits |
| QUEUED → PROCESSING | first job claimed | — |
| PROCESSING → PAUSED | Pause | unclaimed jobs held; in-flight jobs finish |
| PAUSED → PROCESSING | Resume | re-check quota and provider state |
| PROCESSING → COMPLETED | all jobs `completed` or `dead_letter` | — |
| PROCESSING → FAILED | failure threshold hit / provider disabled with no fallback | configurable |
| * → CANCELLED | Stop | pending jobs → `cancelled`; audit logged |

The Admin Panel menu (§6: Pending / Processing / Completed / Failed) is a **filtered view** over these states: Pending = DRAFT + READY + QUEUED (OQ-08).

---

## DS-05 Queue, jobs, scheduling, retry & events

**Status:** Implemented — Baseline + Proposed.

### Job creation (§11–§14)
`POST /campaigns/{id}/start` only changes state and enqueues a **"build batches"** task. A background builder then streams recipients in chunks of 10,000 (§14): validate → suppression check → split into `campaign_batches` of `batch_size` → insert one `jobs` row per batch → push the job id to Redis.

Effective batch size = `min(user choice, user_limits.max_batch_size, provider policy, campaign policy)` (§13).

### Job state machine (§12, §31, §47)

```text
pending ──claim──> claimed ──start──> processing ──ok──> completed
   ▲                  │                    │
   │           lease expired          transient failure
   │                  ▼                    ▼
   └──────────────── retry <────────── failed ──attempts ≥ max──> dead_letter
                                                  (permanent failure → dead_letter directly)
pending | retry ──> cancelled   (campaign cancelled)
```

### Atomic claim & idempotency: see ADR-010
- Claim uses Postgres `UPDATE … WHERE id = (SELECT … FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *`, setting `worker_id`, `status='claimed'`, and `lease_expires_at = now() + lease`.
- The worker must renew its lease while processing. An expired lease makes the job recoverable (§47).
- `ack` and `failure` calls must present the `attempt_id`. Calls for stale attempts are rejected.
- Per-recipient idempotency key: `sha256(campaign_id:recipient_id)`. It is stored before send, so a retry skips recipients that already have a `sent` event.

### Redis keys (§30)

```text
queue:email            ready job ids (list/stream)
queue:retry            zset scored by available_at
queue:dead             dead-letter ids
rate:user:{id}         token bucket
rate:provider:{id}     token bucket (per-second / hourly / daily)
worker:{id}:heartbeat  TTL key
lock:*                 distributed locks
stats:campaign:{id}    live counters for dashboards
```

Redis is an **accelerator**; job state of record lives in Postgres (ADR-002), so losing Redis loses no job (§47).

### Retry (§22, ADR-008)
Default schedule: `30s, 1m, 2m, 5m, 10m`. Max attempts and the schedule are configurable in `system_settings`. SMTP `4xx` and timeouts are transient; `5xx` is permanent (for example, `550` hard bounce leads to suppression).

### Scheduler (§21, §60)
The scheduler handles delayed campaign starts, promotion of due retries, lease recovery, quota enforcement, and capacity-aware dispatch. New work goes to the worker with the most free capacity (`capacity − current_rate`), without ever exceeding provider or user limits. It runs in-process in the backend for the MVP and becomes a dedicated service in Phase 3.

### Delivery events (§23, §61)
`Provider event → Receiver (webhook/DSN) → Validator (signature, schema, dedupe on provider_message_id+type) → Processor → email_events + counters + suppression`.

Statuses: `queued, processing, sent, delivered, deferred, bounced, failed, complained, unsubscribed`.

Domain events (internal bus, Proposed Redis Streams): `CampaignCreated, JobCreated, JobClaimed, JobCompleted, EmailSent, EmailDelivered, EmailBounced, EmailComplained, WorkerOnline, WorkerOffline, ProviderDisabled`.

---

## DS-06 Providers

**Status:** Implemented — Baseline + Proposed.

### Provider abstraction (ADR-007)

```python
class EmailProvider(Protocol):
    async def connect(self) -> None: ...
    async def send(self, message: OutboundMessage) -> SendResult: ...   # provider_message_id | error(code, transient)
    async def test(self) -> HealthCheckResult: ...                       # auth + connection + optional test send
    async def close(self) -> None: ...
```

The first implementation is `SmtpProvider` (aiosmtplib, STARTTLS/TLS). API-based providers are added later behind the same interface.

### Assignment (§16)
Admin assigns providers to users. A user can select only providers with an **active assignment** that are in state `ACTIVE` or `WARNING`.

### State machine & health (§17–§18)

```text
ACTIVE ⇄ WARNING ⇄ DEGRADED ──> DISABLED (auto at threshold, or manual)
DISABLED ──> ACTIVE (manual re-enable only; audit logged)
```

Proposed scoring: `health_score` (0–100) is computed over rolling windows (5 min and 1 h) from failure rate, auth/connection failures, timeouts, deferrals, bounce rate, and complaint rate. The formula is TBD (OQ-07).

| Score | State (Proposed) | Scheduling effect |
|---|---|---|
| ≥ 80 | ACTIVE | normal |
| 50–79 | WARNING | normal; alert admin |
| 20–49 | DEGRADED | throttle to 50% of limits; alert |
| < 20 for N consecutive windows | DISABLED | no new jobs; alert |

Never disable on a single failure (§18). Admin actions: Enable, Disable, Assign, Unassign, Test, Inspect. All are audit logged.

### Sender authentication (§3)
Provider setup shows SPF/DKIM/DMARC status for the from-domain (DNS lookup). Missing records trigger a warning.

---

## DS-07 Recipients & suppression

**Status:** Implemented — Baseline + Proposed.

### Upload & processing (§55–§56)
- **MVP:** CSV upload to the API with streaming parse and a size cap.
- **Phase 3:** presigned URL → object storage (S3/R2/MinIO) → validation worker.
- Pipeline: `validate file → parse (streamed) → normalize (trim, lowercase domain) → syntax check → deduplicate → suppression check → persist campaign_recipients → build batches`.
- Never hold a whole file in memory. Process in chunks of 10,000.

### Consent (§3)
Uploading a list requires the user to confirm the recipients opted in. The confirmation is stored with a timestamp on the campaign (Proposed).

### Suppression (§24)
- Types: `unsubscribe, hard_bounce, complaint, invalid, admin_blocked`.
- Checked twice: at batch build **and** by the worker just before send (catches suppressions added mid-campaign).
- Automatic inserts: hard bounce → `hard_bounce`; complaint/FBL → `complaint`; unsubscribe link → `unsubscribe`.
- Scope (global vs per-user) is unresolved (OQ-05). Proposed: `hard_bounce`, `complaint`, and `admin_blocked` are global; `unsubscribe` is per sending user.

### Unsubscribe (Proposed)
Every message carries `List-Unsubscribe` (mailto + https) and `List-Unsubscribe-Post: List-Unsubscribe=One-Click`, plus a footer link. The link token is HMAC-signed `(campaign_id, recipient_id)`, so no login is needed.

---

## DS-08 Worker

**Status:** Implemented — Baseline + Proposed.

**Lifecycle (§8):** `START → load config → authenticate → register → heartbeat loop → fetch → claim → process batch → send → report → next`.

- **Identity (§9):** `worker_id`, `name`, `version`, `capacity` (msgs/sec). A credential is issued at provisioning and supplied through env or a secret file, never hard-coded. It is exchanged for a short-lived access token (DS-10).
- **Heartbeat (§10):** every 10 s (Proposed) carrying `cpu, memory, active_jobs, current_rate, status`. The server marks the worker **WARNING** after 30 s without a heartbeat and **OFFLINE** after 90 s (Proposed, configurable; OQ-06). Leases of OFFLINE workers are expired so their jobs can be reclaimed.
- **Concurrency:** asyncio. There is one sender task pool per claimed job, bounded by `capacity` and the provider's `per_second_limit` (token bucket in Redis `rate:provider:{id}`, shared across workers).
- **Graceful shutdown:** on SIGTERM the worker stops claiming new jobs, finishes or releases in-flight jobs, and sends a final heartbeat with `status=offline`.
- **Network:** outbound only; no inbound public ports (§39).
- **Scaling (§41):** stateless; queue depth drives replica count (thresholds to be benchmarked).

---

## DS-09 API

**Status:** Implemented — Baseline + Proposed.

Conventions: versioned under `/api/v1/` (§53) · JSON · cursor pagination `?limit=50&cursor=…` with max limit 200 (§54) · errors as `{ "error": { "code", "message", "details" } }` · request size limits · input validation with Pydantic · every mutating admin call is audit logged.

| Surface | Endpoints (baseline §33) | Key operations (Proposed) |
|---|---|---|
| Admin | `/admin/users` | list, create, get, update, suspend, activate, set limits |
| | `/admin/campaigns` | list/filter by state, create, assign to user, pause, resume, cancel |
| | `/admin/providers` | CRUD, enable, disable, test, assign/unassign, health history |
| | `/admin/workers` | list, get, disable, rotate credential, heartbeat history |
| | `/admin/queues` | depth by state, list failed/retry/dead, requeue dead-letter |
| | `/admin/reports` | delivery, bounce, complaint, per-user |
| | `/admin/settings` | get/update system settings, API keys, audit logs |
| User | `/user/profile` | get/update own profile, change password, 2FA |
| | `/user/campaigns` | list own/assigned, get, update draft, upload recipients, start/pause/stop, live stats |
| | `/user/providers` | list assigned providers |
| | `/user/jobs` | list jobs for own campaigns |
| | `/user/reports` | own campaign reports |
| Worker | `/worker/register`, `/worker/heartbeat`, `/worker/jobs/claim`, `/worker/jobs/{id}/ack`, `/worker/jobs/{id}/failure`, `/worker/health` | plus `/worker/jobs/{id}/lease` (renew) and `/worker/token` (credential exchange) |
| Public (+) | `/u/{token}` (unsubscribe), `/hooks/providers/{provider_id}` (delivery events) | signed and rate-limited |

The full request/response schemas will be written to `docs/API.md` once endpoints are implemented. FastAPI's OpenAPI output is the canonical contract.

---

## DS-10 Authentication & authorization

**Status:** Implemented — Baseline (§32, §49). Mechanism is Proposed (ADR-005).

| Actor | Factors | Token |
|---|---|---|
| Admin | email + password + **TOTP 2FA (mandatory)** | access JWT (15 min) + rotating refresh (httpOnly cookie) |
| User | email/username + password, optional 2FA | same |
| Worker | worker_id + provisioned credential | short-lived access JWT (≤ 15 min), `aud=worker` |

- Passwords: Argon2id.
- Worker tokens can never be used on admin or user routes (separate audience), and vice versa.
- **Roles:** `SUPER_ADMIN, ADMIN, OPERATOR, USER, VIEWER`.
- **Permissions:** `users.read/write, campaigns.read/write/start/stop, providers.read/write, workers.read/write, reports.read, settings.write`.

Proposed role → permission matrix (to confirm):

| Permission | SUPER_ADMIN | ADMIN | OPERATOR | USER | VIEWER |
|---|---|---|---|---|---|
| users.read | ✓ | ✓ | ✓ | – | ✓ |
| users.write | ✓ | ✓ | – | – | – |
| campaigns.read | ✓ | ✓ | ✓ | own | ✓ |
| campaigns.write | ✓ | ✓ | ✓ | own | – |
| campaigns.start / stop | ✓ | ✓ | ✓ | own | – |
| providers.read | ✓ | ✓ | ✓ | assigned | ✓ |
| providers.write | ✓ | ✓ | – | – | – |
| workers.read | ✓ | ✓ | ✓ | – | ✓ |
| workers.write | ✓ | ✓ | – | – | – |
| reports.read | ✓ | ✓ | ✓ | own | ✓ |
| settings.write | ✓ | – | – | – | – |

---

## DS-11 Security, secrets & audit

**Status:** Implemented — Baseline.

- TLS everywhere. Secure headers, strict CORS (admin and user origins only), and CSRF protection for cookie-based refresh.
- **Secrets at rest (ADR-006):** envelope encryption, AES-256-GCM, with a key version stored alongside each secret so keys can rotate. `ENCRYPTION_KEY` comes from a secret manager in production.
- Never expose DB, SMTP, or Redis credentials, JWT secrets, or encryption keys to frontends (§34). Provider secrets are **write-only** in the API (never returned).
- **Audit log (§45):** `admin_id, action, resource, resource_id, old_value, new_value, ip, user_agent, timestamp`. Actions include `USER_SUSPENDED, SMTP_DISABLED, CAMPAIGN_CREATED, PROVIDER_ASSIGNED, WORKER_DISABLED, LIMIT_CHANGED`, among others. The table is append-only (no UPDATE/DELETE grant for the app role).
- Least-privilege DB roles: separate roles for the API and for migrations.

---

## DS-12 Admin Panel UI

**Status:** Implemented — Baseline (§6, §42). Visual design is Proposed.

**Navigation**

```text
Dashboard
Users       → All · Active · Suspended · Limits
Campaigns   → All · Pending · Processing · Completed · Failed
Providers   → SMTP/Email Providers · Assignments · Health · Disabled
Workers     → Nodes · Online · Offline · Capacity · Health
Queues      → Pending · Processing · Failed · Retry (+ Dead-letter)
Reports     → Delivery · Bounce · Complaint · User Reports
System      → Settings · API Keys · Audit Logs · Security
```

**Dashboard (real-time, §42):** tiles for Active Workers, Online Providers, Queue Depth, and Emails/sec; counters for Processed, Delivered, Failed, Bounced, Deferred, and Complaints; worker CPU/memory; a provider health list. Updates arrive over SSE (ADR-009).

**UI system (Proposed):** shadcn/ui on Tailwind; left sidebar with top bar; data tables with server-side cursor pagination and filters; status badges with one colour per state (ACTIVE/ONLINE/COMPLETED = green, WARNING/PAUSED = amber, DEGRADED/FAILED/OFFLINE = red, DISABLED/CANCELLED = grey); light and dark themes; destructive actions need a confirm dialog and are audit logged.

---

## DS-13 User Panel UI

**Status:** Implemented — Baseline (§7, §51). The panel must stay **intentionally simple**.

**Flow:** `Login → Dashboard → Assigned Campaign → Select Provider → Select Batch Size → Review → Start → Live Result → Report`.

Screens:
1. **Dashboard**: today's Assigned / Processed / Delivered / Failed / Remaining, current speed (msgs/sec), and a **Start Sending** button.
2. **Campaign**: name, subject, from name, from email, recipient file, batch size (500 / 1000 / 5000, capped by limits; OQ-10), provider dropdown (assigned + ACTIVE only), **Start**.
3. **Review** (Proposed, from §51): summary of recipients after dedupe and suppression, estimated duration at current limits, consent confirmation checkbox.
4. **Sending Monitor**: progress bar with %, Processed / Delivered / Failed / Remaining, speed, **Pause** and **Stop** (Stop requires confirmation).
5. **Report**: final counts, bounce/complaint breakdown, CSV export.

---

## DS-14 Infrastructure & deployment

**Status:** Implemented — Baseline.

- **Local/dev:** `docker-compose.yml` with services `backend, admin, user, worker, postgres, redis, nginx`. Workers are scaled independently (`docker compose up --scale worker=N`).
- **Environment (§35):** `APP_ENV, DATABASE_URL, REDIS_URL, JWT_SECRET, ENCRYPTION_KEY, ADMIN_API_URL, WORKER_API_URL, QUEUE_NAME, LOG_LEVEL`. Proposed additions: `CORS_ORIGINS, OBJECT_STORAGE_*, WORKER_ID, WORKER_CREDENTIAL, PUBLIC_BASE_URL`.
- **Production topology (§65):** `Internet → Cloudflare → Nginx → {Admin Web, User Web} → API cluster → PostgreSQL + Redis → Queue → Worker-01..N → Email Providers`.
- **Scaling path (§40):**
  1. 1 API · 1 PG · 1 Redis · 1 worker
  2. 2 API · 1 PG · Redis HA · 5 workers
  3. API cluster · DB HA · Redis HA · worker cluster · dedicated event processor and scheduler · monitoring cluster
- **DR (§46):** daily full backup plus PITR for Postgres; Redis persistence (AOF); config, secret, and infrastructure backups. Restores are tested periodically.

---

## DS-15 Observability, metrics & testing

**Status:** Implemented — Baseline.

- **Logs:** structured JSON with fields `timestamp, service, worker_id, job_id, event, duration_ms` (§43).
- **Metrics (§44):**
  - `jobs_created_total, jobs_completed_total, jobs_failed_total`
  - `emails_{processed,delivered,bounced,failed}_total`
  - `provider_errors_total, provider_latency`
  - `worker_cpu, worker_memory, queue_depth, queue_latency`
  - `sending_rate, delivery_rate, bounce_rate, complaint_rate`
- **Stack:** Prometheus, Grafana, Loki, OpenTelemetry (simplified for the first release).
- **Tests (§63):**
  - Unit: auth, campaign service, batch generator, retry logic, provider health, suppression.
  - Integration: API↔DB, API↔Queue, Worker↔Queue, Worker↔Provider (using a local SMTP sink such as Mailpit), Event↔DB.
  - Load: 10k jobs; 100k, 500k, and 1M recipients. Measure throughput, latency, queue delay, CPU, memory, DB/Redis load, and provider response.
- **Performance target (§20, §64):** 1,500,000/hour ≈ **416.67 msg/sec** average. This is a *capacity-planning target derived from configuration* (provider limits → user limits → worker capacity → scheduler), never hard-coded. Example: 6 workers × 80/sec = 480/sec theoretical.

---

## DS-16 Asynchronous bounces & complaints (DSN / ARF)

**Status:** Implemented (2026-09-29) — closes compliance gap R-07 / task P2-10.

Plain-SMTP providers accept a message (250) and report problems **later** by email: bounces as
Delivery Status Notifications (DSN, RFC 3464) and complaints as ARF feedback reports (RFC 5965, via
feedback loops). Without processing these, hard bounces and complaints are never suppressed.

**Ingestion paths** (ADR-011), both per provider and both optional:
1. **Bounce mailbox (IMAP).** Admin configures host/port/TLS/username/password(encrypted)/folder on the
   provider. The scheduler leader polls every `bounce_poll_interval_seconds` (default 60) as part of its tick, fetches up to 200 unseen messages, processes them and marks them `\Seen` (or deletes them if
   configured). Point the provider's bounce/Return-Path and FBL address at this mailbox.
2. **Signed raw-message endpoint.** `POST /api/v1/hooks/providers/{id}/inbound` with the raw RFC 5322
   message as body (`Content-Type: message/rfc822`), signed exactly like delivery webhooks (HMAC over
   `timestamp.body`). For relays that can pipe mail to a command (e.g. Postfix `pipe` + the provided
   `infrastructure/deployment/forward-bounce.sh`).

**Parsing** (`backend/app/services/inbound.py`):

| Report | Detection | Result |
|---|---|---|
| DSN | `multipart/report; report-type=delivery-status` | one event per `Final-Recipient` block |
| ARF | `multipart/report; report-type=feedback-report` | `complained` (Feedback-Type abuse/fraud/virus/other) |
| anything else | – | counted as *unrecognised*, left for a human (never guessed) |

DSN classification per recipient (`Action` + `Status`):
- `failed` + `5.1.x` (bad address/mailbox/domain) or `5.2.1` (disabled) → **hard bounce** → global suppression.
- other `failed` + `5.x.x` → **soft bounce** (stored as a deferral event, like soft bounces from webhooks; not suppressed, not counted as bounced).
- `delayed` or `4.x.x` → **deferred** (informational).
- `delivered` / `relayed` / `expanded` → **delivered**.

**Correlation** to a recipient, in order: the original `Message-ID` found in the attached original message
/ headers part (our Message-ID is the stored `provider_message_id`); otherwise `X-OmniSend-Campaign` +
`Final-Recipient`/`Original-Recipient` (campaign + normalised email). Unmatched reports are counted
but ignored — the system never suppresses an address it cannot tie to a message it sent.

Idempotency: events reuse the webhook path (`events.ingest`) and its unique index, so re-reading a
mailbox never double-counts. Security: reports are processed only from the configured mailbox or a
valid signature; bodies are size-limited (1 MB) and parsed with the stdlib email parser (no HTML
rendering, no attachments executed).

---

## DS-17 API keys

**Status:** Implemented (2026-09-29) — task P2-15.

Programmatic access for integrations (CRM, data pipelines) to the **User API** only (ADR-012).

- **Format:** `osk_<8-char prefix>_<32-byte secret>`, shown **once**. Stored as SHA-256 hash + prefix
  (for display/lookup). Optional expiry (days). Revocable; revocation is immediate.
- **Owner:** always a USER account. Created by the user (Profile → API keys) or by an admin with
  `users.write` (Admin → System → API Keys) on the user's behalf.
- **Scopes:** subset of the USER permissions — `campaigns.read`, `campaigns.write`, `campaigns.start`,
  `campaigns.stop`, `providers.read`, `reports.read`. Effective permissions = scopes ∩ owner's role
  permissions, evaluated on every request (a suspended owner disables the key).
- **Auth:** `Authorization: Bearer osk_…` on `/api/v1/user/*`. Keys are rejected on admin, auth and
  worker APIs. No cookies, so no CSRF surface. Profile-changing endpoints (password, 2FA, API-key
  management) are **not** allowed with an API key.
- **Rate limit:** `api_key_requests_per_minute` (default 600) per key, Redis counter → `429`.
- **Audit & usage:** `API_KEY_CREATED` / `API_KEY_REVOKED` audited; `last_used_at` / `last_used_ip`
  updated at most once per minute.
- **Table `api_keys`:** id, user_id, name, prefix (unique), key_hash (unique), scopes (JSONB),
  created_by, created_at, expires_at, revoked_at, last_used_at, last_used_ip.

---

## Architecture Decision Records (ADRs)

Format: **Context → Decision → Consequences**. Status: Proposed / Accepted / Superseded.

### ADR-001: Backend stack: Python, FastAPI, SQLAlchemy 2.x, Alembic, Pydantic v2
- **Status:** Accepted (baseline §37, implemented 2026-09-29)
- **Context:** The spec recommends this stack. The worker is also Python/asyncio, so the two can share code.
- **Decision:** Use the stack as specified; async SQLAlchemy with asyncpg.
- **Consequences:** One language for backend and worker; Pydantic schemas can be shared between API and worker.

### ADR-002: PostgreSQL is the job system of record; Redis is the fast path
- **Status:** Accepted (implemented 2026-09-29)
- **Context:** §47 requires that no job be silently lost when Redis fails. Redis lists alone are not durable enough.
- **Decision:** Every job exists as a row in `jobs`. Redis holds job ids and dispatch hints, rate limits, locks, and live stats. Redis can be rebuilt from Postgres at any time (`status IN (pending, retry)`).
- **Consequences:** Queue recovery is a DB query. Claims touch Postgres, which is fine at batch granularity (416 msg/s ÷ 1000 per batch ≈ 0.4 claims/s). RabbitMQ or Kafka can be re-evaluated in Phase 4.

### ADR-003: Monorepo
- **Status:** Accepted (baseline §36, implemented 2026-09-29)
- **Decision:** Keep `admin-web`, `user-web`, `backend`, `worker`, and `infrastructure` in one repository with per-package tooling.

### ADR-004: Workers claim jobs through the Worker API, not directly from Postgres
- **Status:** Accepted (implemented 2026-09-29)
- **Context:** Workers run on remote VPSs (§8). Giving them DB credentials widens the blast radius.
- **Decision:** Workers talk to the Worker API for register, heartbeat, claim, lease, ack, and failure. They use Redis only for shared rate-limit buckets, reached with a restricted ACL user; this is revisited if Redis is not reachable from worker networks.
- **Consequences:** Worker credentials stay isolated (§68). The API becomes a dependency of the data plane, so it must be horizontally scaled.

### ADR-005: JWT access tokens + rotating refresh tokens; TOTP for admins
- **Status:** Accepted (implemented 2026-09-29)
- **Decision:** 15-minute access JWT; refresh token in an httpOnly Secure SameSite=Strict cookie, rotated on use and revocable (stored hashed). TOTP 2FA is mandatory for admin roles. Workers get a separate audience and signing key.

### ADR-006: Envelope encryption for secrets
- **Status:** Accepted (implemented 2026-09-29)
- **Decision:** AES-256-GCM per secret with a random nonce. The data key comes from `ENCRYPTION_KEY` (dev) or a KMS / secret manager (prod). `key_version` is stored per secret to support rotation.

### ADR-007: Provider abstraction with SMTP as the first adapter
- **Status:** Accepted (implemented 2026-09-29)
- **Decision:** Define the `EmailProvider` protocol (DS-06). Implement `SmtpProvider` first and add API-based adapters later without touching the scheduler or worker loop.

### ADR-008: Retry classification & schedule
- **Status:** Accepted (implemented 2026-09-29)
- **Decision:**
  - SMTP 4xx, timeouts, and connection errors are transient: retry on the schedule `30s, 1m, 2m, 5m, 10m`.
  - SMTP 5xx is permanent: no retry. A 5.1.x bounce also creates a `hard_bounce` suppression.
  - Max attempts defaults to 5, configurable.
  - Retries happen **per recipient** inside a job. A job fails as a whole only on systemic errors (auth or connection).
- **Consequences:** The spec's example schedule is not strictly exponential. It is kept as configurable data, not a formula.

### ADR-009: Server-Sent Events for real-time dashboards
- **Status:** Accepted (implemented 2026-09-29)
- **Decision:** Use SSE; updates are one-way, work over plain HTTP/Nginx, and are simpler than WebSockets. Clients fall back to polling. The server pushes aggregated stats from Redis `stats:*` at 1–2 s intervals.

### ADR-010: Lease-based atomic job claiming
- **Status:** Accepted (implemented 2026-09-29)
- **Decision:** Claim with `SELECT … FOR UPDATE SKIP LOCKED` and set `lease_expires_at`. Workers renew the lease every `lease/3`. The scheduler returns expired leases to `retry`. Each claim creates a `job_attempts` row, and ack/failure must reference the current attempt.
- **Consequences:** Only one worker can own a job (§31), and crashed workers' jobs are recovered automatically (§47).

### ADR-011: Bounce/complaint ingestion via IMAP polling + signed raw endpoint (no inbound SMTP server)
- **Status:** Accepted (2026-09-29)
- **Context:** Plain-SMTP providers deliver DSNs and ARF reports by email. Running our own inbound MX
  adds an internet-facing SMTP service to secure and operate.
- **Decision:** Read a dedicated mailbox over IMAP (works with any mail host) and additionally accept
  raw messages on a signed HTTP endpoint for relays that can pipe mail. Parsing is shared.
- **Consequences:** No new public port. Latency = poll interval (default 60 s), acceptable for
  suppression. IMAP credentials are encrypted like provider secrets.

### ADR-012: API keys are for the User API only
- **Status:** Accepted (2026-09-29)
- **Context:** Admin access requires TOTP (§32); a static key would bypass it and widen the blast radius.
- **Decision:** Keys belong to USER accounts, carry explicit scopes, and are accepted only on
  `/api/v1/user/*` (excluding credential/profile management). Admin automation stays interactive.
- **Consequences:** Integrations can create/upload/start/monitor campaigns; admin tasks cannot be
  scripted with keys (revisit with a separate, IP-restricted service-account design if needed).

---

## Open questions

Resolve these before implementing the affected areas. Record the answer, the date, and who decided.

**2026-09-29:** to unblock Phase 0/1 the *Proposed answer* of every question below was adopted as the working default (status **Accepted (default)**). Any of them can still be overridden by the product owner; changing one requires updating the affected DS sections and the code. OQ-15 remains a question for the document author, but no work is blocked on it.

| ID | Question | Affects | Proposed answer | Status |
|---|---|---|---|---|
| OQ-01 | What is the tenancy model? Is each *user* a tenant, or is there an organisation/account above users? §2 says "multi-tenant" but no tenant table exists. | DS-03, all queries | Start with user = tenant; add `organizations` later if needed | Accepted (default) |
| OQ-02 | Who creates campaigns? §1 says users *receive assigned* campaigns, but §7 lets users enter name, subject, and file. What does `campaigns.user_id` mean: owner or assignee? | DS-04, DS-09, DS-13 | Admin *or* user can create; `user_id` = the user who sends; `created_by` = creator | Accepted (default) |
| OQ-03 | Where is the email content? `campaigns` has no body/template fields. Is personalisation (merge variables) needed? | DS-03, worker | Add `html_body`, `text_body`, and simple `{{var}}` merge from `campaign_recipients.variables` | Accepted (default) |
| OQ-04 | How do jobs map to recipients? `jobs` has no batch reference, even though `campaign_batches` exists. | DS-03, DS-05 | Add `jobs.batch_id`; one job per batch | Accepted (default) |
| OQ-05 | Is suppression global or per user? | DS-07 | Bounce/complaint/admin_blocked global; unsubscribe per user | Accepted (default) |
| OQ-06 | What are the heartbeat interval and the WARNING/OFFLINE thresholds? | DS-08 | 10 s / 30 s / 90 s, configurable | Accepted (default) |
| OQ-07 | What is the provider health formula, and where are the state thresholds (DEGRADED has no example)? | DS-06 | See DS-06 table; tune after load tests | Accepted (default) |
| OQ-08 | The admin menu campaign states (Pending/Processing/Completed/Failed) differ from the §50 state machine. | DS-04, DS-12 | Menu = filtered views over the state machine | Accepted (default) |
| OQ-09 | Job status vocabulary and the missing columns (`attempt_id`, `idempotency_key`, lease) required by §31/§47. | DS-03, DS-05 | Adopt DS-05 state machine + ADR-010 columns | Accepted (default) |
| OQ-10 | Is batch size limited to fixed options (500/1000/5000) or free input within limits? | DS-13 | Presets + custom, capped by limits | Accepted (default) |
| OQ-11 | If a user has several assigned providers, is one chosen per campaign, or can load be spread across them? | DS-05, DS-06 | One provider per campaign (MVP). Any later spreading must respect each provider's own limits and policies, never evade them | Accepted (default) |
| OQ-12 | How are bounce and complaint events received per provider (webhooks, DSN mailbox, FBL)? | DS-05, DS-07 | Webhooks for API providers; DSN mailbox parsing for raw SMTP (Phase 2) | Accepted (default) |
| OQ-13 | What does "Assigned 100,000 / Today's Sending" mean (daily quota vs assigned campaign volume)? What is `subscriptions` for? | DS-03, DS-13 | Daily quota from `user_limits.daily_limit`; `subscriptions` = billing plan (defer) | Accepted (default) |
| OQ-14 | What retention and deletion policy applies to recipient PII and `email_events` (GDPR, erasure requests)? | DS-03, DS-11 | Configurable retention (e.g. 13 months events); erasure keeps a hashed suppression entry | Accepted (default) |
| OQ-15 | `ARCHITECTURE.md` skips §19 and §59. Was content lost? | all | Ask the document author | Accepted (default) |

---

## Implementation notes (v0.2.0)

What was actually built, and every place the implementation refines the design above. Code paths are
relative to the repository root.

### Code map

| Design | Code |
|---|---|
| DS-03 data model | `backend/app/models/`, migration `backend/migrations/versions/0001_initial_schema.py` |
| DS-04 campaign state machine | `backend/app/services/campaigns.py` (`start/pause/resume/cancel`, `build_batches`, `check_completion`) |
| DS-05 jobs, leases, retry, events | `backend/app/services/jobs.py`, `services/quotas.py`, `services/events.py`, `scheduler/loop.py` |
| DS-06 providers & health | `backend/app/services/providers.py`, `services/health.py`, `worker/app/providers/smtp.py` |
| DS-07 recipients & suppression | `backend/app/services/recipients.py`, `services/suppression.py`, `api/v1/public.py` |
| DS-08 worker | `worker/app/` (`main.py`, `sender/runner.py`, `rate_limit/bucket.py`, `retry/classify.py`) |
| DS-09 API | `backend/app/api/v1/` — see `docs/API.md` |
| DS-10/11 auth, RBAC, security | `backend/app/core/security.py`, `core/rbac.py`, `api/deps.py`, `services/auth.py`, `services/audit.py` — see `docs/SECURITY.md` |
| DS-12/13 UI | `admin-web/`, `user-web/`, shared `packages/web-shared/` |
| DS-14 infrastructure | `docker-compose.yml`, `infrastructure/`, `docs/DEPLOYMENT.md` |
| DS-15 observability & tests | `/metrics`, `infrastructure/monitoring/`, `.github/workflows/ci.yml`, `docs/TESTING.md` |

### Refinements and decisions taken during implementation

1. **Queue = Postgres (ADR-002/010).** The Redis `queue:*` lists from DS-05 were not needed. Claims
   select up to 25 claimable jobs `FOR UPDATE OF jobs SKIP LOCKED`, skip over-quota ones, and lease
   the first eligible job. Redis holds quota counters, rate-limit buckets, the scheduler lock, replay
   caches and live per-second stats.
2. **Recipient ↔ job mapping (OQ-04).** `campaign_recipients.batch_id` references `campaign_batches`,
   and each batch has exactly one job (`jobs.batch_id` is unique). Retries move deferred recipients
   into a new batch with `retry_round + 1` and a new job whose `available_at` follows the retry
   schedule. A recipient's `attempts` counts real delivery attempts, so the per-recipient retry limit
   is exact.
3. **Batch building** runs in the scheduler, not the start request. The suppression check and
   batching are single set-based SQL statements per 50k-recipient chunk (`row_number()` grouping),
   so memory use is constant (§14).
4. **Effective batch size** = min(user choice, user max batch, system max, user hourly/daily limit,
   provider hourly/daily limit), so a job can never exceed a quota window it must fit in.
5. **Quotas** are reserved atomically at claim (Lua check-and-increment over the user and provider
   hour/day counters) and the unused part is refunded when the attempt ends (ack, failure, release,
   lease expiry). Per-second limits are enforced by the worker with a Redis token bucket shared per
   provider (local fallback without Redis).
6. **Idempotency.** Every worker write carries `attempt_id`; only the job's current attempt may
   write. Recipient updates apply only while the recipient is `queued` in that job's batch. Results
   stream to the API every second or every 200 recipients, so a crash can re-send at most the
   in-flight messages (at-least-once, with a small window).
7. **Worker receives provider credentials** in the claim payload over TLS (the alternative, a DB or
   secret-store connection from each worker, would violate ADR-004).
8. **Delivered semantics.** A provider without a delivery webhook counts SMTP acceptance as
   `delivered`. With a webhook, `delivered` comes from events, and a later hard bounce moves the
   message from sent/delivered to bounced.
9. **Health scoring (OQ-07).** Window score = 100 × (1 − (systemic errors×1 + bounces×2 +
   deferrals×0.5 + complaints×50) / attempts), smoothed 50/50 with the previous score; thresholds
   and min-sample are system settings. DEGRADED providers send at 50% of their per-second limit.
   DISABLED needs 3 consecutive bad windows and manual re-enable.
10. **Campaign completion.** A campaign is FAILED when nothing was sent but something failed, or
    when the failure ratio exceeds `campaign_failure_threshold` (default 0.5); otherwise COMPLETED.
11. **Unsubscribe (DS-07).** An HMAC-signed `(campaign_id, recipient_id)` token. GET shows a
    confirmation page (mail scanners cannot unsubscribe people); POST performs it and also serves
    RFC 8058 one-click. Only https `List-Unsubscribe` is emitted (no mailbox exists for mailto).
12. **Suppression scope (OQ-05).** hard_bounce, complaint, invalid and admin_blocked are global;
    unsubscribe applies to the sending user. Complaint entries cannot be removed from the admin UI.
13. **From-domain rule.** A campaign's From domain must equal its provider's From domain
    (`require_from_domain_match`, on by default) to keep SPF/DKIM alignment.
14. **Sessions.** Access tokens carry a session-version claim derived from `password_changed_at`,
    so a password change invalidates outstanding access tokens immediately. Refresh cookies are
    per app (`osp_admin_rt`, `osp_user_rt`) so both panels can run on one host during development.
15. **Frontend.** A shared package (`packages/web-shared`) was introduced from the start instead of
    "later if needed" (DS-01), because the API client, auth flow and component set are identical.
    Charts use the validated reference palette (three slots max, legend + table view for
    accessibility). Dashboards poll (2–5 s) until SSE lands (ADR-009, P3-03).
16. **Additional permissions** beyond §49: `queues.read/write`, `suppressions.read/write`,
    `audit.read`, `settings.read`.
17. **nginx** serves both panels (user :8080, admin :8081) and a worker-API-only listener (:8082).
    The panels return 404 for `/api/v1/worker/*`, and inline scripts are forbidden by CSP.

18. **Worker message rendering (v0.3.0).** `CompiledCampaign` builds the constant headers and MIME
    skeleton once per job and renders each recipient straight to wire bytes (base64 bodies, CRLF,
    RFC 2047 only when needed, boundary starting with `=_` which base64 cannot contain). The reference
    `EmailMessage` builder remains the oracle in tests and the path for non-ASCII addresses (SMTPUTF8).
19. **SMTP connection reuse (v0.3.0).** `SmtpPoolRegistry` keeps one pool per provider configuration
    (incl. password fingerprint and `max_connections`) for the life of the worker process.
20. **Provider `max_connections` (v0.3.0, migration 0002).** Simultaneous SMTP sessions one worker opens
    to the provider; overrides `SMTP_CONNECTIONS_PER_JOB` and caps the shared pool. It is per worker,
    not global: providers usually limit per client IP.
21. **Scheduler wake-up (v0.3.0).** Start/resume pushes `wake:scheduler`; only the lock holder waits on
    it (BLPOP with the tick interval as timeout), so non-leaders cannot swallow a wake-up.
22. **Startup bootstrap lock.** Reference data and the bootstrap admin run under a PostgreSQL advisory
    lock so several API processes can start simultaneously.

23. **Explicit `reports_delivery` flag (DS-16).** Whether SMTP acceptance counts as *delivered* used to be
    implied by "has a webhook secret". Since a secret may now exist only to sign raw bounce reports, the
    provider has an explicit `reports_delivery` flag (migration 0003 sets it for providers that already
    had a webhook secret, preserving behaviour).
24. **Bounce mailbox safety.** The IMAP host goes through the same SSRF guard as the SMTP connection test
    (link-local / metadata addresses refused); plain IMAP is only used when the server offers STARTTLS.
    Messages are fetched with `BODY.PEEK[]` and flagged only after processing; unrecognised mail stays
    unread for a human. A provider can only affect messages sent through it (`campaign.provider_id`).

25. **API keys (DS-17).** Resolved in `current_principal` by the `osk_` prefix; the principal gets
    `app="api"`, which `admin_principal` rejects and `user_principal` accepts. Account endpoints (profile
    edit, password, 2FA, key management) and `/auth/me` use a session-only dependency. The per-key limit
    is a fixed one-minute Redis window. At most 20 active keys per user. Shared UI lives in
    `packages/web-shared/src/ui/apiKeys.tsx`.
26. **`useAction({ failed })`.** Operations that report failure in a 200 body (connection tests, mailbox
    polls) show an error toast instead of a success toast.

### Not yet implemented (tracked in PROGRESS.md)
SSE (P3-03), object-storage
uploads (P3-04/05), a dedicated scheduler and event-processor process (P3-02/06), partitioning and
retention (P3-11/12), and Phase 4 hardening.

---

## Design changelog

Newest first.

- **2026-09-29 · v0.4.2**: DS-17 implemented; implementation notes 25–26.
- **2026-09-29 · v0.4.1**: DS-16 implemented; implementation notes 23–24 (explicit `reports_delivery` flag, bounce-mailbox safety).
- **2026-09-29 · v0.4.0**: Added DS-16 (DSN/ARF ingestion), DS-17 (API keys), ADR-011, ADR-012 for the Phase 2 remainder.
- **2026-09-29 · v0.3.0**: Performance pass — implementation notes 18–22 (compiled messages, SMTP pool reuse, provider `max_connections`, scheduler wake-up, bootstrap lock). Measurements in `docs/PERFORMANCE.md`.
- **2026-09-29 · v0.2.0**: Implemented DS-01…DS-15 and ADR-001…ADR-010. OQ-01…OQ-14 adopted as defaults. Added §Implementation notes (code map + 17 refinements).
- **2026-09-29 · v0.1.0**: Initial design document extracted from `ARCHITECTURE.md`. Added DS-01…DS-15, proposed ADR-001…ADR-010, and logged OQ-01…OQ-15.
