# OmniSendPro

Multi-tenant, permission-based email campaign and delivery platform: an **Admin control plane**, a
deliberately simple **User panel**, and horizontally scalable **delivery workers** fed by a durable,
queue-based pipeline.

```text
Admin / User web ──► Control API (FastAPI) ──► PostgreSQL (source of truth) + Redis (limits, locks, live stats)
                                  ▲
               Worker API (pull)  │
                                  ▼
                 worker-01 … worker-N ──► approved SMTP providers ──► recipients
                                                    │
                         delivery webhooks / unsubscribes ──► events ──► reports
```

| Document | Purpose |
|---|---|
| [`ARCHITECTURE.md`](./ARCHITECTURE.md) | Target architecture (baseline spec) |
| [`design.md`](./design.md) | Concrete designs, ADRs, resolved questions |
| [`PROGRESS.md`](./PROGRESS.md) | What is done / in progress / next |
| [`docs/`](./docs) | API, database, worker, deployment, security, testing guides |

## What's in the box

- **backend/** – FastAPI + SQLAlchemy 2 (async) + Alembic. Admin, User, Worker and public APIs,
  Argon2id + JWT with rotating refresh cookies, mandatory TOTP for admins, RBAC, audit log,
  AES-256-GCM secret encryption, Postgres-backed job queue with lease-based atomic claims,
  retry/dead-letter, quotas, provider health scoring, suppression, unsubscribe, delivery webhooks,
  reports, Prometheus metrics and an in-process scheduler (Redis leader lock).
- **worker/** – asyncio delivery worker: pooled SMTP, shared Redis token bucket per provider,
  lease renewal, incremental idempotent result reporting, graceful shutdown.
- **admin-web/**, **user-web/** – React 19 + TypeScript + Vite + Tailwind 4 apps sharing
  **packages/web-shared/** (API client, auth, shadcn-style components, charts; light/dark).
- **infrastructure/** – Dockerfiles, nginx (strict CSP, rate limits, separate worker-API listener).
- **tests/e2e/** – black-box test: API + scheduler + worker process + SMTP sink.

## Quick start (Docker)

```bash
cp .env.example .env
# set POSTGRES_PASSWORD, JWT_SECRET, WORKER_JWT_SECRET, ENCRYPTION_KEY, BOOTSTRAP_ADMIN_PASSWORD
docker compose up -d --build
```

- Admin panel: <http://localhost:8081> – sign in with `BOOTSTRAP_ADMIN_EMAIL`; you will be asked to
  enrol an authenticator app (2FA is mandatory for admins).
- User panel: <http://localhost:8080>

Then, in the admin panel: create a user → add a provider (Providers → Add, then *Test connection*) →
assign it to the user → provision a worker (Workers → Provision) and put its `WORKER_ID` /
`WORKER_CREDENTIAL` in `.env`:

```bash
docker compose --profile worker up -d worker
```

For a local SMTP sink: `docker compose --profile dev up -d mailpit` and use host `mailpit`, port
`1025`, security *None* (UI at <http://localhost:8025>).

## Local development

Requirements: Python 3.11+, Node 22+, PostgreSQL 16, Redis 7.

```bash
python -m venv .venv
.venv/bin/pip install -e "backend[dev]" -e "worker[dev]"
npm install

# database
createdb omnisend && (cd backend && ../.venv/bin/alembic upgrade head)

# API (+ scheduler) on :8000 — creates the first super admin from BOOTSTRAP_ADMIN_* if none exists
(cd backend && BOOTSTRAP_ADMIN_EMAIL=admin@example.com BOOTSTRAP_ADMIN_PASSWORD='Change-Me-Passw0rd!' \
  ../.venv/bin/uvicorn main:app --reload)

npm run dev:admin   # http://localhost:5173 (proxies /api to :8000)
npm run dev:user    # http://localhost:5174

# worker (after provisioning it in the admin panel)
(cd worker && WORKER_API_URL=http://localhost:8000 WORKER_ID=worker-01 WORKER_CREDENTIAL=... \
  REDIS_URL=redis://localhost:6379/0 ../.venv/bin/python worker.py)
```

Alternatively create an admin from the CLI: `cd backend && ../.venv/bin/python -m app.cli create-admin --email you@example.com`.

## Tests

```bash
(cd backend && ../.venv/bin/pytest -q)   # 53 integration + unit tests (real Postgres & Redis)
(cd worker  && ../.venv/bin/pytest -q)   # 17 unit tests
.venv/bin/pytest tests/e2e -q             # full pipeline with a real SMTP sink
npm run typecheck && npm test && npm run build
```

See [`docs/TESTING.md`](./docs/TESTING.md) for environment variables and what each suite covers.

## Compliance

OmniSendPro is for **consent-based** email only. Senders must confirm opt-in before a campaign
starts; every message carries RFC 8058 one-click unsubscribe; bounces, complaints and unsubscribes are
suppressed automatically; provider limits are enforced, never evaded (no IP rotation or filter
bypassing — see ARCHITECTURE.md §3).
