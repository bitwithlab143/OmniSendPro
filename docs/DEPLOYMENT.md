# Deployment

## Topology (ARCHITECTURE.md §39, §65, design DS-19/DS-23)
```text
Internet → Cloudflare/TLS → nginx (web image)
             :8080 User panel  + /api
             :8081 Admin panel + /api     (restrict by IP / SSO in front if possible)
             :8082 Worker API only        (allow-list worker IPs)
         → API (N replicas, RUN_SCHEDULER=false)  ─┐
           runner (scheduler leader + processors) ─┼→ PostgreSQL primary (+ read replica) + Redis (+ Sentinel)
Worker pool instances × N (outbound only) → :8082 ─┘            → SMTP providers
Browser → object storage (presigned POST, large recipient files)
```

Reference deployments: `docker-compose.yml` (single host, with `worker`, `storage`, `monitoring` and `dev`
profiles) and `infrastructure/k8s/omnisend.yaml` (Kubernetes with KEDA-autoscaled workers).

## Checklist
1. **Secrets** in a secret manager: `JWT_SECRET`, `WORKER_JWT_SECRET` (≥32 chars, different), `ENCRYPTION_KEY`
   (`v1:<base64 32 bytes>`), database/Redis passwords. Every secret can also be given as a file with
   `<NAME>_FILE` (Docker/Kubernetes secrets, secret-manager CSI): `DATABASE_URL_FILE`, `JWT_SECRET_FILE`,
   `ENCRYPTION_KEY_FILE`, …; workers accept `WORKER_CREDENTIAL_FILE` and `REDIS_URL_FILE`. With
   `APP_ENV=production` the API refuses to start with weak or missing secrets and disables `/api/docs`.
2. **TLS** everywhere; `COOKIE_SECURE=true` (automatic in production); set HSTS at the TLS terminator.
3. `PUBLIC_BASE_URL` = public User panel origin (used for unsubscribe links). `CORS_ORIGINS` = the two panel origins.
4. Run migrations before rolling the API: `docker compose run --rm migrate` (or `alembic upgrade head`; the
   Kubernetes manifest runs it as an init container).
5. First admin: set `BOOTSTRAP_ADMIN_*` for the first boot only, or `python -m app.cli create-admin`.
6. **Background work** runs in the `runner` service (`python -m app.runner`): the scheduler (leader-elected,
   run two for failover) and the event/import processor (scale freely). API replicas use
   `RUN_SCHEDULER=false`; without a runner, provider reports and imports are not processed.
7. Expose `/metrics` only to the monitoring network (not proxied by the panels' nginx servers).
8. Sender authentication: publish SPF, DKIM and DMARC for every From domain (Provider → *Check*).
9. Process bounces and complaints for every provider (design DS-16), with one or more of:
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
   Webhooks and raw reports are answered with 202 and applied by the runner's processor within seconds.
10. **Large recipient files** (optional, design DS-21): set `OBJECT_STORAGE_ENDPOINT`, `OBJECT_STORAGE_BUCKET`,
    `OBJECT_STORAGE_ACCESS_KEY`, `OBJECT_STORAGE_SECRET_KEY` (and `OBJECT_STORAGE_PUBLIC_ENDPOINT` if browsers
    reach the bucket at a different URL). The bucket must be private, allow CORS `POST` from both panel
    origins, and expire `imports/` objects after 2 days. Set `STORAGE_UPLOAD_ORIGIN` on the web image so the
    panels' CSP allows the upload. `docker compose --profile storage up -d` runs MinIO with the bucket.
11. **Template tags** (design DS-24): set the `template_timezone` setting (e.g. `Asia/Dhaka`) for `#DATE#`
    and `#TIME#`.

## Scaling
- **API:** stateless — scale replicas.
- **Runner:** 2 replicas give scheduler failover within ~15 s; add processor replicas
  (`python -m app.runner processor`) if the event inbox backs up (`omnisend_event_inbox_oldest_seconds`).
- **Workers:** provision a **pool** (Admin → Workers → Provision → *Pool*) and run any number of instances
  with the same `WORKER_ID`/`WORKER_CREDENTIAL`; each registers under `WORKER_INSTANCE` (default: hostname).
  Scale on `omnisend_workers_desired` (open jobs ÷ `autoscale_jobs_per_worker`, bounded by
  `autoscale_min_workers`/`autoscale_max_workers`): KEDA `ScaledObject` in `infrastructure/k8s/`, or
  `infrastructure/autoscale/compose_autoscaler.py` for Docker Compose (scale up at once, down after a
  cooldown). Instances gone for 24 h are removed automatically.
- **Faster sending:** the worker pipelines SMTP when providers advertise PIPELINING/CHUNKING (design
  DS-22); raise the provider's *Max connections* within its terms. See `docs/PERFORMANCE.md`.

## High availability (design DS-23)
- **PostgreSQL:** primary + streaming replica (managed service or Patroni in production). Set
  `DATABASE_READ_URL` to the replica: reports and report exports read from it, everything else uses the
  primary. `infrastructure/ha/docker-compose.ha.yml` is a local reference stack (primary, replica with
  `pg_basebackup`, Redis + replica + 3 Sentinels) used to rehearse replication and failover.
- **Redis:** Sentinel with quorum 2 of 3. Set `REDIS_SENTINELS=host:26379,…` and `REDIS_SENTINEL_MASTER`
  on the API, runner and workers; `REDIS_URL` then only supplies credentials and the DB number. Redis
  holds no job state: a failover (or even total Redis loss) only costs a few seconds of rate-limit and
  live-stat accuracy — the Redis-failure drill in `tests/e2e/test_redis_failure.py` proves no message is
  lost or duplicated.
- **Redis ACL:** `infrastructure/redis/render-acl.sh` renders a users file with an `app` user and a
  `worker` user limited to the provider rate-limit keys (`rl:provider:*`); the default user is disabled.
  Give workers `REDIS_URL=redis://worker:<pw>@…`.

## Backups & disaster recovery (design DS-23)
- **Daily logical backup:** `infrastructure/backup/backup.sh` (`pg_dump` custom format + SHA-256, optional
  copy to `BACKUP_S3_URI`, keeps `BACKUP_KEEP_DAYS`). Schedule it (cron / Kubernetes CronJob).
- **Point-in-time recovery:** enable WAL archiving with `infrastructure/backup/postgresql-wal-archive.conf`
  (or wal-g / pgBackRest for object storage). RPO ≤ 5 minutes with `archive_timeout = 300`.
- **Restore:** `restore.sh <dump> <new database URL>` verifies the checksum and restores next to the live
  database; switch `DATABASE_URL` once verified. It never restores over an existing live database.
- **Drill:** `restore_drill.sh` backs up, restores into a scratch database and compares every table's row
  count and the migration revision. It runs in the test suite; run it weekly in production and alert on
  failure.
- **Retention:** partitions for events, audit logs, health logs and heartbeats are created ahead and dropped
  after `retention_*_days` (Settings) by the hourly maintenance job (design DS-18).

## Monitoring & alerting (design DS-23)
`docker compose --profile monitoring up -d` starts Prometheus (scraping the API, with
`infrastructure/monitoring/alerts.yml`), Alertmanager, Grafana (provisioned *OmniSendPro overview*
dashboard) and Loki + Promtail for logs. Put `slack_webhook_url` and `smtp_password` files in
`infrastructure/monitoring/secrets/` and edit the addresses in `alertmanager.yml`: critical alerts go to Slack
and email, warnings to email. Validate changes with
`promtool check config`, `promtool test rules alerts_test.yml` and `amtool check-config`.
Tracing: install the `otel` extra and set `OTEL_EXPORTER_OTLP_ENDPOINT` (and optionally `OTEL_SERVICE_NAME`).

## Rotations
- Encryption key: append `v2:<key>` to `ENCRYPTION_KEY` (new data uses the last key; old data still
  decrypts), then re-save provider secrets and remove `v1` once none remain.
- Worker credential: Admin → Workers → rotate; update the worker's secret (pool instances pick it up on
  their next token exchange).
- Provider secret / webhook secret / bounce-mailbox password: Provider detail page.

## Local compose
See the README quick start. `docker compose --profile worker up -d worker` runs a worker;
`--profile dev` adds a Mailpit SMTP sink, `--profile storage` MinIO, `--profile monitoring` the monitoring stack.
