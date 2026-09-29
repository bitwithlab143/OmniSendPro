# Deployment

## Topology (ARCHITECTURE.md §39, §65)
```text
Internet → Cloudflare/TLS → nginx (web image)
             :8080 User panel  + /api
             :8081 Admin panel + /api     (restrict by IP / SSO in front if possible)
             :8082 Worker API only        (allow-list worker IPs)
         → backend (N replicas; one scheduler leader via Redis lock) → PostgreSQL + Redis
Worker VPS × N (outbound only, no inbound ports) → :8082 → SMTP providers
```

## Checklist
1. **Secrets** in a secret manager: `JWT_SECRET`, `WORKER_JWT_SECRET` (≥32 chars, different), `ENCRYPTION_KEY`
   (`v1:<base64 32 bytes>`), database/Redis passwords. With `APP_ENV=production` the API refuses to
   start with weak or missing secrets and disables `/api/docs`.
2. **TLS** everywhere; `COOKIE_SECURE=true` (automatic in production); set HSTS at the TLS terminator.
3. `PUBLIC_BASE_URL` = public User panel origin (used for unsubscribe links). `CORS_ORIGINS` = the two panel origins.
4. Run migrations before rolling the API: `docker compose run --rm migrate` (or `alembic upgrade head`).
5. First admin: set `BOOTSTRAP_ADMIN_*` for the first boot only, or `python -m app.cli create-admin`.
6. Expose `/metrics` only to the monitoring network (not proxied by the panels' nginx servers).
7. Sender authentication: publish SPF, DKIM and DMARC for every From domain (Provider → *Check*).
8. Process bounces and complaints for every provider (design DS-16), with one or more of:
   - **Delivery webhook** (Provider → *Enable delivery webhook*) for providers that post JSON events. Tick
     *The provider sends "delivered" events* in the provider settings only if it really does; otherwise
     SMTP acceptance is counted as delivered.
   - **Bounce mailbox** (Provider → *Bounces & complaints* → *Connect mailbox*): an IMAP mailbox that
     receives the Return-Path bounces and feedback-loop (ARF) reports. It is polled every
     `bounce_poll_interval_seconds` (Settings, default 60). Use a dedicated mailbox; implicit TLS or STARTTLS
     is required.
   - **MTA pipe**: pipe the bounce address into `infrastructure/deployment/forward-bounce.sh` with
     `OMNISEND_INBOUND_URL` / `OMNISEND_WEBHOOK_SECRET` (shown when the webhook secret is rotated).
   Register the bounce address with the mailbox providers' feedback loops (e.g. Yahoo CFL, Microsoft JMRP).

## Scaling
- **API:** stateless — scale replicas; the scheduler runs in whichever replica holds `lock:scheduler`
  (`RUN_SCHEDULER=false` to exclude a replica). Move to a dedicated scheduler service in Phase 3.
- **Workers:** one `WORKER_ID` per process/VPS; add more for throughput.
- **Postgres:** primary + replica, PITR backups. **Redis:** AOF persistence; losing Redis loses no jobs
  (Postgres holds them) but resets live stats and in-flight quota counters.

## Rotations
- Encryption key: append `v2:<key>` to `ENCRYPTION_KEY` (new data uses the last key; old data still
  decrypts), then re-save provider secrets and remove `v1` once none remain.
- Worker credential: Admin → Workers → rotate; update the worker's secret.
- Provider secret / webhook secret / bounce-mailbox password: Provider detail page.

## Local compose
See the README quick start. `docker compose --profile worker up -d worker` runs a worker;
`--profile dev` adds a Mailpit SMTP sink.
